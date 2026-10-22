"""چرخه کامل چک‌ها و سناریوهای تسویه — راستی‌آزمایی ثبت دفتر با منطق حسابداری.
نکته: تراز آزمایشی ستون «مانده» می‌دهد (درآمد/بدهی خالص هر حساب)؛
پس هر سند به‌صورت دلتای مانده راستی‌آزمایی می‌شود:
درهکار X در بدهی-طبیعت → debit بیشتر؛ بستانکار به همان → debit کمتر (یا credit بیشتر)."""
from __future__ import annotations

import pytest
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


def _invoice(client, h, party_id, price, date="1405/07/10"):
    return client.post("/api/invoices", headers=h, json={
        "party_id": party_id, "date": date, "description": "فاکتور تست چک",
        "lines": [{"service_type": "tour", "qty": 1, "unit_price": price, "discount": 0,
                   "taxable": False}], "issue": True}).json()


def _party_acc_code_of(client, h, party_id):
    rows = client.get("/api/accounts", headers=h).json()["rows"]
    me = next(r for r in rows if r["name"] and str(party_id) in str(r.get("note") or "")) if False else None
    return None


def test_in_cheque_full_cycle_mid_account(client, accountant):
    """دریافت چک → خواب به بانک → پاس: حساب میانی ۱۱۰۶ بین دریافت و وصول واقعی."""
    h = accountant
    cust = _party(client, h, "مشتری چکی چرخه")
    _invoice(client, h, cust["id"], 20_000_000)
    bank = account_id(client, h, "110201")
    # ثبت چک از مسیر سند تسویه
    b0 = _balances(client, h)
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/11",
        "description": "دریافت چک چرخه",
        "items": [{"method": "cheque", "amount": 20_000_000, "cheque_number": "CYC-100",
                   "cheque_bank": "تجارت", "cheque_due_date": "1405/08/10"}]})
    assert r.status_code == 200, r.text
    d = _diff(b0, _balances(client, h))
    assert d["1104"] == (20_000_000, 0)                      # دریافت چک: بدهکار چک‌های دریافتی
    party_code = next(c for c in d if c.startswith("1103-"))
    assert d[party_code][0] == -20_000_000                   # طلب مشتری تسویه شد (دهی به صفر)
    assert "110201" not in d                                 # بانک دست‌نخورده — وصول نشده

    cheques = client.get("/api/cheques?direction=in", headers=h).json()["rows"]
    ch = next(x for x in cheques if x["number"] == "CYC-100")
    assert ch["status"] == "in_hand"

    # گام واگذاری به بانک — فقط از نزد صندوق به «در راه وصول» می‌رود
    b0 = _balances(client, h)
    r = client.post(f"/api/cheques/{ch['id']}/action", headers=h, json={"action": "deposit"})
    assert r.status_code == 200, r.text
    d = _diff(b0, _balances(client, h))
    assert d["1104"][0] == -20_000_000 and d["1106"][0] == 20_000_000
    # بانک وopiaو خزانه در این گام جابه‌جا نشد
    b0 = _balances(client, h)
    r = client.post(f"/api/cheques/{ch['id']}/action", headers=h,
                    json={"action": "clear", "account_id": bank})
    assert r.status_code == 200, r.text
    d = _diff(b0, _balances(client, h))
    assert d["1106"][0] == -20_000_000 and d["110201"][0] == 20_000_000   # پول واقعی وارد بانک شد
    # اقدام مجدد ممنوع
    r = client.post(f"/api/cheques/{ch['id']}/action", headers=h,
                    json={"action": "clear", "account_id": bank})
    assert r.status_code == 400
    r = client.post(f"/api/cheques/{ch['id']}/action", headers=h, json={"action": "deposit"})
    assert r.status_code == 400


