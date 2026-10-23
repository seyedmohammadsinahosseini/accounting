# -*- coding: utf-8 -*-
"""تست‌های الزامی مأموریت «اصلاح اصولی لغو جزئی» — ۱۶ سناریو.

هر تست جریان‌یاری را با اعداد مشخص، سندهای بدهکار/بستانکار و مانده‌های قبل و بعد می‌سنجد.
قرارداد کلیدی جدید: ثبت لغو از پرداخت واقعی جدا است (پیش‌فرض deferred)؛ پول فقط در
تسویه‌ی صریح جابه‌جا می‌شود؛ اعتبار مشتری روی حساب ۲۱۰۳-تفصیلی است؛ لغو ابطال رسمی دارد.
"""
from __future__ import annotations

import json

from sqlalchemy import select


def _mk_party(client, h, name, kind="customer"):
    r = client.post("/api/parties", headers=h, json={"name": name, "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()


def _mk_invoice(client, h, party_id, price, service_type="ticket", date="1405/03/03", **extra):
    line = {"service_type": service_type, "qty": 1, "unit_price": price, "discount": 0,
            "taxable": False, **extra.pop("line", {})}
    r = client.post("/api/invoices", headers=h, json={
        "party_id": party_id, "date": date, "description": "تست گردشکار لغو",
        "lines": [line], "issue": True, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _receive(client, h, party_id, amount, bank_id, date="1405/03/04", **extra):
    r = client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": party_id, "account_id": bank_id, "amount": amount,
        "date": date, "issue": True, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _bank_id(client, h):
    accs = client.get("/api/accounts", headers=h).json()["rows"]
    return next(a for a in accs if a["code"] == "110201")["id"]


def _entry_lines(client, h, entry_id):
    return client.get(f"/api/entries/{entry_id}", headers=h).json()["lines"]


def _dr_cr(lines):
    dr = sum(l["debit"] for l in lines)
    cr = sum(l["credit"] for l in lines)
    assert dr == cr, f"سند نامتوازن: D{dr} ≠ C{cr}"
    return lines


def _acc_of(lines, code_prefix):
    return sum(l["debit"] - l["credit"] for l in lines if l["account_label"].startswith(code_prefix))


def _treasury_total(client, h):
    return client.get("/api/reports/treasury", headers=h).json()["total"]


def _invoice_row(client, h, inv_id):
    rows = client.get("/api/invoices?status=all", headers=h).json()["rows"]
    return next(r for r in rows if r["id"] == inv_id)


# ---------- ۱) لغو بلیت در مدل gross + تفکیک لغو از پرداخت ----------
def test_gross_ticket_deferred_no_money_moves(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری gross بلیت")
    inv = _mk_invoice(client, h, cust["id"], 60_000_000, "ticket", line={"flight_type": 1})
    bank = _bank_id(client, h)
    _receive(client, h, cust["id"], 60_000_000, bank)
    cash0 = _treasury_total(client, h)

    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 25_000_000, "reason": "لغو یک مسافر از بلیت gross",
        "date": "1405/03/10", "penalty_customer": 5_000_000})
    assert r.status_code == 200, r.text
    pc = r.json()
    assert pc["status"] == "confirmed" and pc["refund_mode"] == "deferred"
    assert pc["refund_due"] == 20_000_000 and pc["refund_paid"] == 0
    assert pc["refund_status"] == "unpaid"
    # خزانه/بانک هیچ تغییری نکرده (ثبت لغو ≠ پرداخت)
    assert _treasury_total(client, h) == cash0
    # سند برگشت: 4102 بلیت بدهکار ۲۵ ، مشتری بستانکار ۲۵ — و سند جریمه: مشتری بدهکار ۵ / ۴۱۰۷ بستانکار ۵
    L = _dr_cr(_entry_lines(client, h, pc["entry_id"]))
    assert _acc_of(L, "4102") == 25_000_000
    Lp = _dr_cr(_entry_lines(client, h, pc["penalty_entry_id"]))
    assert _acc_of(Lp, "4107") == -5_000_000
    # فاکتور دست‌نخورده، paid ولی ۳۵ شده (آزادسازی تخصیص)
    row = _invoice_row(client, h, inv["id"])
    assert row["total"] == 60_000_000 and row["paid"] == 35_000_000


# ---------- ۲) لغو بلیت در مدل commission ----------
def test_commission_ticket_reverses_commission_and_clearing(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری commission")
    sup = _mk_party(client, h, "ایرلاین commission", "supplier")
    inv = _mk_invoice(client, h, cust["id"], 60_000_000, "ticket",
                      line={"purchase_cost": 50_000_000, "supplier_party_id": sup["id"],
                            "revenue_model": "commission"})
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 30_000_000, "reason": "لغو نیمه‌ی بلیت امانی"})
    assert r.status_code == 200, r.text
    L = _dr_cr(_entry_lines(client, h, r.json()["entry_id"]))
    # ۳۰ لغو از فروش ۶۰ با خرید ۵۰: سهم امانی = ۳۰×۵۰/۶۰ = ۲۵ → ۲۱۰۸ ؛ کمیسیون = ۵ → ۴۱۰۵
    assert _acc_of(L, "2108") == 25_000_000, "سهم امانی ایرلاین باید برگردد"
    assert _acc_of(L, "4105") == 5_000_000, "درآمد کمیسیون باید برگردد"
    assert _acc_of(L, "4102") == 0, "در مدل commission برگشت به 4102 نمی‌رود"
    assert _acc_of(L, "1103") == -30_000_000


# ---------- ۳) لغو یک خدمت از فاکتور چندخدمته ----------
def test_single_line_cancel_in_multi_service_invoice(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری چندخدمته")
    r = client.post("/api/invoices", headers=h, json={
        "party_id": cust["id"], "date": "1405/03/03", "description": "تور+بلیت",
        "issue": True, "lines": [
            {"service_type": "tour", "qty": 1, "unit_price": 60_000_000, "discount": 0, "taxable": False},
            {"service_type": "ticket", "qty": 1, "unit_price": 40_000_000, "discount": 0,
             "taxable": False, "flight_type": 2}]})
    assert r.status_code == 200, r.text
    inv = r.json()
    # شناسه‌ی ردیف بلیت را از دیتابیس می‌گیریم (endpoint تک‌فاکتور وجود ندارد)
    from app import db as _db
    from app.models import InvoiceLine as _IL
    with _db.session_scope() as sess:
        tl_row = sess.execute(select(_IL).where(
            _IL.invoice_id == inv["id"], _IL.service_type == "ticket")).scalar_one()
        tl = {"id": tl_row.id, "service_type": tl_row.service_type}
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 40_000_000, "reason": "لغو فقط بلیت از فاکتور ترکیبی",
        "date": "1405/03/09", "line_id": tl["id"]})
    assert r.status_code == 200, r.text
    pc = r.json()
    assert pc["line_id"] == tl["id"]
    L = _dr_cr(_entry_lines(client, h, pc["entry_id"]))
    assert _acc_of(L, "4102") == 40_000_000
    assert _acc_of(L, "4101") == 0, "تور نباید برگردد"


# ---------- ۴) لغو جزئی رزرو هتل ----------
def test_hotel_cancel_maps_to_hotel_revenue(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری هتل")
    inv = _mk_invoice(client, h, cust["id"], 30_000_000, "hotel")
    bank = _bank_id(client, h)
    _receive(client, h, cust["id"], 30_000_000, bank)
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 12_000_000, "reason": "لغو دو شب از رزرو پنج‌شبه", "date": "1405/03/11"})
    assert r.status_code == 200, r.text
    L = _dr_cr(_entry_lines(client, h, r.json()["entry_id"]))
    assert _acc_of(L, "4110") == 12_000_000


