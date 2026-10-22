"""آزمون‌های دارایی ثابت (فاز ۴).

آزمون‌هایی که خود کارفرما خواسته بود:
۱) خرید دارایی سند می‌خورد و پول از حساب درست کم می‌شود.
۲) استهلاک ماهانه حساب می‌شود، ثبت می‌شود و ارزش دفتری را کم می‌کند — بدون تکرار.
۳) فروش دارایی سود یا زیانش خودکار حساب می‌شود و سندش تراز است.

هیچ نرخی در کد ثابت نیست: نرخ و روش از «گروه دارایی» خوانده می‌شود و گروه هم قابل تغییر است.
"""
from __future__ import annotations

from conftest import account_id


def _entry_id(detail: dict, kind: str) -> int:
    """شناسه‌ی سند خرید یا سند فروش/کنارگذاری همان دارایی."""
    return next(e["id"] for e in detail["entries"] if kind in e["kind"])


def _codes(entry: dict) -> dict:
    """کد حساب ← (بدهکار، بستانکار) از سند؛ برچسب حساب‌ها «کد — نام» است."""
    return {l["account_label"].split(" — ")[0]: (l["debit"], l["credit"]) for l in entry["lines"]}

COST = 120_000_000          # بهای خرید: ۱۲۰ میلیون ریال
EXPECTED_M1 = 3_300_000     # ۳۳٪ سالانه روی مانده، ماه اول ≈ ۳٬۳۰۰٬۰۰۰ ریال


def _categories(client, headers) -> dict:
    rows = client.get("/api/assets/categories", headers=headers).json()["rows"]
    return {r["code"]: r for r in rows}


