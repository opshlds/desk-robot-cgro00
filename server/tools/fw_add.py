"""Put a board's app image into the brain's firmware store (server/firmware/).

    cd ~/desk-robot/server && source .venv/bin/activate
    python -m tools.fw_add face ~/face-fw-0.4.1-app.bin      # copies it in, records sha256 + size
    python -m tools.fw_add camera some.bin --version 0.2.1   # a file with another name
    python -m tools.fw_add --list                            # what the brain can install

Then `ota face` in the brain console. Use the *-app.bin (written at 0x10000),
never the -factory.bin. Dropping a correctly named file into
server/firmware/<board>/ works too; this script also records its sha256 in
manifest.json, so a file that changes later is refused.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from brain import config, firmware


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", nargs="?", help="face | camera | neck")
    ap.add_argument("image", nargs="?", type=Path, help="the -app.bin file")
    ap.add_argument("--version", help="version, when the file isn't named <prefix>-<x.y.z>-app.bin")
    ap.add_argument("--list", action="store_true", help="list the store")
    ap.add_argument("--dir", type=Path, default=config.FIRMWARE_DIR, help=f"store (default {config.FIRMWARE_DIR})")
    a = ap.parse_args(argv)
    store = firmware.Store(a.dir)
    if not a.list:
        if not a.board or not a.image:
            ap.error("give a board and an image, or --list")
        try:
            img = store.add(a.board, a.image, a.version)
        except (ValueError, OSError) as e:
            print(f"fw_add: {e}", file=sys.stderr)
            return 1
        print(f"added {img.board} {img.version}: {img.path} ({img.size:,} bytes, sha256 {img.sha256})")
    for board, images in store.scan().items():
        print(f"{board:<7} " + (", ".join(i.version for i in images) or "(none)"))
    for p in store.problems:
        print(f"  ! {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
