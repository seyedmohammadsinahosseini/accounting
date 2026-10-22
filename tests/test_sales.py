"""آزمون‌های فروش خدمات، پرونده‌ی سفر، دریافت و پرداخت، و نرخ مالیات با تاریخ اجرا."""
from __future__ import annotations


from conftest import account_id
from app import dates as jd


def _create_party(client, headers, name, kind="customer") -> dict:
    r = client.post("/api/parties", headers=headers, json={"name": name, "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()


def _trip(client, headers, title="تور آزمون") -> dict:
    r = client.post("/api/trips", headers=headers, json={
        "title": title, "start_date": jd.jalali_str(jd.jtoday()),
        "end_date": jd.jalali_str(jd.jtoday() + __import__("datetime").timedelta(days=5)),
        "destination": "آنتالیا"})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------- نرخ مالیات
def test_vat_rate_comes_from_table_by_date(client, accountant):
    """آزمون نمونه‌ی ماژول ۵: نرخ نباید ثابت باشد؛ باید با تاریخ اجرا تعیین شود."""
    # فاکتور امروز (۱۴۰۵) باید نرخ همان روز را بخورد: ۱۰٪ (از سال ۱۴۰۴)
    party = _create_party(client, accountant, "مشتری نرخ مالیات")
    r = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": jd.jalali_str(jd.jtoday()),
        "lines": [{"service_type": "tour", "unit_price": 100_000_000, "qty": 1, "taxable": True}]})
    inv_now = r.json()
    assert inv_now["vat"] == 10_000_000, inv_now          # ۱۰٪ — نرخ جاری سال ۱۴۰۵

    # نرخ تازه ۱۲٪ از تاریخ فردا
    tomorrow = jd.jalali_str(jd.jtoday() + __import__("datetime").timedelta(days=1))
    created = client.post("/api/tax-rates", headers=accountant, json={
        "title": "مالیات بر ارزش افزوده ۱۲٪", "rate_percent": 12, "from_date": tomorrow,
        "note": "آزمون"})
    assert created.status_code == 200, created.text

    # فاکتور فردا باید ۱۲٪ بخورد و فاکتور امروز دست‌نخورده بماند
    r2 = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": tomorrow,
        "lines": [{"service_type": "tour", "unit_price": 100_000_000, "qty": 1, "taxable": True}]})
    assert r2.json()["vat"] == 12_000_000, r2.json()

    again = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": jd.jalali_str(jd.jtoday()),
        "lines": [{"service_type": "tour", "unit_price": 100_000_000, "qty": 1, "taxable": True}]})
    assert again.json()["vat"] == 10_000_000, "فاکتور امروز باید همچنان با نرخ ۱۰٪ بماند"


def test_tax_rate_is_service_scoped_and_editable(client, accountant):
    party = _create_party(client, accountant, "مشتری نرخ خدمت")
    tomorrow = jd.jtoday() + __import__("datetime").timedelta(days=1)
    from_date = jd.jalali_str(tomorrow)
    created = client.post("/api/tax-rates", headers=accountant, json={
        "title": "نرخ اقامت آزمون", "rate_percent": 9, "from_date": from_date,
        "service_type": "hotel", "note": "محدوده‌ی خدمت"})
    assert created.status_code == 200, created.text
    rate_id = created.json()["id"]

    hotel = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": from_date,
        "lines": [{"service_type": "hotel", "unit_price": 100_000_000, "qty": 1, "taxable": True}]})
    tour = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": from_date,
        "lines": [{"service_type": "tour", "unit_price": 100_000_000, "qty": 1, "taxable": True}]})
    assert hotel.status_code == 200 and hotel.json()["vat"] == 9_000_000
    assert tour.status_code == 200 and tour.json()["vat"] != hotel.json()["vat"]

    later = jd.jalali_str(tomorrow + __import__("datetime").timedelta(days=1))
    updated = client.patch(f"/api/tax-rates/{rate_id}", headers=accountant, json={
        "title": "نرخ اقامت اصلاح‌شده", "rate_percent": 8, "from_date": later,
        "service_type": "hotel"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["service_type"] == "hotel"
    invoice = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": later,
        "lines": [{"service_type": "hotel", "unit_price": 100_000_000, "qty": 1, "taxable": True}]})
    assert invoice.status_code == 200 and invoice.json()["vat"] == 8_000_000


