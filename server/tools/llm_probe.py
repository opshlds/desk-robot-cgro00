"""Send the brain's exact request (system prompt, tools, streaming, 200-token cap)
to the configured LLM and print everything that comes back.
Run from server/:  python -m tools.llm_probe "Tell me about yourself"
"""
import os
import sys

import openai

from brain import config, personality
from brain.thinking import TOOLS

question = " ".join(sys.argv[1:]) or "Tell me about yourself"
client = openai.OpenAI(base_url=config.LLM_BASE_URL, api_key=os.environ.get("LLM_API_KEY", "missing"))


def run(label: str, use_tools: bool, max_tokens: int) -> None:
    print(f"\n=== {label}: tools={'on' if use_tools else 'off'}, max_tokens={max_tokens} ===")
    stream = client.chat.completions.create(
        model=config.MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": personality.SYSTEM_PROMPT},
                  {"role": "user", "content": [{"type": "text", "text": question}]}],
        tools=TOOLS if use_tools else openai.NOT_GIVEN,
        stream=True,
    )
    content, reasoning, tools, finish = [], [], [], None
    for chunk in stream:
        if not chunk.choices:
            continue
        ch = chunk.choices[0]
        d = ch.delta
        if d.content:
            content.append(d.content)
        extra = getattr(d, "model_extra", None) or {}
        r = extra.get("reasoning_content") or extra.get("reasoning")
        if r:
            reasoning.append(r)
        for tc in d.tool_calls or []:
            if tc.function is not None:
                tools.append(f"{tc.function.name or ''}{tc.function.arguments or ''}")
        if ch.finish_reason:
            finish = ch.finish_reason
    print(f"finish_reason : {finish}")
    print(f"content       : {''.join(content)!r}")
    print(f"reasoning     : {len(''.join(reasoning))} chars; starts {''.join(reasoning)[:150]!r}")
    print(f"tool calls    : {''.join(tools)!r}")


run("1. as the brain sends it", use_tools=True, max_tokens=200)
run("2. without tools", use_tools=False, max_tokens=200)
run("3. with room to finish", use_tools=True, max_tokens=2000)
