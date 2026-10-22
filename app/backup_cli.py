"""اجرای پشتیبان‌گیری/بررسی پشتیبان از systemd timer در سرور."""
from __future__ import annotations

import argparse
import json

from . import backup


def main() -> int:
    parser = argparse.ArgumentParser(description="پشتیبان‌گیری امن از سامانه‌ی حسابداری")
    parser.add_argument("--verify", nargs="?", const="__latest__", metavar="[FILE]",
                        help="بررسی یک پشتیبان موجود؛ بدون FILE آخرین پشتیبان بررسی می‌شود")
    args = parser.parse_args()
    if args.verify:
        if args.verify == "__latest__":
            rows = backup.list_backups()
            if not rows:
                parser.error("هنوز پشتیبانی برای بررسی وجود ندارد.")
            target = rows[0]["file"]
        else:
            target = args.verify
        result = backup.verify_backup(target)
        result["file"] = str(target)
    else:
        path = backup.make_backup("scheduled")
        result = backup.verify_backup(path)
        result["file"] = path.name
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
