"""آزمون‌های گردش بازبینی اختتامیه، افتتاحیه و مرز مانده‌ی سال مالی."""
from __future__ import annotations

from conftest import account_id


def _post(client, headers, date, description, lines):
    created = client.post("/api/entries", headers=headers,
                          json={"date": date, "description": description, "lines": lines})
    assert created.status_code == 200, created.text
    posted = client.post(f"/api/entries/{created.json()['id']}/post", headers=headers)
    assert posted.status_code == 200, posted.text
    return posted.json()


def _workflow_close(client, accountant, jy, note=""):
    pre = client.post("/api/fiscal/close-year/precheck", headers=accountant,
                      json={"jy": jy})
    assert pre.status_code == 200, pre.text
    assert pre.json()["ready"], pre.json()["errors"]
    draft = client.post("/api/fiscal/close-year/draft", headers=accountant,
                        json={"jy": jy, "fingerprint": pre.json()["fingerprint"]})
    assert draft.status_code == 200, draft.text
    entry = draft.json()["entry"]
    final = client.post("/api/fiscal/close-year", headers=accountant, json={
        "jy": jy, "note": note, "fingerprint": pre.json()["fingerprint"],
        "draft_entry_id": entry["id"] if entry else None,
    })
    assert final.status_code == 200, final.text
    return pre.json(), draft.json(), final.json()


def _line_for(rows, code):
    return next(row for row in rows if row["code"] == code)


def test_explicit_fiscal_year_creation_is_separate_and_handles_leap_year(client, accountant):
    leap = client.post("/api/fiscal/years", headers=accountant, json={"jy": 1399})
    assert leap.status_code == 200, leap.text
    assert leap.json()["start"] == "1399/01/01"
    assert leap.json()["end"] == "1399/12/30"
    assert leap.json()["status"] == "open"
    assert leap.json()["opening_status"] == "missing"
    assert [(period["jm"], period["status"]) for period in leap.json()["periods"]] == [
        (month, "open") for month in range(1, 13)]

    common = client.post("/api/fiscal/years", headers=accountant, json={"jy": 1400})
    assert common.status_code == 200, common.text
    assert common.json()["start"] == "1400/01/01"
    assert common.json()["end"] == "1400/12/29"
    # ایجاد سال جدید پیش از بستن قبلی مجاز است؛ هر دو صرفاً باز می‌مانند.
    years = {row["title"]: row for row in client.get("/api/fiscal/years", headers=accountant).json()["rows"]}
    assert years["1399"]["status"] == years["1400"]["status"] == "open"
    assert years["1399"]["opening_status"] == years["1400"]["opening_status"] == "missing"
    duplicate = client.post("/api/fiscal/years", headers=accountant, json={"jy": 1399})
    assert duplicate.status_code == 400
    assert "از قبل" in duplicate.json()["detail"]


