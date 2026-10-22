"""آزمون‌های اتمی کانتر، مدل امانی/کمیسیون، تخصیص دریافت و مجوز سود."""
from __future__ import annotations

from app import dates as jd
from conftest import account_id


def _party(client, headers, name, kind="customer"):
    response = client.post("/api/parties", headers=headers, json={"name": name, "kind": kind})
    assert response.status_code == 200, response.text
    return response.json()


def _counter_body(customer_id, **overrides):
    body = {
        "party_id": customer_id,
        "date": jd.jalali_str(jd.jtoday()),
        "due_date": jd.jalali_str(jd.jtoday()),
        "description": "فروش آزمون کانتر",
        "lines": [{
            "service_type": "tour", "description": "خدمت کانتر", "qty": 1,
            "unit_price": 100_000_000, "discount": 0, "taxable": False,
            "revenue_model": "gross", "purchase_cost": 0,
        }],
        "payments": [],
    }
    body.update(overrides)
    return body


def test_counter_commission_sale_posts_liability_separate_receipts_and_queue(client, accountant):
    customer = _party(client, accountant, "مشتری کانتر امانی")
    airline = _party(client, accountant, "ایرلاین کانتر امانی", "supplier")
    cash = account_id(client, accountant, "1101")
    bank = account_id(client, accountant, "110201")
    before = client.get("/api/reports/trial-balance", headers=accountant).json()
    body = _counter_body(customer["id"], description="کانتر امانی آزمون یکپارچه", lines=[{
        "service_type": "ticket", "description": "بلیت آزمایشی", "qty": 1,
        "unit_price": 100_000_000, "discount": 10_000_000, "taxable": False,
        "flight_type": 2, "revenue_model": "commission", "purchase_cost": 60_000_000,
        "supplier_party_id": airline["id"],
    }], payments=[
        {"amount": 10_000_000, "method": "cash", "account_id": cash},
        {"amount": 20_000_000, "method": "card", "account_id": bank},
    ])
    headers = {**accountant, "Idempotency-Key": "counter-commission-atomic-v1"}
    response = client.post("/api/counter/sales", headers=headers, json=body)
    assert response.status_code == 200, response.text
    result = response.json()

    # همان کلید باید همان فروش را برگرداند، نه اینکه فروش/دریافت دوم بسازد.
    replay = client.post("/api/counter/sales", headers=headers, json=body)
    assert replay.status_code == 200 and replay.json() == result

    assert result["invoice"]["status"] == "issued"
    assert result["sales_total"] == 90_000_000
    assert result["purchase_total"] == 60_000_000
    assert result["received_total"] == 30_000_000
    assert result["balance"] == 60_000_000
    assert result["purchase_total"] == 60_000_000
    assert result["profit"] == 30_000_000
    assert len(result["settlements"]) == 2
    assert {s["method"] for s in result["settlements"]} == {"cash", "card"}
    assert all(s["allocation_invoice_id"] == result["invoice"]["id"] for s in result["settlements"])
    assert len(result["supplier_bills"]) == 1
    assert result["supplier_bills"][0]["type"] == "consignment"
    assert result["supplier_bills"][0]["status"] == "issued"
    assert result["moadian"] and result["moadian"]["status"] == "draft"
    assert any("کمیسیونی" in problem for problem in result["moadian"]["problems"])

    invoice = next(row for row in client.get("/api/invoices?status=issued", headers=accountant).json()["rows"]
                   if row["id"] == result["invoice"]["id"])
    assert invoice["paid"] == 30_000_000 and invoice["open"] == 60_000_000
    settlements = client.get("/api/settlements?direction=in", headers=accountant).json()["rows"]
    counter_settlements = [row for row in settlements if row["id"] in {s["id"] for s in result["settlements"]}]
    assert len(counter_settlements) == 2
    assert all(len(row["allocations"]) == 1 and row["allocations"][0]["target_type"] == "invoice"
               and row["allocations"][0]["target_id"] == result["invoice"]["id"]
               and row["allocations"][0]["amount"] == row["amount"] for row in counter_settlements)
    bills = client.get("/api/bills", headers=accountant).json()["rows"]
    bill = next(row for row in bills if row["id"] == result["supplier_bills"][0]["id"])
    assert bill["source_invoice_id"] == result["invoice"]["id"]
    assert "امانی" in bill["expense_account"]
    after = client.get("/api/reports/trial-balance", headers=accountant).json()
    assert before["balanced"] and after["balanced"]


