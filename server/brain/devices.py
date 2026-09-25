"""Which connected board does which job.

The brain used to allow one robot that did everything. HAIL-E's robot is
several boards, each doing part of the job:

    Yahboom (through server/bridge)   mic + speaker
    XIAO ESP32-S3 Sense               camera (later: neck)
    Waveshare AMOLED                  face

Each board says which roles it has in its hello (`"roles": [...]`). A hello
without roles is the original single-board desk-robot, which gets all of
them. A role belongs to one board at a time: a second board claiming a role
that is already taken is refused, so two speakers never talk at once.

Server -> board messages are routed by type (ROUTES). Board -> server
messages are only accepted from the board that owns the matching role
(mic audio from the mic, camera frames from the camera, speak_done from the
speaker).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

ROLES = ("mic", "speaker", "camera", "neck", "face")

# Message type -> roles that receive it. None = every connected board.
ROUTES: dict[str, tuple[str, ...] | None] = {
    "emotion": None,          # the face shows it; the voice board may too
    "asleep": None,           # the voice board ends its session, the face shuts its eyes
    "pan": ("neck",),
    "tilt": ("neck",),
    "glance": ("neck",),
    "speak_begin": ("speaker",),
    "speak_end": ("speaker",),
    "volume": ("speaker",),
    "mic": ("mic",),
    "stream": ("camera",),
}


class RoleTaken(Exception):
    """A board asked for a role another connected board already has."""


@dataclass
class Device:
    conn: Any                       # the WebSocket (anything hashable in tests)
    roles: frozenset[str]
    who: str = "?"
    fw: str = "?"
    peer: str = "?"
    extra: dict = field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.who} (fw {self.fw}, {self.peer}) [{', '.join(sorted(self.roles))}]"


def parse_roles(hello: dict) -> frozenset[str]:
    """Roles from a hello. Missing = the classic all-in-one robot. Unknown
    role names are ignored; an empty or all-unknown list is an error."""
    raw = hello.get("roles")
    if raw is None:
        return frozenset(ROLES)
    if not isinstance(raw, list):
        raise ValueError("roles must be a list")
    roles = frozenset(r for r in raw if isinstance(r, str) and r in ROLES)
    if not roles:
        raise ValueError(f"no known roles in {raw!r} (known: {', '.join(ROLES)})")
    return roles


class Registry:
    def __init__(self) -> None:
        self._devices: dict[Any, Device] = {}

    def __len__(self) -> int:
        return len(self._devices)

    def __iter__(self):
        return iter(list(self._devices.values()))

    def add(self, device: Device) -> None:
        clash = {r: d for d in self._devices.values() for r in device.roles & d.roles}
        if clash:
            taken = ", ".join(f"{r} (by {d.who})" for r, d in sorted(clash.items()))
            raise RoleTaken(f"role already taken: {taken}")
        self._devices[device.conn] = device

    def remove(self, conn: Any) -> Device | None:
        return self._devices.pop(conn, None)

    def get(self, conn: Any) -> Device | None:
        return self._devices.get(conn)

    def owner(self, role: str) -> Device | None:
        for d in self._devices.values():
            if role in d.roles:
                return d
        return None

    def conn_for(self, role: str) -> Any | None:
        d = self.owner(role)
        return d.conn if d else None

    def has_role(self, conn: Any, role: str) -> bool:
        d = self._devices.get(conn)
        return d is not None and role in d.roles

    def targets(self, message_type: str) -> list[Any]:
        """Connections a server->board message of this type goes to.
        Types not in ROUTES go to every board (they ignore what they don't know)."""
        roles = ROUTES.get(message_type)
        if roles is None:
            return [d.conn for d in self._devices.values()]
        return [d.conn for d in self._devices.values() if d.roles & set(roles)]

    def summary(self) -> list[dict]:
        return [{"who": d.who, "fw": d.fw, "peer": d.peer, "roles": sorted(d.roles)}
                for d in self._devices.values()]


def roles_text(roles: Iterable[str]) -> str:
    return ", ".join(sorted(roles))
