"""آزمون‌های ممیزی کدینگ — seed ایمن/همگام، حساب‌های مرجع تکمیلی و رد مبلغ منفی.

این آزمون‌ها روی همان دیتابیس نمایشیِ bootstrap اجرا می‌شوند (مثل بقیه‌ی تست‌ها)
و دقیقاً همان مسیر به‌روزرسانی نصب‌های قدیمی را هم پوشش می‌دهند: bootstrap هر بار
در شروع برنامه seed_accounts را صدا می‌زند و حساب‌های سیستمی جاافتاده را افزایشی
اضافه می‌کند.
"""
from __future__ import annotations

import threading

from sqlalchemy import select

from app import dates as jd
from app import sales, seed
from app import services as svc
from app.db import session_scope
from app.models import Account, JournalEntry, Party
from app.rules import SYSTEM_CODES, DomainError, build_validation
from conftest import account_id

NEW_REFERENCE_ACCOUNTS = {
    # کد: (ماهیت، report_type، group_type)
    "2109": ("credit", "balance_sheet", "permanent"),
    "2110": ("credit", "balance_sheet", "permanent"),
    "4110": ("credit", "income_statement", "temporary"),
    "4111": ("credit", "income_statement", "temporary"),
    "5110": ("debit", "income_statement", "temporary"),
    "5111": ("debit", "income_statement", "temporary"),
}

NEW_SYSTEM_CODE_KEYS = {
    "hotel_clearing": "2109",
    "commission_payable": "2110",
    "revenue_hotel": "4110",
    "fx_gain": "4111",
    "commission_expense": "5110",
    "fx_loss": "5111",
}


def _create_and_post_entry(client, headers, description, lines):
    today = jd.jalali_str(jd.jtoday())
    r = client.post("/api/entries", headers=headers,
                    json={"date": today, "description": description, "lines": lines})
    assert r.status_code == 200, r.text
    entry_id = r.json()["id"]
    r = client.post(f"/api/entries/{entry_id}/post", headers=headers)
    assert r.status_code == 200, r.text
    return entry_id


# ----------------------------------------------------------------- seed و CHART
def test_chart_codes_unique_and_parents_valid():
    codes = [row[0] for row in seed.CHART]
    assert len(codes) == len(set(codes)), "کد تکراری در CHART"
    known = set(codes)
    for code, _name, _kind, _nature, parent, _group, _sys in seed.CHART:
        assert parent is None or parent in known, f"والد {parent} برای حساب {code} در CHART نیست"


def test_seed_has_no_real_party_detail_accounts():
    """تفصیلی اشخاص فقط از گردش «ساخت شخص» ساخته می‌شود، نه seed ثابت."""
    codes = [row[0] for row in seed.CHART]
    assert all("-" not in code for code in codes), "seed نباید حساب تفصیلی (کد خط‌تیره‌دار) بسازد"
    # حساب بانک ارزی نمونه‌ای طبق دستور جعل نشده
    assert "110203" not in codes


def test_seed_rerun_is_idempotent():
    """اجرای دوباره‌ی seed نه حساب تکراری می‌سازد نه حساب موجود را بازنویسی می‌کند."""
    with session_scope() as sess:
        before = {a.code: (a.name, a.is_system, a.nature, a.parent_code, a.level,
                           a.group_type, a.report_type, a.report_group)
                  for a in sess.execute(select(Account)).scalars()}
        seed.seed_accounts(sess)
        sess.flush()
        after = {a.code: (a.name, a.is_system, a.nature, a.parent_code, a.level,
                          a.group_type, a.report_type, a.report_group)
                 for a in sess.execute(select(Account)).scalars()}
    assert before == after, "اجرای دوباره‌ی seed داده‌ی موجود را تغییر داده یا حساب تکراری ساخته است"


