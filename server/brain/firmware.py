"""Over-the-air firmware updates for the robot's own boards (face, camera, neck).

The boards pull their new app image from the brain; nothing pushes into them.

  Store   server/firmware/<board>/<prefix>-<version>-app.bin, git-ignored, plus
          server/firmware/manifest.json (board, version, file, sha256, size).
          `python -m tools.fw_add <board> <file>` copies an image in and records
          it; a file simply dropped into the right folder is found too (its
          sha256 is computed when it is used). A manifest entry whose sha256
          or size no longer matches its file is refused.

  Serving A small HTTP listener (config.OTA_PORT, 8767) that only exists while
          an update is waiting to be fetched. It answers exactly one thing:
          GET /fw/<token>. Each token is random, belongs to one image and one
          board address, works once and expires after config.OTA_TOKEN_SECONDS.
          Anything else gets 404/403 and nothing else is ever served, so the
          live view's password (port 8766) needs no exemption.

The Yahboom is not handled here: Xiaozhi has its own OTA through the bridge.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# console name -> (store folder = the board's role, file name prefix)
BOARDS: dict[str, str] = {"face": "face-fw", "camera": "cam-fw", "neck": "neck-fw"}
ALIASES = {"cam": "camera", "head": "neck"}

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([a-z0-9]+))?$")
_FILE_RE = re.compile(r"^(face|cam|neck)-fw-(\d+\.\d+\.\d+(?:-[a-z0-9]+)?)-app\.bin$")
MAX_IMAGE = 8 * 1024 * 1024   # sanity cap; each board checks its own slot (face 6.25 MB, camera 3.25, neck 1.87)


def board_name(name: str) -> str | None:
    name = name.strip().lower()
    name = ALIASES.get(name, name)
    return name if name in BOARDS else None


def parse_version(v: str) -> tuple | None:
    """'0.4.1' -> (0, 4, 1, 1, ''); '0.4.2-rbtest' -> (0, 4, 2, 0, 'rbtest').
    A suffixed version sorts just below the plain one (like a pre-release)."""
    m = _VERSION_RE.match(str(v).strip().lower())
    if not m:
        return None
    suffix = m.group(4) or ""
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if suffix else 1, suffix)


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Image:
    board: str          # face | camera | neck
    version: str
    path: Path
    sha256: str
    size: int

    @property
    def key(self) -> tuple:
        return parse_version(self.version) or (0, 0, 0, 0, "")


class Store:
    """The image folder on ai1. Rescanned on every use (it is tiny)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.problems: list[str] = []
        self._hashes: dict[Path, tuple[float, int, str]] = {}

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    def _manifest(self) -> dict[str, dict]:
        """file path (relative, forward slashes) -> entry."""
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            self.problems.append(f"manifest.json unreadable ({e}); using the files alone")
            return {}
        entries = raw.get("images", []) if isinstance(raw, dict) else []
        return {str(e.get("file", "")): e for e in entries if isinstance(e, dict)}

    def _hash(self, path: Path) -> tuple[str, int]:
        st = path.stat()
        cached = self._hashes.get(path)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2], st.st_size
        digest = sha256_of(path.read_bytes())
        self._hashes[path] = (st.st_mtime, st.st_size, digest)
        return digest, st.st_size

    def scan(self) -> dict[str, list[Image]]:
        """Every usable image per board, oldest first. Problems (bad names,
        manifest mismatches) are collected in self.problems."""
        self.problems = []
        manifest = self._manifest()
        found: dict[str, list[Image]] = {b: [] for b in BOARDS}
        for board, prefix in BOARDS.items():
            folder = self.root / board
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if not path.is_file() or path.suffix != ".bin":
                    continue
                m = _FILE_RE.match(path.name)
                if not m or not path.name.startswith(prefix + "-"):
                    self.problems.append(f"{board}/{path.name}: not named {prefix}-<x.y.z>-app.bin; ignored")
                    continue
                digest, size = self._hash(path)
                if not 0 < size <= MAX_IMAGE:
                    self.problems.append(f"{board}/{path.name}: {size} bytes; ignored")
                    continue
                rel = f"{board}/{path.name}"
                entry = manifest.get(rel)
                if entry is not None and (str(entry.get("sha256", "")).lower() != digest or int(entry.get("size", -1)) != size):
                    self.problems.append(f"{rel}: sha256/size differ from manifest.json (file changed or corrupt); refused")
                    continue
                found[board].append(Image(board, m.group(2), path, digest, size))
        for images in found.values():
            images.sort(key=lambda i: i.key)
        return found

    def find(self, board: str, version: str | None = None) -> Image | None:
        images = self.scan().get(board, [])
        if version is None:
            plain = [i for i in images if i.key[3] == 1]      # never pick a test build by itself
            return plain[-1] if plain else None
        want = parse_version(version)
        return next((i for i in images if i.key == want), None)

    def add(self, board: str, src: Path, version: str | None = None) -> Image:
        """Copy an image into the store and record it in the manifest."""
        board = board_name(board) or ""
        if not board:
            raise ValueError(f"board must be one of {', '.join(BOARDS)}")
        src = Path(src)
        prefix = BOARDS[board]
        name = src.name
        if version is not None:
            name = f"{prefix}-{version}-app.bin"
        m = _FILE_RE.match(name)
        if not m or not name.startswith(prefix + "-"):
            raise ValueError(f"name it {prefix}-<x.y.z>-app.bin (or pass --version)")
        data = src.read_bytes()
        if not 0 < len(data) <= MAX_IMAGE:
            raise ValueError(f"{len(data)} bytes is not an app image")
        if data[0] != 0xE9:
            raise ValueError("not an ESP32 app image (first byte isn't 0xE9)")
        if len(data) > 0x8002 and data[0x8000:0x8002] == b"\xaa\x50":
            raise ValueError("that's a factory image (bootloader + partition table); use the -app.bin")
        dest = self.root / board / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        entries = self._manifest()
        rel = f"{board}/{name}"
        entries[rel] = {"board": board, "version": m.group(2), "file": rel,
                        "sha256": sha256_of(data), "size": len(data),
                        "added": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"images": sorted(entries.values(), key=lambda e: e["file"])}, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(self.manifest_path)
        return Image(board, m.group(2), dest, sha256_of(data), len(data))