def test_closing_review_and_opening_transfer_reconcile(client, accountant):
    """اختتامیه پس از بازبینی، نتیجه‌ی تاریخی را نگه می‌دارد؛ افتتاحیه حساب دائمی را دقیقاً منتقل می‌کند."""
    jy = 1390
    next_jy = jy + 1
    cash = account_id(client, accountant, "1101")
    bank = account_id(client, accountant, "110201")
    revenue = account_id(client, accountant, "4101")
    amount = 12_345_678
    # سال مقصد را پیش از اختتامیه‌ی مبدأ صریحاً ایجاد می‌کنیم؛ افتتاحیه مستقل و غایب می‌ماند.
    created_year = client.post("/api/fiscal/years", headers=accountant, json={"jy": next_jy})
    assert created_year.status_code == 200, created_year.text
    assert created_year.json()["status"] == "open"
    assert created_year.json()["opening_status"] == "missing"
    assert len(created_year.json()["periods"]) == 12
    # ثبت عملیات عادی پیش از افتتاحیه و حتی پیش از بستن سال قبل مجاز است.
    early_operation = _post(client, accountant, f"{next_jy}/02/01", "انتقال نقدی پیش از افتتاحیه",
                            [{"account_id": cash, "debit": 500, "credit": 0, "description": "ورود"},
                             {"account_id": bank, "debit": 0, "credit": 500, "description": "انتقال"}])
    assert early_operation["status"] == "posted"
    cash_before = client.get("/api/reports/treasury", headers=accountant).json()["total"]
    _post(client, accountant, f"{jy}/02/10", "درآمد قابل‌ردیابی سال ۱۳۹۰",
          [{"account_id": cash, "debit": amount, "credit": 0, "description": "دریافت"},
           {"account_id": revenue, "debit": 0, "credit": amount, "description": "درآمد"}])

    pre = client.post("/api/fiscal/close-year/precheck", headers=accountant, json={"jy": jy})
    assert pre.status_code == 200, pre.text
    assert pre.json()["calculated_at"]
    assert any(warning["code"] == "periods_open" for warning in pre.json()["warnings"])
    before_draft = client.get(f"/api/reports/trial-balance?frm={jy}/01/01&to={jy}/12/29",
                              headers=accountant).json()
    draft = client.post("/api/fiscal/close-year/draft", headers=accountant,
                        json={"jy": jy, "fingerprint": pre.json()["fingerprint"]})
    assert draft.status_code == 200, draft.text
    assert draft.json()["entry"]["status"] == "draft"
    # سندهای اختصاصی مالی را نمی‌توان از endpoint عمومی، بدون بازبینی fingerprint ثبت کرد.
    direct_post = client.post(f"/api/entries/{draft.json()['entry']['id']}/post", headers=accountant)
    assert direct_post.status_code == 400
    assert "مسیر تأیید" in direct_post.json()["detail"]
    after_draft = client.get(f"/api/reports/trial-balance?frm={jy}/01/01&to={jy}/12/29",
                             headers=accountant).json()
    assert after_draft["rows"] == before_draft["rows"], "پیش‌نویس نباید دفتر را تغییر دهد"
    closing_entry = draft.json()["entry"]
    assert closing_entry["balanced"] is True
    close = client.post("/api/fiscal/close-year", headers=accountant, json={
        "jy": jy, "note": "آزمون چرخه‌ی بازبینی", "fingerprint": pre.json()["fingerprint"],
        "draft_entry_id": closing_entry["id"],
    })
    assert close.status_code == 200, close.text
    assert close.json()["profit"] == amount
    direct_close_reverse = client.post(f"/api/entries/{closing_entry['id']}/reverse",
                                      headers=accountant, json={"reason": "تلاش ابطال مستقیم اختتامیه"})
    assert direct_close_reverse.status_code == 400
    years_after_close = {row["title"]: row for row in
                         client.get("/api/fiscal/years", headers=accountant).json()["rows"]}
    assert years_after_close[str(jy)]["status"] == "closed"
    assert all(period["status"] == "closed" for period in years_after_close[str(jy)]["periods"])
    audit_rows = client.get("/api/audit?limit=500", headers=accountant).json()["rows"]
    closed_month_audit = [row for row in audit_rows if row["action"] == "period.close"
                          and row["entity"] == "period" and row["entity_id"].startswith(f"{jy}-")]
    assert len(closed_month_audit) == 12

    historical = client.get(f"/api/reports/income-statement?frm={jy}/01/01&to={jy}/12/29",
                            headers=accountant).json()
    assert historical["total_revenue"] == amount
    assert historical["profit"] == amount
    year_tb = client.get(f"/api/reports/trial-balance?frm={jy}/01/01&to={jy}/12/29",
                         headers=accountant).json()["rows"]
    assert not any(row["code"].startswith(("4", "5")) for row in year_tb)

    opening_pre = client.post("/api/fiscal/opening/preview", headers=accountant, json={
        "destination_jy": next_jy, "source_jy": jy, "mode": "transfer", "lines": [],
    })
    assert opening_pre.status_code == 200, opening_pre.text
    assert opening_pre.json()["ready"], opening_pre.json()["errors"]
    assert any(warning["code"] == "destination_activity_before_opening"
               for warning in opening_pre.json()["warnings"])
    assert opening_pre.json()["totals"]["debit"] == opening_pre.json()["totals"]["credit"]
    assert any(row["code"] == "1101" and row["debit"] == amount for row in opening_pre.json()["rows"])
    opening_draft = client.post("/api/fiscal/opening/draft", headers=accountant, json={
        "destination_jy": next_jy, "source_jy": jy, "mode": "transfer",
        "fingerprint": opening_pre.json()["fingerprint"], "lines": [],
    })
    assert opening_draft.status_code == 200, opening_draft.text
    entry = opening_draft.json()["entry"]
    assert entry["source"] == "opening" and entry["source_ref"] == f"{jy}->{next_jy}"
    assert entry["balanced"] is True
    before_opening_tb = client.get(
        f"/api/reports/trial-balance?frm={next_jy}/01/01&to={next_jy}/12/29",
        headers=accountant).json()["rows"]
    assert _line_for(before_opening_tb, "1101")["debit"] == 500
    assert _line_for(before_opening_tb, "110201")["credit"] == 500

    opening_body = {"destination_jy": next_jy, "source_jy": jy, "mode": "transfer",
                    "fingerprint": opening_pre.json()["fingerprint"],
                    "draft_entry_id": entry["id"], "lines": []}
    posted = client.post("/api/fiscal/opening/post", headers=accountant, json=opening_body)
    assert posted.status_code == 200, posted.text
    assert posted.json()["reconciliation"]["ready"] is True
    assert posted.json()["reconciliation"]["unmatched_count"] == 0
    tb = client.get(f"/api/reports/trial-balance?frm={next_jy}/01/01&to={next_jy}/12/29",
                    headers=accountant).json()
    assert tb["balanced"] is True
    assert _line_for(tb["rows"], "1101")["debit"] == amount + 500
    current_cash = client.get("/api/reports/treasury", headers=accountant).json()["total"]
    assert current_cash == cash_before + amount, "افتتاحیه نباید مانده‌ی سال مبدأ را بار دوم به مانده‌ی جاری اضافه کند"
    bs = client.get(f"/api/reports/balance-sheet?to={next_jy}/12/29", headers=accountant).json()
    assert bs["balanced"] is True
    cashflow = client.get(f"/api/reports/cash-flow?frm={next_jy}/01/01&to={next_jy}/12/29",
                          headers=accountant).json()
    assert cashflow["opening_cash"] == amount
    assert cashflow["closing_cash"] == amount
    assert cashflow["net"] == 0 and cashflow["matches_cash"] is True

    again = client.post("/api/fiscal/opening/post", headers=accountant, json=opening_body)
    assert again.status_code == 200, again.text
    assert again.json()["entry"]["id"] == entry["id"]
    assert len(client.get(f"/api/entries?frm={next_jy}/01/01&to={next_jy}/01/01",
                          headers=accountant).json()["rows"]) == 1

    blocked_reopen = client.post("/api/fiscal/reopen-year/precheck", headers=accountant,
                                 json={"jy": jy})
    assert blocked_reopen.status_code == 200
    assert blocked_reopen.json()["ready"] is False
    assert blocked_reopen.json()["active_opening_ids"] == [entry["id"]]
    direct_opening_reverse = client.post(f"/api/entries/{entry['id']}/reverse", headers=accountant,
                                         json={"reason": "پاک‌سازی آزمون چرخه‌ی افتتاحیه"})
    assert direct_opening_reverse.status_code == 400
    controlled_opening_reverse = client.post("/api/fiscal/reopen-year/reverse-opening",
        headers=accountant, json={"source_jy": jy, "entry_id": entry["id"],
                                  "reason": "پاک‌سازی آزمون چرخه‌ی افتتاحیه",
                                  "fingerprint": blocked_reopen.json()["fingerprint"],
                                  "acknowledge_dependencies": True})
    assert controlled_opening_reverse.status_code == 200, controlled_opening_reverse.text
    reopen_preview = client.post("/api/fiscal/reopen-year/precheck", headers=accountant,
                                 json={"jy": jy})
    assert reopen_preview.status_code == 200 and reopen_preview.json()["ready"]
    assert reopen_preview.json()["requires_acknowledgement"] is True
    assert client.post("/api/fiscal/reopen-year", headers=accountant, json={
        "jy": jy, "reason": "پاک‌سازی آزمون چرخه‌ی اختتامیه",
        "fingerprint": reopen_preview.json()["fingerprint"],
        "acknowledge_dependencies": True,
    }).status_code == 200
    assert client.get(f"/api/entries/{early_operation['id']}", headers=accountant).json()["status"] == "posted"


