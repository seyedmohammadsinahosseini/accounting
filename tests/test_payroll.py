"""آزمون‌های حقوق و دستمزد (فاز ۳).

سه آزمون از خود کارفرما اینجا هست:
۱) کارمند سبک و سنگین — حقوق کم مالیات ندارد، حقوق بالا با پله‌ها حساب می‌شود.
۲) سقف معافیت — هر قلم مالیات‌پذیر می‌تواند سقف معافیت داشته باشد.
۳) تغییر یک قلم مزایا — فقط همان قسمت عوض شود، نه همه‌چیز.

بقیه‌ی آزمون‌ها را خودم برای اطمینان از درستی محاسبه و سندها اضافه کرده‌ام.
هیچ عددی در کد برنامه ثابت نیست؛ همه از جدول «اعداد قانونی سال» خوانده می‌شود.
"""
from __future__ import annotations

from conftest import account_id

# اعداد پیش‌فرض سال ۱۴۰۴ که در برنامه هست (باید با قانون جاری تطبیق داده شود)
INS_EMPLOYEE_BP = 700          # ۷٪ سهم کارمند
INS_EMPLOYER_BP = 2300         # ۲۳٪ سهم کارفرما (۲۰٪ + ۳٪ بیمه بیکاری)
EXEMPT = 240_000_000           # پله‌ی معاف از مالیات (ماهانه)


def _items(client, headers) -> dict:
    rows = client.get("/api/payroll/items", headers=headers).json()["rows"]
    return {r["code"]: r for r in rows}


def _new_employee(client, headers, name, items, work_days=30, hire="1400/01/01") -> dict:
    r = client.post("/api/payroll/employees", headers=headers, json={
        "name": name, "position": "آزمون", "hire_date": hire, "contract_type": "permanent",
        "work_days": work_days, "items": items})
    assert r.status_code == 200, r.text
    return r.json()


def _run(client, headers, jy, jm, employees, overtime=None) -> dict:
    r = client.post("/api/payroll/runs", headers=headers, json={
        "jy": jy, "jm": jm, "employees": employees, "overtime": overtime or {}})
    assert r.status_code == 200, r.text
    return r.json()


def _row(run_detail, employee_id) -> dict:
    return next(r for r in run_detail["rows"] if r["employee_id"] == employee_id)


def _bracket_sum(taxable: int, brackets: list[dict]) -> int:
    """پله‌ها را از خود تنظیمات می‌خوانیم تا آزمون به عدد ثابتی گره نخورد."""
    tax, lower = 0, 0
    for b in brackets:
        up_to, rate = b["up_to"], b["rate_bp"]
        amount = (taxable - lower) if up_to is None else max(0, min(taxable, up_to) - lower)
        tax += amount * rate // 10000
        if up_to is None or taxable <= up_to:
            break
        lower = up_to
    return tax


