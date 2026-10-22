# -*- coding: utf-8 -*-
"""رگرسیون یافته‌ی ممیزی سناریوی ۸ مرحله: سند برگشت لغو جزئی باید به حساب درآمدِ خود خدمت برود.

اشکال پیدا‌شده: SERVICE_TYPES یک کلید SYSTEM_CODES برمی‌گرداند نه کد حساب؛ account_by_code
روی کلید ناموفق می‌شد و fallback به ۴۱۰۱ می‌رفت؛ در نتیجه برگشت بلیت (که باید ۴۱۰۲ باشد)
و ویزا/هتل/سایر (۴۱۰۳) همه به ۴۱۰۱ ثبت می‌شدند. این تست هر سه دسته را با API واقعی بررسی می‌کند.
"""
from __future__ import annotations

import json


def _invoice(client, h, party_id, service_type, price=10_000_000, **line_extra):
    line = {"service_type": service_type, "qty": 1, "unit_price": price, "discount": 0,
            "taxable": False, **line_extra}
    r = client.post("/api/invoices", headers=h, json={
        "party_id": party_id, "date": "1405/03/03",
        "description": f"تست نگاشت حساب برگشت — {service_type}",
        "lines": [line], "issue": True})
    assert r.status_code == 200, r.text
    return r.json()


def _receipt(client, h, party_id, bank_id, amount=10_000_000):
    r = client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": party_id, "account_id": bank_id, "amount": amount,
        "date": "1405/03/04", "issue": True})
    assert r.status_code == 200, r.text


def _cancel_debit_account(client, h, inv_id, acc_code_of):
    r = client.post(f"/api/invoices/{inv_id}/partial-cancel", headers=h, json={
        "amount": 2_000_000, "reason": "بررسی نگاشت حساب برگشت — باید به حساب خود خدمت برود",
        "date": "1405/03/05", "penalty_customer": 0})
    assert r.status_code == 200, r.text
    pc = r.json()
    entry = client.get(f"/api/entries/{pc['entry_id']}", headers=h).json()
    debits = [l for l in entry["lines"] if l["debit"] > 0]
    assert len(debits) == 1, f"سند برگشت باید یک سطر بدهکار داشته باشد: {entry['lines']}"
    return debits[0]["account_label"].split(" ")[0]


def test_cancel_maps_to_service_revenue_account(client, accountant):
    h = accountant
    rows = client.get("/api/parties", headers=h).json()["rows"]
    party = next(p for p in rows if p["kind"] in ("customer", "both"))
    acc_rows = client.get("/api/accounts", headers=h).json()["rows"]
    bank = next(a for a in acc_rows if a["code"] == "110201")

    for service_type, expected_code, extra in (
        ("visa", "4103", {}),
        ("ticket", "4102", {"flight_type": 1}),
        ("hotel", "4103", {}),
        ("tour", "4101", {}),
    ):
        inv = _invoice(client, h, party["id"], service_type, **extra)
        _receipt(client, h, party["id"], bank["id"])
        code = _cancel_debit_account(client, h, inv["id"], None)
        assert code == expected_code, (
            f"برگشت {service_type} به {code} خورد؛ باید {expected_code} باشد")
