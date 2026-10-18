"""Online SQLite backup: ``python -m lrb.service.backup <db> <dest_dir> [keep]``."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from .store import Store


def main() -> int:
    db, dest = sys.argv[1], Path(sys.argv[2])
    keep = int(sys.argv[3]) if len(sys.argv) > 3 else 14
    out = dest / f"lrb_service_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.db"
    Store(db).backup(str(out))
    backups = sorted(dest.glob("lrb_service_*.db"))
    for old in backups[:-keep]:
        old.unlink()
    print(f"backup written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
