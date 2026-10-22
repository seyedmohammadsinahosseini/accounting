"""آزمون‌های صورت‌های مالی و بستن سال (فاز ۴).

آزمون‌هایی که خود کارفرما خواسته بود:
۱) «جمع دارایی‌ها = بدهی‌ها + حقوق مالکانه» — ترازنامه هیچ‌وقت نباید ناتراز باشد.
۲) صورت سود و زیان با دفتر کل بخواند.
۳) جریان نقدی: انتقال بین دو حساب بانکی نه درآمد است نه هزینه.

به‌علاوه: بستن سال، ممنوعیت ثبت سند در سال بسته، و بازگشایی سال.
"""
from __future__ import annotations

from conftest import account_id


def _codes(entry: dict) -> dict:
    return {l["account_label"].split(" — ")[0]: (l["debit"], l["credit"]) for l in entry["lines"]}


def _balance_sheet(client, headers, to=None):
    url = "/api/reports/balance-sheet" + (f"?to={to}" if to else "")
    r = client.get(url, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _income(client, headers, frm=None, to=None):
    params = "&".join(p for p in (f"frm={frm}" if frm else "", f"to={to}" if to else "") if p)
    r = client.get("/api/reports/income-statement" + (f"?{params}" if params else ""), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _cash(client, headers, frm=None, to=None):
    params = "&".join(p for p in (f"frm={frm}" if frm else "", f"to={to}" if to else "") if p)
    r = client.get("/api/reports/cash-flow" + (f"?{params}" if params else ""), headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _balance_of(client, headers, code: str, to: str | None = None, frm: str | None = None) -> int:
    """مانده‌ی یک حساب از تراز آزمایشی: مثبت = بدهکار، منفی = بستانکار."""
    params = "&".join(p for p in (f"frm={frm}" if frm else "", f"to={to}" if to else "") if p)
    url = "/api/reports/trial-balance" + (f"?{params}" if params else "")
    rows = client.get(url, headers=headers).json()["rows"]
    for row in rows:
        if row["code"] == code:
            return row["debit"] - row["credit"]
    return 0


def _new_entry(client, headers, date, description, lines, post=True) -> dict:
    """سند تازه. پیش‌فرض این است که بلافاصله ثبت نهایی هم بشود تا در گزارش‌ها بیاید."""
    r = client.post("/api/entries", headers=headers,
                    json={"date": date, "description": description, "lines": lines})
    assert r.status_code == 200, r.text
    entry = r.json()
    if post:
        p = client.post(f"/api/entries/{entry['id']}/post", headers=headers)
        assert p.status_code == 200, p.text
    return entry


def _reopen_if_closed(client, headers, jy) -> None:
    """سال آزمون باید باز باشد؛ اگر آزمون قبلی بسته گذاشته، بازش می‌کنیم."""
    years = {r["title"]: r for r in client.get("/api/fiscal/years", headers=headers).json()["rows"]}
    if str(jy) in years and years[str(jy)]["status"] == "closed":
        r = client.post("/api/fiscal/reopen-year", headers=headers,
                        json={"jy": jy, "note": "آماده‌سازی آزمون"})
        assert r.status_code == 200, r.text


def test_balance_sheet_balances(client, accountant):
    """جمع دارایی‌ها = جمع بدهی‌ها + حقوق مالکانه."""
    bs = _balance_sheet(client, accountant)
    assert bs["balanced"] is True, bs
    assert bs["difference"] == 0
    assert bs["total_assets"] == bs["total_liabilities"] + bs["total_equity"]
    assert bs["total_assets"] > 0


def test_balance_sheet_balances_after_new_entries(client, accountant):
    """هر سند تازه‌ای هم ترازنامه را ناتراز نمی‌کند."""
    cash = account_id(client, accountant, "1101")
    revenue = account_id(client, accountant, "4101")
    bank = account_id(client, accountant, "110201")
    _new_entry(client, accountant, "1405/07/05", "آزمون ترازنامه — فروش نقدی",
               [{"account_id": cash, "debit": 250_000_000, "credit": 0, "description": "دریافت"},
                {"account_id": revenue, "debit": 0, "credit": 250_000_000, "description": "فروش"}])
    _new_entry(client, accountant, "1405/07/06", "آزمون ترازنامه — واریز به بانک",
               [{"account_id": bank, "debit": 100_000_000, "credit": 0, "description": "واریز"},
                {"account_id": cash, "debit": 0, "credit": 100_000_000, "description": "برداشت"}])
    bs = _balance_sheet(client, accountant)
    assert bs["balanced"] is True, bs
    assert bs["total_assets"] == bs["total_liabilities"] + bs["total_equity"]


def test_income_statement_matches_trial_balance(client, accountant):
    """سود و زیان با تراز آزمایشی می‌خواند: جمع درآمد منهای هزینه = سود."""
    income = _income(client, accountant)
    trial = client.get("/api/reports/trial-balance", headers=accountant).json()
    # تراز آزمایشی مانده‌ی هر حساب را نشان می‌دهد: درآمد بستانکار، هزینه بدهکار
    revenue = sum(r["credit"] for r in trial["rows"] if r["code"].startswith("4"))
    expense = sum(r["debit"] for r in trial["rows"] if r["code"].startswith("5"))
    assert income["total_revenue"] == revenue, (income["total_revenue"], revenue)
    assert income["total_expense"] == expense, (income["total_expense"], expense)
    assert income["profit"] == revenue - expense


def test_cash_flow_transfer_between_banks(client, accountant):
    """انتقال بین دو حساب بانکی: نه درآمد، نه هزینه — و در جریان نقدی هم چیزی عوض نمی‌کند."""
    bank1 = account_id(client, accountant, "110201")
    bank2 = account_id(client, accountant, "110202")
    before = _cash(client, accountant, frm="1405/01/01", to="1405/12/29")
    income_before = _income(client, accountant, frm="1405/01/01", to="1405/12/29")

    _new_entry(client, accountant, "1405/08/01", "انتقال آزمایشی بین دو بانک",
               [{"account_id": bank2, "debit": 90_000_000, "credit": 0, "description": "واریز به بانک دوم"},
                {"account_id": bank1, "debit": 0, "credit": 90_000_000, "description": "برداشت از بانک اول"}])

    income_after = _income(client, accountant, frm="1405/01/01", to="1405/12/29")
    assert income_after["total_revenue"] == income_before["total_revenue"], "انتقال بانکی درآمد نیست"
    assert income_after["total_expense"] == income_before["total_expense"], "انتقال بانکی هزینه نیست"
    assert income_after["profit"] == income_before["profit"]

    after = _cash(client, accountant, frm="1405/01/01", to="1405/12/29")
    assert after["net"] == before["net"], "انتقال بانکی پول را کم و زیاد نمی‌کند"
    assert after["closing_cash"] == before["closing_cash"]


def test_cash_flow_ties_to_treasury(client, accountant):
    """خالص جریان نقدی = تغییر مانده‌ی صندوق و بانک."""
    cash = _cash(client, accountant)
    assert cash["matches_cash"] is True, cash
    assert cash["net"] == cash["closing_cash"] - cash["opening_cash"]
    assert cash["closing_cash"] == client.get("/api/reports/treasury", headers=accountant).json()["total"]


def test_statements_access_control(client, manager, employee, auditor):
    """مدیر و بازرس صورت‌ها را می‌بینند؛ کارمند نه. بستن سال فقط حسابدار."""
    assert client.get("/api/reports/balance-sheet", headers=manager).status_code == 200
    assert client.get("/api/reports/income-statement", headers=auditor).status_code == 200
    assert client.get("/api/reports/balance-sheet", headers=employee).status_code == 403
    assert client.post("/api/fiscal/close-year", headers=manager,
                       json={"jy": 1404}).status_code == 403
    assert client.post("/api/fiscal/close-year", headers=employee,
                       json={"jy": 1404}).status_code == 403


def test_fiscal_years_list(client, accountant):
    data = client.get("/api/fiscal/years", headers=accountant).json()
    years = {r["title"]: r for r in data["rows"]}
    assert "1405" in years
    assert years["1405"]["end"].endswith("12/29"), "سال مالی باید منتهی به ۲۹ اسفند باشد"


def test_close_year_and_reopen(client, accountant):
    """بستن سال: حساب‌های درآمد و هزینه صفر می‌شوند، سود به سود و زیان انباشته می‌رود."""
    _reopen_if_closed(client, accountant, 1404)
    # اول سال ۱۴۰۴ را با یک درآمد و یک هزینه‌ی کوچک می‌سازیم
    cash = account_id(client, accountant, "1101")
    revenue = account_id(client, accountant, "4101")
    expense = account_id(client, accountant, "5101")
    _new_entry(client, accountant, "1404/06/10", "درآمد سال ۱۴۰۴ برای آزمون بستن سال",
               [{"account_id": cash, "debit": 300_000_000, "credit": 0, "description": "دریافت"},
                {"account_id": revenue, "debit": 0, "credit": 300_000_000, "description": "درآمد"}])
    _new_entry(client, accountant, "1404/06/11", "هزینه سال ۱۴۰۴ برای آزمون بستن سال",
               [{"account_id": expense, "debit": 80_000_000, "credit": 0, "description": "هزینه"},
                {"account_id": cash, "debit": 0, "credit": 80_000_000, "description": "پرداخت"}])
    before = _balance_of(client, accountant, "3102", frm="1404/01/01", to="1404/12/29")  # پیش از بستن: صفر

    # سود سال ۱۴۰۴ را از صورت سود و زیان قبل از بستن می‌خوانیم (آزمون‌های دیگر هم در این سال سند دارند)
    before_income = _income(client, accountant, frm="1404/01/01", to="1404/12/29")
    expected = before_income["profit"]

    close = client.post("/api/fiscal/close-year", headers=accountant,
                        json={"jy": 1404, "note": "آزمون بستن سال"})
    assert close.status_code == 200, close.text
    assert close.json()["profit"] == expected, close.text

    # سند اختتامیه: درآمدها و هزینه‌ها صفر می‌شوند و سود (زیان) به ۳۱۰۲ می‌رود
    entries = client.get("/api/entries?q=اختتامیه", headers=accountant).json()
    closing_entry = [e for e in entries["rows"] if "اختتامیه" in e["description"]][0]
    detail = client.get(f"/api/entries/{closing_entry['id']}", headers=accountant).json()
    codes = _codes(detail)
    assert detail["balanced"] is True
    if expected > 0:
        assert codes["3102"][1] == expected, codes          # سود انباشته بستانکار
    elif expected < 0:
        assert codes["3102"][0] == -expected, codes         # زیان انباشته بدهکار

    # هیچ حساب درآمد یا هزینه‌ای در سال ۱۴۰۴ مانده ندارد
    left = [r for r in client.get("/api/reports/trial-balance?frm=1404/01/01&to=1404/12/29",
                                  headers=accountant).json()["rows"]
            if r["code"][:1] in ("4", "5")]
    assert not left, left

    # سال بسته شده و ثبت سند تازه در آن ممکن نیست
    status = {r["title"]: r for r in client.get("/api/fiscal/years", headers=accountant).json()["rows"]}
    assert status["1404"]["status"] == "closed"
    blocked = client.post("/api/entries", headers=accountant, json={
        "date": "1404/07/01", "description": "سند در سال بسته",
        "lines": [{"account_id": cash, "debit": 1000, "credit": 0, "description": "x"},
                  {"account_id": revenue, "debit": 0, "credit": 1000, "description": "y"}]})
    assert blocked.status_code >= 400
    assert "سال" in blocked.json()["detail"] or "بسته" in blocked.json()["detail"]

    assert _balance_of(client, accountant, "3102", frm="1404/01/01", to="1404/12/29") == -expected

    # ترازنامه‌ی پایان ۱۴۰۴ هم تراز می‌ماند
    bs = _balance_sheet(client, accountant, to="1404/12/29")
    assert bs["balanced"] is True, bs

    # بازگشایی: سند اختتامیه ابطال می‌شود و ثبت سند تازه دوباره ممکن است
    back = client.post("/api/fiscal/reopen-year", headers=accountant,
                       json={"jy": 1404, "note": "آزمون بازگشایی"})
    assert back.status_code == 200, back.text
    assert _balance_of(client, accountant, "3102", frm="1404/01/01", to="1404/12/29") == before, \
        "با بازگشایی سال، سود انباشته باید به حالت قبل برگردد"
    _new_entry(client, accountant, "1404/07/02", "سند بعد از بازگشایی",
               [{"account_id": cash, "debit": 5_000_000, "credit": 0, "description": "x"},
                {"account_id": revenue, "debit": 0, "credit": 5_000_000, "description": "y"}])
    # سال ۱۴۰۴ باز می‌ماند تا آزمون‌های بعدی هم بتوانند روی آن کار کنند



def test_close_year_blocked_by_draft(client, accountant):
    """سال با سند پیش‌نویس بسته نمی‌شود."""
    _reopen_if_closed(client, accountant, 1404)
    cash = account_id(client, accountant, "1101")
    revenue = account_id(client, accountant, "4101")
    draft = _new_entry(client, accountant, "1404/08/10", "پیش‌نویس سال ۱۴۰۴ برای آزمون",
                       [{"account_id": cash, "debit": 1_000_000, "credit": 0, "description": "x"},
                        {"account_id": revenue, "debit": 0, "credit": 1_000_000, "description": "y"}],
                       post=False)
    r = client.post("/api/fiscal/close-year", headers=accountant, json={"jy": 1404})
    assert r.status_code >= 400
    assert "پیش‌نویس" in r.json()["detail"]
    _ = draft
    # پاک‌سازی: این پیش‌نویس را ثبت نهایی می‌کنیم تا باقی آزمون‌ها تمیز بمانند
    assert client.post(f"/api/entries/{draft['id']}/post", headers=accountant).status_code == 200
    assert client.post("/api/fiscal/close-year", headers=accountant,
                       json={"jy": 1404, "note": "آزمون بستن با پیش‌نویس"}).status_code == 200
    assert client.post("/api/fiscal/reopen-year", headers=accountant,
                       json={"jy": 1404, "note": "پاک‌سازی آزمون"}).status_code == 200


def test_export_statement_excel(client, accountant):
    for url in ("/api/export/income-statement.xlsx", "/api/export/balance-sheet.xlsx",
                "/api/export/cash-flow.xlsx"):
        r = client.get(url, headers=accountant)
        assert r.status_code == 200, url
        assert r.content[:2] == b"PK", url
