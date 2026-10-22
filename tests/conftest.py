"""آماده‌سازی محیط آزمون: یک محل داده‌ی جداگانه می‌سازد تا به داده‌ی واقعی دست نزند."""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

import pytest

TMP = tempfile.mkdtemp(prefix="acct-test-")
os.environ["ACCT_DATA_DIR"] = TMP
os.environ["ACCT_DEMO_SEED"] = "1"
os.environ["ACCT_BACKUP_ENABLED"] = "0"
os.environ["ACCT_BACKUP_DIR"] = os.path.join(TMP, "backups")
os.environ["ACCT_LOG_DIR"] = os.path.join(TMP, "logs")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import seed  # noqa: E402
from app.main import app  # noqa: E402

CREDS = seed.bootstrap(demo=True)


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """شمارنده‌ی «درخواست زیاد در یک دقیقه» بین آزمون‌ها صفر می‌شود.

    محدودیت نرخ برای محافظت از برنامه در برابر سوءاستفاده است و دست‌نخورده می‌ماند؛
    ولی آزمون‌ها صدها درخواست پشت‌سرهم می‌فرستند و نباید به آن برخورد کنند.
    """
    from app import api
    api._hits.clear()
    api._login_fails.clear()
    yield
    api._hits.clear()
    api._login_fails.clear()


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, username: str) -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": CREDS[username]})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture(scope="session")
def accountant(client) -> dict:
    return _login(client, "accountant")


@pytest.fixture(scope="session")
def manager(client) -> dict:
    return _login(client, "manager")


@pytest.fixture(scope="session")
def employee(client) -> dict:
    return _login(client, "employee")


@pytest.fixture(scope="session")
def auditor(client) -> dict:
    return _login(client, "auditor")


@pytest.fixture(scope="session")
def admin(client) -> dict:
    return _login(client, "admin")


def account_id(client, headers, code: str) -> int:
    rows = client.get("/api/accounts", headers=headers).json()["rows"]
    return next(a["id"] for a in rows if a["code"] == code)
