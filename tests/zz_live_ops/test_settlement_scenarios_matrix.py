"""ماتریس کامل سناریوهای دریافت/پرداخت — نقد آزاد، حواله، پیش‌دریافت، استرداد و وصول طلب لغو.

هر سناریو هم جریان پول را هم مانده طرف‌حساب و فاکتور را هم می‌چکد (دلتای مانده از تراز آزمایشی).
"""
from __future__ import annotations

from conftest import account_id


def _party(client, h, name, kind="customer"):
    return client.post("/api/parties", headers=h, json={"name": name, "kind": kind}).json()


def _balances(client, h):
    rows = client.get("/api/reports/trial-balance", headers=h).json()["rows"]
    return {r["code"]: (int(r["debit"] or 0), int(r["credit"] or 0)) for r in rows}


def _diff(before, after):
    out = {}
    for code in set(before) | set(after):
        d = after.get(code, (0, 0))[0] - before.get(code, (0, 0))[0]
        c = after.get(code, (0, 0))[1] - before.get(code, (0, 0))[1]
        if d or c:
            out[code] = (d, c)
    return out


def _invoice(client, h, party_id, price, date="1405/07/11"):
    return client.post("/api/invoices", headers=h, json={
        "party_id": party_id, "date": date, "description": "فاکتور ماتریس",
        "lines": [{"service_type": "tour", "qty": 1, "unit_price": price, "discount": 0,
                   "taxable": False}], "issue": True}).json()


def _inv_row(client, h, inv_id):
    rows = client.get("/api/invoices?status=all", headers=h).json()["rows"]
    return next(r for r in rows if r["id"] == inv_id)


def test_cash_receipt_and_free_advance_auto_allocation(client, accountant):
    """دریافت نقد با تخصیص مشخص + پیش‌دریافت آزاد که خودش به فاکتور باز می‌چسبد."""
    h = accountant
    cust = _party(client, h, "مشتری ماتریس")
    inv = _invoice(client, h, cust["id"], 10_000_000)
    bank = account_id(client, h, "110201")
    cash = account_id(client, h, "1101")
    # ۱) نقد با تخصیص: بدهکار صندوق / بستانکار تفصیلی؛ مانده فاکتور ۶ باقی
    b0 = _balances(client, h)
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/12",
        "target_invoice_id": inv["id"],
        "items": [{"method": "cash", "amount": 4_000_000, "account_id": cash}]})
    assert r.status_code == 200, r.text
    d = _diff(b0, _balances(client, h))
    assert d["1101"][0] == 4_000_000
    assert next(c for c in d if c.startswith("1103-")) and d[next(c for c in d if c.startswith("1103-"))][0] == -4_000_000
    assert _inv_row(client, h, inv["id"])["open"] == 6_000_000
    # ۲) پیش‌دریافت آزاد به همان مشتری — باید خودش به قدیمی‌ترین فاکتور باز بچسبد
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/13",
        "items": [{"method": "cash", "amount": 5_000_000, "account_id": bank}]})
    assert r.status_code == 200
    row = _inv_row(client, h, inv["id"])
    assert row["open"] == 1_000_000                     # به‌طور خودکار تخصیص یافت
    # ۳) حواله با شماره پیگیری به عنوان باقی فاکتور — روش و پیگیری در فهرست دیده می‌شود
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/14",
        "items": [{"method": "transfer", "amount": 1_000_000, "account_id": bank,
                   "ref_no": "TR-555-قرارداد"}]})
    assert r.status_code == 200
    mine = next(x for x in client.get("/api/settlements", headers=h).json()["rows"]
                if x["method"] == "transfer" and x.get("ref_no") == "TR-555-قرارداد")
    assert mine
    assert _inv_row(client, h, inv["id"])["open"] == 0