def test_employee_counter_drafts_are_atomic_and_profit_is_not_returned(client, employee, accountant):
    customer = _party(client, accountant, "مشتری کانتر کارمند")
    supplier = _party(client, accountant, "تأمین‌کننده کانتر کارمند", "supplier")
    meta = client.get("/api/counter/meta", headers=employee)
    assert meta.status_code == 200 and meta.json()["show_profit"] is False
    body = _counter_body(customer["id"], description="کانتر کارمند پیش‌نویس آزمون", lines=[{
        "service_type": "tour", "description": "خدمت با بهای خرید", "qty": 1,
        "unit_price": 100_000_000, "discount": 0, "taxable": False,
        "revenue_model": "gross", "purchase_cost": 25_000_000,
        "supplier_party_id": supplier["id"],
    }], payments=[
        {"amount": 15_000_000, "method": "cash", "account_id": account_id(client, accountant, "1101")}
    ])
    response = client.post("/api/counter/sales", headers=employee, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["invoice"]["status"] == "draft"
    assert result["post_now"] is False
    assert "profit" not in result and "purchase_total" not in result
    assert all("amount" not in bill for bill in result["supplier_bills"])
    listed = client.get("/api/invoices?status=all", headers=employee).json()["rows"]
    own_invoice = next(row for row in listed if row["id"] == result["invoice"]["id"])
    assert "purchase_cost" not in own_invoice["lines"][0]
    assert "supplier_party_id" not in own_invoice["lines"][0]
    assert result["moadian"] is not None
    assert result["moadian"]["status"] == "draft"
    assert result["settlements"][0]["status"] == "draft"
    assert result["settlements"][0]["allocation_invoice_id"] == result["invoice"]["id"]


def test_counter_target_allocation_survives_approval_and_ignores_older_open_invoice(client, employee, accountant):
    customer = _party(client, accountant, "مشتری تخصیص مستقیم کانتر")
    today = jd.jalali_str(jd.jtoday())
    older = client.post("/api/invoices", headers=accountant, json={
        "party_id": customer["id"], "date": today, "due_date": today,
        "lines": [{"service_type": "visa", "unit_price": 30_000_000, "qty": 1, "taxable": False}],
        "issue": True,
    })
    assert older.status_code == 200, older.text
    earlier_invoice = older.json()
    body = _counter_body(customer["id"], description="کانتر با تخصیص دقیق آزمون", lines=[{
        "service_type": "tour", "description": "فروش تازه", "qty": 1,
        "unit_price": 50_000_000, "discount": 0, "taxable": False,
        "revenue_model": "gross", "purchase_cost": 0,
    }], payments=[{
        "amount": 10_000_000, "method": "cash", "account_id": account_id(client, accountant, "1101")
    }])
    result = client.post("/api/counter/sales", headers=employee, json=body)
    assert result.status_code == 200, result.text
    counter = result.json()
    assert counter["invoice"]["status"] == "draft"
    assert counter["settlements"][0]["status"] == "draft"

    issued = client.post(f"/api/invoices/{counter['invoice']['id']}/issue", headers=accountant, json={})
    assert issued.status_code == 200, issued.text
    settlement = client.post(f"/api/settlements/{counter['settlements'][0]['id']}/issue",
                             headers=accountant, json={})
    assert settlement.status_code == 200, settlement.text
    allocs = settlement.json()["allocations"]
    assert len(allocs) == 1
    assert allocs[0]["target_type"] == "invoice"
    assert allocs[0]["target_id"] == counter["invoice"]["id"]
    assert allocs[0]["amount"] == 10_000_000
    invoices = client.get("/api/invoices?status=issued", headers=accountant).json()["rows"]
    rows = {row["id"]: row for row in invoices}
    assert rows[earlier_invoice["id"]]["paid"] == 0
    assert rows[counter["invoice"]["id"]]["paid"] == 10_000_000


def test_counter_failure_rolls_back_invoice_and_every_related_row(client, accountant):
    customer = _party(client, accountant, "مشتری اتمی رول‌بک")
    description = "کانتر باید کامل rollback شود"  # شناسه‌ی قابل جست‌وجو برای تأیید نبودن آثار
    body = _counter_body(customer["id"], description=description, payments=[{
        "amount": 5_000_000, "method": "cheque", "account_id": None,
        "cheque_number": "", "cheque_due_date": "",
    }])
    failed = client.post("/api/counter/sales", headers=accountant, json=body)
    assert failed.status_code == 400, failed.text
    assert "شماره چک" in failed.json()["detail"]
    invoices = client.get("/api/invoices?status=all", headers=accountant).json()["rows"]
    assert all(row["description"] != description for row in invoices)
    customer_rows = [row for row in client.get("/api/settlements", headers=accountant).json()["rows"]
                     if row["party_id"] == customer["id"]]
    assert all("دریافت کانتر" not in row["description"] for row in customer_rows)


def _issue_credit(client, h, customer, amount):
    """فاکتور + دریافت کامل + لغو با حالت اعتبار تا مشتری اعتبار بگیرد."""
    inv = client.post("/api/invoices", headers=h, json={
        "party_id": customer["id"], "date": "1405/03/03", "description": "فاکتور برای صدور اعتبار",
        "lines": [{"service_type": "tour", "qty": 1, "unit_price": amount, "discount": 0,
                   "taxable": False}], "issue": True})
    assert inv.status_code == 200, inv.text
    bank = account_id(client, h, "110201")
    rec = client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": customer["id"], "account_id": bank,
        "amount": amount, "date": "1405/03/04", "issue": True})
    assert rec.status_code == 200, rec.text
    pc = client.post(f"/api/invoices/{inv.json()['id']}/partial-cancel", headers=h, json={
        "amount": amount, "reason": "اعتبار برای فروش کانتر", "date": "1405/03/10",
        "mode": "credit"})
    assert pc.status_code == 200, pc.text
    return pc.json()