# ---------- ۵) لغو چندمرحله‌ای در تاریخ‌های مختلف ----------
def test_multi_stage_cancels_different_dates(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری چندمرحله‌ای")
    inv = _mk_invoice(client, h, cust["id"], 100_000_000, "tour")
    bank = _bank_id(client, h)
    _receive(client, h, cust["id"], 100_000_000, bank)
    for i, (amt, dt) in enumerate(((25_000_000, "1405/03/05"), (25_000_000, "1405/03/15"),
                                   (10_000_000, "1405/03/25")), 1):
        r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
            "amount": amt, "reason": f"مرحله {i} — انصراف تدریجی مسافران", "date": dt})
        assert r.status_code == 200, (i, r.text)
    hist = client.get(f"/api/invoices/{inv['id']}/partial-cancels", headers=h).json()["rows"]
    assert len([x for x in hist if x["status"] == "confirmed"]) == 3
    # سقف تجمیعی: ۶۰ لغو شده — لغو ۴۵ دیگر باید رد شود
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 45_000_000, "reason": "لغوی بیش از سقف تجمیعی", "date": "1405/03/26"})
    assert r.status_code == 400


# ---------- ۶) پیش‌نویس، تأیید و رد ----------
def test_draft_confirm_and_reject_flow(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری پیش‌نویس")
    inv = _mk_invoice(client, h, cust["id"], 50_000_000, "tour")
    cash0 = _treasury_total(client, h)
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 10_000_000, "reason": "پیش‌نویس لغو برای بررسی مدیر",
        "date": "1405/03/07", "as_draft": True})
    assert r.status_code == 200, r.text
    pc = r.json()
    assert pc["status"] == "draft" and pc["entry_id"] is None
    assert _invoice_row(client, h, inv["id"])["paid"] == 0    # هنوز هیچ اثری ندارد
    assert _treasury_total(client, h) == cash0
    r = client.post(f"/api/partial-cancels/{pc['id']}/confirm", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed" and r.json()["entry_id"] is not None
    # رد کردن یک پیش‌نویس دیگر و ثبت دوباره با همان مشخصات باید ممکن شود
    r2 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 5_000_000, "reason": "لغوی که مدیر قبول نکرد",
        "date": "1405/03/08", "as_draft": True})
    pc2 = r2.json()["id"]
    assert client.post(f"/api/partial-cancels/{pc2}/reject", headers=h,
                       json={"reason": "عدم تأیید مدیریت"}).status_code == 200
    r3 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 5_000_000, "reason": "لغوی که مدیر قبول نکرد", "date": "1405/03/08"})
    assert r3.status_code == 200, "پس از رد، ثبت دوباره باید ممکن باشد"


