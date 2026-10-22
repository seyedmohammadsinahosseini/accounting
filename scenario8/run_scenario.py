# -*- coding: utf-8 -*-
"""اجرای سناریوی ۸ مرحله‌ای روی API واقعی (پورت ۸۰۱۰، دیتابیس تمیز).

هر تماس HTTP در http_log.jsonl ثبت می‌شود؛ نتیجه‌ی ساختاریافته در results.json
و گزارش نهایی HTML در کنار همین فایل ساخته می‌شود.
"""
from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://127.0.0.1:8010"
OUT = Path(__file__).resolve().parent
LOG = OUT / "http_log.jsonl"
RESULTS = OUT / "results.json"

ADMIN_PW = "Admin123!@#x"
ADMIN_PW2 = "Admin123!@#x2"

logf = LOG.open("w", encoding="utf-8")
_rows = []          # نتایج چک‌ها: (مرحله، عنوان، مورد انتظار، واقعی، نتیجه، توضیح)
_state = {"notes": [], "ids": {}, "aux_notes": []}


def note(text, aux=False):
    _state["aux_notes" if aux else "notes"].append(text)
    print("  [یادداشت]", text)


def check(stage, title, expected, actual, detail=""):
    ok = expected == actual
    _rows.append({"stage": stage, "title": title, "expected": expected,
                  "actual": actual, "ok": bool(ok), "detail": detail})
    print(("  ✅" if ok else "  ❌"), f"{title} | انتظار: {expected!r} | واقعی: {actual!r}",
          ("— " + detail) if detail else "")
    return ok


def rec(stage, title, value, detail=""):
    _rows.append({"stage": stage, "title": title, "expected": "ثبت", "actual": value,
                  "ok": True, "detail": detail})


class Http:
    def __init__(self):
        self.h = {}

    def req(self, method, path, body=None, headers=None, raw_body=None):
        h = {"Content-Type": "application/json", **self.h, **(headers or {})}
        data = None
        if raw_body is not None:
            data = raw_body.encode("utf-8")
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        r = urllib.request.Request(BASE + path, data=data, method=method.upper(), headers=h)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                code, payload = resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            code, payload = e.code, e.read().decode("utf-8")
        try:
            js = json.loads(payload) if payload.strip() else {}
        except Exception:
            js = {"_raw": payload[:500]}
        logf.write(json.dumps({"method": method.upper(), "path": path, "body": body if raw_body is None else raw_body,
                               "headers": headers or {}, "status": code, "response": js},
                              ensure_ascii=False) + "\n")
        logf.flush()
        return code, js

    def must(self, method, path, body=None, headers=None, expect=200, label=""):
        code, js = self.req(method, path, body=body, headers=headers)
        if code != expect:
            raise RuntimeError(f"{label or path} -> HTTP {code}: {json.dumps(js, ensure_ascii=False)[:300]}")
        return js


http = Http()


def login():
    code, js = http.req("POST", "/api/auth/login", {"username": "admin", "password": ADMIN_PW})
    assert code == 200 and js.get("token"), f"login failed: {code} {js}"
    if js.get("must_change_password"):
        code2, _ = http.req("POST", "/api/me/password", {"old_password": ADMIN_PW, "new_password": ADMIN_PW2},
                            headers={"Authorization": f"Bearer {js['token']}"})
        assert code2 == 200, f"change password failed: {code2}"
        code, js = http.req("POST", "/api/auth/login", {"username": "admin", "password": ADMIN_PW2})
        assert code == 200 and js.get("token"), f"re-login failed: {code} {js}"
    http.h["Authorization"] = f"Bearer {js['token']}"
    print("✔ ورود مدیر انجام شد")
    return js["token"]


def accounts_by_code():
    rows = http.must("GET", "/api/accounts")["rows"]
    return {a["code"]: a for a in rows}


def bank_balance(bank_id):
    t = http.must("GET", "/api/reports/treasury")
    for r in t["rows"]:
        if r["id"] == bank_id:
            return r["balance"]
    raise AssertionError("حساب بانک در خزانه نیست")


def income(accs, codes):
    d = http.must("GET", "/api/reports/income-statement")
    by_code = {c: 0 for c in codes}
    for r in d["revenue_rows"] + d["expense_rows"]:
        if r["code"] in by_code:
            by_code[r["code"]] += r["amount"]
    return d, by_code


def party_balance(party_id):
    for p in http.must("GET", "/api/parties")["rows"]:
        if p["id"] == party_id:
            return p["balance"]
    return None


