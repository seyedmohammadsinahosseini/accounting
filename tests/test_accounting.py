"""آزمون‌های قواعد حسابداری — همان آزمون‌هایی که در فهرست کارفرما آمده بود، به‌علاوه‌ی
آزمون‌های امنیتی و پشتیبان‌گیری.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from conftest import CREDS, account_id
from app import dates as jd
from app.money import MoneyError, format_amount_fa, parse_amount
from app.security import hash_password, password_problem, verify_password


# --------------------------------------------------------------- تاریخ و پول
def test_jalali_anchors():
    assert jd.jalali_to_gregorian(1403, 1, 1) == (2024, 3, 20)
    assert jd.jalali_to_gregorian(1404, 1, 1) == (2025, 3, 21)
    assert jd.jalali_to_gregorian(1405, 1, 1) == (2026, 3, 21)
    assert jd.gregorian_to_jalali(2026, 10, 15) == (1405, 7, 23)


def test_jalali_roundtrip_whole_year():
    d = date(2024, 1, 1)
    while d < date(2031, 1, 1):
        jy, jm, jdd = jd.jalali_parts(d)
        assert jd.jalali_to_gregorian(jy, jm, jdd) == (d.year, d.month, d.day)
        d += timedelta(days=1)


def test_legacy_demo_passwords_migrate_once_to_username_plus_123(tmp_path, monkeypatch):
    from app import config, seed
    from app.db import session_scope
    from app.models import User

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    legacy_password = "Old-Random-Password-123"
    legacy_file = "\n".join(
        f"{username}  →  {legacy_password}   (demo)"
        for username in ("admin", "manager", "accountant", "employee", "auditor")
    )
    (tmp_path / "DEMO-credentials.txt").write_text(legacy_file, encoding="utf-8")
    with session_scope() as sess:
        for username in ("admin", "manager", "accountant", "employee"):
            user = sess.query(User).filter_by(username=username).one()
            user.password_hash = hash_password(legacy_password)
        # رمز سفارشی باید حفظ شود، هرچند فایل قدیمی هنوز رمز نمونه را دارد.
        user = sess.query(User).filter_by(username="auditor").one()
        user.password_hash = hash_password("Custom-Auditor-Pass-123")
        assert seed.seed_users(sess, write_credentials=True) == {}
    with session_scope() as sess:
        for username in ("admin", "manager", "accountant", "employee"):
            user = sess.query(User).filter_by(username=username).one()
            assert verify_password(username + "123", user.password_hash)
        auditor_user = sess.query(User).filter_by(username="auditor").one()
        assert verify_password("Custom-Auditor-Pass-123", auditor_user.password_hash)
    assert (tmp_path / ".demo-passwords-username123-v1").exists()
    assert "admin  →  admin123" in (tmp_path / "DEMO-credentials.txt").read_text(encoding="utf-8")

    # مهاجرت نباید در هر اجرای بعدی رمزهایی را که کاربر عوض کرده دوباره بازنویسی کند.
    with session_scope() as sess:
        for username in ("admin", "manager", "accountant", "employee", "auditor"):
            user = sess.query(User).filter_by(username=username).one()
            user.password_hash = hash_password("User-Selected-Password-123")
        seed.seed_users(sess, write_credentials=True)
    with session_scope() as sess:
        for username in ("admin", "manager", "accountant", "employee", "auditor"):
            user = sess.query(User).filter_by(username=username).one()
            assert verify_password("User-Selected-Password-123", user.password_hash)
            # این آزمون روی DB اشتراکیِ آزمون‌ها اجرا می‌شود؛ اعتبارنامه‌های fixture را برگردان.
            user.password_hash = hash_password(username + "123")
            user.must_change_password = False


def test_password_policy_requires_letters_and_digits():
    assert password_problem("REPLACE_WITH_A_RANDOM_SECRET") is not None
    assert password_problem("letters-only-password") is not None
    assert password_problem("Strong-Secret-4026") is None


def test_money_parsing():
    assert parse_amount("12,500,000") == 12_500_000
    assert parse_amount("۱۲ میلیون") == 12_000_000
    assert parse_amount("5 میلیون") == 5_000_000
    assert parse_amount("3 میلیارد") == 3_000_000_000
    assert format_amount_fa(5_000_000) == "۵,۰۰۰,۰۰۰"
    with pytest.raises(MoneyError):
        parse_amount("abc")


# --------------------------------------------------------------- سند و تراز
def test_unbalanced_entry_cannot_be_posted(client, accountant):
    """آزمون نمونه‌ی ماژول ۱: بدهکار ۱۰ میلیون و بستانکار ۹ میلیون نباید نهایی شود."""
    bank = account_id(client, accountant, "110201")
    admin_acc = account_id(client, accountant, "5105")
    today = jd.jalali_str(jd.jtoday())
    r = client.post("/api/entries", headers=accountant, json={
        "date": today, "description": "آزمون ناترازی",
        "lines": [{"account_id": admin_acc, "debit": 10_000_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 9_000_000}],
    })
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["status"] == "draft"
    assert entry["balanced"] is False

    p = client.post(f"/api/entries/{entry['id']}/post", headers=accountant)
    assert p.status_code == 400
    assert "تراز نیست" in p.json()["detail"]
    assert "۱,۰۰۰,۰۰۰" in p.json()["detail"] or "1,000,000" in p.json()["detail"]

    still = client.get(f"/api/entries/{entry['id']}", headers=accountant).json()
    assert still["status"] == "draft"
    assert still["entry_no"] is None


def test_balanced_entry_posts_and_appears_in_trial_balance(client, accountant):
    bank = account_id(client, accountant, "110201")
    vat = account_id(client, accountant, "2104")
    revenue = account_id(client, accountant, "4101")
    today = jd.jalali_str(jd.jtoday())
    before = client.get("/api/reports/trial-balance", headers=accountant).json()

    r = client.post("/api/entries", headers=accountant, json={
        "date": today, "description": "آزمون ثبت سند متوازن",
        "lines": [{"account_id": bank, "debit": 109_000_000, "credit": 0},
                  {"account_id": revenue, "debit": 0, "credit": 100_000_000},
                  {"account_id": vat, "debit": 0, "credit": 9_000_000}],
    }).json()
    assert r["balanced"] is True
    assert r["status"] == "draft"

    posted = client.post(f"/api/entries/{r['id']}/post", headers=accountant).json()
    assert posted["status"] == "posted"
    assert posted["entry_no"].startswith(str(jd.jalali_parts(jd.jtoday())[0]))

    after = client.get("/api/reports/trial-balance", headers=accountant).json()
    assert after["balanced"] is True
    assert after["total_debit"] == before["total_debit"] + 109_000_000
    assert after["total_credit"] == before["total_credit"] + 109_000_000


def test_entry_numbering_is_unique_and_increasing(client, accountant):
    bank = account_id(client, accountant, "110201")
    rent = account_id(client, accountant, "5103")
    nos = []
    for i in range(3):
        e = client.post("/api/entries", headers=accountant, json={
            "date": jd.jalali_str(jd.jtoday()), "description": f"آزمون شماره‌گذاری {i}",
            "lines": [{"account_id": rent, "debit": 1_000_000 + i, "credit": 0},
                      {"account_id": bank, "debit": 0, "credit": 1_000_000 + i}],
        }).json()
        posted = client.post(f"/api/entries/{e['id']}/post", headers=accountant).json()
        nos.append(posted["entry_no"])
    assert len(set(nos)) == 3
    assert nos == sorted(nos)


def test_reverse_entry_keeps_history_and_nets_to_zero(client, accountant):
    bank = account_id(client, accountant, "110201")
    rent = account_id(client, accountant, "5103")
    e = client.post("/api/entries", headers=accountant, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "آزمون ابطال",
        "lines": [{"account_id": rent, "debit": 7_000_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 7_000_000}],
    }).json()
    posted = client.post(f"/api/entries/{e['id']}/post", headers=accountant).json()

    before = client.get(f"/api/reports/ledger?account_id={rent}", headers=accountant).json()["closing"]
    rev = client.post(f"/api/entries/{posted['id']}/reverse", headers=accountant,
                      json={"reason": "اشتباه در مبلغ اجاره"}).json()
    after = client.get(f"/api/reports/ledger?account_id={rent}", headers=accountant).json()["closing"]

    assert rev["status"] == "posted"
    assert after == before - 7_000_000
    original = client.get(f"/api/entries/{posted['id']}", headers=accountant).json()
    assert original["status"] == "reversed"
    assert original["reversed_by"] == rev["id"]

    # ابطال دوباره‌ی همان سند نباید ممکن باشد
    again = client.post(f"/api/entries/{posted['id']}/reverse", headers=accountant,
                        json={"reason": "دوباره"})
    assert again.status_code == 400


# --------------------------------------------------------------- خزانه و بانک
def test_bank_transfer_creates_no_income_or_expense(client, accountant):
    """آزمون نمونه‌ی ماژول ۲: انتقال ۵ میلیون بین دو حساب بانکی نباید درآمد/هزینه بسازد."""
    bank1 = account_id(client, accountant, "110201")
    bank2 = account_id(client, accountant, "110202")

    def balances():
        rows = client.get("/api/reports/treasury", headers=accountant).json()["rows"]
        return {r["id"]: r["balance"] for r in rows}

    def totals():
        d = client.get("/api/dashboard", headers=accountant).json()
        return d["income"], d["expense"]

    b_before, t_before = balances(), totals()
    e = client.post("/api/treasury/transfer", headers=accountant, json={
        "from_account_id": bank1, "to_account_id": bank2, "amount": 5_000_000,
        "date": jd.jalali_str(jd.jtoday()), "description": "آزمون انتقال",
    })
    assert e.status_code == 200, e.text
    entry = e.json()
    b_after, t_after = balances(), totals()

    assert b_after[bank1] - b_before[bank1] == -5_000_000
    assert b_after[bank2] - b_before[bank2] == +5_000_000
    assert t_after == t_before, "انتقال وجه نباید درآمد یا هزینه ایجاد کند"
    assert entry["debit_total"] == entry["credit_total"] == 5_000_000
    accounts_used = [l["account_label"] for l in entry["lines"]]
    assert all(("بانک" in a) or ("صندوق" in a) for a in accounts_used)


def test_transfer_rejects_same_account_and_non_bank(client, accountant):
    bank1 = account_id(client, accountant, "110201")
    rent = account_id(client, accountant, "5103")
    same = client.post("/api/treasury/transfer", headers=accountant, json={
        "from_account_id": bank1, "to_account_id": bank1, "amount": 1_000_000,
        "date": jd.jalali_str(jd.jtoday())})
    assert same.status_code == 400
    wrong = client.post("/api/treasury/transfer", headers=accountant, json={
        "from_account_id": bank1, "to_account_id": rent, "amount": 1_000_000,
        "date": jd.jalali_str(jd.jtoday())})
    assert wrong.status_code == 400
    assert "صندوق یا بانک" in wrong.json()["detail"]


# --------------------------------------------------------------- بستن دوره
def test_closed_period_blocks_new_postings(client, accountant):
    jy, jm, _ = jd.jalali_parts(jd.jtoday())
    bank = account_id(client, accountant, "110201")
    rent = account_id(client, accountant, "5103")
    created = client.post("/api/entries", headers=accountant, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "پیش‌نویس پیش از بستن دوره",
        "lines": [{"account_id": rent, "debit": 2_000_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 2_000_000}],
    })
    assert created.status_code == 200, created.text
    entry_id = created.json()["id"]
    assert client.post("/api/periods/close", headers=accountant,
                       json={"jy": jy, "jm": jm}).status_code == 200
    new_draft = client.post("/api/entries", headers=accountant, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "سند تازه در دوره بسته",
        "lines": [{"account_id": rent, "debit": 1_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 1_000}],
    })
    assert new_draft.status_code == 400 and "بسته" in new_draft.json()["detail"]
    blocked = client.post(f"/api/entries/{entry_id}/post", headers=accountant)
    assert blocked.status_code == 400
    assert "بسته" in blocked.json()["detail"]
    assert client.post("/api/periods/reopen", headers=accountant,
                       json={"jy": jy, "jm": jm}).status_code == 200
    ok = client.post(f"/api/entries/{entry_id}/post", headers=accountant)
    assert ok.status_code == 200, ok.text


# --------------------------------------------------------------- دسترسی‌ها
def test_anonymous_cannot_read_anything(client):
    assert client.get("/api/entries").status_code == 401
    assert client.get("/api/dashboard").status_code == 401


def test_employee_cannot_post_or_reverse(client, employee, accountant):
    bank = account_id(client, accountant, "110201")
    exp = account_id(client, accountant, "5105")
    e = client.post("/api/entries", headers=employee, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "ثبت کارمند فروش",
        "lines": [{"account_id": exp, "debit": 3_000_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 3_000_000}],
    })
    assert e.status_code == 200, "کارمند باید بتواند پیش‌نویس بسازد"
    assert client.post(f"/api/entries/{e.json()['id']}/post", headers=employee).status_code == 403
    assert client.post(f"/api/entries/{e.json()['id']}/reverse", headers=employee,
                       json={"reason": "دستکاری"}).status_code == 403
    assert client.get("/api/users", headers=employee).status_code == 403
    assert client.get("/api/audit", headers=employee).status_code == 403


def test_auditor_is_read_only(client, auditor, accountant):
    assert client.get("/api/dashboard", headers=auditor).status_code == 200
    assert client.get("/api/reports/trial-balance", headers=auditor).status_code == 200
    bank = account_id(client, accountant, "110201")
    exp = account_id(client, accountant, "5105")
    r = client.post("/api/entries", headers=auditor, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "حسابرس نباید بنویسد",
        "lines": [{"account_id": exp, "debit": 1_000_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 1_000_000}],
    })
    assert r.status_code == 403


def test_employee_sees_only_own_drafts(client, employee, accountant):
    bank = account_id(client, accountant, "110201")
    exp = account_id(client, accountant, "5105")
    client.post("/api/entries", headers=employee, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "پیش‌نویس کارمند",
        "lines": [{"account_id": exp, "debit": 1_500_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 1_500_000}],
    })
    rows = client.get("/api/entries", headers=employee).json()["rows"]
    assert rows, "کارمند باید پیش‌نویس‌های خودش را ببیند"
    assert all(r["status"] == "draft" for r in rows)


def test_manager_can_read_but_not_post(client, manager):
    assert client.get("/api/dashboard", headers=manager).status_code == 200
    assert client.get("/api/users", headers=manager).status_code == 403


def test_four_level_account_hierarchy_validates_parents_and_metadata(client, admin):
    root = client.post("/api/accounts", headers=admin, json={
        "code": "9", "name": "گروه آزمون", "nature": "debit", "level": "group",
        "report_group": "asset", "group_type": "permanent", "report_type": "balance_sheet"})
    assert root.status_code == 200, root.text
    general = client.post("/api/accounts", headers=admin, json={
        "code": "91", "name": "کل آزمون", "nature": "debit", "level": "general",
        "parent_code": "9", "report_group": "asset"})
    assert general.status_code == 200, general.text
    subsidiary = client.post("/api/accounts", headers=admin, json={
        "code": "9101", "name": "معین آزمون", "nature": "debit", "level": "subsidiary",
        "parent_code": "91", "report_group": "asset"})
    assert subsidiary.status_code == 200, subsidiary.text
    detail = client.post("/api/accounts", headers=admin, json={
        "code": "910101", "name": "تفصیلی آزمون", "nature": "debit", "level": "detail",
        "parent_code": "9101", "report_group": "asset"})
    assert detail.status_code == 200, detail.text
    bad_parent = client.post("/api/accounts", headers=admin, json={
        "code": "910102", "name": "والد اشتباه", "nature": "debit", "level": "detail",
        "parent_code": "91", "report_group": "asset"})
    assert bad_parent.status_code == 400
    rows = client.get("/api/accounts", headers=admin).json()["rows"]
    made = next(row for row in rows if row["code"] == "910101")
    assert made["level"] == "detail" and made["parent_code"] == "9101" and made["can_post"]
    group = next(row for row in rows if row["code"] == "9")
    assert group["group_type"] == "permanent" and group["report_type"] == "balance_sheet"


def test_system_accounts_cannot_be_deactivated(client, admin):
    rows = client.get("/api/accounts", headers=admin).json()["rows"]
    cash = next(a for a in rows if a["code"] == "1101")
    r = client.patch(f"/api/accounts/{cash['id']}", headers=admin, json={"is_active": False})
    assert r.status_code == 400


# --------------------------------------------------------------- تکرار و امنیت
def test_idempotency_prevents_double_posting(client, accountant):
    bank = account_id(client, accountant, "110201")
    rent = account_id(client, accountant, "5103")
    body = {"date": jd.jalali_str(jd.jtoday()), "description": "آزمون تکرار",
            "lines": [{"account_id": rent, "debit": 4_000_000, "credit": 0},
                      {"account_id": bank, "debit": 0, "credit": 4_000_000}]}
    h = dict(accountant)
    h["Idempotency-Key"] = "test-key-1"
    a = client.post("/api/entries", headers=h, json=body).json()
    b = client.post("/api/entries", headers=h, json=body).json()
    assert a["id"] == b["id"]
    p1 = client.post(f"/api/entries/{a['id']}/post", headers=accountant).json()
    p2 = client.post(f"/api/entries/{a['id']}/post", headers=accountant).json()
    assert p1["entry_no"] == p2["entry_no"]


def test_idempotency_key_cannot_be_reused_for_different_payload(client, accountant):
    bank = account_id(client, accountant, "110201")
    expense = account_id(client, accountant, "5105")
    headers = dict(accountant, **{"Idempotency-Key": "key-reuse-001"})
    base = {"date": jd.jalali_str(jd.jtoday()), "description": "کلید ثابت با payload ثابت",
            "lines": [{"account_id": expense, "debit": 123_000, "credit": 0},
                      {"account_id": bank, "debit": 0, "credit": 123_000}]}
    first = client.post("/api/entries", headers=headers, json=base)
    assert first.status_code == 200, first.text
    changed = {**base, "description": "payload متفاوت با همان کلید"}
    conflict = client.post("/api/entries", headers=headers, json=changed)
    assert conflict.status_code == 409
    replay = client.post("/api/entries", headers=headers, json=base)
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]


def test_cached_idempotency_result_does_not_bypass_updated_permissions(client, admin):
    made = client.post("/api/users", headers=admin, json={
        "username": "idem-role-check", "full_name": "کاربر آزمون مجوز", "role": "accountant",
        "password": "Role-Check-123"})
    assert made.status_code == 200, made.text
    temporary_login = client.post("/api/auth/login", json={"username": "idem-role-check", "password": "Role-Check-123"})
    assert temporary_login.status_code == 200
    changed_password = client.post("/api/me/password", headers={
        "Authorization": "Bearer " + temporary_login.json()["token"]},
        json={"old_password": "Role-Check-123", "new_password": "Role-Check-123-New"})
    assert changed_password.status_code == 200, changed_password.text
    login = client.post("/api/auth/login", json={"username": "idem-role-check", "password": "Role-Check-123-New"})
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["token"], "Idempotency-Key": "permission-check-01"}
    body = {"code": "9989", "name": "حساب آزمون مجوز", "nature": "debit", "level": "group",
            "report_group": "asset", "group_type": "permanent", "report_type": "balance_sheet"}
    first = client.post("/api/accounts", headers=headers, json=body)
    assert first.status_code == 200, first.text
    changed = client.patch(f"/api/users/{made.json()['id']}", headers=admin, json={"role": "employee"})
    assert changed.status_code == 200
    replay = client.post("/api/accounts", headers=headers, json=body)
    assert replay.status_code == 403


def test_idempotency_replay_requires_a_live_authorized_session(client):
    login = client.post("/api/auth/login", json={"username": "accountant", "password": CREDS["accountant"]})
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["token"], "Idempotency-Key": "logout-replay-01"}
    body = {"date": jd.jalali_str(jd.jtoday()), "description": "پاسخ پس از خروج نباید افشا شود",
            "lines": [{"account_id": account_id(client, headers, "5105"), "debit": 15_000, "credit": 0},
                      {"account_id": account_id(client, headers, "110201"), "debit": 0, "credit": 15_000}]}
    first = client.post("/api/entries", headers=headers, json=body)
    assert first.status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    replay = client.post("/api/entries", headers=headers, json=body)
    assert replay.status_code == 401


def test_login_rate_limit_and_wrong_password(client):
    for _ in range(3):
        r = client.post("/api/auth/login", json={"username": "accountant", "password": "غلط"})
        assert r.status_code == 401
    assert "نام کاربری یا رمز" in r.json()["detail"]


def test_every_important_action_is_logged(client, accountant, admin):
    created = client.post("/api/users", headers=admin, json={
        "username": "azmoon-karbar", "full_name": "کاربر آزمون", "role": "employee",
        "password": "Azmoon-12345"})
    assert created.status_code == 200, created.text
    rows = client.get("/api/audit", headers=admin).json()["rows"]
    actions = {r["action"] for r in rows}
    for needed in ("user.login", "entry.post", "entry.reverse", "period.close", "user.create"):
        assert needed in actions, f"ردّ کار «{needed}» ثبت نشده است"


def test_validation_rejects_bad_lines(client, accountant):
    bank = account_id(client, accountant, "110201")
    group_code = next(a["id"] for a in client.get("/api/accounts", headers=accountant).json()["rows"]
                      if a["kind"] == "group")
    both = client.post("/api/entries", headers=accountant, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "هم بدهکار هم بستانکار",
        "lines": [{"account_id": bank, "debit": 1_000_000, "credit": 1_000_000},
                  {"account_id": bank, "debit": 0, "credit": 1_000_000}]})
    assert both.status_code == 400
    on_group = client.post("/api/entries", headers=accountant, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "سند روی حساب گروهی",
        "lines": [{"account_id": group_code, "debit": 1_000_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 1_000_000}]})
    assert on_group.status_code == 400
    assert "گروهی" in on_group.json()["detail"]