def _new_asset(client, headers, title="دستگاه آزمون", cat="IT", cost=COST,
               purchase="1405/01/15", in_service="1405/01/15", counter=None, **extra) -> dict:
    cat_id = _categories(client, headers)[cat]["id"]
    body = {"title": title, "category_id": cat_id, "purchase_date": purchase,
            "in_service_date": in_service, "cost": cost}
    if counter is not None:
        body["counter_account_id"] = counter
    body.update(extra)
    r = client.post("/api/assets", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_asset_purchase_posts_entry(client, accountant):
    """خرید دارایی: سند ثبت می‌شود، دارایی بدهکار و صندوق بستانکار."""
    cash = account_id(client, accountant, "1101")
    before = client.get("/api/reports/treasury", headers=accountant).json()
    asset = _new_asset(client, accountant, "رایانه آزمون خرید", cost=COST, counter=cash)

    detail = client.get(f"/api/assets/{asset['id']}", headers=accountant).json()
    assert detail["asset"]["cost"] == COST
    assert detail["entries"], "سند خرید ثبت نشده است"
    entry = client.get(f"/api/entries/{detail['entries'][0]['id']}", headers=accountant).json()
    lines = _codes(entry)
    assert lines["110901"] == (COST, 0), lines
    assert lines["1101"] == (0, COST), lines
    assert entry["status"] == "posted"

    after = client.get("/api/reports/treasury", headers=accountant).json()
    assert after["total"] == before["total"] - COST


def test_depreciation_monthly(client, accountant):
    """استهلاک ماهانه: حساب می‌شود، ثبت می‌شود و ارزش دفتری پایین می‌آید."""
    asset = _new_asset(client, accountant, "دارایی استهلاک آزمون", cost=COST,
                       purchase="1405/07/01", in_service="1405/07/01")
    r = client.post("/api/depreciation/runs", headers=accountant, json={"jy": 1405, "jm": 7})
    assert r.status_code == 200, r.text
    run = r.json()
    mine = [x for x in run["rows"] if x["asset_id"] == asset["id"]]
    assert mine, run
    assert mine[0]["amount"] == EXPECTED_M1, mine

    # تا وقتی سند ثبت نشده، استهلاک انباشته‌ی دارایی دست‌نخورده است
    untouched = client.get(f"/api/assets/{asset['id']}", headers=accountant).json()["asset"]
    assert untouched["accumulated"] == 0

    # ثبت سند استهلاک
    posted = client.post(f"/api/depreciation/runs/{run['id']}/post", headers=accountant)
    assert posted.status_code == 200, posted.text
    entry_id = client.get(f"/api/depreciation/runs/{run['id']}", headers=accountant).json()["entry_id"]
    entry = client.get(f"/api/entries/{entry_id}", headers=accountant).json()
    kinds = _codes(entry)
    # سند استهلاک یک ماه برای همه‌ی دارایی‌ها یک‌جا خورده می‌شود؛ سهم همین دارایی را جدا چک می‌کنیم
    assert kinds["111001"][0] == 0 and kinds["111001"][1] >= EXPECTED_M1, kinds    # استهلاک انباشته بستانکار
    assert "استهلاک" in entry["description"]
    assert entry["balanced"] is True

    detail = client.get(f"/api/assets/{asset['id']}", headers=accountant).json()["asset"]
    assert detail["accumulated"] == EXPECTED_M1
    assert detail["book_value"] == COST - EXPECTED_M1

    # ماه بعد: روی مانده‌ی باقی‌مانده حساب می‌شود (نزولی)، نه دوباره روی کل بها
    r2 = client.post("/api/depreciation/runs", headers=accountant, json={"jy": 1405, "jm": 8})
    assert r2.status_code == 200, r2.text
    amount2 = next(x["amount"] for x in r2.json()["rows"] if x["asset_id"] == asset["id"])
    assert amount2 < EXPECTED_M1, "روش نزولی: ماه بعد باید کمتر باشد"
    assert amount2 == round((COST - EXPECTED_M1) * 0.33 / 12)


def test_depreciation_duplicate_month_rejected(client, accountant):
    """یک ماه دوبار سند استهلاک نمی‌خورد؛ تا ثبت نشده می‌شود از نو حسابش کرد، بعد از ثبت نه."""
    first = client.post("/api/depreciation/runs", headers=accountant, json={"jy": 1405, "jm": 9})
    assert first.status_code == 200, first.text
    # پیش از ثبت: دوباره حساب کردن مجاز است (اصلاح محاسبه)
    recalc = client.post("/api/depreciation/runs", headers=accountant, json={"jy": 1405, "jm": 9})
    assert recalc.status_code == 200, recalc.text
    assert client.post(f"/api/depreciation/runs/{first.json()['id']}/post",
                       headers=accountant).status_code == 200
    again = client.post("/api/depreciation/runs", headers=accountant, json={"jy": 1405, "jm": 9})
    assert again.status_code >= 400, "بعد از ثبت سند، استهلاک همان ماه دوباره حساب نمی‌شود"


def test_depreciation_cancel_rolls_back(client, accountant):
    """ابطال سند استهلاک، استهلاک انباشته را برمی‌گرداند."""
    asset = _new_asset(client, accountant, "دارایی ابطال آزمون", cost=60_000_000,
                       purchase="1405/05/01", in_service="1405/05/01")
    run = client.post("/api/depreciation/runs", headers=accountant,
                      json={"jy": 1405, "jm": 10}).json()
    client.post(f"/api/depreciation/runs/{run['id']}/post", headers=accountant)
    acc = client.get(f"/api/assets/{asset['id']}", headers=accountant).json()["asset"]
    assert acc["accumulated"] > 0

    ok = client.post(f"/api/depreciation/runs/{run['id']}/cancel", headers=accountant,
                     json={"reason": "اشتباه بود"})
    assert ok.status_code == 200, ok.text
    back = client.get(f"/api/assets/{asset['id']}", headers=accountant).json()["asset"]
    assert back["accumulated"] == 0, back


def _depreciate(client, headers, jy, jm):
    run = client.post("/api/depreciation/runs", headers=headers, json={"jy": jy, "jm": jm})
    assert run.status_code == 200, run.text
    posted = client.post(f"/api/depreciation/runs/{run.json()['id']}/post", headers=headers)
    assert posted.status_code == 200, posted.text
    return run.json()


def test_asset_disposal_gain_and_loss(client, accountant):
    """فروش دارایی: سود (زیان) خودکار حساب می‌شود و سند تراز است."""
    cash = account_id(client, accountant, "1101")
    # الف) فروش گران‌تر از ارزش دفتری ⇒ سود. اول یک ماه استهلاک می‌گیریم تا استهلاک انباشته هم داشته باشد.
    asset = _new_asset(client, accountant, "دارایی فروش سود", cost=100_000_000,
                       purchase="1405/01/01", in_service="1405/01/01", counter=cash)
    _depreciate(client, accountant, 1405, 11)      # ماهی که آزمون‌های دیگر با آن کار ندارند
    before = client.get(f"/api/assets/{asset['id']}", headers=accountant).json()["asset"]
    assert before["accumulated"] > 0
    book = before["book_value"]
    price = book + 20_000_000
    r = client.post(f"/api/assets/{asset['id']}/dispose", headers=accountant,
                    json={"date": "1405/06/10", "amount": price, "account_id": cash,
                          "reason": "فروش به کارمند"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sold"

    detail = client.get(f"/api/assets/{asset['id']}", headers=accountant).json()
    entry = client.get(f"/api/entries/{_entry_id(detail, 'فروش')}", headers=accountant).json()
    lines = _codes(entry)
    assert lines["110901"] == (0, 100_000_000), lines      # دارایی از دفاتر خارج می‌شود
    assert lines["111001"] == (before["accumulated"], 0), lines        # استهلاک انباشته بدهکار و بسته می‌شود
    assert lines["1101"] == (price, 0), lines              # پول دریافت شد
    assert lines["4106"][1] == 20_000_000, lines           # سود فروش دارایی
    assert entry["balanced"] is True

    # ب) فروش ارزان‌تر ⇒ زیان
    cheap = _new_asset(client, accountant, "دارایی فروش زیان", cost=40_000_000,
                       purchase="1405/01/01", in_service="1405/01/01", counter=cash)
    _depreciate(client, accountant, 1405, 12)
    book2 = client.get(f"/api/assets/{cheap['id']}", headers=accountant).json()["asset"]["book_value"]
    price2 = book2 - 5_000_000
    assert client.post(f"/api/assets/{cheap['id']}/dispose", headers=accountant,
                       json={"date": "1405/06/11", "amount": price2, "account_id": cash,
                             "reason": "فروش فوری"}).status_code == 200
    d2 = client.get(f"/api/assets/{cheap['id']}", headers=accountant).json()
    e2 = client.get(f"/api/entries/{_entry_id(d2, 'فروش')}", headers=accountant).json()
    l2 = _codes(e2)
    assert l2["5108"][0] == 5_000_000, l2                  # زیان فروش دارایی
    assert e2["balanced"] is True


def test_disposed_asset_stops_depreciating(client, accountant):
    """دارایی فروخته‌شده دیگر استهلاک نمی‌گیرد."""
    cash = account_id(client, accountant, "1101")
    asset = _new_asset(client, accountant, "دارایی متوقف‌شده", cost=30_000_000,
                       purchase="1405/07/01", in_service="1405/07/01", counter=cash)
    client.post(f"/api/assets/{asset['id']}/dispose", headers=accountant,
                json={"date": "1405/07/20", "amount": 0, "account_id": None,
                      "reason": "از رده خارج شد"})
    run = client.post("/api/depreciation/runs", headers=accountant,
                      json={"jy": 1406, "jm": 1})       # ماه آزاد: نه در داده‌ی نمونه است نه در آزمون‌های دیگر
    assert run.status_code == 200, run.text
    assert all(x["asset_id"] != asset["id"] for x in run.json()["rows"])


def test_asset_access_control(client, accountant, manager, employee, auditor):
    """کارمند دسترسی ندارد؛ مدیر و بازرس فقط می‌بینند و دست نمی‌زنند."""
    assert client.get("/api/assets", headers=employee).status_code == 403
    assert client.get("/api/assets", headers=auditor).status_code == 200
    assert client.get("/api/assets", headers=manager).status_code == 200
    cat_id = _categories(client, accountant)["IT"]["id"]
    body = {"title": "بدون اجازه", "category_id": cat_id, "purchase_date": "1405/01/01",
            "cost": 1000}
    assert client.post("/api/assets", headers=manager, json=body).status_code == 403
    assert client.post("/api/assets", headers=auditor, json=body).status_code == 403
    assert client.post("/api/assets", headers=employee, json=body).status_code == 403
    assert client.get("/api/depreciation/runs", headers=employee).status_code == 403


def test_asset_excel_export(client, accountant):
    r = client.get("/api/export/assets.xlsx", headers=accountant)
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