# ---------------------------------------------------------------- آزمون نمونه ۱ کارفرما
def test_sample_1_light_and_heavy_employee(client, accountant):
    """کارفرما: «کارمند سبک و سنگین را با هم تست کن»."""
    items = _items(client, accountant)
    light = _new_employee(client, accountant, "کامران سبک (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 100_000_000},
        {"pay_item_id": items["HOUSING"]["id"], "amount_rial": 9_000_000},
        {"pay_item_id": items["FOOD"]["id"], "amount_rial": 22_000_000},
    ])
    heavy = _new_employee(client, accountant, "دلارا سنگین (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 400_000_000},
        {"pay_item_id": items["HOUSING"]["id"], "amount_rial": 9_000_000},
        {"pay_item_id": items["FOOD"]["id"], "amount_rial": 22_000_000},
    ])
    out = _run(client, accountant, 1404, 1, [light["id"], heavy["id"]])
    d = client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json()
    brackets = d["rules"]["brackets"]
    assert d["rules"]["insurance_employee_percent"] == INS_EMPLOYEE_BP / 100
    assert d["rules"]["insurance_employer_percent"] == INS_EMPLOYER_BP / 100

    # سبک: جمع ۱۳۱ میلیون — زیر سقف معافیت ⇒ مالیات صفر
    r1 = _row(d, light["id"])
    assert r1["gross"] == 131_000_000, r1
    assert r1["taxable_base"] == 131_000_000, r1
    assert r1["tax"] == 0, "حقوق زیر سقف معافیت نباید مالیات داشته باشد"
    assert r1["insurance_employee"] == 131_000_000 * INS_EMPLOYEE_BP // 10000
    assert r1["net"] == r1["gross"] - r1["insurance_employee"], r1

    # سنگین: جمع ۴۳۱ میلیون — سه پله‌ی اول درگیر می‌شود
    r2 = _row(d, heavy["id"])
    assert r2["gross"] == 431_000_000, r2
    expected_tax = (60_000_000 * 1000 + 80_000_000 * 1500 + 51_000_000 * 2000) // 10000
    assert expected_tax == 28_200_000
    assert r2["tax"] == expected_tax, r2
    assert r2["tax"] == _bracket_sum(r2["taxable_base"], brackets), "مالیات باید از همان پله‌های تنظیمات بیاید"
    assert r2["net"] == 431_000_000 - 431_000_000 * INS_EMPLOYEE_BP // 10000 - expected_tax

    # مالیات سبک و سنگین با هم = مالیات لیست؛ جمع‌ها هم با سطرها می‌خواند
    assert d["totals"]["tax"] == r1["tax"] + r2["tax"]
    assert d["totals"]["gross"] == r1["gross"] + r2["gross"]
    assert d["totals"]["net"] == r1["net"] + r2["net"]
    assert d["totals"]["employer_cost"] == (r1["gross"] + r1["insurance_employer"] + r1["unemployment"]
                                            + r2["gross"] + r2["insurance_employer"] + r2["unemployment"])


# ---------------------------------------------------------------- آزمون نمونه ۲ کارفرما
def test_sample_2_exemption_ceiling_on_one_item(client, accountant):
    """کارفرما: «برای یک قلم سقف معافیت بگذار و ببین مالیات درست حساب می‌شود»."""
    items = _items(client, accountant)
    food = items["FOOD"]
    emp = _new_employee(client, accountant, "لیلا سقف (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 300_000_000},
        {"pay_item_id": food["id"], "amount_rial": 22_000_000},
    ])
    try:
        # بدون سقف: مالیات روی ۳۲۲ میلیون
        out = _run(client, accountant, 1404, 2, [emp["id"]])
        without = _row(client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json(), emp["id"])
        assert without["taxable_base"] == 322_000_000

        # سقف معافیت ۲۰ میلیون برای خواربار: ۲ میلیون اضافه مالیات‌پذیر می‌شود
        r = client.patch(f"/api/payroll/items/{food['id']}", headers=accountant,
                         json={"tax_exempt_monthly_rial": 20_000_000})
        assert r.status_code == 200, r.text
        again = _run(client, accountant, 1404, 2, [emp["id"]])
        with_ceiling = _row(client.get(f"/api/payroll/runs/{again['id']}", headers=accountant).json(), emp["id"])
        assert with_ceiling["taxable_base"] == 302_000_000, with_ceiling
        assert with_ceiling["gross"] == without["gross"], "سقف معافیت نباید جمع حقوق را عوض کند"
        assert with_ceiling["tax"] < without["tax"]
        # شماتیک: مالیات ۳۰۲ میلیون = ۶۰م×۱۰٪ + ۲م×۱۵٪
        assert with_ceiling["tax"] == (60_000_000 * 1000 + 2_000_000 * 1500) // 10000
    finally:
        client.patch(f"/api/payroll/items/{food['id']}", headers=accountant,
                     json={"tax_exempt_monthly_rial": 0})