def test_closed_esfand_blocks_year_closing_until_explicit_reopen(client, accountant):
    jy = 1392
    cash = account_id(client, accountant, "1101")
    bank = account_id(client, accountant, "110201")
    _post(client, accountant, f"{jy}/01/05", "ایجاد سال برای آزمون اسفند بسته",
          [{"account_id": cash, "debit": 100_000, "credit": 0, "description": "ورود"},
           {"account_id": bank, "debit": 0, "credit": 100_000, "description": "انتقال"}])
    closed = client.post("/api/periods/close", headers=accountant, json={"jy": jy, "jm": 12})
    assert closed.status_code == 200, closed.text
    pre = client.post("/api/fiscal/close-year/precheck", headers=accountant, json={"jy": jy})
    assert pre.status_code == 200, pre.text
    assert pre.json()["ready"] is False
    assert any(error["code"] == "year_end_period_closed" for error in pre.json()["errors"])
    reopened = client.post("/api/periods/reopen", headers=accountant, json={"jy": jy, "jm": 12})
    assert reopened.status_code == 200, reopened.text
    invalid_month = client.post("/api/periods/close", headers=accountant,
                                json={"jy": jy, "jm": 13})
    assert invalid_month.status_code == 400
    assert "بین ۱ تا ۱۲" in invalid_month.json()["detail"]