# ---------- ۷) پرداخت فوری و پرداخت جزئی استرداد ----------
def test_immediate_and_partial_refund_payments(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری استرداد جزئی")
    inv = _mk_invoice(client, h, cust["id"], 60_000_000, "tour")
    bank = _bank_id(client, h)
    _receive(client, h, cust["id"], 60_000_000, bank)
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 25_000_000, "reason": "استرداد دو قسطی", "date": "1405/03/12",
        "penalty_customer": 5_000_000})
    pc = r.json()
    cash0 = _treasury_total(client, h)
    for amt in (12_000_000, 8_000_000):
        rp = client.post(f"/api/partial-cancels/{pc['id']}/refund-payment", headers=h,
                         json={"account_id": bank, "amount": amt, "date": "1405/03/13"})
        assert rp.status_code == 200, rp.text
        assert rp.json()["number"], "هر پرداخت باید شماره تسویه خودش را داشته باشد"
    assert _treasury_total(client, h) == cash0 - 20_000_000
    d = client.get(f"/api/partial-cancels/{pc['id']}", headers=h).json()
    assert d["refund_status"] == "paid" and len(d["payments"]) == 2
    # هر ریال بیشتر از مانده → رد
    rp = client.post(f"/api/partial-cancels/{pc['id']}/refund-payment", headers=h,
                     json={"account_id": bank, "amount": 1, "date": "1405/03/13"})
    assert rp.status_code in (400, 422)


# ---------- ۸) ایجاد و مصرف اعتبار مشتری ----------
def test_customer_credit_issue_and_spend(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری اعتباری")
    inv = _mk_invoice(client, h, cust["id"], 60_000_000, "tour")
    bank = _bank_id(client, h)
    _receive(client, h, cust["id"], 60_000_000, bank)
    cash0 = _treasury_total(client, h)
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 25_000_000, "reason": "اعتبار برای خرید نوروزی", "date": "1405/03/12",
        "penalty_customer": 5_000_000, "mode": "credit"})
    pc = r.json()
    assert pc["refund_status"] == "credited" and pc["credit_remaining"] == 20_000_000
    assert _treasury_total(client, h) == cash0, "اعتبار نباید پول جابه‌جا کند"
    # خرید جدید و تسویه با اعتبار
    inv2 = _mk_invoice(client, h, cust["id"], 15_000_000, "ticket",
                       date="1405/03/20", line={"flight_type": 1})
    acc_rows = client.get("/api/accounts", headers=h).json()["rows"]
    credit_acc = next(a for a in acc_rows if a["id"] == pc["credit_account_id"])
    rs = client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": cust["id"], "account_id": credit_acc["id"],
        "amount": 15_000_000, "date": "1405/03/21", "issue": True,
        "target_invoice_id": inv2["id"]})
    assert rs.status_code == 200, rs.text
    assert _invoice_row(client, h, inv2["id"])["paid"] == 15_000_000
    cred = client.get("/api/reports/customer-credits", headers=h).json()["rows"]
    row = next(c for c in cred if c["party_id"] == cust["id"])
    assert row["issued"] == 20_000_000 and row["spent"] == 15_000_000 and row["remaining"] == 5_000_000
    # اعتبار به پول نقد تبدیل نمی‌شود
    rbad = client.post("/api/settlements", headers=h, json={
        "direction": "out", "party_id": cust["id"], "account_id": credit_acc["id"],
        "amount": 5_000_000, "date": "1405/03/21", "issue": True})
    assert rbad.status_code == 400
    # و اعتبار مشتری دیگر قابل استفاده برای این مشتری نیست
    other = _mk_party(client, h, "مشتری دیگر")
    rbad2 = client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": other["id"], "account_id": credit_acc["id"],
        "amount": 1_000_000, "date": "1405/03/21", "issue": True})
    assert rbad2.status_code == 400