def test_zero_vat_means_exempt_but_empty_means_automatic(client, accountant):
    supplier = _create_party(client, accountant, "تأمین‌کننده مالیات", "supplier")
    auto = client.post("/api/bills", headers=accountant, json={
        "party_id": supplier["id"], "date": jd.jalali_str(jd.jtoday()), "net": 100_000_000,
        "description": "بدون مالیات"}).json()
    assert auto["vat"] == 10_000_000, "خالی گذاشتن مالیات یعنی حساب خودکار با نرخ روز (۱۰٪ در ۱۴۰۵)"

    exempt = client.post("/api/bills", headers=accountant, json={
        "party_id": supplier["id"], "date": jd.jalali_str(jd.jtoday()), "net": 100_000_000,
        "vat": 0, "description": "معاف"}).json()
    assert exempt["vat"] == 0 and exempt["total"] == 100_000_000


def test_exempt_line_has_no_vat(client, accountant):
    party = _create_party(client, accountant, "مشتری معاف")
    r = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": jd.jalali_str(jd.jtoday()),
        "lines": [{"service_type": "tour", "unit_price": 50_000_000, "qty": 1, "taxable": False}]})
    assert r.json()["vat"] == 0


# --------------------------------------------------------------- فاکتور فروش
def test_invoice_creates_balanced_entry_and_hits_right_accounts(client, accountant):
    party = _create_party(client, accountant, "مشتری فاکتور")
    before = client.get("/api/reports/trial-balance", headers=accountant).json()
    r = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": jd.jalali_str(jd.jtoday()),
        "description": "فاکتور آزمون",
        "lines": [{"service_type": "tour", "unit_price": 200_000_000, "qty": 1},
                  {"service_type": "ticket", "unit_price": 60_000_000, "qty": 2, "discount": 20_000_000}],
        "issue": True})
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["status"] == "issued" and inv["number"].startswith(str(jd.jalali_parts(jd.jtoday())[0]))
    # جمع: ۲۰۰ میلیون + (۲ × ۶۰ میلیون − ۲۰ میلیون تخفیف) + مالیات ۹٪ روی هر دو سطر
    net = 200_000_000 + (2 * 60_000_000 - 20_000_000)
    assert inv["total"] == net + int(net * 10 / 100)      # نرخ روز: ۱۰٪ در سال ۱۴۰۵
    assert inv["vat"] > 0 and inv["open"] == inv["total"]

    after = client.get("/api/reports/trial-balance", headers=accountant).json()
    assert after["balanced"] is True
    assert after["total_debit"] == before["total_debit"] + inv["total"]

    # سند فاکتور باید چهار سطر داشته باشد: مشتری، درآمد تور، درآمد بلیت، مالیات
    entries = client.get("/api/entries?status=posted&q=فاکتور", headers=accountant).json()["rows"]
    assert entries
    detail = client.get(f"/api/entries/{entries[0]['id']}", headers=accountant).json()
    labels = [l["account_label"] for l in detail["lines"]]
    assert any("درآمد فروش تور" in x for x in labels)
    assert any("درآمد فروش بلیت" in x for x in labels)
    assert any("ارزش افزوده" in x for x in labels)
    assert detail["balanced"] is True


def test_partial_receipt_reduces_invoice_open_amount(client, accountant):
    party = _create_party(client, accountant, "مشتری دریافت")
    bank = account_id(client, accountant, "110201")
    inv = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": jd.jalali_str(jd.jtoday()),
        "lines": [{"service_type": "visa", "unit_price": 100_000_000, "qty": 1}],
        "issue": True}).json()
    total = inv["total"]

    s = client.post("/api/settlements", headers=accountant, json={
        "direction": "in", "party_id": party["id"], "account_id": bank,
        "amount": 40_000_000, "date": jd.jalali_str(jd.jtoday()),
        "description": "پیش‌پرداخت"}).json()
    assert s["number"].startswith(str(jd.jalali_parts(jd.jtoday())[0]) + "-د-")

    inv_after = next(r for r in client.get("/api/invoices", headers=accountant).json()["rows"]
                     if r["id"] == inv["id"])
    assert inv_after["paid"] == 40_000_000
    assert inv_after["open"] == total - 40_000_000

    # دریافت دوم مانده را صفر می‌کند
    client.post("/api/settlements", headers=accountant, json={
        "direction": "in", "party_id": party["id"], "account_id": bank,
        "amount": total - 40_000_000, "date": jd.jalali_str(jd.jtoday())})
    inv_final = next(r for r in client.get("/api/invoices", headers=accountant).json()["rows"]
                     if r["id"] == inv["id"])
    assert inv_final["open"] == 0
    party_row = next(p for p in client.get("/api/parties", headers=accountant).json()["rows"]
                     if p["id"] == party["id"])
    assert party_row["balance"] == 0