def test_in_cheque_bounce_in_hand_and_after_deposit(client, accountant):
    """برگشت چک: نزد صندوق → برگرد به ۱۱۰۴؛ واگذارشده → برگرد به ۱۱۰۶، نه بانک."""
    h = accountant
    cust = _party(client, h, "مشتری چک برگشتی")
    _invoice(client, h, cust["id"], 60_000_000)
    # چک اول — برگشت مستقیم از نزد صندوق
    client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/11",
        "items": [{"method": "cheque", "amount": 25_000_000, "cheque_number": "BNC-1",
                   "cheque_bank": "ملی", "cheque_due_date": "1405/08/01"}]})
    # چک دوم — خواب می‌دهیم و بعد برمی‌گردد
    client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/11",
        "items": [{"method": "cheque", "amount": 35_000_000, "cheque_number": "BNC-2",
                   "cheque_bank": "ملی", "cheque_due_date": "1405/08/01"}]})
    chs = {x["number"]: x for x in client.get("/api/cheques?direction=in", headers=h).json()["rows"]
           if x["number"] in ("BNC-1", "BNC-2")}
    client.post(f"/api/cheques/{chs['BNC-2']['id']}/action", headers=h, json={"action": "deposit"})
    b0 = _balances(client, h)
    r = client.post(f"/api/cheques/{chs['BNC-1']['id']}/action", headers=h, json={"action": "bounce"})
    assert r.status_code == 200
    d = _diff(b0, _balances(client, h))
    assert d["1104"][0] == -25_000_000
    code = next(c for c in d if c.startswith("1103-") and d[c][0] == 25_000_000)
    assert code                                  # طلب مشتری به‌دفتر برگشت
    b0 = _balances(client, h)
    r = client.post(f"/api/cheques/{chs['BNC-2']['id']}/action", headers=h,
                    json={"action": "bounce", "note": "موجودی کافی نیست"})
    assert r.status_code == 200
    d = _diff(b0, _balances(client, h))
    assert d["1106"][0] == -35_000_000          # از حساب میانی خارج شد نه مستقیم بانک
    assert "110201" not in d
    # چک پاس‌شده دیگر برگشت نمی‌خورد
    r = client.post(f"/api/cheques/{chs['BNC-2']['id']}/action", headers=h, json={"action": "bounce"})
    assert r.status_code == 400


def test_out_cheque_lifecycle_and_validation(client, accountant):
    """چک پرداختی: صدور → پاس (۲۱۰۲→بانک)، برگشت/ابطال از وضعیت صادرشده فقط."""
    h = accountant
    sup = _party(client, h, "تأمین چکی", "supplier")
    bank = account_id(client, h, "110201")
    b0 = _balances(client, h)
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "out", "party_id": sup["id"], "date": "1405/07/11",
        "items": [{"method": "cheque", "amount": 15_000_000, "cheque_number": "OUT-1",
                   "cheque_bank": "شهر", "cheque_due_date": "1405/08/15"}]})
    assert r.status_code == 200, r.text
    d = _diff(b0, _balances(client, h))
    assert d["2102"] == (0, 15_000_000)
    ch = next(x for x in client.get("/api/cheques?direction=out", headers=h).json()["rows"]
              if x["number"] == "OUT-1")
    assert ch["status"] == "issued"
    # آخی چک دوم برای برگشت
    client.post("/api/settlements/batch", headers=h, json={
        "direction": "out", "party_id": sup["id"], "date": "1405/07/11",
        "items": [{"method": "cheque", "amount": 5_000_000, "cheque_number": "OUT-2",
                   "cheque_bank": "شهر", "cheque_due_date": "1405/08/15"}]})
    ch2 = next(x for x in client.get("/api/cheques?direction=out", headers=h).json()["rows"]
               if x["number"] == "OUT-2")
    b0 = _balances(client, h)
    r = client.post(f"/api/cheques/{ch2['id']}/action", headers=h, json={"action": "bounce"})
    assert r.status_code == 200
    d = _diff(b0, _balances(client, h))
    assert d["2102"][1] == -5_000_000
    code = next(c for c in d if c.startswith("2101-") and d[c][0] == -5_000_000)
    assert code                                    # بدهی در انتظار تامین برمی‌گردد (مانده بدهی/طلب بستنی)
    # پاس چک اول
    b0 = _balances(client, h)
    r = client.post(f"/api/cheques/{ch['id']}/action", headers=h,
                    json={"action": "clear", "account_id": bank})
    assert r.status_code == 200
    d = _diff(b0, _balances(client, h))
    assert d["2102"][1] == -15_000_000 and d["110201"][0] == -15_000_000   # پول واقعی از بانک خارج شد
    # بعد از پاس هیچ اقدامی مجاز نیست
    for bad in ({"action": "bounce"}, {"action": "cancel"}, {"action": "clear", "account_id": bank}):
        r = client.post(f"/api/cheques/{ch['id']}/action", headers=h, json=bad)
        assert r.status_code == 400, bad