# ---------- ۹) استرداد جزئی و کامل تأمین‌کننده ----------
def test_supplier_partial_and_full_receipts(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری تأمین")
    sup = _mk_party(client, h, "هتل‌آریا", "supplier")
    inv = _mk_invoice(client, h, cust["id"], 60_000_000, "hotel",
                      line={"supplier_party_id": sup["id"]})
    bank = _bank_id(client, h)
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 25_000_000, "reason": "وصول تدریجی از هتل", "date": "1405/03/12",
        "supplier_refund": 18_000_000, "penalty_supplier": 2_000_000,
        "supplier_party_id": sup["id"]})
    pc = r.json()
    assert pc["supplier_claim"] == 20_000_000 and pc["supplier_status"] == "pending"
    rp = client.post(f"/api/partial-cancels/{pc['id']}/receive-supplier", headers=h,
                     json={"account_id": bank, "amount": 12_000_000, "date": "1405/03/20"})
    assert rp.status_code == 200, rp.text
    d = client.get(f"/api/partial-cancels/{pc['id']}", headers=h).json()
    assert d["supplier_status"] == "partial" and d["supplier_received"] == 12_000_000
    rp = client.post(f"/api/partial-cancels/{pc['id']}/receive-supplier", headers=h,
                     json={"account_id": bank, "amount": 8_000_000, "date": "1405/03/25"})
    assert rp.status_code == 200
    d = client.get(f"/api/partial-cancels/{pc['id']}", headers=h).json()
    assert d["supplier_status"] == "settled" and d["supplier_remaining"] == 0
    # چهارمین دریافت (تکراری/مازاد) → رد
    rp = client.post(f"/api/partial-cancels/{pc['id']}/receive-supplier", headers=h,
                     json={"account_id": bank, "amount": 1_000_000, "date": "1405/03/26"})
    assert rp.status_code in (400, 422)


# ---------- ۱۰) کنترل سقف‌ها ----------
def test_amount_and_cost_ceilings(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری سقف")
    sup = _mk_party(client, h, "تامین سقف", "supplier")
    inv = _mk_invoice(client, h, cust["id"], 60_000_000, "ticket",
                      line={"flight_type": 1, "purchase_cost": 45_000_000,
                            "supplier_party_id": sup["id"], "revenue_model": "gross"})
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 61_000_000, "reason": "لغوی بیش از کل فاکتور", "date": "1405/03/12"})
    assert r.status_code == 400
    # برگشت تامین بالاتر از هزینه‌ی ثبت‌شده‌ی بخش (بدون مجوز) → رد
    # هزینه‌ی سهم لغو ۲۰ از ۶۰ = ۱۵
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 20_000_000, "reason": "برگشت تامین بیش از هزینه", "date": "1405/03/12",
        "supplier_refund": 20_000_000, "supplier_party_id": sup["id"]})
    assert r.status_code == 400
    # با مجوز ویژه و دلیل مستند → قبول
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 20_000_000, "reason": "توافق خاص مدیران: برگشت کامل وجه تشریفات",
        "date": "1405/03/12", "supplier_refund": 20_000_000,
        "supplier_party_id": sup["id"], "allow_over_cost": True})
    assert r.status_code == 200, r.text
    hist = client.get(f"/api/invoices/{inv['id']}/partial-cancels", headers=h).json()["rows"]
    assert hist[-1]["nonrefundable_cost"] == 0  # بیش از هزینه برگشت خورد — چیزی سوخت نشد


# ---------- ۱۱) تکرار درخواست ----------
def test_duplicate_request_blocked(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری تکرار")
    inv = _mk_invoice(client, h, cust["id"], 30_000_000, "tour")
    body = {"amount": 5_000_000, "reason": "بررسی عدم تکرار ثبت", "date": "1405/03/14"}
    r1 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json=body)
    r2 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json=body)
    assert r1.status_code == 200 and r2.status_code == 400
    assert "قبلاً" in r2.json()["detail"]


# ---------- ۱۲) ابطال رسمی لغو و بازگشت مانده‌ها ----------
def test_official_void_restores_balances(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری ابطال")
    inv = _mk_invoice(client, h, cust["id"], 60_000_000, "tour")
    bank = _bank_id(client, h)
    _receive(client, h, cust["id"], 60_000_000, bank)
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 25_000_000, "reason": "لغوی که بعداً اشتباه بود", "date": "1405/03/12",
        "penalty_customer": 5_000_000})
    pc = r.json()
    rp = client.post(f"/api/partial-cancels/{pc['id']}/refund-payment", headers=h,
                     json={"account_id": bank, "amount": 20_000_000, "date": "1405/03/13"})
    settle_id = rp.json()["settlement_id"]
    # ابطال لغو درحالی‌که پرداخت زنده است → رد با نام شماره تسویه
    r = client.post(f"/api/partial-cancels/{pc['id']}/cancel", headers=h,
                    json={"reason": "لغو اشتباه ثبت شده بود"})
    assert r.status_code == 400
    # ابطال پرداخت سپس ابطال لغو
    cs = client.post(f"/api/settlements/{settle_id}/cancel", headers=h,
                     json={"reason": "بازگشت پرداخت استرداد بابت ابطال لغو"})
    assert cs.status_code == 200, cs.text
    r = client.post(f"/api/partial-cancels/{pc['id']}/cancel", headers=h,
                    json={"reason": "لغو اشتباه ثبت شده بود"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "cancelled"
    # مانده فاکتور برگشته (تخصیص‌های آزادشده بازسازی شدند)
    row = _invoice_row(client, h, inv["id"])
    assert row["paid"] == 60_000_000, f"paid باید به ۶۰ برگردد: {row}"
    # و ثبت لغوی جدید با همان مبلغ/تاریخ ممکن است (رکورد ابطال‌شده جلودار نیست)
    r2 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 25_000_000, "reason": "لغوی که بعداً اشتباه بود", "date": "1405/03/12",
        "penalty_customer": 5_000_000})
    assert r2.status_code == 200, "بعد از ابطال، ثبت دوباره باید آزاد باشد"


