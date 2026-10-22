"""Database Reset and Seed Utility for Accounting Platform.

Resets the SQLite database, runs schema migrations, generates double-entry chart
of accounts, seeds demo fiscal periods, sample journal entries, invoices, trips,
and payroll runs with deterministic evaluation credentials.

Usage:
    python3 reset_demo.py
"""
from __future__ import annotations

import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sqlalchemy import select

from app import config, payroll as P, seed
from app.db import session_scope
from app.models import User
from app.security import ROLE_LABELS, hash_password

FIXED_PASSWORDS = {
    username: username + "123"
    for username in ("admin", "manager", "accountant", "employee", "auditor")
}


def main() -> None:
    data = pathlib.Path(config.DATA_DIR)
    for name in ("accounting.db", "accounting.db-shm", "accounting.db-wal"):
        (data / name).unlink(missing_ok=True)
    for folder in ("backups", "logs"):
        shutil.rmtree(data / folder, ignore_errors=True)

    seed.bootstrap(demo=True)

    with session_scope() as sess:
        for user in sess.execute(select(User)).scalars():
            if user.username in FIXED_PASSWORDS:
                user.password_hash = hash_password(FIXED_PASSWORDS[user.username])
                user.must_change_password = False
        users = [(u.username, u.role) for u in sess.execute(select(User).order_by(User.id)).scalars()]

    lines = [
        "# Accounting Platform Demo Credentials (Development / Evaluation Mode)",
        "# Note: These credentials are for testing and evaluation purposes only.",
        "",
    ]
    lines += [f"{u:<12} ->  {FIXED_PASSWORDS[u]:<16} ({ROLE_LABELS.get(r, r)})" for u, r in users]
    credentials = data / "DEMO-credentials.txt"
    credentials.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        credentials.chmod(0o600)
    except OSError:
        pass

    # Generate a draft payroll run for the current month ready for accountant approval
    with session_scope() as sess:
        accountant = sess.execute(select(User).where(User.role == "accountant")).scalar_one()
        from app import dates as jd
        jy, jm, _ = jd.jalali_parts(jd.jtoday())
        run = P.create_run(sess, accountant, jy, jm)
        detail = P.run_detail(sess, run.id)
        print(f"[Success] Demo database initialized with {len(users)} users.")
        print(f"[Payroll] Sample payroll run created: {detail['number']} ({detail['period']})")
        print(f"          Employees: {len(detail['rows'])}, Net Total: {detail['totals']['net']:,} Rials")


if __name__ == "__main__":
    main()