def test_cheque_cancelled_when_settlement_reversed(client, accountant, admin):
    """ابطال ردیف چکی سند → برگ چک هم باطل می‌شود تا دوباره به جریان نیفتد."""
    h = accountant
    cust = _party(client, h, "مشتری ابطال چکی")
    _invoice(client, h, cust["id"], 12_000_000)
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/11",
        "items": [{"method": "cheque", "amount": 12_000_000, "cheque_number": "RVS-1",
                   "cheque_bank": "پاسارگاد", "cheque_due_date": "1405/08/20"}]})
    sid = r.json()["rows"][0]["id"]
    # ابطال با نقش بالادست
    r = client.post(f"/api/settlements/{sid}/cancel", headers=admin, json={"reason": "تست ابطال"})
    assert r.status_code == 200, r.text
    ch = next(x for x in client.get("/api/cheques?direction=in", headers=h).json()["rows"]
              if x["number"] == "RVS-1")
    assert ch["status"] in ("cancelled", "ابطال شد") or ch["status"] == "cancelled"
    # دیگر نمی‌شود deposit کرد
    r = client.post(f"/api/cheques/{ch['id']}/action", headers=h, json={"action": "deposit"})
    assert r.status_code == 400


def test_transfer_cheque_needs_target_party(client, accountant):
    """واگذاری چک دریافتی به تأمین بدون طرف مقصد پذیرفته نیست؛ با طرف، به تفصیلی او می‌نشیند."""
    h = accountant
    cust = _party(client, h, "مشتری واگذار")
    sup = _party(client, h, "تامین مقصد واگذاری", "supplier")
    _invoice(client, h, cust["id"], 8_000_000)
    client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/11",
        "items": [{"method": "cheque", "amount": 8_000_000, "cheque_number": "TRF-1",
                   "cheque_bank": "آینده", "cheque_due_date": "1405/08/25"}]})
    ch = next(x for x in client.get("/api/cheques?direction=in", headers=h).json()["rows"]
              if x["number"] == "TRF-1")
    r = client.post(f"/api/cheques/{ch['id']}/action", headers=h, json={"action": "transfer"})
    assert r.status_code == 400 and "تأمین‌کننده" in r.json()["detail"]
    b0 = _balances(client, h)
    r = client.post(f"/api/cheques/{ch['id']}/action", headers=h,
                    json={"action": "transfer", "party_id": sup["id"]})
    assert r.status_code == 200, r.text
    d = _diff(b0, _balances(client, h))
    assert d["1104"][0] == -8_000_000
    assert any(c.startswith("2101-") and v[0] == 8_000_000 for c, v in d.items())


def test_cancel_one_row_of_batch_keeps_others(client, accountant, admin):
    """ابطال تنها یک ردیف از سند گروهی: باقی ردیف‌ها و سند پایه سالم می‌مانند."""
    h = accountant
    cust = _party(client, h, "مشتری ابطال جزئی")
    inv = _invoice(client, h, cust["id"], 30_000_000)
    cash = account_id(client, h, "1101")
    r = client.post("/api/settlements/batch", headers=h, json={
        "direction": "in", "party_id": cust["id"], "date": "1405/07/11",
        "target_invoice_id": inv["id"],
        "items": [{"method": "cash", "amount": 18_000_000, "account_id": cash},
                  {"method": "cheque", "amount": 12_000_000, "cheque_number": "PART-1",
                   "cheque_bank": "ملت", "cheque_due_date": "1405/08/30"}]})
    ids = {row["method"]: row for row in r.json()["rows"]}
    # ابطال فقط ردیف چک
    r = client.post(f"/api/settlements/{ids['cheque']['id']}/cancel", headers=admin,
                    json={"reason": "برگشت با بانک"})
    assert r.status_code == 200
    row = {x["id"]: x for x in client.get("/api/settlements", headers=h).json()["rows"]}
    assert row[ids["cash"]["id"]]["status"] == "issued"
    assert row[ids["cheque"]["id"]]["status"] == "cancelled"
    # مانده فاکتور: پرداختشده = فقط نقد ۱۸م
    rows = client.get("/api/invoices?status=all", headers=h).json()["rows"]
    mine = next(x for x in rows if x["id"] == inv["id"])
    assert mine["paid"] == 18_000_000 and mine["open"] == 12_000_000