# ---------- ۱۳) رول‌بک اتمیک هنگام خطای میانی ----------
def test_atomic_rollback_on_mid_failure(client, accountant, monkeypatch):
    h = accountant
    cust = _mk_party(client, h, "مشتری رول‌بک")
    inv = _mk_invoice(client, h, cust["id"], 60_000_000, "tour")
    bank = _bank_id(client, h)
    _receive(client, h, cust["id"], 60_000_000, bank)
    from app import services as svc, db, sales
    calls = {"n": 0}
    real = svc.create_draft

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:      # سند دوم (جریمه) می‌شکند — سند اول و آزادسازی انجام شده
            raise RuntimeError("خطای شبیه‌سازی‌شده میانه‌ی عملیات")
        return real(*a, **k)

    monkeypatch.setattr(svc, "create_draft", flaky)
    import pytest
    with pytest.raises(RuntimeError):
        with db.session_scope() as sess:
            from app.models import User
            user = sess.execute(select(User).where(User.username == "accountant")).scalar_one()
            sales.create_partial_cancel(sess, user, inv["id"], 25_000_000,
                                        "رول‌بک اتمیک باید کامل باشد", penalty_customer=5_000_000)
    # پس از رول‌بک: نه رکورد لغو، نه تغییر paid، تراز سالم
    with db.session_scope() as sess:
        from app.models import Invoice, InvoicePartialCancel
        after = sess.get(Invoice, inv["id"])
        assert after.paid_rial == 60_000_000
        n = sess.execute(select(InvoicePartialCancel).where(
            InvoicePartialCancel.invoice_id == inv["id"])).scalars().all()
        assert len(n) == 0
    t = client.get("/api/reports/trial-balance", headers=h).json()
    assert t["balanced"] is True


# ---------- ۱۴) توازن همه‌ی اسناد سناریو ----------
def test_all_cancel_entries_balanced(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری توازن")
    sup = _mk_party(client, h, "تامین توازن", "supplier")
    inv = _mk_invoice(client, h, cust["id"], 66_000_000, "ticket",
                      line={"flight_type": 2, "taxable": True})
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 22_000_000, "reason": "بررسی توازن با مالیات فروش", "date": "1405/03/15",
        "penalty_customer": 2_000_000, "agency_fee": 1_000_000,
        "supplier_refund": 18_000_000, "penalty_supplier": 3_000_000,
        "supplier_party_id": sup["id"]})
    assert r.status_code == 200, r.text
    pc = r.json()
    for eid in (pc["entry_id"], pc["penalty_entry_id"], pc["agency_fee_entry_id"],
                pc["supplier_refund_entry_id"], pc["supplier_penalty_entry_id"]):
        _dr_cr(_entry_lines(client, h, eid))
    # مالیات فروش سهم لغو باید برگردد: ۲۲ از ۷۲٫۶ کل → سهم vat ≈ ۲ میلیون
    L = _entry_lines(client, h, pc["entry_id"])
    vat_dr = sum(l["debit"] for l in L if l["account_label"].startswith("2104"))
    assert vat_dr == 22_000_000 * 6_600_000 // 72_600_000


# ---------- ۱۵) فاکتور با صورتحساب الکترونیکی ----------
def test_moadian_sent_requires_ack_and_pending(client, accountant, admin):
    h = accountant
    cust = _mk_party(client, h, "مشتری مودیان")
    inv = _mk_invoice(client, h, cust["id"], 40_000_000, "tour")
    import app.dates as jdmod
    from app import db
    from app.models import MoadianInvoice
    with db.session_scope() as sess:
        sess.add(MoadianInvoice(invoice_id=inv["id"], doc_date=jdmod.jtoday(),
                                status="sent", subject=1))
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 10_000_000, "reason": "لغوی فاکتور مودیانی بدون تأکید", "date": "1405/03/16"})
    assert r.status_code == 400 and "مودیان" in r.json()["detail"]
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 10_000_000, "reason": "ابطالیه مالیاتی صادر و ثبت شد", "date": "1405/03/16",
        "allow_moadian": True})
    assert r.status_code == 200, r.text
    pc = r.json()
    assert pc["status"] == "pending" and pc["entry_id"] is None
    # تأیید با حسابدار معمولی اگر مجوز reverse نداشته باشد باید رد شود؛ با admin موفق
    rc = client.post(f"/api/partial-cancels/{pc['id']}/confirm", headers=admin)
    assert rc.status_code == 200, rc.text
    assert rc.json()["status"] == "confirmed" and rc.json()["entry_id"] is not None


