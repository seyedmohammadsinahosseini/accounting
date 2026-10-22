# -*- coding: utf-8 -*-
"""رگرسیون گزارش کاربر: «روی فاکتور دکمه‌ی دریافت پول را می‌زنم، پول ثبت می‌شود اما مانده‌ی همین فاکتور عوض نمی‌شود».

علت ریشه‌ای: دکمه‌ی «دریافت پول» روی ردیف فاکتور، settlementEditor را بدون
target_invoice_id فراخوانی می‌کرد؛ در نتیجه بک‌اند تخصیص را FIFO (به قدیمی‌ترین
فاکتور بازِ همان مشتری) انجام می‌داد و فاکتورِ کلیک‌شده ممکن بود دست‌نخورده بماند.
اصلاح UI: دکمه اکنون target_invoice_id=inv.id می‌فرستد. این تست قرارداد API را
برای هر دو حالت سند می‌کند تا رفتار شفاف و پایدار بماند.
"""
from __future__ import annotations


def _mk_customer(client, h, name):
    r = client.post("/api/parties", headers=h, json={"name": name, "kind": "customer",
                                                     "phone": "", "note": "تست تخصیص دریافت"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _mk_invoice(client, h, party_id, price):
    r = client.post("/api/invoices", headers=h, json={
        "party_id": party_id, "date": "1405/03/03", "description": "تست تخصیص دریافت",
        "lines": [{"service_type": "tour", "qty": 1, "unit_price": price, "discount": 0,
                   "taxable": False}], "issue": True})
    assert r.status_code == 200, r.text
    return r.json()


def _invoice_row(client, h, inv_id):
    rows = client.get("/api/invoices?status=all", headers=h).json()["rows"]
    return next(r for r in rows if r["id"] == inv_id)


def _bank(client, h):
    acc_rows = client.get("/api/accounts", headers=h).json()["rows"]
    return next(a for a in acc_rows if a["code"] == "110201")


def test_untargeted_receipt_sticks_to_oldest_open_invoice(client, accountant):
    """بدون target_invoice_id — رفتار قراردادی FIFO: پول به قدیمی‌ترین فاکتور باز می‌چسبد
    و فاکتور جدیدتر (که کاربر روی دکمه‌اش کلیک کرده بود) دست‌نخورده می‌ماند؛
    دقیقاً همان چیزی که کاربر به‌عنوان باگ دید."""
    h = accountant
    party_id = _mk_customer(client, h, "مشتری تست FIFO دریافت")
    bank = _bank(client, h)
    inv1 = _mk_invoice(client, h, party_id, 5_000_000)   # قدیمی‌تر
    inv2 = _mk_invoice(client, h, party_id, 10_000_000)  # جدیدتر (فاکتوری که کاربر انتخاب کرده بود)

    r = client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": party_id, "account_id": bank["id"],
        "amount": 3_000_000, "date": "1405/03/04", "issue": True})
    assert r.status_code == 200, r.text

    assert _invoice_row(client, h, inv1["id"])["paid"] == 3_000_000
    assert _invoice_row(client, h, inv2["id"])["paid"] == 0  # ← مانده فاکتور دوم عوض نشد


def test_targeted_receipt_hits_exactly_the_clicked_invoice(client, accountant):
    """با target_invoice_id — همان قراردادی که دکمه‌ی اصلاح‌شده استفاده می‌کند:
    فقط همان فاکتور تسویه می‌شود و فاکتور قدیمی‌تر باز می‌ماند؛ مازاد بر مانده هم رد می‌شود."""
    h = accountant
    party_id = _mk_customer(client, h, "مشتری تست دریافت هدفمند")
    bank = _bank(client, h)
    inv1 = _mk_invoice(client, h, party_id, 5_000_000)
    inv2 = _mk_invoice(client, h, party_id, 10_000_000)

    r = client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": party_id, "account_id": bank["id"],
        "amount": 3_000_000, "date": "1405/03/04", "issue": True,
        "target_invoice_id": inv2["id"], "description": "دریافت بابت فاکتور دوم"})
    assert r.status_code == 200, r.text

    row1 = _invoice_row(client, h, inv1["id"])
    row2 = _invoice_row(client, h, inv2["id"])
    assert row1["paid"] == 0            # فاکتور قدیمی‌تر دست‌نخورده
    assert row2["paid"] == 3_000_000    # دریافت دقیقاً به فاکتور کلیک‌شده نشست
    assert row2["open"] == 7_000_000

    # مازاد بر مانده‌ی فاکتور هدف باید رد شود (به فاکتور دیگری نشت خودکار ندارد)
    r2 = client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": party_id, "account_id": bank["id"],
        "amount": 20_000_000, "date": "1405/03/04", "issue": True,
        "target_invoice_id": inv2["id"]})
    assert r2.status_code == 400
    assert _invoice_row(client, h, inv2["id"])["paid"] == 3_000_000  # تراکنش ناقص ثبت نشد