def test_counter_credit_payment_spends_customer_credit(client, accountant):
    customer = _party(client, accountant, "مشتری اعتباری کانتر")
    pc = _issue_credit(client, accountant, customer, 20_000_000)
    assert pc["credit_remaining"] == 20_000_000

    # متای کانتر باید مانده اعتبار مشتری را نشان بدهد
    meta = client.get("/api/counter/meta", headers=accountant).json()
    meta_cust = next(c for c in meta["customers"] if c["id"] == customer["id"])
    assert meta_cust["credit_balance"] == 20_000_000
    assert meta_cust["credit_account_id"] == pc["credit_account_id"]

    # فروش ۳۰ میلیونی: ۲۰ با اعتبار + ۱۰ نقد — هیچ حسابی برای اعتبار لازم نیست
    cash = account_id(client, accountant, "1101")
    treasury0 = client.get("/api/reports/treasury", headers=accountant).json()["total"]
    body = _counter_body(customer["id"], description="فروش ترکیبی اعتبار و نقد", lines=[{
        "service_type": "tour", "description": "خدمت ترکیبی", "qty": 1,
        "unit_price": 30_000_000, "discount": 0, "taxable": False,
        "revenue_model": "gross", "purchase_cost": 0,
    }], payments=[
        {"amount": 20_000_000, "method": "credit", "account_id": None},
        {"amount": 10_000_000, "method": "cash", "account_id": cash},
    ])
    result = client.post("/api/counter/sales", headers=accountant, json=body)
    assert result.status_code == 200, result.text
    counter = result.json()
    assert counter["received_total"] == 30_000_000 and counter["balance"] == 0
    assert {s["method"] for s in counter["settlements"]} == {"credit", "cash"}
    assert all(s["allocation_invoice_id"] == counter["invoice"]["id"] for s in counter["settlements"])

    # خزانه فقط ۱۰ میلیون نقد رشد می‌کند؛ اعتبار پول جابه‌جا نمی‌کند
    treasury1 = client.get("/api/reports/treasury", headers=accountant).json()["total"]
    assert treasury1 - treasury0 == 10_000_000

    # سند مصرف اعتبار: بدهکار ۲۱۰۳/تفصیلی اعتبار، بستانکار تفصیلی مشتری — مانده اعتبار صفر
    meta2 = client.get("/api/counter/meta", headers=accountant).json()
    meta_cust2 = next(c for c in meta2["customers"] if c["id"] == customer["id"])
    assert meta_cust2["credit_balance"] == 0
    credits = client.get("/api/reports/customer-credits", headers=accountant).json()["rows"]
    row = next(c for c in credits if c["party_id"] == customer["id"])
    assert row["issued"] == 20_000_000 and row["spent"] == 20_000_000 and row["remaining"] == 0
    # سند رسمی مصرف اعتبار: dr تفصیلی ۲۱۰۳، cr تفصیلی مشتری ۱۱۰۳
    credit_settlement = next(s for s in counter["settlements"] if s["method"] == "credit")
    from app import db as _db
    from sqlalchemy import select as _select
    from app.models import Account as _Account, JournalEntry as _Entry, JournalLine as _Line
    with _db.session_scope() as sess:
        entry = sess.execute(_select(_Entry).where(
            _Entry.source_ref == credit_settlement["number"])).scalar_one()
        rows = [(sess.get(_Account, l.account_id).code, l.debit_rial, l.credit_rial)
                for l in sess.execute(_select(_Line).where(_Line.entry_id == entry.id)).scalars()]
    codes = {r[0] for r in rows}
    credit_code = next(c for c in codes if c.startswith("2103-"))
    assert (credit_code, 20_000_000, 0) in rows
    assert sum(r[2] for r in rows if r[0].startswith("1103")) == 20_000_000

    # مصرف بیش‌تر از مانده اعتبار رد شود (حالا صفر است)
    body2 = _counter_body(customer["id"], description="سوءاستفاده از اعتبار", lines=[{
        "service_type": "tour", "description": "تلاش مجدد", "qty": 1,
        "unit_price": 5_000_000, "discount": 0, "taxable": False,
        "revenue_model": "gross", "purchase_cost": 0,
    }], payments=[{"amount": 5_000_000, "method": "credit", "account_id": None}])
    failed = client.post("/api/counter/sales", headers=accountant, json=body2)
    assert failed.status_code == 400
    assert "اعتبار" in failed.json()["detail"]
    invoices = client.get("/api/invoices?status=all", headers=accountant).json()["rows"]
    assert all(row["description"] != "تلاش مجدد" for row in invoices)


def test_counter_credit_requires_existing_credit(client, accountant):
    customer = _party(client, accountant, "مشتری بدون اعتبار")
    body = _counter_body(customer["id"], description="اعتبار ندارد", lines=[{
        "service_type": "tour", "description": "بدون اعتبار", "qty": 1,
        "unit_price": 5_000_000, "discount": 0, "taxable": False,
        "revenue_model": "gross", "purchase_cost": 0,
    }], payments=[{"amount": 5_000_000, "method": "credit", "account_id": None}])
    failed = client.post("/api/counter/sales", headers=accountant, json=body)
    assert failed.status_code == 400
    assert "اعتبار خرید بعدی ندارد" in failed.json()["detail"]
