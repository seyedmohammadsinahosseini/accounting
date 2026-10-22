#!/usr/bin/env python3
"""Command-line utility for database operations, health verification, and system maintenance.

Usage:
    python3 tools.py backup                 Trigger an immediate SQLite WAL consistent backup
    python3 tools.py backups                List all available backups with timestamps and sizes
    python3 tools.py restore <filename>     Restore accounting database from a specified backup
    python3 tools.py password <username>    Reset or update password for a specified user
    python3 tools.py check-balance          Verify double-entry ledger balance across all accounts
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import select

from app import backup, config, dates as jd, services as svc
from app.db import session_scope
from app.models import User
from app.security import hash_password, password_problem
from app.seed import bootstrap


def main(argv: list[str]) -> int:
    cmd = argv[1].lower() if len(argv) > 1 else "help"

    if cmd in ("backup", "پشتیبان"):
        if not config.DB_PATH.exists():
            print("Error: Database not found. Please initialize the system using 'python3 reset_demo.py'.")
            return 1
        p = backup.make_backup("manual")
        print(f"Consistent backup created successfully: {p}")

    elif cmd in ("backups", "فهرست"):
        backups = backup.list_backups()
        if not backups:
            print("No existing backups found.")
            return 0
        print(f"{'Date (Jalali)':<15} {'Timestamp':<22} {'Size (KB)':<10} {'Filename'}")
        print("-" * 75)
        for b in backups:
            print(f"{b['jalali']:<15} {b['at']:<22} {b['size_kb']:<10} {b['file']}")

    elif cmd in ("restore", "بازیابی"):
        if len(argv) < 3:
            print("Usage error: backup filename required.")
            print("Example: python3 tools.py restore accounting-20261018-manual.db")
            return 2
        p = backup.restore(argv[2])
        print(f"Database successfully restored from: {p}")
        print("Note: Restart the application service to ensure clean in-memory state.")

    elif cmd in ("password", "رمز"):
        if len(argv) < 3:
            print("Usage error: username required.")
            print("Example: python3 tools.py password admin")
            return 2
        bootstrap(demo=False)
        with session_scope() as sess:
            u = sess.execute(select(User).where(User.username == argv[2])).scalar_one_or_none()
            if u is None:
                print(f"Error: User '{argv[2]}' was not found.")
                return 1
            pwd = getpass.getpass("Enter new password: ")
            problem = password_problem(pwd)
            if problem:
                print(f"Password error: {problem}")
                return 1
            u.password_hash = hash_password(pwd)
            u.must_change_password = True
        print(f"Password updated for '{argv[2]}'. User must change password upon next login.")

    elif cmd in ("check-balance", "تراز"):
        if not config.DB_PATH.exists():
            print("Error: Database not found. Please run 'python3 reset_demo.py' to initialize the database.")
            return 1
        with session_scope() as sess:
            today = jd.jtoday()
            tb = svc.trial_balance(sess, jd.fiscal_year_of(today)[1], today)
            print(f"Total Debit  : {tb['total_debit']:,}")
            print(f"Total Credit : {tb['total_credit']:,}")
            if tb["balanced"]:
                print("Status       : Ledger is in balance (Debit == Credit).")
            else:
                print("Status       : WARNING! Ledger is NOT in balance!")
                return 1

    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