@dataclass
class Grant:
    token: str
    image: Image
    data: bytes           # the exact bytes whose sha256 went to the board
    peer: str             # only this address may fetch it
    expires: float
    used: bool = False


@dataclass
class _Listener:
    server: asyncio.base_events.Server
    grants: set[str] = field(default_factory=set)


class ImageServer:
    """One-time download links for board images."""

    def __init__(self, port: int, token_seconds: float = 300.0,
                 log: Callable[[str], None] = print) -> None:
        self.port = port
        self.token_seconds = token_seconds
        self.log = log
        self.grants: dict[str, Grant] = {}
        self._listeners: dict[str, _Listener] = {}   # bind address -> listener
        self._bind_of: dict[str, str] = {}           # token -> bind address
        # Called when a board has fetched (or failed to fetch) its image.
        self.on_served: Callable[[Grant, bool, str], None] | None = None

    def listening(self) -> list[str]:
        return sorted(self._listeners)

    async def grant(self, image: Image, peer: str, host: str) -> tuple[str, Grant]:
        """A URL on `host` (the address the board reached the brain on) that
        serves `image` once, to `peer` only. Re-reads and re-hashes the file,
        so what is served is exactly what the sha256 describes."""
        data = image.path.read_bytes()
        if len(data) != image.size or sha256_of(data) != image.sha256:
            raise ValueError(f"{image.path.name} changed on disk since it was listed; run ota again")
        token = secrets.token_urlsafe(24)
        g = Grant(token, image, data, peer, time.monotonic() + self.token_seconds)
        await self._listen(host)
        self.grants[token] = g
        self._bind_of[token] = host
        self._listeners[host].grants.add(token)
        asyncio.get_running_loop().call_later(self.token_seconds + 0.5, lambda: asyncio.ensure_future(self._expire(token)))
        url_host = f"[{host}]" if ":" in host else host
        return f"http://{url_host}:{self.port}/fw/{token}", g

    async def revoke(self, token: str) -> None:
        await self._expire(token, quiet=True)

    async def close(self) -> None:
        for token in list(self.grants):
            await self._expire(token, quiet=True)
        for host in list(self._listeners):
            await self._stop(host)

    async def _listen(self, host: str) -> None:
        if host in self._listeners:
            return
        server = await asyncio.start_server(self._handle, host, self.port, limit=8192)
        self._listeners[host] = _Listener(server)

    async def _stop(self, host: str) -> None:
        lst = self._listeners.pop(host, None)
        if lst is not None:
            lst.server.close()
            try:
                await asyncio.wait_for(lst.server.wait_closed(), 2)
            except asyncio.TimeoutError:
                pass

    async def _drop(self, token: str) -> None:
        self.grants.pop(token, None)
        host = self._bind_of.pop(token, None)
        lst = self._listeners.get(host) if host else None
        if lst is not None:
            lst.grants.discard(token)
            if not lst.grants:
                await self._stop(host)            # nothing left to fetch: close the port

    async def _expire(self, token: str, quiet: bool = False) -> None:
        g = self.grants.get(token)
        if g is None:
            return
        if not g.used and not quiet:
            self.log(f"ota: the {g.image.board} image link expired unused")
            if self.on_served:
                self.on_served(g, False, "the link expired before the board fetched it")
        await self._drop(token)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = (writer.get_extra_info("peername") or ("?",))[0]
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split(" ")
        token = ""
        if len(parts) == 3 and parts[0] == "GET" and parts[1].startswith("/fw/"):
            token = parts[1][4:]
        g = self.grants.get(token) if token else None
        try:
            if g is None:
                await self._reply(writer, 404, b"not found")
            elif g.peer != peer:
                self.log(f"ota: refused {peer} asking for the {g.image.board} image (it belongs to {g.peer})")
                await self._reply(writer, 403, b"not yours")
            elif g.used or time.monotonic() > g.expires:
                await self._reply(writer, 410, b"gone")
                await self._drop(token)
            else:
                g.used = True                      # one download, even if it fails halfway
                ok, why = await self._send_image(writer, g)
                if self.on_served:
                    self.on_served(g, ok, why)
                await self._drop(token)
        finally:
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), 5)
            except (asyncio.TimeoutError, ConnectionError, OSError):
                pass

    async def _send_image(self, writer: asyncio.StreamWriter, g: Grant) -> tuple[bool, str]:
        head = ("HTTP/1.1 200 OK\r\n"
                "Content-Type: application/octet-stream\r\n"
                f"Content-Length: {len(g.data)}\r\n"
                "Cache-Control: no-store\r\n"
                "Connection: close\r\n\r\n").encode()
        try:
            writer.write(head)
            view = memoryview(g.data)
            for i in range(0, len(view), 16384):
                writer.write(view[i:i + 16384])
                await asyncio.wait_for(writer.drain(), 30)
            return True, ""
        except (asyncio.TimeoutError, ConnectionError, OSError) as e:
            return False, f"download broke off ({type(e).__name__})"

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, code: int, body: bytes) -> None:
        reason = {403: "Forbidden", 404: "Not Found", 410: "Gone"}.get(code, "Error")
        writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/plain\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        try:
            await asyncio.wait_for(writer.drain(), 5)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