# ---------- ۱۶) سلامت جریان‌های عادی (رگرسیون سبک داخل همین فایل) ----------
def test_normal_sales_and_settlements_untouched(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری عادی")
    sup = _mk_party(client, h, "تامین عادی", "supplier")
    inv = _mk_invoice(client, h, cust["id"], 50_000_000, "tour")
    bank = _bank_id(client, h)
    _receive(client, h, cust["id"], 30_000_000, bank)
    row = _invoice_row(client, h, inv["id"])
    assert row["paid"] == 30_000_000 and row["open"] == 20_000_000
    rp = client.post("/api/settlements", headers=h, json={
        "direction": "out", "party_id": sup["id"], "account_id": bank,
        "amount": 10_000_000, "date": "1405/03/17", "issue": True})
    assert rp.status_code == 200
    t = client.get("/api/reports/trial-balance", headers=h).json()
    assert t["balanced"] is True


# ---------- ۱۷) گزارش سود فاکتور ----------
def test_invoice_profit_summary_numbers(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری سود فاکتور")
    sup = _mk_party(client, h, "تامین سود فاکتور", "supplier")
    inv = _mk_invoice(client, h, cust["id"], 60_000_000, "ticket",
                      line={"flight_type": 1, "purchase_cost": 45_000_000,
                            "supplier_party_id": sup["id"], "revenue_model": "gross"})
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 25_000_000, "reason": "بررسی سود این فاکتور در گزارش", "date": "1405/03/20",
        "penalty_customer": 5_000_000, "supplier_refund": 18_000_000,
        "supplier_party_id": sup["id"]})
    assert r.status_code == 200, r.text
    r = client.get(f"/api/invoices/{inv['id']}/profit-summary", headers=h)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["documented"] is True
    assert p["revenue_base"] == 60_000_000 and p["documented_cost"] == 45_000_000
    assert p["gross_profit"] == 15_000_000
    # اثر لغو = جریمه مشتری + طلب تأمین − مبلغ لغو = 5 + 18 − 25 = −2
    assert p["cancels"][0]["profit_impact"] == -2_000_000
    assert p["cancels"][0]["nonrefundable_cost"] == 750_000          # ۱۸٫۷۵م − ۱۸م هزینه سوخته
    assert p["cancel_effect_total"] == -2_000_000
    assert p["final_profit"] == 13_000_000                           # ۱۵م − ۲م


def test_partial_cancels_excel_export(client, accountant):
    """خروجی اکسل گزارش لغوهای جزئی: فایل معتبر .xlsx با همان فیلترهای گزارش."""
    h = accountant
    cust = _mk_party(client, h, "مشتری اکسل کنسلی")
    sup = _mk_party(client, h, "تامین اکسل کنسلی", "supplier")
    inv = _mk_invoice(client, h, cust["id"], 30_000_000, "ticket",
                      line={"flight_type": 1, "purchase_cost": 20_000_000,
                            "supplier_party_id": sup["id"]})
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 10_000_000, "reason": "بررسی خروجی اکسل", "date": "1405/03/21",
        "supplier_refund": 6_000_000, "supplier_party_id": sup["id"]})
    assert r.status_code == 200, r.text
    r = client.get("/api/export/partial-cancels.xlsx?frm=1405/03/01&to=1405/03/30", headers=h)
    assert r.status_code == 200, r.text
    assert r.content[:2] == b"PK"                      # امضای فایل xlsx (zip)
    assert ".xlsx" in r.headers.get("Content-Disposition", "") and "attachment" in r.headers.get("Content-Disposition", "")
    # بدون داده هم باید ۲۰۰ با کتاب‌کار معتبر بدهد
    r = client.get("/api/export/partial-cancels.xlsx?frm=1405/01/01&to=1405/01/02", headers=h)
    assert r.status_code == 200 and r.content[:2] == b"PK"


def _entry_by_ref(sess, ref):
    from sqlalchemy import select
    from app.models import JournalEntry, JournalLine, Account
    e = sess.execute(select(JournalEntry).where(JournalEntry.source_ref == ref)).scalar_one_or_none()
    assert e is not None, f"سندی با source_ref={ref} ثبت نشده"
    lines = sess.execute(select(JournalLine).where(JournalLine.entry_id == e.id)).scalars().all()
    return [(sess.get(Account, l.account_id).code, l.debit_rial, l.credit_rial) for l in lines]


def test_counter_party_creation_and_gateway(client, accountant):
    h = accountant
    c = _mk_party(client, h, "کانتر رضا محمدی", "employee")
    assert c["code"].startswith("ک-")
    prow = next(p for p in client.get("/api/parties", headers=h).json()["rows"] if p["id"] == c["id"])
    assert prow["kind"] == "employee"
    accs = client.get("/api/accounts", headers=h).json()["rows"]
    acc = next(a for a in accs if a["id"] == prow["account_id"])
    assert acc["code"].startswith("1108-"), acc["code"]          # تفصیلی زیر مطالبات کارکنان