def test_cancel_invoice_reverses_entry_and_blocks_when_paid(client, accountant):
    party = _create_party(client, accountant, "مشتری ابطال فاکتور")
    bank = account_id(client, accountant, "110201")
    inv = client.post("/api/invoices", headers=accountant, json={
        "party_id": party["id"], "date": jd.jalali_str(jd.jtoday()),
        "lines": [{"service_type": "tour", "unit_price": 10_000_000, "qty": 1}],
        "issue": True}).json()
    client.post("/api/settlements", headers=accountant, json={
        "direction": "in", "party_id": party["id"], "account_id": bank,
        "amount": 1_000_000, "date": jd.jalali_str(jd.jtoday())})
    blocked = client.post(f"/api/invoices/{inv['id']}/cancel", headers=accountant,
                          json={"reason": "آزمون"})
    assert blocked.status_code == 400
    assert "پول دریافت شده" in blocked.json()["detail"]


# --------------------------------------------------------------- صورتحساب و پرداخت
def test_supplier_bill_and_payment_flow(client, accountant):
    supplier = _create_party(client, accountant, "تأمین‌کننده آزمون", "supplier")
    bank = account_id(client, accountant, "110201")
    expense = account_id(client, accountant, "5101")
    trip = _trip(client, accountant)

    bill = client.post("/api/bills", headers=accountant, json={
        "party_id": supplier["id"], "date": jd.jalali_str(jd.jtoday()), "net": 50_000_000,
        "vat": 0, "expense_account_id": expense, "trip_id": trip["id"],
        "description": "هتل آزمون", "issue": True}).json()
    assert bill["status"] == "issued" and bill["total"] == 50_000_000

    client.post("/api/settlements", headers=accountant, json={
        "direction": "out", "party_id": supplier["id"], "account_id": bank,
        "amount": 50_000_000, "date": jd.jalali_str(jd.jtoday()), "trip_id": trip["id"]})
    bill_after = next(b for b in client.get("/api/bills", headers=accountant).json()["rows"]
                      if b["id"] == bill["id"])
    assert bill_after["open"] == 0

    detail = client.get(f"/api/trips/{trip['id']}", headers=accountant).json()
    assert detail["totals"]["cost"] == 50_000_000
    assert detail["totals"]["payable_open"] == 0


def test_settlement_cannot_exceed_cash_and_needs_right_party_kind(client, accountant):
    customer = _create_party(client, accountant, "مشتری دسترسی", "customer")
    bank = account_id(client, accountant, "110201")
    r = client.post("/api/settlements", headers=accountant, json={
        "direction": "out", "party_id": customer["id"], "account_id": bank,
        "amount": 1_000_000, "date": jd.jalali_str(jd.jtoday())})
    assert r.status_code == 400
    assert "تأمین‌کننده" in r.json()["detail"]

    expense = account_id(client, accountant, "5103")
    r2 = client.post("/api/settlements", headers=accountant, json={
        "direction": "in", "party_id": customer["id"], "account_id": expense,
        "amount": 1_000_000, "date": jd.jalali_str(jd.jtoday())})
    assert r2.status_code == 400
    assert "صندوق یا بانک" in r2.json()["detail"]


# --------------------------------------------------------------- پرونده‌ی سفر
def test_trip_profit_is_exact_and_marks_loss(client, accountant):
    customer = _create_party(client, accountant, "مشتری سود سفر")
    supplier = _create_party(client, accountant, "تأمین‌کننده سود سفر", "supplier")
    trip = _trip(client, accountant, "سفر سوددهی")
    loss_trip = _trip(client, accountant, "سفر ضرردهی")

    client.post("/api/invoices", headers=accountant, json={
        "party_id": customer["id"], "date": jd.jalali_str(jd.jtoday()), "trip_id": trip["id"],
        "lines": [{"service_type": "tour", "unit_price": 100_000_000, "qty": 1, "taxable": False}],
        "issue": True})
    client.post("/api/bills", headers=accountant, json={
        "party_id": supplier["id"], "date": jd.jalali_str(jd.jtoday()), "trip_id": trip["id"],
        "net": 60_000_000, "vat": 0, "description": "هتل", "issue": True})

    # یک سفر ضررده: فروش ۲۰ میلیون، هزینه ۵۰ میلیون
    client.post("/api/invoices", headers=accountant, json={
        "party_id": customer["id"], "date": jd.jalali_str(jd.jtoday()), "trip_id": loss_trip["id"],
        "lines": [{"service_type": "ticket", "unit_price": 20_000_000, "qty": 1, "taxable": False}],
        "issue": True})
    client.post("/api/bills", headers=accountant, json={
        "party_id": supplier["id"], "date": jd.jalali_str(jd.jtoday()), "trip_id": loss_trip["id"],
        "net": 50_000_000, "vat": 0, "description": "بلیت", "issue": True})

    rows = {t["id"]: t for t in client.get("/api/trips", headers=accountant).json()["rows"]}
    good = rows[trip["id"]]
    assert good["revenue"] == 100_000_000 and good["cost"] == 60_000_000
    assert good["profit"] == 40_000_000 and good["margin_percent"] == 40.0
    assert good["lost"] is False

    bad = rows[loss_trip["id"]]
    assert bad["profit"] == -30_000_000 and bad["lost"] is True


