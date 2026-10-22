"""دریافت/پرداخت چندابزاری (به سبک سند محک): چند روش در یک سند + تخفیف تسویه + چک کامل."""
from __future__ import annotations

from app import dates as jd
from conftest import account_id


def _party(headers_client, headers, name, kind="customer", **extra):
    r = headers_client.post("/api/parties", headers=headers,
                            json={"name": name, "kind": kind, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _invoice(client, h, party_id, price):
    r = client.post("/api/invoices", headers=h, json={
        "party_id": party_id, "date": "1405/07/01", "description": "فاکتور تست تسویه گروهی",
        "lines": [{"service_type": "tour", "qty": 1, "unit_price": price, "discount": 0,
                   "taxable": False}], "issue": True})
    assert r.status_code == 200, r.text
    return r.json()


def _invoice_row(client, h, inv_id):
    rows = client.get("/api/invoices?status=all", headers=h).json()["rows"]
    return next(r for r in rows if r["id"] == inv_id)


def test_batch_multi_instrument_atomic_with_discount(client, accountant):
    h = accountant
    cust = _party(client, h, "مشتری تسویه گروهی")
    inv = _invoice(client, h, cust["id"], 100_000_000)
    cash = account_id(client, h, "1101")
    bank = account_id(client, h, "110201")
    body = {"direction": "in", "party_id": cust["id"], "date": "1405/07/02",
            "description": "تسویه کامل ترکیبی", "discount": 10_000_000,
            "target_invoice_id": inv["id"], "items": [
                {"method": "cash", "amount": 30_000_000, "account_id": cash},
                {"method": "transfer", "amount": 20_000_000, "account_id": bank,
                 "ref_no": "PE-88001"},
                {"method": "cheque", "amount": 40_000_000,
                 "cheque_number": "445566", "cheque_bank": "ملت", "cheque_branch": "ونک",
                 "cheque_account_number": "12345678", "cheque_sheba": "IR120190000000123456789012",
                 "cheque_sayad_id": "SA-998877", "cheque_due_date": "1405/09/15"},
            ]}
    headers = {**h, "Idempotency-Key": "batch-atomic-test-1"}
    r = client.post("/api/settlements/batch", headers=headers, json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["total"] == 90_000_000 and res["discount"] == 10_000_000 and res["count"] == 4
    assert all(row["batch_no"] == res["batch_no"] for row in res["rows"])
    methods = {row["method"] for row in res["rows"]}
    assert methods == {"cash", "transfer", "cheque", "discount"}

    # پاسخ با همان کلید همان سند را برمی‌گرداند، نه سند دوم
    again = client.post("/api/settlements/batch", headers=headers, json=body)
    assert again.status_code == 200 and again.json() == res

    # فاکتور کاملاً تسویه شد: ۹۰ پول + ۱۰ تخفیف
    row = _invoice_row(client, h, inv["id"])
    assert row["paid"] == 100_000_000 and row["open"] == 0

    # اسناد روی دفتر: صندوق ۳۰ + بانک ۲۰ + چک دریافتی ۴۰ + تخفیف ۴۱۰۴ → همه cr تفصیلی مشتری
    from app.db import session_scope
    from sqlalchemy import select
    from app.models import Cheque as ChequeModel, JournalLine, JournalEntry, Account, Settlement
    with session_scope() as sess:
        rows = {}
        for num in [rw["number"] for rw in res["rows"]]:
            e = sess.execute(select(JournalEntry).where(JournalEntry.source_ref == num)).scalar_one()
            lines = sess.execute(select(JournalLine).where(JournalLine.entry_id == e.id)).scalars().all()
            rows[num] = [(sess.get(Account, l.account_id).code, l.debit_rial, l.credit_rial) for l in lines]
        nums = list(rows.values())
        assert any(("1101", 30_000_000, 0) in l for l in nums)       # نقد سند یافت
        assert any(("110201", 20_000_000, 0) in l for l in nums)     # حواله
        assert any(("1104", 40_000_000, 0) in l for l in nums)       # چک دریافتی
        assert any(("4104", 10_000_000, 0) in l for l in nums)       # تخفیف تسویه (کسر درآمد)
        # همهٔ بستانکارها به تفصیلی مشتری (کد شروع با 1103)
        total_credit = sum(a[2] for l in nums for a in l if a[0].startswith("1103"))
        assert total_credit == 100_000_000
        # رکورد چک با فیلدهای کامل محک
        ch = sess.execute(select(ChequeModel).where(ChequeModel.number == "445566")).scalar_one()
        assert ch.sheba == "IR120190000000123456789012" and ch.sayad_id == "SA-998877"
        assert ch.account_number == "12345678" and ch.direction == "in"
        # شماره پیگیری حواله روی تسویه نشست
        st = sess.execute(select(Settlement).where(Settlement.method == "transfer")).scalar_one()
        assert st.ref_no == "PE-88001"

    # و اگر مجموع از ماندهٔ فاکتور بیشتر می‌شد، قبل از نوشتن هر سندی رد می‌شد (اتمی بودن)
    inv2 = _invoice(client, h, cust["id"], 50_000_000)
    bad = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/03",
        "target_invoice_id": inv2["id"], "description": "بیش از مانده",
        "items": [{"method": "cash", "amount": 40_000_000, "account_id": cash},
                  {"method": "cash", "amount": 20_000_000, "account_id": cash}]})
    assert bad.status_code == 400 and "مانده‌ی فاکتور" in bad.json()["detail"]
    # هیچ ردیفی نباید از آن تلاش ناموفق ثبت شده باشد
    allrows = client.get("/api/settlements", headers=h).json()["rows"]
    assert not any(r["description"] == "بیش از مانده" for r in allrows)


def test_batch_empty_rejected(client, accountant):
    h = accountant
    cust = _party(client, h, "مشتری خالی")
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/02", "items": []})
    assert r.status_code == 400


def test_batch_credit_spends_and_overcredit_rejected(client, accountant):
    h = accountant
    cust = _party(client, h, "مشتری اعتباری گروهی")
    # صدور اعتبار ۱۵م با لغو
    inv = _invoice(client, h, cust["id"], 15_000_000)
    bank = account_id(client, h, "110201")
    client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": cust["id"], "account_id": bank,
        "amount": 15_000_000, "date": "1405/07/01", "issue": True})
    pc = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 15_000_000, "reason": "تولید اعتبار", "date": "1405/07/02", "mode": "credit"})
    assert pc.status_code == 200
    # فاکتور ۲۵م با ۱۵ اعتبار + ۱۰ نقد بسته شود
    cash = account_id(client, h, "1101")
    inv2 = _invoice(client, h, cust["id"], 25_000_000)
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/03",
        "target_invoice_id": inv2["id"],
        "items": [{"method": "credit", "amount": 15_000_000},
                  {"method": "cash", "amount": 10_000_000, "account_id": cash}]})
    assert r.status_code == 200, r.text
    row = _invoice_row(client, h, inv2["id"])
    assert row["paid"] == 25_000_000 and row["open"] == 0
    # دو ردیف اعتباری که مجموع آن‌ها از مانده بیشتر است رد می‌شود
    bad = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/03",
        "items": [{"method": "credit", "amount": 10_000_000},
                  {"method": "credit", "amount": 8_000_000}]})
    assert bad.status_code == 400 and "اعتبار" in bad.json()["detail"]


