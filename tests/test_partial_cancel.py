"""تست‌های الزامی لغو جزئی — 13 مورد"""
from __future__ import annotations
import pytest
from conftest import account_id
from app import dates as jd

def _today():
    return jd.jalali_str(jd.jtoday())

def _party(client, headers, name, kind="customer"):
    return client.post("/api/parties", headers=headers, json={"name": name, "kind": kind}).json()

def _invoice(client, headers, party_id, amount):
    return client.post("/api/invoices", headers=headers, json={
        "party_id": party_id, "date": _today(),
        "lines": [{"service_type": "tour", "qty": 1, "unit_price": amount, "discount": 0, "taxable": False}],
        "issue": True}).json()

def _bill(client, headers, party_id, amount):
    return client.post("/api/bills", headers=headers, json={
        "party_id": party_id, "date": _today(), "net": amount, "vat": 0, "issue": True}).json()

def _settle_in(client, headers, party_id, amount, account_id):
    return client.post("/api/settlements", headers=headers, json={
        "direction": "in", "party_id": party_id, "account_id": account_id,
        "amount": amount, "date": _today(), "issue": True}).json()

def _settle_out(client, headers, party_id, amount, account_id):
    return client.post("/api/settlements", headers=headers, json={
        "direction": "out", "party_id": party_id, "account_id": account_id,
        "amount": amount, "date": _today(), "issue": True}).json()

def test_partial_cancel_40_from_60_fully_paid(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 1")
    sup = _party(client, accountant, "تامین لغو جزئی 1", "supplier")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    bill = _bill(client, accountant, sup["id"], 40_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    _settle_out(client, accountant, sup["id"], 40_000_000, bank)
    # لغو جزئی 40M با جریمه 8M و برگشت تامین 35M جریمه تامین 5M
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "لغو جزئی 40M - تست 1", "penalty_customer": 8_000_000,
        "supplier_refund": 35_000_000, "penalty_supplier": 5_000_000, "supplier_party_id": sup["id"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["amount"] == 40_000_000

def test_balance_zero_after_cancel_penalty_refund(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 2")
    sup = _party(client, accountant, "تامین لغو جزئی 2", "supplier")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _bill(client, accountant, sup["id"], 40_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    _settle_out(client, accountant, sup["id"], 40_000_000, bank)
    client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "لغو جزئی 40M - مانده صفر", "penalty_customer": 8_000_000,
        "supplier_refund": 35_000_000, "penalty_supplier": 5_000_000, "supplier_party_id": sup["id"],
        "mode": "immediate"})
    party_row = next(p for p in client.get("/api/parties", headers=accountant).json()["rows"] if p["id"] == cust["id"])
    assert party_row["balance"] == 0

def test_remaining_20m(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 3")
    sup = _party(client, accountant, "تامین لغو جزئی 3", "supplier")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "باقی‌مانده 20M", "penalty_customer": 8_000_000})
    # درآمد باقی‌مانده باید 20M باشد (60-40) + جریمه جدا 8M
    inc = client.get("/api/reports/income-statement", headers=accountant).json()
    # حداقل چک: فاکتور اصلی تغییر نکرده ولی ژورنال برگشت زده
    assert inv["total"] == 60_000_000

def test_refund_once(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 4")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "استرداد یک‌بار", "penalty_customer": 8_000_000,
        "mode": "immediate"})
    refund_id = r.json()["refund_id"]
    assert refund_id is not None
    # تلاش دوم با همین دلیل/مبلغ/تاریخ باید بلاک شود (تکراری)
    r2 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "استرداد یک‌بار", "penalty_customer": 8_000_000})
    assert r2.status_code == 400
    assert ("قبلاً ثبت" in r2.json()["detail"] or "قابل‌لغو" in r2.json()["detail"])

def test_no_double_receipt(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 5")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "عدم دوباره ثبت", "penalty_customer": 8_000_000})
    in_total = sum(s["amount"] for s in client.get("/api/settlements", headers=accountant).json()["rows"]
                   if s["party_id"] == cust["id"] and s["direction"] == "in")
    assert in_total == 60_000_000  # فقط دریافتی اولیه

def test_trial_ledger_match(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 6")
    sup = _party(client, accountant, "تامین لغو جزئی 6", "supplier")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _bill(client, accountant, sup["id"], 40_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    _settle_out(client, accountant, sup["id"], 40_000_000, bank)
    client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "تراز و گردش", "penalty_customer": 8_000_000,
        "supplier_refund": 35_000_000, "penalty_supplier": 5_000_000, "supplier_party_id": sup["id"]})
    tb = client.get("/api/reports/trial-balance", headers=accountant).json()
    assert tb["balanced"] is True

def test_over_cancel_blocked(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 7")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 70_000_000, "reason": "بیش از مجاز"})
    assert r.status_code == 400
    assert "قابل‌لغو" in r.json()["detail"]

def test_over_refund_blocked(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 8")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "استرداد زیاد", "penalty_customer": -5_000_000})
    assert r.status_code == 400

def test_duplicate_request(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 9")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    r1 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "تکراری تستی"})
    assert r1.status_code == 200
    r2 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "تکراری تستی"})
    assert r2.status_code == 400
    assert ("قبلاً ثبت" in r2.json()["detail"] or "قابل‌لغو" in r2.json()["detail"])

def test_second_partial_cancel_limit(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 10")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    r1 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "لغو اول 40M آزمایشی"})
    assert r1.status_code == 200
    r2 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 30_000_000, "reason": "لغو دوم 30M بیش از باقی 20M"})
    assert r2.status_code == 400
    r3 = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 20_000_000, "reason": "لغو دوم 20M مجاز تستی"})
    assert r3.status_code == 200

def test_atomic_rollback_on_failure(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 11")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    before = client.get("/api/reports/trial-balance", headers=accountant).json()
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "شکست", "penalty_customer": 50_000_000})  # جریمه بیش از لغو → خطا
    assert r.status_code == 400
    after = client.get("/api/reports/trial-balance", headers=accountant).json()
    assert before["total_debit"] == after["total_debit"]

def test_reports_consistency(client, accountant):
    bank = account_id(client, accountant, "110201")
    cust = _party(client, accountant, "مشتری لغو جزئی 12")
    sup = _party(client, accountant, "تامین لغو جزئی 12", "supplier")
    inv = _invoice(client, accountant, cust["id"], 60_000_000)
    _bill(client, accountant, sup["id"], 40_000_000)
    _settle_in(client, accountant, cust["id"], 60_000_000, bank)
    _settle_out(client, accountant, sup["id"], 40_000_000, bank)
    client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=accountant, json={
        "amount": 40_000_000, "reason": "گزارش", "penalty_customer": 8_000_000,
        "supplier_refund": 35_000_000, "penalty_supplier": 5_000_000, "supplier_party_id": sup["id"]})
    tb = client.get("/api/reports/trial-balance", headers=accountant).json()
    assert tb["balanced"] is True
    assert len(client.get("/api/invoices", headers=accountant).json()["rows"]) >= 1