# ---------------------------------------------------------------- آزمون نمونه ۳ کارفرما
def test_sample_3_changing_one_item_touches_only_that_part(client, accountant):
    """کارفرما: «یک قلم مزایا را عوض کن، بقیه سر جای خودش بماند»."""
    items = _items(client, accountant)
    a = _new_employee(client, accountant, "مهدی تغییر (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 200_000_000},
        {"pay_item_id": items["HOUSING"]["id"], "amount_rial": 9_000_000},
    ])
    b = _new_employee(client, accountant, "نگین بی‌تغییر (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 200_000_000},
        {"pay_item_id": items["HOUSING"]["id"], "amount_rial": 9_000_000},
    ])
    before = _run(client, accountant, 1404, 3, [a["id"], b["id"]])
    before_detail = client.get(f"/api/payroll/runs/{before['id']}", headers=accountant).json()
    a_before, b_before = _row(before_detail, a["id"]), _row(before_detail, b["id"])

    # فقط حق مسکن کارمند اول از ۹ به ۲۰ میلیون می‌رود
    detail = client.get(f"/api/payroll/employees/{a['id']}", headers=accountant).json()
    new_items = [{"pay_item_id": it["pay_item_id"], "amount_rial": it["amount"]}
                 for it in detail["items"]]
    for it in new_items:
        if it["pay_item_id"] == items["HOUSING"]["id"]:
            it["amount_rial"] = 20_000_000
    r = client.patch(f"/api/payroll/employees/{a['id']}", headers=accountant, json={"items": new_items})
    assert r.status_code == 200, r.text

    after = _run(client, accountant, 1404, 3, [a["id"], b["id"]])
    d_after = client.get(f"/api/payroll/runs/{after['id']}", headers=accountant).json()
    a_after, b_after = _row(d_after, a["id"]), _row(d_after, b["id"])

    diff = 11_000_000                      # ۲۰ − ۹ میلیون
    assert a_after["gross"] == 220_000_000, a_after        # ۲۰۰ + ۲۰ میلیون
    assert after["number"] == before["number"], "شماره‌ی لیست با محاسبه‌ی مجدد عوض نشود"
    assert a_after["gross"] - a_before["gross"] == diff
    assert a_after["insurable_base"] - a_before["insurable_base"] == diff
    assert a_after["insurance_employee"] - a_before["insurance_employee"] == diff * INS_EMPLOYEE_BP // 10000
    assert a_after["taxable_base"] - a_before["taxable_base"] == diff
    # کارمند دوم دست‌نخورده
    assert b_after == b_before, "تغییر حکم یک کارمند نباید روی کارمند دیگر اثر بگذارد"
    # قلم‌های دیگر در فهرست کارمند اول دست‌نخورده مانده‌اند
    assert {e["code"]: e["amount"] for e in a_after["earnings"]}["BASE"] == 200_000_000


# ---------------------------------------------------------------- سند حقوق
def test_salary_entry_is_balanced_and_matches_the_run(client, accountant):
    items = _items(client, accountant)
    emp = _new_employee(client, accountant, "سند حقوق (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 400_000_000},
        {"pay_item_id": items["HOUSING"]["id"], "amount_rial": 9_000_000},
    ])
    out = _run(client, accountant, 1404, 4, [emp["id"]])
    d = client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json()
    row = d["rows"][0]
    assert client.post(f"/api/payroll/runs/{out['id']}/post", headers=accountant).status_code == 200

    rows = client.get("/api/entries", headers=accountant,
                      params={"frm": "1404/01/01", "to": "1404/12/29", "q": d["number"],
                              "limit": 500}).json()["rows"]
    entry = next(e for e in rows if e["source_ref"] == d["number"])
    assert entry["status"] == "posted"
    assert entry["balanced"] is True
    assert entry["debit_total"] == entry["credit_total"], "سند حقوق باید تراز باشد"

    by_code = {}
    for line in entry["lines"]:
        code = line["account_label"].split(" ")[0]
        by_code.setdefault(code, [0, 0])
        by_code[code][0] += line["debit"]
        by_code[code][1] += line["credit"]
    employer_total = row["insurance_employer"] + row["unemployment"]
    assert by_code["5102"][0] == row["gross"] + employer_total, "هزینه‌ی حقوق = جمع حقوق + بیمه‌ی کارفرما"
    assert by_code["2105"][1] == row["insurance_employee"] + employer_total, "بیمه‌ی پرداختنی"
    assert by_code["2106"][1] == row["tax"] and row["tax"] > 0
    assert by_code["2107"][1] == row["net"], "خالص حقوق پرداختنی"

    count_before = len(client.get("/api/entries", headers=accountant,
                                  params={"frm": "1404/01/01", "to": "1404/12/29"}).json()["rows"])
    client.post(f"/api/payroll/runs/{out['id']}/post", headers=accountant)   # دوباره
    count_after = len(client.get("/api/entries", headers=accountant,
                                 params={"frm": "1404/01/01", "to": "1404/12/29"}).json()["rows"])
    assert count_before == count_after, "ثبت دوباره‌ی همان لیست نباید سند تکراری بسازد"

    tb = client.get("/api/reports/trial-balance", headers=accountant,
                    params={"frm": "1404/01/01", "to": "1404/12/29"}).json()
    assert tb["balanced"] is True, "پس از ثبت حقوق هم تراز آزمایشی باید تراز بماند"