def test_batch_pay_supplier_with_income_discount(client, accountant):
    h = accountant
    sup = _party(client, h, "تامین تخفیفی", "supplier")
    bank = account_id(client, h, "110201")
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "out", "party_id": sup["id"], "date": "1405/07/05",
        "description": "پرداخت با تخفیف گرفتن از تامین", "discount": 5_000_000,
        "items": [{"method": "transfer", "amount": 45_000_000, "account_id": bank,
                   "ref_no": "TR-999"}]})
    assert r.status_code == 200, r.text
    from app.db import session_scope
    from sqlalchemy import select
    from app.models import JournalLine, JournalEntry, Account
    rows = r.json()["rows"]
    disc = next(x for x in rows if x["method"] == "discount")
    with session_scope() as sess:
        e = sess.execute(select(JournalEntry).where(JournalEntry.source_ref == disc["number"])).scalar_one()
        lines = [(sess.get(Account, l.account_id).code, l.debit_rial, l.credit_rial)
                 for l in sess.execute(select(JournalLine).where(JournalLine.entry_id == e.id)).scalars()]
    assert ("4109", 0, 5_000_000) in lines          # تخفیف نقدی خرید — درآمد
    assert any(a[0].startswith("2101") and a[1] == 5_000_000 for a in lines)


def test_party_groups_and_balance_endpoint(client, accountant):
    h = accountant
    p1 = _party(client, h, "شرکت آلفا", group_name="مشتریان شرکت")
    p2 = _party(client, h, "شخص خانوادگی", group_name="توافق خانوادگی")
    groups = client.get("/api/parties/groups?kind=customer", headers=h).json()["rows"]
    assert "مشتریان شرکت" in groups and "توافق خانوادگی" in groups
    rows = client.get("/api/parties", headers=h).json()["rows"]
    r1 = next(r for r in rows if r["id"] == p1["id"])
    assert r1["group_name"] == "مشتریان شرکت"
    bal = client.get(f"/api/parties/{p2['id']}/balance", headers=h).json()
    assert bal["balance"] == 0 and bal["balance_label"] == "تسویه"
    # بعد از فاکتور، مانده زنده به‌روز است
    _invoice(client, h, p2["id"], 7_000_000)
    bal = client.get(f"/api/parties/{p2['id']}/balance", headers=h).json()
    assert bal["balance"] == 7_000_000 and bal["balance_label"] == "طلب ما"
    # ویرایش گروه از PATCH
    r = client.patch(f"/api/parties/{p2['id']}", headers=h, json={"group_name": "مشتریان شرکت"})
    assert r.status_code == 200 and r.json()["group_name"] == "مشتریان شرکت"