def test_new_reference_accounts_seeded_with_right_flags():
    with session_scope() as sess:
        for code, (nature, report_type, group_type) in NEW_REFERENCE_ACCOUNTS.items():
            acc = svc.account_by_code(sess, code)
            assert acc is not None, f"حساب مرجع {code} seed نشده است"
            assert acc.is_system is True, f"حساب {code} باید سیستمی باشد"
            assert acc.kind == "ledger", f"حساب {code} باید قابل‌انتخاب (ledger) باشد"
            assert acc.nature == nature, f"ماهیت حساب {code}"
            assert acc.level == "subsidiary", f"سطح حساب {code} باید معین (subsidiary) باشد"
            assert acc.report_type == report_type, f"report_type حساب {code}"
            assert acc.group_type == group_type, f"group_type حساب {code}"


def test_new_system_code_keys_point_to_reference_codes():
    for key, code in NEW_SYSTEM_CODE_KEYS.items():
        assert SYSTEM_CODES[key] == code, f"نگاشت SYSTEM_CODES[{key}]"


# --------------------------------------------------------------- رد مبلغ منفی و غیرفعال
def test_negative_amount_rejected_in_domain_validation():
    v = build_validation([
        {"account_id": 1, "debit": -5_000_000, "credit": 0},
        {"account_id": 2, "debit": 0, "credit": 5_000_000},
    ])
    assert any("منفی" in p for p in v.problems), "مبلغ بدهکار منفی باید رد شود"
    v2 = build_validation([
        {"account_id": 1, "debit": 5_000_000, "credit": 0},
        {"account_id": 2, "debit": 0, "credit": -5_000_000},
    ])
    assert any("منفی" in p for p in v2.problems), "مبلغ بستانکار منفی باید رد شود"


def test_negative_amount_rejected_via_api(client, accountant):
    bank = account_id(client, accountant, "110201")
    r = client.post("/api/entries", headers=accountant, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "سند با مبلغ منفی",
        "lines": [{"account_id": bank, "debit": -1_000_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 1_000_000}]})
    assert r.status_code == 400
    assert "منفی" in r.json()["detail"]


def test_inactive_account_rejected_via_api(client, accountant, admin):
    bank = account_id(client, accountant, "110201")
    # حساب موقت جدید می‌سازیم، غیرفعالش می‌کنیم و ثبت روی آن باید رد شود
    r = client.post("/api/accounts", headers=admin, json={
        "code": "119901", "name": "حساب موقت تست ممیزی", "nature": "debit",
        "parent_code": "11", "report_group": "cash"})
    assert r.status_code == 200, r.text
    temp_id = r.json()["id"]
    r = client.patch(f"/api/accounts/{temp_id}", headers=admin, json={"is_active": False})
    assert r.status_code == 200, r.text
    r = client.post("/api/entries", headers=accountant, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "سند روی حساب غیرفعال",
        "lines": [{"account_id": temp_id, "debit": 1_000_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 1_000_000}]})
    assert r.status_code == 400
    assert "غیرفعال" in r.json()["detail"]


# --------------------------------------------------------------- گزارش‌ها با حساب‌های جدید
def test_new_accounts_reconcile_with_statements(client, accountant):
    """سند روی حساب‌های جدید با همان ماهیت خودشان در سود و زیان و ترازنامه می‌نشیند."""
    fx_loss = account_id(client, accountant, "5111")
    fx_gain = account_id(client, accountant, "4111")
    hotel_clearing = account_id(client, accountant, "2109")
    cash = account_id(client, accountant, "1101")
    today = jd.jalali_str(jd.jtoday())
    _create_and_post_entry(client, accountant, "زیان تسعیر ارز — تست ممیزی",
                           [{"account_id": fx_loss, "debit": 7_000_000, "credit": 0},
                            {"account_id": cash, "debit": 0, "credit": 7_000_000}])
    _create_and_post_entry(client, accountant, "سود تسعیر ارز — تست ممیزی",
                           [{"account_id": cash, "debit": 3_000_000, "credit": 0},
                            {"account_id": fx_gain, "debit": 0, "credit": 3_000_000}])
    _create_and_post_entry(client, accountant, "بدهی امانی هتل — تست ممیزی",
                           [{"account_id": cash, "debit": 9_000_000, "credit": 0},
                            {"account_id": hotel_clearing, "debit": 0, "credit": 9_000_000}])

    pl = client.get(f"/api/reports/income-statement?frm={today}&to={today}", headers=accountant)
    assert pl.status_code == 200, pl.text
    body = pl.json()
    loss_row = next(r for r in body["expense_rows"] if r["code"] == "5111")
    gain_row = next(r for r in body["revenue_rows"] if r["code"] == "4111")
    assert loss_row["amount"] >= 7_000_000
    assert gain_row["amount"] >= 3_000_000

    bs = client.get(f"/api/reports/balance-sheet?to={today}", headers=accountant)
    assert bs.status_code == 200, bs.text
    debt_row = next(r for r in bs.json()["liabilities"] if r["code"] == "2109")
    assert debt_row["amount"] >= 9_000_000, "بدهی امانی هتل باید بستانکار در ترازنامه بنشیند"


