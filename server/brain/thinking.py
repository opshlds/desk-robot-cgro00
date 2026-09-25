"""Thinking: sends a question (and the newest camera frame) to the language
model and hands back what Rocky should say and feel, one sentence at a time
as the model writes it.

The request is the OpenAI-style chat API, which OpenRouter, Anthropic and
OpenAI all serve, so the provider is a base URL and the model is a string
(config.LLM_BASE_URL / config.MODEL; the key is LLM_API_KEY in server/.env).

Rocky has real abilities the model can call (tool use): `look` moves the
head and comes back with a fresh camera frame from the new angle,
`track_face` starts/stops following the human, and `time_in` gives the exact
time somewhere else (brain/clock.py). main.py supplies the functions that
actually do those things.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import openai

from . import config
from . import personality

_EMOTION_TAG = re.compile(r"^\s*\[(\w+)\]\s*", re.S)
# A sentence ends at . ! or ? followed by a space — but not at an ellipsis:
# "just... clear answer." is one sentence, not two.
_SENTENCE_END = re.compile(r"(?<=[!?])\s+|(?<=[^.]\.)\s+")

# An action returns (text for the model, optional fresh camera JPEG).
Action = Callable[[dict], tuple[str, bytes | None]]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "look",
            "description": (
                "Move your head to look somewhere. Use it whenever you are asked to "
                "look left/right/down/up/around, or need to see something outside "
                "the current picture. Always call it when asked, even if you think "
                "you are already there: the result tells you where your head really "
                "is and whether it is at a limit. You get a fresh camera image "
                "afterwards; describe only what that image shows. Up is as high as "
                "'level': your neck cannot tilt above eye level."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right", "down", "level", "center"],
                        "description": (
                            "left/right turn the head only (no nod); down/level nod only (no turn); "
                            "center = straight ahead and level"
                        ),
                    },
                    "degrees": {
                        "type": "number",
                        "description": "How far: 5-60 for left/right, 5-60 for down. Omit for a normal look.",
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "time_in",
            "description": (
                "The exact local time and date somewhere else, and how far ahead or behind "
                "your human it is. Use it for ANY question about the time in another city, "
                "country or time zone, or the time difference to one. Never work time "
                "differences out yourself; read the answer this gives you."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "place": {
                        "type": "string",
                        "description": "An IANA time zone name if you know it (Asia/Dhaka, Europe/London), else the city or country.",
                    },
                },
                "required": ["place"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_face",
            "description": "Start or stop following the human's face with your head.",
            "parameters": {
                "type": "object",
                "properties": {"on": {"type": "boolean"}},
                "required": ["on"],
            },
        },
    },
]


@dataclass
class Situation:
    """What is true right now, told to the model with each question: a short
    note (the time, which parts of the body are connected) and which
    abilities can actually work (None = all of them)."""
    note: str = ""
    abilities: frozenset[str] | None = None


@dataclass
class Reply:
    text: str
    emotion: str


class Interrupted(Exception):
    """The human started talking again; drop this reply."""


def _image_part(jpeg: bytes) -> dict:
    data = base64.standard_b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


class RobotBrain:
    def __init__(
        self,
        actions: dict[str, Action] | None = None,
        situation: Callable[[], Situation] | None = None,
    ) -> None:
        self.client = openai.OpenAI(
            base_url=config.LLM_BASE_URL,
            api_key=os.environ.get("LLM_API_KEY", "missing"),
            default_headers={"X-Title": "desk-robot"},  # shows up in OpenRouter's usage page; others ignore it
        )
        self.history: list[dict] = []
        self.actions = actions or {}
        self.situation = situation      # called once per question
        self._tools: list[dict] = []    # abilities offered for the question being answered
        self.emotion = "neutral"        # emotion of the reply in progress
        self._inflight: tuple[int, dict] | None = None  # (index, user message) being answered

    def ask(self, question: str, jpeg: bytes | None = None, camera_wanted: bool = False) -> Reply:
        """The whole reply at once. See reply() for the streaming form."""
        sentences = list(self.reply(question, jpeg, camera_wanted=camera_wanted))
        return Reply(" ".join(sentences), self.emotion)

    def reply(
        self,
        question: str,
        jpeg: bytes | None = None,
        on_emotion: Callable[[str], None] | None = None,
        cancelled: threading.Event | None = None,
        camera_wanted: bool = False,
    ) -> Iterator[str]:
        """Rocky's reply, one sentence at a time as the model writes it.

        on_emotion(name) is called as soon as the emotion tag at the start of
        the reply is known — before the first sentence — so the face can
        change while he's still composing. Set `cancelled`, or close the
        generator early, and the question is dropped from his memory as if it
        was never asked. `camera_wanted` with no `jpeg` means the question
        was about seeing but the camera had no fresh picture; he's told so,
        otherwise he answers from memory and claims to see things.
        """
        content: list[dict] = [{"type": "text", "text": question}]
        if jpeg is not None:
            content.append({"type": "text", "text": "(Live picture from your camera, taken just now, because the question seems to be about what you can see. If it isn't, ignore the picture.)"})
            content.append(_image_part(jpeg))
        elif camera_wanted:
            content.append({"type": "text", "text": "(Your camera has no fresh picture right now, so you cannot see anything at the moment.)"})
        now = self.situation() if self.situation is not None else Situation()
        if now.note:
            # On the question, not the system prompt: the system prompt stays
            # identical every turn, so the model server can reuse its cache.
            content.append({"type": "text", "text": now.note})
        self._tools = [t for t in TOOLS if t["function"]["name"] in self.actions
                       and (now.abilities is None or t["function"]["name"] in now.abilities)]
        user_msg = {"role": "user", "content": content}
        self.history.append(user_msg)
        mark = len(self.history) - 1
        self._inflight = (mark, user_msg)
        self.emotion = "neutral"
        spoken: list[str] = []
        error: tuple[str, str] | None = None

        gen = self._converse(on_emotion, cancelled or threading.Event())
        try:
            for sentence in gen:
                spoken.append(sentence)
                yield sentence
        except (Interrupted, GeneratorExit):
            gen.close()
            self._forget(mark, user_msg)
            raise
        except openai.APIConnectionError:
            error = ("Brain cannot reach internet. Bad bad bad.", "sad")
        except openai.AuthenticationError:
            error = ("Brain has no key. Set LLM_API_KEY, human.", "sad")
        except openai.APIStatusError as e:
            error = (f"Ow. Brain hurts. API error {e.status_code}.", "sad")
        finally:
            gen.close()

        if error is not None:
            self._forget(mark, user_msg)
            self.emotion = error[1]
            if on_emotion is not None:
                on_emotion(self.emotion)
            yield error[0]
            return

        if spoken:
            # Remember what was actually said, so he can't refer back to a
            # part that got cut.
            self.history.append({"role": "assistant", "content": f"[{self.emotion}] {' '.join(spoken)}"})
        self._inflight = None
        self._strip_images()
        self._trim_history()

    def abandon(self) -> None:
        """Forget the question being answered right now (the human kept talking)."""
        if self._inflight is not None:
            self._forget(*self._inflight)

    def _forget(self, mark: int, user_msg: dict) -> None:
        # Only cut if that question is still where we left it: a newer one may
        # already have taken its place.
        if mark < len(self.history) and self.history[mark] is user_msg:
            del self.history[mark:]
        self._inflight = None

    def _converse(self, on_emotion: Callable[[str], None] | None, cancelled: threading.Event) -> Iterator[str]:
        """One question, possibly several model calls if it uses its abilities.
        Yields sentences as they complete."""
        nudged = False
        limit = config.REPLY_MAX_SENTENCES
        for _ in range(5):
            if cancelled.is_set():
                raise Interrupted()
            stream = self.client.chat.completions.create(
                model=config.MODEL,
                max_tokens=200,  # backstop; the sentence limit does the real work
                messages=[{"role": "system", "content": personality.SYSTEM_PROMPT}, *self.history],
                tools=self._tools or openai.NOT_GIVEN,
                stream=True,
            )
            buf = ""                 # text not yet released as a sentence
            raw: list[str] = []      # everything the model wrote this round
            tag_decided = False
            calls: dict[int, dict] = {}
            spoken = 0
            try:
                for chunk in stream:
                    if cancelled.is_set():
                        raise Interrupted()
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    for tc in delta.tool_calls or []:
                        entry = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function is not None:
                            if tc.function.name:
                                entry["name"] = tc.function.name
                            if tc.function.arguments:
                                entry["arguments"] += tc.function.arguments
                    if not delta.content:
                        continue
                    raw.append(delta.content)
                    buf += delta.content
                    if not tag_decided:
                        buf, tag_decided = self._take_emotion_tag(buf, final=False)
                        if not tag_decided:
                            continue
                        if on_emotion is not None:
                            on_emotion(self.emotion)
                    parts = _SENTENCE_END.split(buf)
                    while len(parts) > 1:  # everything but the last piece is a whole sentence
                        s = parts.pop(0).strip()
                        if s:
                            spoken += 1
                            yield s
                        if spoken >= limit:
                            break
                    buf = parts[-1] if parts else ""
                    if spoken >= limit:
                        print(f"  (trimmed reply to {limit} sentences)")
                        buf = ""
                        break
            finally:
                stream.close()

            if not tag_decided:
                buf, _ = self._take_emotion_tag(buf, final=True)
                if on_emotion is not None and (buf.strip() or not calls):
                    on_emotion(self.emotion)

            if calls:
                # He decided to do something: say any lead-in, run it, tell him what happened.
                if buf.strip():
                    yield buf.strip()
                    buf = ""
                self.history.append({
                    "role": "assistant",
                    "content": "".join(raw),
                    "tool_calls": [
                        {"id": c["id"], "type": "function",
                         "function": {"name": c["name"], "arguments": c["arguments"]}}
                        for _, c in sorted(calls.items())
                    ],
                })
                fresh: bytes | None = None
                for _, c in sorted(calls.items()):
                    if cancelled.is_set():
                        raise Interrupted()
                    try:
                        args = json.loads(c["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    action = self.actions.get(c["name"])
                    if action is None:
                        text = f"unknown ability {c['name']}"
                    else:
                        try:
                            text, img = action(args)
                        except Exception as e:  # the robot didn't cooperate; say so
                            text, img = f"could not do that: {e}", None
                        if img:
                            fresh = img
                    print(f"  [{c['name']} {args} -> {text}]")
                    self.history.append({"role": "tool", "tool_call_id": c["id"], "content": text})
                if fresh is not None:
                    self.history.append({
                        "role": "user",
                        "content": [{"type": "text", "text": "Camera view after moving:"}, _image_part(fresh)],
                    })
                continue

            if not "".join(raw).strip() and not nudged:
                # Some models go quiet right after using an ability. Ask once.
                nudged = True
                self.history.append({"role": "user", "content": "(Tell your human what you just did, in one short line.)"})
                continue
            if nudged and self.history and self.history[-1].get("role") == "user":
                self.history.pop()  # don't keep the nudge in the transcript
            tail = buf.strip()
            if tail and spoken < limit:
                yield tail
            elif not spoken and not tail:
                self.emotion = "thinking"
                if on_emotion is not None:
                    on_emotion(self.emotion)
                yield "Hmm. Words did not come. Ask again."
            return

        self.emotion = "thinking"
        if on_emotion is not None:
            on_emotion(self.emotion)
        yield "Too many things at once. Ask again, human."

    def _take_emotion_tag(self, buf: str, final: bool) -> tuple[str, bool]:
        """Look for "[happy] " at the start of the reply. Returns (text with the
        tag removed, decided). Not decided means: need more text to know."""
        lead = buf.lstrip()
        if not lead:
            return buf, final
        if not lead.startswith("["):
            return buf, True
        if "]" in lead:
            m = _EMOTION_TAG.match(lead)
            if m:
                candidate = m.group(1).lower()
                if candidate in config.EMOTIONS:
                    self.emotion = candidate
                return _EMOTION_TAG.sub("", lead, count=1), True
            return buf, True
        if final or len(lead) > 24:
            return buf, True  # a "[" with no "]" in sight: not a tag
        return buf, False

    def _strip_images(self) -> None:
        """Replace old camera frames with a note. Keeping every image in the
        history would make each later question cost far more."""
        for m in self.history:
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                parts = [p for p in m["content"] if p.get("type") == "text"]
                if len(parts) != len(m["content"]):
                    parts.append({"type": "text", "text": "(a camera image was attached here)"})
                m["content"] = parts

    def _trim_history(self) -> None:
        max_msgs = config.MAX_HISTORY_TURNS * 2
        if len(self.history) > max_msgs:
            cut = len(self.history) - max_msgs
            # Never start the history on a tool result: back up to a user turn.
            while cut < len(self.history) and self.history[cut].get("role") != "user":
                cut += 1
            del self.history[:cut]