def test_salary_entry_date_is_never_in_the_future(client, accountant):
    """سند حقوق تاریخش پایان ماه است، ولی اگر ماه هنوز تمام نشده، امروز."""
    from app import dates as jd
    items = _items(client, accountant)
    emp = _new_employee(client, accountant, "تاریخ سند (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 90_000_000}])
    today = jd.jtoday()
    jy, jm, _ = jd.jalali_parts(today)

    past = _run(client, accountant, 1404, 7, [emp["id"]])
    d_past = client.get(f"/api/payroll/runs/{past['id']}", headers=accountant).json()
    client.post(f"/api/payroll/runs/{past['id']}/post", headers=accountant)
    rows = client.get("/api/entries", headers=accountant,
                      params={"frm": "1404/07/01", "to": "1404/07/30", "q": d_past["number"]}).json()["rows"]
    assert rows, "سند حقوق ماه گذشته باید در پایان همان ماه ثبت شود"
    assert rows[0]["date"] == "1404/07/30", rows[0]

    run_now = _run(client, accountant, jy, jm, [emp["id"]])
    d_now = client.get(f"/api/payroll/runs/{run_now['id']}", headers=accountant).json()
    client.post(f"/api/payroll/runs/{run_now['id']}/post", headers=accountant)
    rows_now = client.get("/api/entries", headers=accountant, params={"q": d_now["number"]}).json()["rows"]
    assert rows_now, "سند حقوق ماه جاری باید پیدا شود"
    assert rows_now[0]["date_iso"] <= today.isoformat(), "سند نباید تاریخ آینده بخورد"
    # و در تراز همین امروز دیده می‌شود
    tb = client.get("/api/reports/trial-balance", headers=accountant).json()
    assert any(r["code"] == "5102" for r in tb["rows"]), "هزینه‌ی حقوق باید در تراز امروز باشد"


def test_payment_entry_moves_cash_and_clears_salary_payable(client, accountant):
    items = _items(client, accountant)
    emp = _new_employee(client, accountant, "پرداخت حقوق (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 150_000_000},
    ])
    out = _run(client, accountant, 1404, 5, [emp["id"]])
    d = client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json()
    client.post(f"/api/payroll/runs/{out['id']}/post", headers=accountant)
    bank = account_id(client, accountant, "110201")
    r = client.post(f"/api/payroll/runs/{out['id']}/pay", headers=accountant,
                    json={"account_id": bank, "date": "1404/05/31"})
    assert r.status_code == 200, r.text
    rows = client.get("/api/entries", headers=accountant,
                      params={"frm": "1404/05/01", "to": "1404/05/31", "q": d["number"],
                              "limit": 500}).json()["rows"]
    pay_entry = [e for e in rows if "پرداخت حقوق" in e["description"]][0]
    assert pay_entry["balanced"] is True
    lines = {(l["account_label"].split(" ")[0]): (l["debit"], l["credit"]) for l in pay_entry["lines"]}
    assert lines["2107"][0] == d["totals"]["net"], "بدهی حقوق باید تسویه شود"
    assert lines["110201"][1] == d["totals"]["net"], "پول از بانک خارج شود"
    # پرداخت دوباره‌ی همان ماه
    again = client.post(f"/api/payroll/runs/{out['id']}/pay", headers=accountant, json={"account_id": bank})
    assert again.status_code >= 400, "نمی‌شود یک ماه را دو بار پرداخت کرد"