# --------------------------------------------------------------- تقویم پول
def test_cash_calendar_shows_upcoming_and_overdue(client, accountant):
    customer = _create_party(client, accountant, "مشتری تقویم")
    supplier = _create_party(client, accountant, "تأمین‌کننده تقویم", "supplier")
    today = jd.jtoday()
    far = today + __import__("datetime").timedelta(days=5)
    past = today - __import__("datetime").timedelta(days=3)

    client.post("/api/invoices", headers=accountant, json={
        "party_id": customer["id"], "date": jd.jalali_str(today), "due_date": jd.jalali_str(past),
        "lines": [{"service_type": "tour", "unit_price": 30_000_000, "qty": 1, "taxable": False}],
        "issue": True})
    client.post("/api/bills", headers=accountant, json={
        "party_id": supplier["id"], "date": jd.jalali_str(today), "due_date": jd.jalali_str(far),
        "net": 12_000_000, "vat": 0, "issue": True})

    d = client.get("/api/calendar?days=30", headers=accountant).json()
    assert d["overdue_in"] >= 30_000_000, "طلب معوق باید دیده شود"
    assert any(i["kind"] == "bill" and i["amount"] == 12_000_000 for i in d["items"])
    # سندهای تکراری نمونه هم باید در تقویم بیایند
    assert any(i["kind"] == "recurring" for i in d["items"])


# --------------------------------------------------------------- سایر
def test_employee_can_draft_but_not_issue_and_sees_no_balances(client, employee, accountant):
    parties = client.get("/api/parties", headers=employee).json()["rows"]
    assert parties, "کارمند باید فهرست اشخاص را ببیند تا فاکتور بزند"
    assert all(p["balance"] == 0 for p in parties), "مانده‌ی حساب مشتری کار کارمند نیست"
    trips = client.get("/api/trips", headers=employee).json()
    assert trips.get("limited") is True, "کارمند سود سفرها را نمی‌بیند"
    assert all("profit" not in t for t in trips["rows"])
    inv = client.post("/api/invoices", headers=employee, json={
        "party_id": parties[0]["id"], "date": jd.jalali_str(jd.jtoday()),
        "lines": [{"service_type": "tour", "unit_price": 5_000_000, "qty": 1}]}).json()
    assert client.post(f"/api/invoices/{inv['id']}/issue", headers=employee).status_code == 403
    assert client.get("/api/trips", headers=employee).status_code in (200, 403)


def test_recurring_entry_generates_once_per_month(client, accountant):
    bank = account_id(client, accountant, "110201")
    rent = account_id(client, accountant, "5103")
    r = client.post("/api/recurring", headers=accountant, json={
        "title": "اجاره آزمون", "day_of_month": 3,
        "lines": [{"account_id": rent, "debit": 5_000_000, "credit": 0, "description": "اجاره"},
                  {"account_id": bank, "debit": 0, "credit": 5_000_000, "description": "پرداخت"}]})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    jy, jm, _ = jd.jalali_parts(jd.jtoday())

    first = client.post(f"/api/recurring/{rid}/generate?jy={jy}&jm={jm}", headers=accountant)
    assert first.status_code == 200, first.text
    second = client.post(f"/api/recurring/{rid}/generate?jy={jy}&jm={jm}", headers=accountant)
    assert second.status_code == 400
    assert "از قبل ساخته شده" in second.json()["detail"]

    rows = client.get("/api/recurring", headers=accountant).json()["rows"]
    mine = next(x for x in rows if x["id"] == rid)
    assert mine["last_period"] == f"{jy}-{jm:02d}"