def test_counter_penalty_moves_burn_to_counter_receivable(client, accountant):
    """جریمه بر عهده کانتر: به‌جای سوخت آژانس (۵۱۰۹)، مطالبه از تفصیلی کانتر ثبت می‌شود."""
    from app.db import session_scope
    h = accountant
    cust = _mk_party(client, h, "مشتری جریمه کانتر")
    sup = _mk_party(client, h, "تامین جریمه کانتر", "supplier")
    ctr = _mk_party(client, h, "کانتر علی قنبری", "employee")
    inv = _mk_invoice(client, h, cust["id"], 30_000_000, "ticket",
                      line={"flight_type": 1, "purchase_cost": 20_000_000,
                            "supplier_party_id": sup["id"], "revenue_model": "gross"})
    # لغو ۱۵م از ۳۰م → سهم هزینه ۱۰م؛ برگشت تامین ۸م → سوخت = ۲م — کانتر ۱٬۵م به عهده می‌گیرد
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 15_000_000, "reason": "خطای کانتر در صدور — جریمه به عهده کانتر", "date": "1405/03/22",
        "supplier_refund": 8_000_000, "counter_penalty": 1_500_000,
        "counter_party_id": ctr["id"], "supplier_party_id": sup["id"]})
    assert r.status_code == 200, r.text
    pc = r.json()
    assert pc["nonrefundable_cost"] == 2_000_000                 # سوخت کل این بخش
    assert pc["counter_penalty"] == 1_500_000
    assert pc["counter_party"] == "کانتر علی قنبری"
    # اثر لغو بر سود: برگشت تامین − لغو + جریمه کانتر = ۸ − ۱۵ + ۱٫۵ = −۵٫۵م
    assert pc["profit_impact"] == -5_500_000
    p = client.get(f"/api/invoices/{inv['id']}/profit-summary", headers=h).json()
    assert p["cancels"][0]["counter_penalty"] == 1_500_000
    assert p["final_profit"] == 10_000_000 + pc["profit_impact"] # ۱۰م سود ناخالص − ۵٫۵م = ۴٫۵م
    # سندها: ردیف‌های سوخت → ۵۱۰۹ بدهکار/۵۱۰۱ بستانکار، سند کانتر → تفصیلی کانتر بدهکار/۵۱۰۹ بستانکار
    with session_scope() as sess:
        burn_lines = _entry_by_ref(sess, f"cancel-burn-{pc['id']}")
        assert ("5109", 2_000_000, 0) in burn_lines and ("5101", 0, 2_000_000) in burn_lines
        from app.models import Party, Account
        ctr_party = sess.get(Party, ctr["id"])
        ctr_acc_code = sess.get(Account, ctr_party.account_id).code
        assert ctr_acc_code.startswith("1108-"), ctr_acc_code
        ctr_lines = _entry_by_ref(sess, f"counter-penalty-{pc['id']}")
        assert (ctr_acc_code, 1_500_000, 0) in ctr_lines and ("5109", 0, 1_500_000) in ctr_lines
    # جریمه کانتر بیش از سوخت → رد ۴۰۰ با همان پیام سقف
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 15_000_000, "reason": "تلاش دوم با جریمه کانتر بیشتر از سوخت", "date": "1405/03/23",
        "supplier_refund": 8_000_000, "counter_penalty": 3_000_000,
        "counter_party_id": ctr["id"]})
    assert r.status_code == 400 and "خسارت برنگشتی" in r.json()["detail"]
    # بدون انتخاب کانتر → رد
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 15_000_000, "reason": "جریمه کانتر بدون انتخاب کانتر", "date": "1405/03/24",
        "supplier_refund": 8_000_000, "counter_penalty": 1_500_000})
    assert r.status_code == 400


def test_burn_account_5109_independent_of_cogs(client, accountant):
    """سوخت بدون کانتر → کل سوخت در ۵۱۰۹ می‌نشیند و ۵۱۰۱ فقط به اندازه‌ی برگشتی کم می‌شود."""
    from app.db import session_scope
    h = accountant
    cust = _mk_party(client, h, "مشتری سوخت")
    sup = _mk_party(client, h, "تامین سوخت", "supplier")
    inv = _mk_invoice(client, h, cust["id"], 20_000_000, "ticket",
                      line={"flight_type": 1, "purchase_cost": 12_000_000,
                            "supplier_party_id": sup["id"], "revenue_model": "gross"})
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 20_000_000, "reason": "لغو کامل بلیت بدون مسئول کانتر", "date": "1405/03/25",
        "supplier_refund": 10_000_000, "supplier_party_id": sup["id"]})
    assert r.status_code == 200, r.text
    pc = r.json()
    assert pc["nonrefundable_cost"] == 2_000_000                 # ۱۲م − ۱۰م
    with session_scope() as sess:
        burn_lines = _entry_by_ref(sess, f"cancel-burn-{pc['id']}")
        assert ("5109", 2_000_000, 0) in burn_lines and ("5101", 0, 2_000_000) in burn_lines
        # سند برگشت تامین هم باید ۱۰م را از ۵۱۰۱ خارج کند (بدون مالیات برگشتی)
        sup_lines = _entry_by_ref(sess, f"supplier-refund-{pc['id']}")
        assert ("5101", 0, 10_000_000) in sup_lines
    # گزارش سود: اثر لغو = برگشت تامین − مبلغ لغو = ۱۰ − ۲۰ = −۱۰م؛ سود ناخالص ۸م → نهایی −۲م
    p = client.get(f"/api/invoices/{inv['id']}/profit-summary", headers=h).json()
    assert p["gross_profit"] == 8_000_000 and pc["profit_impact"] == -10_000_000
    assert p["final_profit"] == -2_000_000