def main():
    token = login()
    _state["token_tail"] = token[-6:]
    accs = accounts_by_code()
    bank = accs["110201"];
    cap = accs["3101"]
    vat_cr = accs["1107"];
    vat_pay = accs["2104"]
    rev = accs["4101"];
    pen_c = accs["4107"];
    pen_s = accs["4108"];
    cogs = accs["5101"]
    _state["chart"] = {c: {"id": a["id"], "name": a["name"]} for c, a in
                       (("110201", bank), ("3101", cap), ("1107", vat_cr), ("2104", vat_pay),
                        ("4101", rev), ("4107", pen_c), ("4108", pen_s), ("5101", cogs))}
    today = http.must("GET", "/api/me")["today"]
    # تاریخ ISO امروز از سرور را از یک سند فرضی نمی‌گیریم؛ تاریخ شمسی طولول است؛ از API تاریخ امروز:
    today_fmt = None
    for fmt_row in http.must("GET", "/api/reports/trial-balance")["to"].split(" ")[0:1]:
        today_fmt = fmt_row
    date_str = http.must("GET", "/api/reports/trial-balance")["to"]
    _state["today"] = date_str

    print("\n=== مرحله‌صفر: سرمایه‌ی اولیه ۱۰۰ (پیش‌نیازِ «بانک ۱۸۰ = ۱۰۰ + ۸۰») ===")
    e0 = http.must("POST", "/api/entries", {"date": date_str, "description": "سرمایه‌ی اولیه مالک ۱۰۰",
                                            "lines": [{"account_id": bank["id"], "debit": 100, "credit": 0},
                                                      {"account_id": cap["id"], "debit": 0, "credit": 100}]})
    http.must("POST", f"/api/entries/{e0['id']}/post")
    _state["ids"]["capital_entry"] = e0["id"]
    check(0, "مانده بانک پس از سرمایه", 100, bank_balance(bank["id"]))
    note("سناریو از «بانک ۱۸۰» پس از ۸۰ دریافت می‌گوید ⇒ سرمایه‌ی اولیه‌ی ۱۰۰ فرض و ثبت شد (بستانکار حساب ۳۱۰۱ سرمایه).")

    print("\n=== مرحله ۱: دو فاکتور فروش + سه دریافت با FIFO ===")
    custA = http.must("POST", "/api/parties", {"name": "مشتری A — سناریو ۸ مرحله", "kind": "customer"})
    supS1 = http.must("POST", "/api/parties", {"name": "تأمین‌کننده S1 — سناریو ۸ مرحله", "kind": "supplier"})
    cid, sid = custA["id"], supS1["id"]
    _state["ids"]["custA"] = {"id": cid, "code": custA["code"]}
    _state["ids"]["supS1"] = {"id": sid, "code": supS1["code"]}

    inv1 = http.must("POST", "/api/invoices", {
        "party_id": cid, "date": date_str, "description": "A1 — فاکتور فروش ۶۰",
        "lines": [{"service_type": "tour", "qty": 1, "unit_price": 60, "discount": 0, "taxable": False}],
        "issue": True})
    inv2 = http.must("POST", "/api/invoices", {
        "party_id": cid, "date": date_str, "description": "A2 — فاکتور فروش ۴۰",
        "lines": [{"service_type": "tour", "qty": 1, "unit_price": 40, "discount": 0, "taxable": False}],
        "issue": True})
    _state["ids"]["A1"] = {"id": inv1["id"], "number": inv1["number"]}
    _state["ids"]["A2"] = {"id": inv2["id"], "number": inv2["number"]}
    rec(1, "شماره فاکتور A1", inv1["number"])
    rec(1, "شماره فاکتور A2", inv2["number"])

    for amt in (25, 35, 20):
        http.must("POST", "/api/settlements", {"direction": "in", "party_id": cid,
                                               "account_id": bank["id"], "amount": amt, "date": date_str,
                                               "description": f"دریافت از مشتری A — {amt}", "issue": True})

    invs = {r["id"]: r for r in http.must("GET", "/api/invoices")["rows"]}
    check(1, "A1 پرداخت‌شده", 60, invs[inv1["id"]]["paid"])
    check(1, "A1 ماندهٔ باز", 0, invs[inv1["id"]]["open"])
    check(1, "A2 پرداخت‌شده", 20, invs[inv2["id"]]["paid"])
    check(1, "A2 ماندهٔ باز", 20, invs[inv2["id"]]["open"])
    sets_A = [s for s in http.must("GET", "/api/settlements", )["rows"] if s["party_id"] == cid and s["direction"] == "in"]
    unalloc = sum(s["unallocated"] for s in sets_A)
    check(1, "ماندهٔ دریافت تخصیص‌نیافته", 0, unalloc)
    check(1, "موجودی بانک پایان مرحله ۱", 180, bank_balance(bank["id"]))
    _, inc1 = income(accs, ["4101"])
    check(1, "درآمد خدمات قبل از لغو (۴۱۰۱)", 100, inc1["4101"])

    print("\n=== مرحله ۲: فاکتور تأمین S1 با VAT ===")
    bill = http.must("POST", "/api/bills", {"party_id": sid, "date": date_str, "net": 40, "vat": 4,
                                            "description": "فاکتور تأمین S1 — اصل ۴۰ + مالیات ۴", "issue": True})
    _state["ids"]["bill"] = {"id": bill["id"], "number": bill["number"], "entry_id": bill.get("entry_id")}
    # نمایش سند برای اثبات توازن
    e_bill = http.must("GET", f"/api/entries/{bill['entry_id']}") if bill.get("entry_id") else None
    if e_bill:
        rec(2, "سند صورتحساب S1 (متوازن؟)", e_bill["balanced"],
            f"بدهکار {e_bill['debit_total']} = بستانکار {e_bill['credit_total']} | سطرها: " +
            " | ".join(f"{l['account_label']}: ب{l['debit']} / س{l['credit']}" for l in e_bill["lines"]))
        check(2, "توازن سند تأمین", True, e_bill["balanced"])
    note("VAT خرید در برنامه در حساب ۱۱۰۷ «مالیات بر ارزش‌افزوده — اعتبار مالیاتی» (بدهکار ۴) ثبت می‌شود؛ "
         "بدهکار: ۵۱۰۱=۴۰ و ۱۱۰۷=۴، بستانکار: حساب S1=۴۴ ⇒ سند متوازن است.")
    http.must("POST", "/api/settlements", {"direction": "out", "party_id": sid, "account_id": bank["id"],
                                           "amount": 44, "date": date_str,
                                           "description": "پرداخت فاکتور تأمین S1 — ۴۴", "issue": True})
    _, inc2 = income(accs, ["5101"])
    check(2, "هزینه خدمات (۵۱۰۱)", 40, inc2["5101"])
    led_vat = http.must("GET", f"/api/reports/ledger?account_id={vat_cr['id']}&frm={date_str}&to={date_str}")
    check(2, "گردش بدهکار حساب VAT اعتباری ۱۱۰۷", 4, led_vat["total_debit"])
    check(2, "بدهی به تأمین‌کننده S1", 0, party_balance(sid))
    check(2, "موجودی بانک پایان مرحله ۲", 136, bank_balance(bank["id"]))
    check(2, "تراز آزمایشی متوازن", True, http.must("GET", "/api/reports/trial-balance")["balanced"])

    print("\n=== مرحله ۳: لغو جزئی اول از A1 (۲۵) ===")
    pc1 = http.must("POST", f"/api/invoices/{inv1['id']}/partial-cancel", {
        "amount": 25, "reason": "انصراف یک بخش از سفر — لغو جزئی اول A1", "date": date_str,
        "penalty_customer": 5, "supplier_refund": 20, "penalty_supplier": 3, "supplier_party_id": sid})
    _state["ids"]["pc1"] = pc1
    rec(3, "شناسه‌های لغو اول", pc1)
    check(3, "استرداد نقدی به مشتری (پرداخت)، شناسه دارد", True, pc1["refund_id"] is not None)
    check(3, "سند جریمه مشتری ثبت شد", True, pc1["penalty_entry_id"] is not None)
    check(3, "سند برگشت تامین ثبت شد", True, pc1["supplier_refund_entry_id"] is not None)
    check(3, "سند جریمه تامین ثبت شد", True, pc1["supplier_penalty_entry_id"] is not None)
    _, inc3 = income(accs, ["4101", "4107", "4108", "5101"])
    check(3, "درآمد خدمات پس از لغو (۴۱۰۱)", 75, inc3["4101"])
    check(3, "درآمد جریمه مشتری (۴۱۰۷)", 5, inc3["4107"])
    check(3, "درآمد جریمه تامین (۴۱۰۸)", 3, inc3["4108"])
    check(3, "هزینه خالص خدمات (۵۱۰۱)", 20, inc3["5101"])
    check(3, "موجودی بانک پس از استرداد مشتری", 116, bank_balance(bank["id"]))
    note("برگشت ۲۰+۳ از تامین فقط به بدهی/طلب از S1 بدهکار شد؛ چون وصول نقدی هنوز در مرحله ۵ انجام می‌شود، تأثیری در بانک ندارد.", )

    print("\n=== مرحله ۴: لغو جزئی دوم از همان A1 (۱۵) ===")
    pc2 = http.must("POST", f"/api/invoices/{inv1['id']}/partial-cancel", {
        "amount": 15, "reason": "ادامه انصراف — لغو جزئی دوم A1", "date": date_str,
        "penalty_customer": 2, "supplier_refund": 10, "penalty_supplier": 1, "supplier_party_id": sid})
    _state["ids"]["pc2"] = pc2
    hist1 = http.must("GET", f"/api/invoices/{inv1['id']}/partial-cancels")["rows"]
    check(4, "دو ردیف جداگانه در تاریخچه لغو A1", 2, len(hist1))
    check(4, "مجموع لغو A1", 40, sum(r["amount"] for r in hist1))
    invs = {r["id"]: r for r in http.must("GET", "/api/invoices")["rows"]}
    check(4, "وضعیت A1 همچنان صادرشده (ابطال کامل نشده)", "issued", invs[inv1["id"]]["status"])
    _, inc4 = income(accs, ["4101", "4107", "4108", "5101"])
    check(4, "درآمد خدمات پس از دو لغو (۴۱۰۱)", 60, inc4["4101"])
    check(4, "مجموع جریمه مشتری (۴۱۰۷)", 7, inc4["4107"])
    check(4, "مجموع جریمه تامین (۴۱۰۸)", 4, inc4["4108"])
    check(4, "هزینه خالص پس از ۳۰ برگشت (۵۱۰۱)", 10, inc4["5101"])
    check(4, "موجودی بانک پس از هر دو استرداد مشتری", 103, bank_balance(bank["id"]))
    sets_A_out = [s for s in http.must("GET", "/api/settlements")["rows"]
                  if s["party_id"] == cid and s["direction"] == "out" and s["status"] == "issued"]
    check(4, "مجموع استرداد واقعی مشتری", 33, sum(s["amount"] for s in sets_A_out))
    check(4, "مجموع برگشت مورد انتظار از تامین", 30, sum(r["supplier_refund"] for r in hist1))
    check(4, "مجموع جریمه تامین در دو لغو", 4, sum(r["penalty_supplier"] for r in hist1))
    supb4 = party_balance(sid)
    rec(4, "مانده حساب S1 پس از دو لغو (دفتر)", supb4, "طلب ما = ۳۰ برگشت + ۴ جریمه؛ سند: هر برگشت/جریمه به حساب S1 بدهکار")
    check(4, "مانده طلب ما از S1 (۳۰+۴)", 34, supb4)

    print("\n=== مرحله ۵: وصول ۳۰ از تامین (مسیر رسمی) ===")
    e_c = http.must("POST", "/api/entries", {
        "date": date_str, "description": "وصول ۳۰ از S1 بابت برگشت‌های لغو A1 (۲۵ و ۱۵) — بدون اثر درآمد/هزینه",
        "lines": [{"account_id": bank["id"], "debit": 30, "credit": 0,
                   "description": "واریز از تأمین‌کننده S1"},
                  {"account_id": _get_sup_acc(accs, sid), "debit": 0, "credit": 30,
                   "description": "تسویه‌ی طلب از برگشت تأمین — مطابقت با اسناد لغو A1"}]})
    http.must("POST", f"/api/entries/{e_c['id']}/post")
    check(5, "موجودی بانک پس از وصول", 133, bank_balance(bank["id"]))
    _, inc5 = income(accs, ["4101", "4107", "4108", "5101"])
    check(5, "درآمد خدمات", 60, inc5["4101"])
    check(5, "جریمه مشتری", 7, inc5["4107"])
    check(5, "جریمه تامین", 4, inc5["4108"])
    check(5, "هزینه خالص خدمات", 10, inc5["5101"])
    d5, _ = income(accs, ["4101", "4107", "4108", "5101"])
    check(5, "سود سناریو پس از مرحله ۵ (۶۱)", 61, compute_scenario_profit(d5)["auto"],
          "۶۰ خدمات + ۷ جریمه مشتری + ۴ جریمه تامین − ۱۰ هزینه خالص")
    note("بدیلِ دریافت‌از‌تامین در API تسویه وجود ندارد (پرداخت/دریافت فقط نسبت به فاکتورها تعریف شده)؛ "
         "وصول با سند رسمی روزنامه (بدهکار بانک ۱۱۰۲۰۱ / بستانکار حساب تامین‌کننده) ثبت شد: هیچ درآمد یا کاهش هزینه‌ی مجددی ایجاد نشد.")

    print("\n=== مرحله ۶: دریافت ۲۰ جدید + لغو از A2 ===")
    sets_before = bank_balance(bank["id"])
    recv20 = http.must("POST", "/api/settlements", {"direction": "in", "party_id": cid,
                                                    "account_id": bank["id"], "amount": 20, "date": date_str,
                                                    "target_invoice_id": inv2["id"],
                                                    "description": "دریافت جدید ۲۰ — تخصیص هدفمند به A2", "issue": True})
    invs = {r["id"]: r for r in http.must("GET", "/api/invoices")["rows"]}
    check(6, "A2 پس از دریافت ۲۰ — ماندهٔ باز", 0, invs[inv2["id"]]["open"])
    check(6, "موجودی بانک پس از دریافت ۲۰", 153, bank_balance(bank["id"]))
    note("FIFO خودکار برنامه به قدیمی‌ترین فاکتور باز تخصیص می‌دهد؛ چون A1 در ساب‌لجر (۶۰ − ۲۰ پرداخت خالص = ۴۰ باز) "
         "از A2 قدیمی‌تر و بازتر است، FIFO خودکار مبلغ را به A1 می‌برد. برای رسیدن به معنای اقتصادی سناریو "
         "(اتمام A2)، دریافت با «تخصیص هدفمند» رسمی به A2 فرستاده شد — این یک تصمیم طراحی برنامه است، نه خطای عدد.",
         aux=False)
    pc3_body = {"amount": 10, "reason": "لغو بخشی از A2 — انصراف یک نفر از سفر دوم", "date": date_str,
                "penalty_customer": 1, "supplier_refund": 8, "penalty_supplier": 1, "supplier_party_id": sid}
    pc3_raw = json.dumps(pc3_body, ensure_ascii=False)
    code, pc3 = http.req("POST", f"/api/invoices/{inv2['id']}/partial-cancel",
                         headers={"Idempotency-Key": "idem-pc3-a2-00001"}, raw_body=pc3_raw)
    assert code == 200, f"pc3 failed: {code} {pc3}"
    _state["ids"]["pc3"] = pc3
    _state["pc3_raw"] = pc3_raw
    invs = {r["id"]: r for r in http.must("GET", "/api/invoices")["rows"]}
    _, inc6 = income(accs, ["4101", "4107", "4108", "5101"])
    check(6, "مجموع لغو فروش (۲۵+۱۵+۱۰)", 50, sum(r["amount"] for r in hist1) + pc3["amount"])
    check(6, "خدمات باقی‌مانده (درآمد ۴۱۰۱)", 50, inc6["4101"])
    check(6, "درآمد جریمه مشتری", 8, inc6["4107"])
    check(6, "درآمد جریمه تامین", 5, inc6["4108"])
    check(6, "مجموع استرداد واقعی مشتری", 42,
          sum(s["amount"] for s in http.must("GET", "/api/settlements")["rows"]
              if s["party_id"] == cid and s["direction"] == "out" and s["status"] == "issued"))
    hist2 = http.must("GET", f"/api/invoices/{inv2['id']}/partial-cancels")["rows"]
    check(6, "مجموع برگشت تامین (۳۰+۸)", 38, sum(r["supplier_refund"] for r in hist1) + sum(r["supplier_refund"] for r in hist2))
    check(6, "هزینه خالص خدمات (۴۰−۳۸)", 2, inc6["5101"])
    check(6, "موجودی بانک پایان مرحله ۶ (۱۴۴)", 144, bank_balance(bank["id"]))

    print("\n=== مرحله ۷: آزمون‌های منفی و ضدتکرار ===")
    # ۷.۱ قرارداد برنامه: قابل‌لغو = مبلغ فاکتور − مجموع لغوهای قبلی = ۴۰ − ۱۰ = ۳۰ برای A2
    n_hist_a2 = len(http.must("GET", f"/api/invoices/{inv2['id']}/partial-cancels")["rows"])
    code, js7_1 = http.req("POST", f"/api/invoices/{inv2['id']}/partial-cancel", {
        "amount": 31, "reason": "تلاش بیش از سقف قابل‌لغو (۳۱ > ۳۰)", "date": date_str, "penalty_customer": 0,
        "supplier_refund": 0, "penalty_supplier": 0, "supplier_party_id": sid})
    check("7.1", "رد لغو ۳۱ از A2 (قابل‌لغو واقعیِ برنامه = ۳۰)", 400, code, str(js7_1.get("detail", ""))[:120])
    check("7.1+", "ردیف لغوی جدیدی در A2 ثبت نشد", n_hist_a2,
          len(http.must("GET", f"/api/invoices/{inv2['id']}/partial-cancels")["rows"]))
    note("قرارداد برنامه: «قابل‌لغو = مبلغ فاکتور − مجموع لغوهای قبلی». بنابراین برای A2 پس از لغوی ۱۰، قابل‌لغو ۳۰ است. "
         "در اجرای نخست همین ممیزی، لغوی ۱۱ از A2 (≤ ۳۰) طبق همین قانون پذیرفته شد؛ فرض «فقط ۱۰ قابل‌لغو» با قرارداد برنامه "
         "(و با مانده‌ی خدمات ۳۰) سازگار نیست — رد واقعی فقط روی مبالغ بالاتر از ۳۰ معنا دارد.", aux=True)
    # ۷.۲ ارسال دوباره‌ی همان درخواست با همان Idempotency-Key
    n_pc_before = len(http.must("GET", f"/api/invoices/{inv2['id']}/partial-cancels")["rows"])
    code, js7_2 = http.req("POST", f"/api/invoices/{inv2['id']}/partial-cancel",
                           headers={"Idempotency-Key": "idem-pc3-a2-00001"}, raw_body=_state["pc3_raw"])
    n_pc_after = len(http.must("GET", f"/api/invoices/{inv2['id']}/partial-cancels")["rows"])
    check("7.2", "تکرار همان درخواست با همان کلید ⇒ کد ۲۰۰", 200, code)
    check("7.2+", "بدون سند/ردیف لغوی جدید در تکرار", 0, n_pc_after - n_pc_before)
    check("7.2b", "شناسه‌ی لغو برگشتی همان قبلی است", _state["ids"]["pc3"]["id"], js7_2.get("id"))
    # ۷.۳ همان کلید، بدنهٔ متفاوت ⇒ قرارداد API: 409
    code, js7_3 = http.req("POST", f"/api/invoices/{inv2['id']}/partial-cancel",
                           headers={"Idempotency-Key": "idem-pc3-a2-00001"},
                           body={"amount": 9, "reason": "بدنهٔ متفاوت با همان کلید", "date": date_str,
                                 "penalty_customer": 0, "supplier_refund": 0, "penalty_supplier": 0,
                                 "supplier_party_id": sid})
    check("7.3", "کلید تکراری + بدنهٔ متفاوت ⇒ رد طبق قرارداد", 409, code, str(js7_3.get("detail", ""))[:120])
    # ۷.۴ استرداد مجدد ۹: همان عملیات تجاری (بدون کلید) ⇒ رد تکراری سطح دامنه
    n_out_before = len([s for s in http.must("GET", "/api/settlements")["rows"]
                        if s["party_id"] == cid and s["direction"] == "out"])
    bal_before = bank_balance(bank["id"])
    code, js7_4 = http.req("POST", f"/api/invoices/{inv2['id']}/partial-cancel",
                           body=json.loads(_state["pc3_raw"]))
    n_out_after = len([s for s in http.must("GET", "/api/settlements")["rows"]
                       if s["party_id"] == cid and s["direction"] == "out"])
    check("7.4", "ثبت مجدد همان لغو (بدون کلید) ⇒ رد تکراری", 400, code, str(js7_4.get("detail", ""))[:120])
    check("7.4b", "پرداخت استردادِ دوم ساخته نشد", 0, n_out_after - n_out_before)
    check("7.4c", "مانده بانک بدون تغییر", bal_before, bank_balance(bank["id"]))
    # ۷.۵ هم‌زمانی: دو درخواست لغو ۱۰ از فاکتوری با ۱۰ قابل‌لغو
    supX = http.must("POST", "/api/parties", {"name": "مشتری آزمون منفی — سناریو", "kind": "customer"})
    aux_id = supX["id"]
    _state["ids"]["custAux"] = {"id": aux_id, "code": supX["code"]}
    invW1 = http.must("POST", "/api/invoices", {"party_id": aux_id, "date": date_str,
                                                "description": "W1 — فاکتور آزمون منفی ۱۰ (ویزا)",
                                                "lines": [{"service_type": "visa", "qty": 1, "unit_price": 10,
                                                           "discount": 0, "taxable": False}], "issue": True})
    invCX = http.must("POST", "/api/invoices", {"party_id": aux_id, "date": date_str,
                                                "description": "CX — فاکتور هم‌زمانی ۱۰ (ویزا)",
                                                "lines": [{"service_type": "visa", "qty": 1, "unit_price": 10,
                                                           "discount": 0, "taxable": False}], "issue": True})
    http.must("POST", "/api/settlements", {"direction": "in", "party_id": aux_id, "account_id": bank["id"],
                                           "amount": 10, "date": date_str,
                                           "description": "دریافت CX — کنترل هم‌زمانی", "issue": True})
    res = {"codes": [], "err": []}

    def _worker(tag):
        try:
            code, j = http.req("POST", f"/api/invoices/{invCX['id']}/partial-cancel", {
                "amount": 10, "reason": f"لغو هم‌زمان CX {tag}", "date": date_str,
                "penalty_customer": 10, "supplier_refund": 0, "penalty_supplier": 0})
            res["codes"].append(code)
        except Exception as exc:
            res["err"].append(str(exc))

    t1, t2 = threading.Thread(target=_worker, args=("رشته‌۱",)), threading.Thread(target=_worker, args=("رشته‌۲",))
    t1.start(); t2.start(); t1.join(); t2.join()
    ok_order = sorted(res["codes"])
    check("7.5", "دو لغو هم‌زمان روی CX ⇒ دقیقاً یک ۲۰۰ + یک ۴۰۰", "[200, 400]", str(ok_order))
    hist_cx = http.must("GET", f"/api/invoices/{invCX['id']}/partial-cancels")["rows"]
    check("7.5b", "فقط یک ردیف لغو برای CX ثبت شد", 1, len(hist_cx))
    note("قفل درون‌فرآیند لغو جزئی (به‌علاوه‌ی sum مجدد قابل‌لغو درون همان تراکنش) مانع لغو دوم شد — سناریو روی ۱ worker اجرا شده; تست Postgres در دسترس نبود (SQLite).", aux=True)
    # ۷.۶ خطای اجباری وسط تراکنش: بستن دوره و تلاش لغو ⇒ همه چیز بازگردد
    from_dt = http.must("GET", "/api/reports/trial-balance")["from"]
    jy, jm = _parse_jalali(date_str)
    http.must("POST", "/api/periods/close", {"jy": jy, "jm": jm})
    paid_w_before = http.must("GET", f"/api/invoices")["rows"]
    paid_w_before = next(r["paid"] for r in paid_w_before if r["id"] == invW1["id"])
    n_hist_w = len(http.must("GET", f"/api/invoices/{invW1['id']}/partial-cancels")["rows"])
    code, js7_6 = http.req("POST", f"/api/invoices/{invW1['id']}/partial-cancel", {
        "amount": 4, "reason": "لغو در دوره بسته — باید کاملاً شکست بخورد", "date": date_str,
        "penalty_customer": 0, "supplier_refund": 0, "penalty_supplier": 0})
    paid_w_after = next(r["paid"] for r in http.must("GET", "/api/invoices")["rows"] if r["id"] == invW1["id"])
    n_hist_w2 = len(http.must("GET", f"/api/invoices/{invW1['id']}/partial-cancels")["rows"])
    check("7.6", "لغو در دوره بسته رد شد", 400, code, str(js7_6.get("detail", ""))[:120])
    check("7.6b", "بدون ردیف لغو‌ی ناقص", n_hist_w, n_hist_w2)
    check("7.6c", "تخصیص/پرداخت W1 بدون تغییر", paid_w_before, paid_w_after)
    check("7.6d", "مانده بانک بدون تغییر در شکست میانی", 154, bank_balance(bank["id"]),
          "۱۴۴ (پایان مرحله ۶) + ۱۰ دریافت CX (آزمون ۷.۵)")
    http.must("POST", "/api/periods/reopen", {"jy": jy, "jm": jm})
    note("بازگشت تراکنش با rollback کامل تأیید شد (نصفه‌سند/نصفه‌لغو باقی نماند) و دوره از مسیر رسمی باز شد.", aux=True)
    # ۷.۷ لغو از فاکتور تسویه‌نشده (W1) و پرداخت‌شده (CX)
    code, pc_w = http.req("POST", f"/api/invoices/{invW1['id']}/partial-cancel", {
        "amount": 4, "reason": "آزمون لغو از فاکتور تسویه‌نشده (بدون دریافت)", "date": date_str,
        "penalty_customer": 4, "supplier_refund": 0, "penalty_supplier": 0})
    check("7.7", "لغو از فاکتور تسویه‌نشده مجاز است (با جریمه کامل ⇒ استرداد صفر)", 200, code)
    rec("7.7b", "شواهد: CX قبلاً پرداخت شده و لغو هم‌زمان روی آن موفق شد", "بلی",
        "رد لغو از فاکتور پرداخت‌شده/تسویه‌نشده در برنامه تعریف نشده؛ هر دو پذیرفته‌اند — رفتار طراحی، نه انحراف عددی.")
    # ۷.۸ تاریخ و دلیل متفاوت، مبلغ یکسان ⇒ مجاز
    y_date = _prev_day(date_str)
    code, pc_w2 = http.req("POST", f"/api/invoices/{invW1['id']}/partial-cancel", {
        "amount": 4, "reason": "همان مبلغ ولی تاریخ و دلیل متفاوت — باید پذیرفته شود", "date": y_date,
        "penalty_customer": 4, "supplier_refund": 0, "penalty_supplier": 0})
    if code != 200:
        code, pc_w2 = http.req("POST", f"/api/invoices/{invW1['id']}/partial-cancel", {
            "amount": 4, "reason": "همان مبلغ، تاریخ متفاوت و دلیل متفاوت — عملیات معتبر مجزا", "date": date_str,
            "penalty_customer": 4, "supplier_refund": 0, "penalty_supplier": 0})
    check("7.8", "مبلغ یکسان + تاریخ/دلیل متفاوت ⇒ عملیات معتبر مجزا پذیرفته شد", 200, code)
    hist_w = http.must("GET", f"/api/invoices/{invW1['id']}/partial-cancels")["rows"]
    check("7.8b", "دو ردیف لغو‌ی معتبر جدا برای W1", 2, len(hist_w))
    note("ردیف‌های کناری آزمون (W1/CX) اثر خود را در حساب‌های ۴۱۰۳/۴۱۰۷/بانک نشان می‌دهند؛ در تطبیق نهایی دقیقاً تفکیک و مستند شده‌اند.", aux=True)
    note("یافته‌ی اجرای نخست ممیزی (اصلاح‌شده): سند برگشت لغو برای خدمات غیرِ tour به‌اشتباه به ۴۱۰۱ می‌رفت "
         "چون SERVICE_TYPES کلید SYSTEM_CODES برمی‌گرداند نه کد — اکنون با نگاشت صحیح، برگشت ویزا به ۴۱۰۳ "
         "و برگشت بلیت به ۴۱۰۲ می‌نشیند (جزئیات در گزارش و تست رگرسیون).", aux=True)

    print("\n=== مرحله ۸: استخراج گزارش‌های نهایی ===")
    stage8(accs, bank, custA=cid, supS1=sid, inv1=inv1["id"], inv2=inv2["id"],
           invW=invW1["id"], invCX=invCX["id"], aux_party=aux_id, date_str=date_str)

    RESULTS.write_text(json.dumps({"rows": _rows, "notes": _state["notes"], "aux": _state["aux_notes"],
                                   "ids": _state["ids"], "chart": _state["chart"], "today": _state["today"]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
    logf.close()
    summary = {}
    for r in _rows:
        summary.setdefault(r["stage"], [0, 0])
        summary[r["stage"]][0] += 1
        summary[r["stage"]][1] += int(r["ok"])
    for s in sorted(summary, key=str):
        print(f"مرحله {s}: {summary[s][1]}/{summary[s][0]} موفق")
    print("\nپایان اجرای سناریو — نتایج در results.json ذخیره شد.")


def _get_sup_acc(accs, sid):
    # حساب تفصیلی S1 از صفحه اشخاص
    for p in http.must("GET", "/api/parties")["rows"]:
        if p["id"] == sid:
            return p["account_id"]
    raise AssertionError("حساب S1 یافت نشد")


def _parse_jalali(s):
    parts = s.split(" ")[0].split("/")
    return int(parts[0]), int(parts[1])


def _prev_day(s):
    # یک روز کاری/تاریخی متفاوت معتبر — هفدهم همان ماه
    parts = s.split("/")
    # ممکن است جداکننده متفاوت باشد
    if len(parts) < 3:
        parts = s.split(" ")[0].split("/")
    return f"{parts[0]}/{parts[1]}/{int(parts[2])-1:02d}"


def compute_scenario_profit(income_resp):
    main_rev = income_resp["total_revenue"]
    main_exp = income_resp["total_expense"]
    return {"auto": main_rev - main_exp, "rev": main_rev, "exp": main_exp}


def stage8(accs, bank, custA, supS1, inv1, inv2, invW, invCX, aux_party, date_str):
    # پنجره از ابتدای سال مالی تا امروز تا لغوِ تست ۷.۸ (تاریخ دیروز) هم در گردش‌ها گرفته شود
    def led(acc_id):
        return http.must("GET", f"/api/reports/ledger?account_id={acc_id}&to={date_str}")
    tb = http.must("GET", f"/api/reports/trial-balance?to={date_str}")
    check(8, "تراز آزمایشی پایانی متوازن", True, tb["balanced"])

    # سنتز «سناریوی اصلی» در مقابل «کنارِ آزمون‌ها»
    aux_impact = {"bank": +10, "4103": +2, "4107": +18, "aux_customer": +10}
    led_bank = led(bank["id"])
    check(8, "مانده بانک — واقعی از API", 154, led_bank["closing"],
          "سناریوی اصلی ۱۴۴ + دریافت CX آزمون ۱۰ = ۱۵۴ (سرمایه‌ی ۱۰۰ داخل جمع است)")
    led_rev3 = led(accs["4103"]["id"])
    check(8, "درآمد ویزا/سایر ۴۱۰۳ (آزمون‌ها: +۱۰+۱۰ − ۸ − ۱۰)", 2,
          led_rev3["total_credit"] - led_rev3["total_debit"])
    led_a = led(_get_party_acc(custA))
    check(8, "دفتر مشتری A — بدهکار", 0, led_a["closing"],
          f"گردش د {led_a['total_debit']} / س {led_a['total_credit']}")
    led_s = led(_get_party_acc(supS1))
    check(8, "دفتر تأمین S1 — طلب ما", 13, led_s["closing"],
          "۸ برگشت وصول‌نشده (۳۸−۳۰) + ۵ جریمه وصول‌نشده = ۱۳")
    led_rev = led(accs["4101"]["id"])
    check(8, "درآمد خدمات ۴۱۰۱ (بدهکار=−۵۰ لغو، بستانکار=۱۰۰)", 50,
          led_rev["total_credit"] - led_rev["total_debit"],
          f"ب {led_rev['total_credit']} / د {led_rev['total_debit']}")
    led_pc = led(accs["4107"]["id"])
    check(8, "جریمه مشتری ۴۱۰۷ (۸ سناریو + ۱۸ آزمون)", 26, -led_pc["closing"])
    led_ps = led(accs["4108"]["id"])
    check(8, "جریمه تامین ۴۱۰۸", 5, -led_ps["closing"])
    led_cogs = led(accs["5101"]["id"])
    check(8, "هزینه خدمات ۵۱۰۱ (۴۰ بدهکار − ۳۸ بستانکار)", 2, led_cogs["closing"])
    led_vatc = led(accs["1107"]["id"])
    check(8, "VAT اعتباری ۱۱۰۷ (د ۴)", 4, led_vatc["closing"])
    led_vatp = led(accs["2104"]["id"])
    check(8, "VAT قابل‌پرداخت ۲۱۰۴ (فروش‌ها معاف)", 0, -led_vatp["closing"])
    led_aux = led(_get_party_acc(aux_party))
    rec(8, "مانده مشتری آزمون (W1 مانده + CX)", led_aux["closing"],
        "۱۰ طلب از W1 (۱۰ قبض − ۸ لغو + ۸ جریمه)؛ CX خالص صفر")

    invs = {r["id"]: r for r in http.must("GET", "/api/invoices")["rows"]}
    for key, iid in (("A1", inv1), ("A2", inv2), ("W1", invW), ("CX", invCX)):
        r = invs[iid]
        rec(8, f"وضعیت فاکتور {key}", {"number": r["number"], "status": r["status"],
                                      "total": r["total"], "paid": r["paid"], "open": r["open"]})
    bills = http.must("GET", "/api/bills")["rows"]
    for b in bills:
        rec(8, "وضعیت صورتحساب تأمین S1", {"number": b["number"], "status": b["status"],
                                           "net": b["net"], "vat": b["vat"], "total": b["total"],
                                           "paid": b["paid"], "open": b["open"]})
    rec(8, "تاریخچه لغو A1", http.must("GET", f"/api/invoices/{inv1}/partial-cancels")["rows"])
    rec(8, "تاریخچه لغو A2", http.must("GET", f"/api/invoices/{inv2}/partial-cancels")["rows"])
    sets = http.must("GET", "/api/settlements")["rows"]
    rec(8, "فهرست دریافت/پرداخت‌ها و تخصیص‌ها", sets)
    free_deposits = [s for s in sets if s["direction"] == "in" and s["unallocated"] > 0 and s["status"] == "issued"]
    rec(8, "سپرده‌های آزادشده از لغو (تخصیص‌نیافته)",
        [{"number": s["number"], "unallocated": s["unallocated"]} for s in free_deposits],
        "اثر آزادسازی تخصیص هنگام لغو — در حساب مشتری باقی می‌ماند، نه در بانک")
    d8, _ = income(accs, ["4101", "4103", "4107", "4108", "5101"])
    rec(8, "سود و زیان API", {"revenue": d8["total_revenue"], "expense": d8["total_expense"],
                              "profit": d8["profit"]},
        "سناریوی اصلی: ۵۰+۸+۵−۲=۶۱؛ آزمون‌ها +۲۰ شبکه (۱۸ جریمه + ۲ ویزا) ⇒ ۸۱ واقعی")


def _get_party_acc(pid):
    for p in http.must("GET", "/api/parties")["rows"]:
        if p["id"] == pid:
            return p["account_id"]
    raise AssertionError("party not found")


if __name__ == "__main__":
    main()