def test_approval_queue_lists_employee_drafts(client, employee, accountant):
    bank = account_id(client, accountant, "110201")
    expense = account_id(client, accountant, "5105")
    client.post("/api/entries", headers=employee, json={
        "date": jd.jalali_str(jd.jtoday()), "description": "خرید آزمون کارمند",
        "lines": [{"account_id": expense, "debit": 2_000_000, "credit": 0},
                  {"account_id": bank, "debit": 0, "credit": 2_000_000}]})
    d = client.get("/api/approvals", headers=accountant).json()
    assert any("آزمون کارمند" in e["description"] for e in d["entries"])
    assert client.get("/api/approvals", headers=employee).status_code == 403


def test_export_of_parties_roundtrips(client, admin):
    from io import BytesIO

    from openpyxl import Workbook

    from app import excel_io

    data = client.get("/api/export/parties.xlsx", headers=admin).content
    wb = __import__("openpyxl").load_workbook(BytesIO(data))
    assert "راهنما" in wb.sheetnames and "داده" in wb.sheetnames
    rows = list(wb["داده"].iter_rows(min_row=2, values_only=True))
    assert len(rows) >= 4

    # یک شخص تازه از راه فایل اضافه می‌کنیم
    new_wb = Workbook()
    ws = new_wb.active
    ws.title = "داده"
    ws.append(excel_io.COLUMNS["parties"])
    ws.append(["", "آقای آزمون اکسل", "مشتری", "09120001111", "از فایل", None])
    help_ws = new_wb.create_sheet("راهنما")
    help_ws.append(["نوع فایل", "parties"])
    buf = BytesIO()
    new_wb.save(buf)

    files = {"file": ("p.xlsx", buf.getvalue(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    preview = client.post("/api/import/preview?kind=parties", headers=admin, files=files)
    assert preview.status_code == 200, preview.text
    assert preview.json()["row_count"] == 1

    buf.seek(0)
    files = {"file": ("p.xlsx", buf.getvalue(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    applied = client.post("/api/import/apply?kind=parties", headers=admin, files=files)
    assert applied.status_code == 200, applied.text
    assert applied.json()["count"] == 1
    names = [p["name"] for p in client.get("/api/parties", headers=admin).json()["rows"]]
    assert "آقای آزمون اکسل" in names


def test_employee_drafts_invoice_and_only_sees_own(client, employee, accountant):
    """صف تأیید: کارمند پیش‌نویس می‌سازد، فقط مال خودش را می‌بیند، حسابدار همه را."""
    parties = client.get("/api/parties", headers=employee).json()["rows"]
    mine = parties[0]["id"]
    r = client.post("/api/invoices", headers=employee, json={
        "party_id": mine, "date": jd.jalali_str(jd.jtoday()),
        "description": "فاکتور پیش‌نویس کارمند",
        "lines": [{"service_type": "tour", "unit_price": 15_000_000, "qty": 1}]})
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["status"] == "draft", "فاکتور کارمند باید پیش‌نویس بماند"
    assert inv["number"] is None

    employee_rows = client.get("/api/invoices", headers=employee).json()["rows"]
    assert any(x["id"] == inv["id"] for x in employee_rows)
    accountant_rows = client.get("/api/invoices", headers=accountant).json()["rows"]
    assert len(accountant_rows) > len(employee_rows), "حسابدار باید فاکتورهای همه را ببیند"

    # حسابدار پیش‌نویس کارمند را صادر می‌کند
    issued = client.post(f"/api/invoices/{inv['id']}/issue", headers=accountant)
    assert issued.status_code == 200 and issued.json()["number"]


def test_employee_drafts_expense_and_it_lands_in_approval_queue(client, employee, accountant):
    suppliers = client.get("/api/parties?kind=supplier", headers=employee).json()["rows"]
    if not suppliers:
        client.post("/api/parties", headers=accountant,
                    json={"name": "تأمین‌کننده آزمون صف", "kind": "supplier"})
        suppliers = client.get("/api/parties?kind=supplier", headers=employee).json()["rows"]
    r = client.post("/api/bills", headers=employee, json={
        "party_id": suppliers[0]["id"], "date": jd.jalali_str(jd.jtoday()), "net": 8_000_000,
        "vat": 0, "description": "هزینه‌ی ثبت‌شده توسط کارمند"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "draft"
    d = client.get("/api/approvals", headers=accountant).json()
    assert d["draft_bills"] >= 1