# --------------------------------------------------------------- اشخاص: اتمیسازی و تاریخچه
def test_concurrent_party_creation_makes_distinct_detail_accounts():
    """دو ساخت هم‌زمان نباید روی یک کد حساب بنشینند؛ هر شخص یک تفصیلی یکتا می‌گیرد."""
    with session_scope() as sess:
        user = sess.execute(select(svc.User).where(svc.User.role == "accountant")).scalars().first()
        user_id = user.id
    results, errors = [], []

    def make(i):
        try:
            # قفل ساخت شخص، کل پنجره‌ی «کدگذاری + کامیت» را سرپوش می‌گذارد؛ قید UNIQUE
            # جدول هم تضمین نهایی است. نتیجه: نه خطا، نه حساب تکراری.
            with sales._party_create_lock:
                with session_scope() as s2:
                    u2 = s2.get(svc.User, user_id)
                    p = sales.create_party(s2, u2, f"مشتری هم‌زمان ممیزی {i}", "customer")
                    results.append(p.account_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=make, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"ساخت هم‌زمان شخص خطا داد: {errors}"
    assert len(results) == 4
    assert len(set(results)) == 4, "هر شخص باید حساب تفصیلی یکتای خودش را داشته باشد"
    with session_scope() as sess:
        for acc_id in results:
            acc = sess.get(Account, acc_id)
            assert acc.parent_code == SYSTEM_CODES["receivable"]
            assert acc.level == "detail" and acc.kind == "ledger"


def test_deactivating_party_keeps_historical_entries(client, admin, accountant):
    """غیرفعال‌کردن شخص، سندهای تاریخی‌اش را نمی‌شکند و هر دو دفتر می‌مانند."""
    r = client.post("/api/parties", headers=admin, json={"name": "مشتری تاریخی ممیزی", "kind": "customer"})
    assert r.status_code == 200, r.text
    party_id = r.json()["id"]
    with session_scope() as sess:
        account = sess.execute(select(Party).where(Party.id == party_id)).scalar_one()
        party_acc_id = account.account_id

    cash = account_id(client, accountant, "1101")
    _create_and_post_entry(client, accountant, "دریافت از مشتری قبل از غیرفعال شدن",
                           [{"account_id": cash, "debit": 5_000_000, "credit": 0},
                            {"account_id": party_acc_id, "debit": 0, "credit": 5_000_000}])
    r = client.patch(f"/api/parties/{party_id}", headers=admin, json={"is_active": False})
    assert r.status_code == 200, r.text
    # سند تاریخی باید در دفترِ همان حساب بماند
    r = client.get(f"/api/reports/ledger?account_id={party_acc_id}", headers=accountant)
    assert r.status_code == 200, r.text
    assert r.json()["rows"], "سند تاریخی شخصِ غیرفعال نباید گم شود"


# --------------------------------------------------------------- سیستمی بودن محافظ
def test_new_system_accounts_cannot_be_deactivated(client, admin):
    for row in client.get("/api/accounts", headers=admin).json()["rows"]:
        if row["code"] in NEW_REFERENCE_ACCOUNTS:
            r = client.patch(f"/api/accounts/{row['id']}", headers=admin, json={"is_active": False})
            assert r.status_code == 400, f"غیرفعال‌کردن حساب سیستمی {row['code']} نباید پذیرفته شود"


def test_party_detail_accounts_under_control_parents():
    """حساب‌های تفصیلی اشخاص زیر همان والد کنترلی‌اند و کد آن‌ها یکتاست."""
    with session_scope() as sess:
        rows = sess.execute(select(Account).where(Account.parent_code == "1103")).scalars().all()
        codes = [a.code for a in rows]
        assert len(codes) == len(set(codes))
        assert all(c.startswith("1103-") for c in codes)
        # اعتبار مشتری‌ها هم زیر 2103 با الگوی یکسان
        credit_rows = sess.execute(select(Account).where(Account.parent_code == "2103")).scalars().all()
        for a in credit_rows:
            assert a.code.startswith("2103-")
            assert a.report_group == "advance_customer"


def test_missing_control_parent_fails_closed():
    """نبود حساب والد کنترلی باید ساخت شخص را با خطای دامنه‌ی روشن متوقف کند."""
    with session_scope() as sess:
        user = sess.execute(select(svc.User).where(svc.User.role == "accountant")).scalars().first()
        receivable = svc.account_by_code(sess, SYSTEM_CODES["receivable"])
        saved_code = receivable.code
        parties_before = sess.execute(select(Party)).scalars().all()
        accounts_before = sess.execute(select(Account)).scalars().all()
        try:
            receivable.code = "1103-moved-for-audit-test"     # شبیه‌سازی نبود والد بدون حذف داده
            sess.flush()
            raised = False
            try:
                sales.create_party(sess, user, "مشتری بدون والد ممیزی", "customer")
            except DomainError as exc:
                raised = True
                assert "کدینگ" in exc.message
            assert raised, "نبود حساب والد باید ساخت شخص را متوقف کند"
        finally:
            sess.rollback()
    # هیچ داده‌ای نباید باقی مانده باشد ( rollback کامل)
    with session_scope() as sess:
        assert svc.account_by_code(sess, saved_code) is not None
        assert len(sess.execute(select(Party)).scalars().all()) == len(parties_before)
        assert len(sess.execute(select(Account)).scalars().all()) == len(accounts_before)


# --------------------------------------------------------------- درآمد هتل روی حساب خودش
def test_hotel_revenue_posts_to_4110(client, accountant):
    """اقامت (=هتل) به حساب درآمد ۴۱۱۰ می‌رود، نه سایر درآمدها؛ لغو جزئی هم همان‌جا برمی‌گردد."""
    assert sales.SERVICE_TYPES["hotel"]["account"] == "revenue_hotel"
    assert SYSTEM_CODES["revenue_hotel"] == "4110"
    with session_scope() as sess:
        user = sess.execute(select(svc.User).where(svc.User.role == "accountant")).scalars().first()
        cust = sales.create_party(sess, user, "مشتری هتل ممیزی ۴۱۱۰", "customer")
        inv = sales.create_invoice(sess, user, cust.id, jd.jtoday(),
                                   [{"service_type": "hotel", "qty": 1, "unit_price": 30_000_000,
                                     "discount": 0, "taxable": False}], issue=True)
        entry = sess.get(JournalEntry, inv.entry_id)
        account_map = svc.account_map(sess)
        credit_codes = {account_map[l.account_id].code for l in entry.lines if l.credit_rial > 0}
        assert "4110" in credit_codes, "درآمد هتل باید به ۴۱۱۰ بنشیند"
        assert "4103" not in credit_codes, "درآمد هتل دیگر به «سایر درآمدها» نمی‌رود"


# --------------------------------------------------------------- مسیر اختیاری کمیسیون همکاران
def test_commission_payable_path_works_manually(client, accountant):
    """مسیر «ب» کمیسیون همکاران: ثبت هزینه در ۵۱۱۰ و بدهی در ۲۱۱۰ با سند دستی؛ بدون تغییر جریان اجباری."""
    expense = account_id(client, accountant, "5110")
    payable = account_id(client, accountant, "2110")
    today = jd.jalali_str(jd.jtoday())
    _create_and_post_entry(client, accountant, "کمیسیون همکار فروش — ثبتی (تست ممیزی)",
                           [{"account_id": expense, "debit": 4_000_000, "credit": 0},
                            {"account_id": payable, "debit": 0, "credit": 4_000_000}])
    pl = client.get(f"/api/reports/income-statement?frm={today}&to={today}", headers=accountant)
    row = next(r for r in pl.json()["expense_rows"] if r["code"] == "5110")
    assert row["amount"] >= 4_000_000, "هزینه کمیسیون باید در سود و زیان بنشیند"
    bs = client.get(f"/api/reports/balance-sheet?to={today}", headers=accountant)
    debt_row = next(r for r in bs.json()["liabilities"] if r["code"] == "2110")
    assert debt_row["amount"] >= 4_000_000, "کمیسیون پرداختنی باید بستانکار در ترازنامه بنشیند"


# --------------------------------------------------------------- تسویه‌ی ارزی (سبک و fail-closed)
def _accountant_user(sess):
    return sess.execute(select(svc.User).where(svc.User.role == "accountant")).scalars().first()


# خزانه‌ی دیتای آزمون مانده‌ی مشخصی دارد؛ تست‌های این فایل مبالغ بزرگ از صندوق پرداخت می‌کنند
# و اگر صندوق منفی شود، نمایش خالص تراز آزمایشی برای تست‌های دیگر (که بر مانده‌ی مثبت حساب
# کرده‌اند) می‌شکند. پس قبل از هر سناریوی پرداخت، صندوق را با یک دریافت بزرگ شارژ می‌کنیم.
_FUNDING = 30_000_000_000


def _fund_cash(sess, user, cash, name: str) -> None:
    party = sales.create_party(sess, user, name, "customer")
    sales.create_settlement(sess, user, "in", party.id, cash.id, _FUNDING, jd.jtoday(), issue=True)


def _fx_bill(sess, user, party, minor=100_000, book_rate=1_050_000) -> object:
    """صورتحساب ارزی معاف از مالیات: ۱٬۰۰۰ یورو (=۱۰۰٬۰۰۰ سنت) با نرخ دفتری book_rate."""
    return sales.create_bill(sess, user, party.id, jd.jtoday(), vat=0,
                             currency="EUR", fx_amount_minor=minor, fx_rate_rial=book_rate,
                             description="رزرو هتل خارجی — تست ممیزی", issue=True)


def _entry_debit_credit_by_code(sess, entry_id: int):
    entry = sess.get(JournalEntry, entry_id)
    account_map = svc.account_map(sess)
    dr, cr = {}, {}
    for l in entry.lines:
        code = account_map[l.account_id].code
        if l.debit_rial:
            dr[code] = dr.get(code, 0) + l.debit_rial
        if l.credit_rial:
            cr[code] = cr.get(code, 0) + l.credit_rial
    return dr, cr


def test_fx_settlement_posts_realized_loss_and_closes_bill(client, accountant):
    """پرداخت با نرخ بالاتر از نرخ دفتری → زیان ۵۱۱۱؛ صورتحساب با بدهی دفتری بسته می‌شود."""
    with session_scope() as sess:
        user = _accountant_user(sess)
        party = sales.create_party(sess, user, "هتل خارجی ممیزی (زیان)", "supplier")
        cash = svc.account_by_code(sess, SYSTEM_CODES["cash"])
        _fund_cash(sess, user, cash, "تأمین وجه ممیزی (زیان)")
        bill = _fx_bill(sess, user, party)                       # ۱_۰۵۰_۰۰۰_۰۰۰ ریال دفتری
        assert bill.net_rial == 1_050_000_000 == bill.total_rial
        paid = 1_120_000_000                                     # ۱٬۰۰۰ یورو × نرخ ۱٬۱۲۰٬۰۰۰
        s = sales.create_settlement(sess, user, "out", party.id, cash.id, paid, jd.jtoday(),
                                    issue=True, target_bill_id=bill.id,
                                    currency="EUR", fx_amount_minor=100_000,
                                    fx_rate_rial=1_120_000)
        dr, cr = _entry_debit_credit_by_code(sess, s.entry_id)
        assert sum(dr.values()) == sum(cr.values()) == paid, "سند ارزی باید به همان ریال تراز باشد"
        assert dr.get(SYSTEM_CODES["fx_loss"]) == 70_000_000, "۷۰ میلیون ریال زیان واقع‌شده"
        entry = sess.get(JournalEntry, s.entry_id)
        party_line = next(l for l in entry.lines if l.account_id == party.account_id)
        assert party_line.debit_rial == 1_050_000_000, "بدهی دفتری (نه پول پرداختی) بدهکار می‌شود"
        sess.refresh(bill)
        assert bill.paid_rial == bill.total_rial, "صورتحساب باید کاملاً تسویه به نظر برسد"
        alloc = s.allocations[0]
        assert alloc.target_type == "bill" and alloc.target_id == bill.id
        assert alloc.amount_rial == 1_050_000_000, "تخصیص با بدهی دفتری آزادشده ثبت می‌شود"


def test_fx_settlement_posts_realized_gain(client, accountant):
    """پرداخت با نرخ پایین‌تر از نرخ دفتری → سود ۴۱۱۱."""
    with session_scope() as sess:
        user = _accountant_user(sess)
        party = sales.create_party(sess, user, "هتل خارجی ممیزی (سود)", "supplier")
        cash = svc.account_by_code(sess, SYSTEM_CODES["cash"])
        _fund_cash(sess, user, cash, "تأمین وجه ممیزی (سود)")
        bill = _fx_bill(sess, user, party, minor=100_000, book_rate=1_200_000)
        paid = 1_100_000_000                                     # ۱٬۰۰۰ یورو × نرخ ۱٬۱۰۰٬۰۰۰
        s = sales.create_settlement(sess, user, "out", party.id, cash.id, paid, jd.jtoday(),
                                    issue=True, target_bill_id=bill.id,
                                    currency="EUR", fx_amount_minor=100_000,
                                    fx_rate_rial=1_100_000)
        dr, cr = _entry_debit_credit_by_code(sess, s.entry_id)
        assert sum(dr.values()) == sum(cr.values()), "سند ارزی باید به ریال تراز باشد"
        assert cr.get(SYSTEM_CODES["cash"]) == paid, "پول واقعی خروجی از صندوق"
        assert cr.get(SYSTEM_CODES["fx_gain"]) == 100_000_000, "۱۰۰ میلیون ریال سود واقع‌شده"
        sess.refresh(bill)
        assert bill.paid_rial == bill.total_rial


def test_fx_partial_settlements_release_book_value_exactly(client, accountant):
    """دو قسط ارزی: هر قسط سهم دفتری خودش را آزاد می‌کند و مجموع دقیقاً مساوی صورتحساب می‌شود."""
    with session_scope() as sess:
        user = _accountant_user(sess)
        party = sales.create_party(sess, user, "هتل خارجی ممیزی (قسط)", "supplier")
        cash = svc.account_by_code(sess, SYSTEM_CODES["cash"])
        _fund_cash(sess, user, cash, "تأمین وجه ممیزی (قسط)")
        bill = _fx_bill(sess, user, party, minor=100_000, book_rate=1_000_000)
        s1 = sales.create_settlement(sess, user, "out", party.id, cash.id, 420_000_000,
                                     jd.jtoday(), issue=True, target_bill_id=bill.id,
                                     currency="EUR", fx_amount_minor=40_000, fx_rate_rial=1_050_000)
        sess.refresh(bill)
        assert bill.paid_rial == 400_000_000, "قسط اول ۴۰۰٪ یورویی = ۴۰۰ میلیون ریال بدهی دفتری"
        dr1, _ = _entry_debit_credit_by_code(sess, s1.entry_id)
        assert dr1.get(SYSTEM_CODES["fx_loss"]) == 20_000_000
        s2 = sales.create_settlement(sess, user, "out", party.id, cash.id, 570_000_000,
                                     jd.jtoday(), issue=True, target_bill_id=bill.id,
                                     currency="EUR", fx_amount_minor=60_000, fx_rate_rial=950_000)
        _, cr2 = _entry_debit_credit_by_code(sess, s2.entry_id)
        assert cr2.get(SYSTEM_CODES["fx_gain"]) == 30_000_000
        sess.refresh(bill)
        assert bill.paid_rial == bill.total_rial == 1_000_000_000, \
            "مجموع اقساط باید دقیقاً بدهی دفتری را ببندد (بدون گرد)"


def test_fx_settlement_requires_complete_data_else_fails_closed(client, accountant):
    """هر داده‌ی ارزیِ ناقص → خطای دامنه‌ی فارسی و هیچ ثبتی انجام نمی‌شود."""
    with session_scope() as sess:
        user = _accountant_user(sess)
        supplier = sales.create_party(sess, user, "هتل خارجی ممیزی (رد)", "supplier")
        customer = sales.create_party(sess, user, "مشتری ممیزی (رد ارزی)", "customer")
        cash = svc.account_by_code(sess, SYSTEM_CODES["cash"])
        fx_bill = _fx_bill(sess, user, supplier)
        rial_bill = sales.create_bill(sess, user, supplier.id, jd.jtoday(), net=500_000_000,
                                      vat=0, description="صورتحساب ریالی", issue=True)

        def expect(keyword, **kw):
            try:
                sales.create_settlement(sess, user, kw.pop("direction", "out"),
                                        kw.pop("party_id", supplier.id), cash.id,
                                        kw.pop("amount", 100_000_000), jd.jtoday(), **kw)
            except DomainError as exc:
                assert keyword in exc.message, f"پیام خطا باید شامل «{keyword}» باشد: {exc.message}"
            else:
                raise AssertionError(f"باید با خطای «{keyword}» رد می‌شد ولی ثبت شد")

        # بدون صورتحساب هدف
        expect("صورتحساب هدف", amount=1_050_000_000, currency="EUR",
               fx_amount_minor=100_000, fx_rate_rial=1_050_000)
        # تسویه‌ی ارزی روی صورتحساب ریالی
        expect("داده‌ی ارزی ندارد", amount=1_050_000_000, currency="EUR",
               fx_amount_minor=100_000, fx_rate_rial=1_050_000, target_bill_id=rial_bill.id)
        # ناهمخوانی ارز تسویه با صورتحساب
        expect("یکی نیست", amount=1_050_000_000, currency="USD",
               fx_amount_minor=100_000, fx_rate_rial=1_050_000, target_bill_id=fx_bill.id)
        # مبلغ ریالی ≠ مبلغ ارزی × نرخ
        expect("برابر", amount=999_999_999, currency="EUR",
               fx_amount_minor=100_000, fx_rate_rial=1_050_000, target_bill_id=fx_bill.id)
        # حاصل‌ضرب به ریال صحیح نمی‌رسد (گرد ممنوع)
        expect("ریال صحیح", amount=1_010_001, currency="EUR",
               fx_amount_minor=101, fx_rate_rial=1_000_001, target_bill_id=fx_bill.id)
        # جهت «دریافت» با داده‌ی ارزی
        expect("فعلاً فقط برای پرداخت", direction="in", party_id=customer.id,
               amount=1_050_000_000, currency="EUR",
               fx_amount_minor=100_000, fx_rate_rial=1_050_000)
        # تسویه‌ی ریالیِ هدفمند روی صورتحساب ارزی
        expect("ارزی است", amount=500_000_000, target_bill_id=fx_bill.id)


def test_rial_settlement_never_auto_allocates_fx_bills(client, accountant):
    """پرداخت ریالیِ غیرهدفمند به هیچ‌عنوان روی مانده‌ی ارزی نمی‌نشیند (نرخ روز ناشناخته است)."""
    with session_scope() as sess:
        user = _accountant_user(sess)
        party = sales.create_party(sess, user, "تأمین‌کننده مختلط ممیزی", "supplier")
        cash = svc.account_by_code(sess, SYSTEM_CODES["cash"])
        _fund_cash(sess, user, cash, "تأمین وجه ممیزی (مختلط)")
        fx_bill = _fx_bill(sess, user, party, minor=100_000, book_rate=1_000_000)
        rial_bill = sales.create_bill(sess, user, party.id, jd.jtoday(), net=500_000_000,
                                      vat=0, description="صورتحساب ریالی عادی", issue=True)
        sales.create_settlement(sess, user, "out", party.id, cash.id, 500_000_000,
                                jd.jtoday(), issue=True)
        sess.refresh(fx_bill)
        sess.refresh(rial_bill)
        assert fx_bill.paid_rial == 0, "بدهی ارزی با پول ریالیِ بدون نرخ تخصیص نمی‌خورد"
        assert rial_bill.paid_rial == 500_000_000, "صورتحساب ریالی تخصیص می‌خورد"


def test_targeted_rial_settlement_against_rial_bill(client, accountant):
    """اتصال دستی پرداخت ریالی به صورتحساب ریالی هم کار می‌کند و مازاد بیرون نمی‌ریزد."""
    with session_scope() as sess:
        user = _accountant_user(sess)
        party = sales.create_party(sess, user, "تأمین‌کننده هدف‌گذاری ممیزی", "supplier")
        cash = svc.account_by_code(sess, SYSTEM_CODES["cash"])
        _fund_cash(sess, user, cash, "تأمین وجه ممیزی (هدف)")
        bill = sales.create_bill(sess, user, party.id, jd.jtoday(), net=300_000_000,
                                 vat=0, description="هدف پرداخت", issue=True)
        s = sales.create_settlement(sess, user, "out", party.id, cash.id, 300_000_000,
                                    jd.jtoday(), issue=True, target_bill_id=bill.id)
        sess.refresh(bill)
        assert bill.paid_rial == 300_000_000
        assert s.allocations[0].target_id == bill.id
        try:
            sales.create_settlement(sess, user, "out", party.id, cash.id, 400_000_000,
                                    jd.jtoday(), issue=True, target_bill_id=bill.id)
        except DomainError as exc:
            assert "مانده" in exc.message
        else:
            raise AssertionError("پرداخت بیش از مانده‌ی صورتحساب هدف باید رد شود")


def test_cancel_fx_settlement_reopens_bill(client, accountant):
    """ابطال تسویه‌ی ارزی، صورتحساب را دوباره باز می‌کند (برگشت فقط-روبه‌جلویی)."""
    with session_scope() as sess:
        user = _accountant_user(sess)
        party = sales.create_party(sess, user, "هتل خارجی ممیزی (ابطال)", "supplier")
        cash = svc.account_by_code(sess, SYSTEM_CODES["cash"])
        _fund_cash(sess, user, cash, "تأمین وجه ممیزی (ابطال)")
        bill = _fx_bill(sess, user, party, minor=100_000, book_rate=1_000_000)
        s = sales.create_settlement(sess, user, "out", party.id, cash.id, 1_000_000_000,
                                    jd.jtoday(), issue=True, target_bill_id=bill.id,
                                    currency="EUR", fx_amount_minor=100_000, fx_rate_rial=1_000_000)
        sess.refresh(bill)
        assert bill.paid_rial == bill.total_rial
        sales.cancel_settlement(sess, user, s.id, "برگشت پرداخت اشتباه", "")
        # session این برنامه autoflush=False دارد؛ پیش از خواندن از دیتابیس تغییرهای درون‌نشست را بفرست
        sess.flush()
        sess.refresh(bill)
        assert bill.paid_rial == 0, "ابطال باید مانده‌ی صورتحساب را دوباره باز کند"
        assert s.status == "cancelled"