def test_refund_to_customer_and_supplier_claim_posts(client, accountant):
    """تنها پنجاه میلیون لغو: استرداد (در مشتری/بستانکار بانک) + وصول طلب (در بانک/بستانکار تأمین)."""
    h = accountant
    cust = _party(client, h, "مشتری لغوی")
    sup = _party(client, h, "تأمین لغوی", "supplier")
    bank = account_id(client, h, "110201")
    inv = _invoice(client, h, cust["id"], 50_000_000)
    # وصول کامل اولیه برای اینکه لغو معتبر جلوبَرَد
    client.post("/api/settlements", headers=h, json={
        "direction": "in", "party_id": cust["id"], "account_id": bank,
        "amount": 50_000_000, "date": "1405/07/10", "issue": True})
    # بیل تامین برای دانستن هزینه خرید (برگشت تامین با برگشت کامل خالص‌استردادی)
    bill = client.post("/api/bills", headers=h, json={
        "invoice_id": inv["id"], "party_id": sup["id"], "net": 30_000_000,
        "date": "1405/07/10", "description": "هزینه تور", "issue": True})
    assert bill.status_code == 200, bill.text
    r = client.post(f"/api/invoices/{inv['id']}/partial-cancel", headers=h, json={
        "amount": 20_000_000, "reason": "لغوی بخشی از تور", "date": "1405/07/12",
        "mode": "deferred", "supplier_refund": 12_000_000, "supplier_party_id": sup["id"]})
    assert r.status_code == 200, r.text
    pc_id = r.json()["id"]
    # استرداد به مشتری ۲۰م
    b0 = _balances(client, h)
    r = client.post(f"/api/partial-cancels/{pc_id}/refund-payment", headers=h, json={
        "account_id": bank, "amount": 20_000_000, "date": "1405/07/13"})
    assert r.status_code == 200, r.text
    d = _diff(b0, _balances(client, h))
    assert d["110201"][0] == -20_000_000                # پول از بانک خارج شد
    # طلب مشتری‌مان برگشت (هزینهٔ لغو در تفصیلی مشتری نشست)
    pc = client.get(f"/api/partial-cancels/{pc_id}", headers=h).json()
    assert pc["refund_paid"] == 20_000_000 and pc["refund_due"] == 20_000_000
    # وصولطلب از تامین ۱۲م: بدهکار بانک، بستانکار تفصیلی تأمین (طلب بسته)
    b0 = _balances(client, h)
    r = client.post(f"/api/partial-cancels/{pc_id}/receive-supplier", headers=h, json={
        "account_id": bank, "amount": 12_000_000, "date": "1405/07/14"})
    assert r.status_code == 200, r.text
    d = _diff(b0, _balances(client, h))
    assert d["110201"][0] == 12_000_000
    pc = client.get(f"/api/partial-cancels/{pc_id}", headers=h).json()
    assert pc["supplier_received"] == 12_000_000
    # پرداختِ دوباره بیشتر از مانده رد شود
    r = client.post(f"/api/partial-cancels/{pc_id}/refund-payment", headers=h, json={
        "account_id": bank, "amount": 1_000, "date": "1405/07/15"})
    assert r.status_code == 400
    r = client.post(f"/api/partial-cancels/{pc_id}/receive-supplier", headers=h, json={
        "account_id": bank, "amount": 1_000, "date": "1405/07/15"})
    assert r.status_code == 400


def test_bill_settlement_and_report_consistency(client, accountant):
    """پرداخت به تأمین روی بیل + صحت‌سنجی نهایی دفتر: ستون بدهکار = ستون بستانکار."""
    h = accountant
    sup = _party(client, h, "تأمین بیلدار", "supplier")
    bank = account_id(client, h, "110201")
    inv = _invoice(client, h, _party(client, h, "مشتری برای بیل")["id"], 40_000_000)
    client.post("/api/bills", headers=h, json={
        "invoice_id": inv["id"], "party_id": sup["id"], "net": 22_000_000,
        "date": "1405/07/15", "description": "بیل هتل", "issue": True})
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "out", "party_id": sup["id"], "date": "1405/07/16",
        "target_invoice_id": None,
        "items": [{"method": "transfer", "amount": 22_000_000, "account_id": bank,
                   "ref_no": "HB-77"}]})
    assert r.status_code == 200, r.text
    # صحت‌سنجی کلی سیستم: ستون بدهکار = بستانکار در تراز آزمایشی
    rows = _balances(client, h)
    assert sum(v[0] for v in rows.values()) == sum(v[1] for v in rows.values())