def test_initial_opening_requires_balanced_permanent_account_lines(client, accountant):
    cash = account_id(client, accountant, "1101")
    income = account_id(client, accountant, "4101")
    unbalanced = client.post("/api/fiscal/opening/preview", headers=accountant, json={
        "destination_jy": 1394, "mode": "initial", "lines": [
            {"account_id": cash, "debit": 5_000_000, "credit": 0, "description": "منبع بیرونی"},
        ],
    })
    assert unbalanced.status_code == 200, unbalanced.text
    assert unbalanced.json()["ready"] is False
    assert any(error["code"] == "initial_lines_invalid" for error in unbalanced.json()["errors"])

    nonpermanent = client.post("/api/fiscal/opening/preview", headers=accountant, json={
        "destination_jy": 1394, "mode": "initial", "lines": [
            {"account_id": cash, "debit": 5_000_000, "credit": 0, "description": "منبع بیرونی"},
            {"account_id": income, "debit": 0, "credit": 5_000_000, "description": "حساب موقت نامعتبر"},
        ],
    })
    assert nonpermanent.status_code == 200, nonpermanent.text
    assert nonpermanent.json()["ready"] is False
    assert any(error["code"] == "initial_nonpermanent_account" for error in nonpermanent.json()["errors"])


def test_initial_opening_is_explicitly_external_and_idempotent(client, accountant):
    cash = account_id(client, accountant, "1101")
    retained = account_id(client, accountant, "3102")
    amount = 8_765_432
    payload = {"destination_jy": 1395, "mode": "initial", "lines": [
        {"account_id": cash, "debit": amount, "credit": 0, "description": "مانده‌ی بیرونی صندوق"},
        {"account_id": retained, "debit": 0, "credit": amount, "description": "مانده‌ی بیرونی حقوق مالکانه"},
    ]}
    pre = client.post("/api/fiscal/opening/preview", headers=accountant, json=payload)
    assert pre.status_code == 200 and pre.json()["ready"], pre.text
    assert "منبع بیرونی" in pre.json()["origin_label"]
    draft = client.post("/api/fiscal/opening/draft", headers=accountant,
                        json={**payload, "fingerprint": pre.json()["fingerprint"]})
    assert draft.status_code == 200, draft.text
    entry = draft.json()["entry"]
    assert entry["source_ref"] == "external->1395"
    assert client.post(f"/api/entries/{entry['id']}/post", headers=accountant).status_code == 400
    year_rows = {row["title"]: row for row in client.get("/api/fiscal/years", headers=accountant).json()["rows"]}
    assert year_rows["1395"]["opening_status"] == "draft"
    assert year_rows["1395"]["opening_draft_entry_id"] == entry["id"]
    body = {**payload, "fingerprint": pre.json()["fingerprint"], "draft_entry_id": entry["id"]}
    first = client.post("/api/fiscal/opening/post", headers=accountant, json=body)
    assert first.status_code == 200, first.text
    second = client.post("/api/fiscal/opening/post", headers=accountant, json=body)
    assert second.status_code == 200, second.text
    assert second.json()["entry"]["id"] == entry["id"]
    assert "source" in first.json()["entry"]
    assert client.post(f"/api/entries/{entry['id']}/reverse", headers=accountant,
                       json={"reason": "پاک‌سازی آزمون افتتاحیه‌ی اولیه"}).status_code == 200


def test_closing_preview_fingerprint_expires_after_new_posted_entry(client, accountant):
    jy = 1396
    cash = account_id(client, accountant, "1101")
    revenue = account_id(client, accountant, "4101")
    _post(client, accountant, f"{jy}/02/01", "درآمد پیش از پیش‌نمایش",
          [{"account_id": cash, "debit": 1_000, "credit": 0, "description": "دریافت"},
           {"account_id": revenue, "debit": 0, "credit": 1_000, "description": "درآمد"}])
    pre = client.post("/api/fiscal/close-year/precheck", headers=accountant, json={"jy": jy})
    assert pre.status_code == 200 and pre.json()["ready"], pre.text
    _post(client, accountant, f"{jy}/02/02", "درآمد پس از پیش‌نمایش",
          [{"account_id": cash, "debit": 2_000, "credit": 0, "description": "دریافت"},
           {"account_id": revenue, "debit": 0, "credit": 2_000, "description": "درآمد"}])
    draft = client.post("/api/fiscal/close-year/draft", headers=accountant,
                        json={"jy": jy, "fingerprint": pre.json()["fingerprint"]})
    assert draft.status_code >= 400
    assert "تغییر" in draft.json()["detail"]