def test_cancel_returns_loan_installment_and_frees_the_month(client, accountant):
    items = _items(client, accountant)
    emp = _new_employee(client, accountant, "وام حقوق (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 120_000_000},
    ])
    loan = client.post("/api/payroll/loans", headers=accountant, json={
        "employee_id": emp["id"], "amount": 30_000_000, "kind": "loan", "installments": 3,
        "date": "1404/05/20", "description": "آزمون قسط"}).json()
    loans = client.get("/api/payroll/loans", headers=accountant,
                       params={"employee_id": emp["id"], "status": "all"}).json()["rows"]
    mine = next(l for l in loans if l["id"] == loan["id"])
    assert mine["monthly"] == 10_000_000, mine

    out = _run(client, accountant, 1404, 6, [emp["id"]])
    d = client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json()
    assert d["rows"][0]["loan"] == 10_000_000, "قسط در محاسبه دیده شود"
    # تا وقتی ثبت نشده، از وام کسر نمی‌شود
    still = client.get("/api/payroll/loans", headers=accountant,
                       params={"employee_id": emp["id"], "status": "all"}).json()["rows"]
    assert next(l for l in still if l["id"] == loan["id"])["paid"] == 0

    client.post(f"/api/payroll/runs/{out['id']}/post", headers=accountant)
    after_post = client.get("/api/payroll/loans", headers=accountant,
                            params={"employee_id": emp["id"], "status": "all"}).json()["rows"]
    row = next(l for l in after_post if l["id"] == loan["id"])
    assert row["paid"] == 10_000_000 and row["remaining"] == 20_000_000, row

    # همان ماه دوباره محاسبه نمی‌شود تا برگردانده شود
    blocked = client.post("/api/payroll/runs", headers=accountant,
                          json={"jy": 1404, "jm": 6, "employees": [emp["id"]]})
    assert blocked.status_code >= 400
    assert client.post(f"/api/payroll/runs/{out['id']}/cancel", headers=accountant,
                       json={"reason": "آزمون"}).status_code == 200
    back = client.get("/api/payroll/loans", headers=accountant,
                      params={"employee_id": emp["id"], "status": "all"}).json()["rows"]
    row = next(l for l in back if l["id"] == loan["id"])
    assert row["paid"] == 0 and row["remaining"] == 30_000_000, "با ابطال لیست، قسط باید برگردد"
    fresh = client.post("/api/payroll/runs", headers=accountant,
                        json={"jy": 1404, "jm": 6, "employees": [emp["id"]]})
    assert fresh.status_code == 200, "بعد از ابطال باید بشود همان ماه را از نو حساب کرد"
    assert fresh.json()["id"] == out["id"]


# ---------------------------------------------------------------- انعطاف‌پذیری اعداد
def test_numbers_come_from_settings_and_are_not_hardcoded(client, accountant):
    """اگر فردا قانون عوض شد، فقط همین اعداد در برنامه عوض می‌شود — نه کد."""
    items = _items(client, accountant)
    emp = _new_employee(client, accountant, "سال تازه (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 200_000_000},
    ])

    r = client.post("/api/payroll/settings", headers=accountant, json={
        "year": 1406, "employee_percent": 8, "employer_percent": 25, "unemployment_percent": 3,
        "standard_monthly_hours": 220, "overtime_coefficient": 1.4,
        "source_note": "آزمون: اعداد ساختگی سال ۱۴۰۶",
        "brackets": [{"up_to": 100_000_000, "rate_percent": 0}, {"up_to": None, "rate_percent": 10}]})
    assert r.status_code == 200, r.text
    out = _run(client, accountant, 1406, 1, [emp["id"]])
    d = client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json()
    row = d["rows"][0]
    assert d["rules"]["insurance_employee_percent"] == 8
    assert d["rules"]["insurance_employer_percent"] == 25
    assert d["rules"]["settings_year"] == 1406 and d["rules"]["fell_back"] is False
    assert row["insurance_employee"] == 200_000_000 * 8 // 100
    assert row["tax"] == (200_000_000 - 100_000_000) * 10 // 100, row   # ۱۰ میلیون
    assert d["rules"]["confirmed"] is False, "تا حسابدار تأیید نکند، اعداد تأییدنشده‌اند"

    assert client.post("/api/payroll/settings/1406/confirm", headers=accountant,
                       json={"note": "تأیید آزمون"}).status_code == 200
    d2 = client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json()
    assert d2["rules"]["confirmed"] is True

    # تغییر اعداد ⇒ تأیید باطل می‌شود و حساب با اعداد تازه انجام می‌شود
    client.post("/api/payroll/settings", headers=accountant, json={
        "year": 1406, "employee_percent": 5, "employer_percent": 25, "unemployment_percent": 3,
        "brackets": [{"up_to": None, "rate_percent": 0}], "source_note": "تغییر آزمون"})
    out2 = _run(client, accountant, 1406, 2, [emp["id"]])
    d3 = client.get(f"/api/payroll/runs/{out2['id']}", headers=accountant).json()
    assert d3["rows"][0]["insurance_employee"] == 200_000_000 * 5 // 100
    assert d3["rows"][0]["tax"] == 0
    assert d3["rules"]["confirmed"] is False