def test_counter_penalty_void_reverses_both_entries(client, accountant):
    """ابطال لغوی با جریمه کانتر باید هر دو سند (سوخت + مطالبه کانتر) را معکوس کند."""
    from app.db import session_scope
    from sqlalchemy import select
    from app.models import JournalEntry
    h = accountant
    cust = _mk_party(client, h, "مشتری ابطال کانتر")
    sup = _mk_party(client, h, "تامین ابطال کانتر", "supplier")
    ctr = _mk_party(client, h, "کانتر ابطال", "employee")
    inv = _mk_invoice(client, h, cust["id"], 20_000_000, "ticket",
                      line={"flight_type": 1, "purchase_cost": 12_000_000,
                            "supplier_party_id": sup["id"], "revenue_model": "gross"})
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 20_000_000, "reason": "خطای کانتر — پس بررسی ابطال می‌شود", "date": "1405/03/26",
        "supplier_refund": 10_000_000, "counter_penalty": 2_000_000,
        "counter_party_id": ctr["id"], "supplier_party_id": sup["id"]})
    assert r.status_code == 200, r.text
    pc_id = r.json()["id"]
    r = client.post(f"/api/partial-cancels/{pc_id}/cancel", headers=h, json={"reason": "ابطال پس از بررسی مدیر"})
    assert r.status_code == 200, r.text
    # هر دو سند جدید باید معکوس شده باشند
    with session_scope() as sess:
        for ref in (f"cancel-burn-{pc_id}", f"counter-penalty-{pc_id}"):
            e = sess.execute(select(JournalEntry).where(JournalEntry.source_ref == ref)).scalar_one()
            assert e.reversed_by_id is not None, f"سند {ref} معکوس نشده"
    # گزارش سود: لغو ابطال‌شده دیگر اثری ندارد (فقط confirmed حساب می‌شود)
    p = client.get(f"/api/invoices/{inv['id']}/profit-summary", headers=h).json()
    assert p["cancels"] == [] and p["final_profit"] == p["gross_profit"]


# ---------- اعتبار مشتری در لیست اشخاص و سقف مصرف ----------


def test_parties_expose_credit_balance(client, accountant):
    h = accountant
    cust = _mk_party(client, h, "مشتری نمایش اعتبار")
    inv = _mk_invoice(client, h, cust["id"], 30_000_000, "tour")
    bank = _bank_id(client, h)
    _receive(client, h, cust["id"], 30_000_000, bank)
    pc = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 12_000_000, "reason": "اعتبار قابل نمایش", "date": "1405/03/12",
        "mode": "credit"}).json()
    rows = client.get("/api/parties", headers=h).json()["rows"]
    row = next(r for r in rows if r["id"] == cust["id"])
    assert row["credit_account_id"] == pc["credit_account_id"]
    assert row["credit_balance"] == 12_000_000
    # دریافت بیش از مانده اعتبار از درگاه تسویه عادی هم رد شود
    rbad = client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": cust["id"], "account_id": pc["credit_account_id"],
        "amount": 13_000_000, "date": "1405/03/21", "issue": True})
    assert rbad.status_code == 400 and "مانده اعتبار" in rbad.json()["detail"]


def test_counter_penalty_error_explains_zero_cost(client, accountant):
    """اگر برای بخش لغوی هزینه‌ی خرید ثبت نشده باشد، خطا باید محاسبه را خودش بگوید."""
    h = accountant
    cust = _mk_party(client, h, "مشتری سوخت صفر")
    ctr = _mk_party(client, h, "کانتر یزدان", "employee")
    inv = _mk_invoice(client, h, cust["id"], 5_000_000, "tour")   # بدون قیمت خرید
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 5_000_000, "reason": "خطای کانتر — تأمین ۲۰۰۰ برگرداند", "date": "1405/07/29",
        "supplier_refund": 2_000_000, "counter_penalty": 3_000_000, "counter_party_id": ctr["id"]})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "قیمت خرید ثبت‌شده‌ی این بخش (0" in detail and "تأمین برمی‌گرداند" in detail
    assert "قیمت خریدی در سیستم ثبت نشده" in detail