def test_overtime_uses_base_salary_and_the_year_coefficient(client, accountant):
    items = _items(client, accountant)
    emp = _new_employee(client, accountant, "اضافه‌کاری (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 120_000_000},
    ])
    client.post("/api/payroll/settings", headers=accountant, json={
        "year": 1407, "employee_percent": 7, "employer_percent": 23, "unemployment_percent": 3,
        "standard_monthly_hours": 200, "overtime_coefficient": 2,
        "brackets": [{"up_to": None, "rate_percent": 0}], "source_note": "آزمون ضریب"})
    out = _run(client, accountant, 1407, 1, [emp["id"]], overtime={str(emp["id"]): 10})
    row = _row(client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json(), emp["id"])
    hourly = 120_000_000 // 200                                   # ۶۰۰٬۰۰۰ ریال
    assert row["overtime_hours"] == 10
    assert row["gross"] == 120_000_000 + hourly * 10 * 2
    assert row["insurable_base"] == row["gross"]                  # اضافه‌کاری مشمول بیمه است

    # کارمندی که حقوق پایه ندارد، برای اضافه‌کاری باید پیام روشن بگیرد
    empty = _new_employee(client, accountant, "بی‌پایه (آزمون)", [
        {"pay_item_id": items["HOUSING"]["id"], "amount_rial": 9_000_000}])
    bad = client.post("/api/payroll/runs", headers=accountant,
                      json={"jy": 1407, "jm": 2, "employees": [empty["id"]],
                            "overtime": {str(empty["id"]): 5}})
    assert bad.status_code >= 400 and "پایه" in bad.text


# ---------------------------------------------------------------- کارکنان و اقلام
def test_new_pay_item_can_be_added_from_the_app(client, accountant):
    """قلم تازه (مثلاً حق مأموریت) بدون دست‌زدن به کد برنامه."""
    r = client.post("/api/payroll/items", headers=accountant, json={
        "title": "حق مأموریت (آزمون)", "kind": "earning", "insurable": False, "taxable": True,
        "exempt_ceiling": 5_000_000, "note": "قلم ساختگی آزمون"})
    assert r.status_code == 200, r.text
    items = _items(client, accountant)
    new = items[r.json()["code"]]
    assert new["insurable"] is False and new["taxable"] is True and new["exempt_ceiling"] == 5_000_000
    emp = _new_employee(client, accountant, "مأموریت (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 100_000_000},
        {"pay_item_id": new["id"], "amount_rial": 12_000_000}])
    out = _run(client, accountant, 1404, 8, [emp["id"]])
    row = _row(client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json(), emp["id"])
    assert row["gross"] == 112_000_000
    assert row["insurable_base"] == 100_000_000, "قلم غیرمشمول نباید در پایه‌ی بیمه بیاید"
    assert row["taxable_base"] == 100_000_000 + (12_000_000 - 5_000_000), "سقف معافیت همان قلم"


def test_employee_change_and_deactivation(client, accountant):
    items = _items(client, accountant)
    emp = _new_employee(client, accountant, "تغییر وضعیت (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 100_000_000}], work_days=26)
    detail = client.get(f"/api/payroll/employees/{emp['id']}", headers=accountant).json()
    assert detail["work_days"] == 26
    r = client.patch(f"/api/payroll/employees/{emp['id']}", headers=accountant,
                     json={"work_days": 31, "position": "سرپرست فروش"})
    assert r.status_code == 200 and r.json()["is_active"] is True
    out = _run(client, accountant, 1404, 9, [emp["id"]])
    assert _row(client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json(),
                emp["id"])["work_days"] == 31
    # خروج از لیست حقوق
    client.patch(f"/api/payroll/employees/{emp['id']}", headers=accountant, json={"is_active": False})
    gone = client.post("/api/payroll/runs", headers=accountant,
                       json={"jy": 1404, "jm": 10, "employees": [emp["id"]]})
    assert gone.status_code >= 400, "کارمند غیرفعال نباید در لیست حقوق بیاید"


def test_1405_numbers_are_used_and_exemption_boundary_is_exact(client, accountant):
    """اعداد ۱۴۰۵ (سقف معافیت ۴۰۰ میلیون ریال) و مرز دقیق معافیت."""
    items = _items(client, accountant)
    on_boundary = _new_employee(client, accountant, "مرز معافیت ۱۴۰۵ (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 400_000_000}])
    above = _new_employee(client, accountant, "بالای معافیت ۱۴۰۵ (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 450_000_000}])
    out = _run(client, accountant, 1405, 3, [on_boundary["id"], above["id"]])
    d = client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json()
    assert d["rules"]["settings_year"] == 1405, "برای سال ۱۴۰۵ باید اعداد همان سال به کار برود"
    assert d["rules"]["fell_back"] is False
    r1, r2 = _row(d, on_boundary["id"]), _row(d, above["id"])
    assert r1["tax"] == 0, "درست روی سقف معافیت، مالیات صفر است"
    assert r2["tax"] == 5_000_000, "مازاد بر سقف با نرخ پله‌ی اول (۱۰٪) حساب شود"
    assert d["rules"]["brackets"][0]["up_to"] == 400_000_000
    assert d["rules"]["confirmed"] is False, "اعداد ۱۴۰۵ تا تأیید حسابدار، تأییدنشده است"


# ---------------------------------------------------------------- دسترسی‌ها و خروجی‌ها
def test_payroll_is_confidential(client, accountant, employee, manager, auditor):
    assert client.get("/api/payroll/employees", headers=accountant).status_code == 200
    assert client.get("/api/payroll/runs", headers=manager).status_code == 200       # مدیر فقط می‌بیند
    assert client.get("/api/payroll/employees", headers=employee).status_code == 403
    assert client.get("/api/payroll/runs", headers=employee).status_code == 403
    assert client.post("/api/payroll/runs", headers=manager,
                       json={"jy": 1404, "jm": 11}).status_code == 403              # ثبت کار حسابدار است
    assert client.get("/api/payroll/employees", headers=auditor).status_code == 403
    assert client.post("/api/payroll/items", headers=manager,
                       json={"title": "قلم مدیر"}).status_code == 403


def test_payslip_and_legal_exports(client, accountant, employee):
    items = _items(client, accountant)
    emp = _new_employee(client, accountant, "فیش حقوق (آزمون)", [
        {"pay_item_id": items["BASE"]["id"], "amount_rial": 150_000_000},
        {"pay_item_id": items["HOUSING"]["id"], "amount_rial": 9_000_000}])
    out = _run(client, accountant, 1404, 12, [emp["id"]])
    d = client.get(f"/api/payroll/runs/{out['id']}", headers=accountant).json()
    row = d["rows"][0]
    slip = client.get(f"/api/payroll/runs/{out['id']}/payslip/{emp['id']}", headers=accountant).json()
    assert slip["gross"] == row["gross"] and slip["net"] == row["net"]
    assert slip["employee"]["name"] == "فیش حقوق (آزمون)"
    assert sum(e["amount"] for e in slip["earnings"]) == slip["gross"]
    assert slip["net_fa"] and slip["net_fa"].replace(",", "").replace("٬", "").replace("،", "").isdigit()

    for path in (f"/api/export/payroll.xlsx?run_id={out['id']}",
                 f"/api/export/payroll-insurance.xlsx?run_id={out['id']}",
                 f"/api/export/payroll-tax.xlsx?run_id={out['id']}",
                 "/api/export/tax-brackets.xlsx"):
        r = client.get(path, headers=accountant)
        assert r.status_code == 200, (path, r.text)
        assert r.content[:2] == b"PK", path          # فایل اکسل واقعی
    assert client.get(f"/api/export/payroll.xlsx?run_id={out['id']}",
                      headers=employee).status_code == 403
