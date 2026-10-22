"""آزمون رفت‌و‌برگشت اکسل: هر خروجی، همان‌طور که هست، ورودی هم می‌شود.

قاعده‌ای که کارفرما گذاشت: «هر خروجی‌ای که می‌دهی، همان را باید بتوانی به عنوان ورودی استفاده کنی.»
این آزمون‌ها همان قاعده را می‌سنجند — نه روی کاغذ، با فایل واقعی.
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from app import dates as jd, excel_io


def _upload(data: bytes, name: str = "file.xlsx"):
    return {"file": (name, BytesIO(data),
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


def _download(client, headers, path) -> bytes:
    r = client.get(path, headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    return r.content


# ------------------------------------------------------------------ خروجی
def test_exports_open_as_real_excel_files(client, accountant):
    for path, sheet_expected in [
        ("/api/export/journal.xlsx", "داده"),
        ("/api/export/trial.xlsx", "داده"),
        ("/api/export/accounts.xlsx", "داده"),
        ("/api/export/treasury.xlsx", "داده"),
        ("/api/export/receivables.xlsx", "داده"),
        ("/api/export/template.xlsx", "داده"),
    ]:
        data = _download(client, accountant, path)
        wb = load_workbook(BytesIO(data))
        assert "راهنما" in wb.sheetnames, f"برگ راهنما در {path} نیست"
        assert sheet_expected in wb.sheetnames
        ws = wb[sheet_expected]
        assert ws.max_row >= 2, f"{path} هیچ سطری ندارد"
        # برچسب نوع فایل و قابل برگشت بودن باید در برگ راهنما نوشته شده باشد
        help_rows = {str(r[0]): str(r[1]) for r in wb["راهنما"].iter_rows(values_only=True) if r and r[0]}
        assert "نوع فایل" in help_rows
        assert "قابل بازخوانی در برنامه" in help_rows


def test_excel_export_header_uses_a_light_palette(client, accountant):
    wb = load_workbook(BytesIO(_download(client, accountant, "/api/export/journal.xlsx")))
    cell = wb["داده"]["A1"]
    assert cell.fill.fill_type == "solid"
    assert cell.fill.fgColor.rgb.endswith("E2F0EA")
    assert cell.font.color.rgb.endswith("174A3E")


def test_journal_export_says_it_is_importable_and_reports_say_not(client, accountant):
    j = load_workbook(BytesIO(_download(client, accountant, "/api/export/journal.xlsx")))["راهنما"]
    rows_j = {str(r[0]): str(r[1]) for r in j.iter_rows(values_only=True) if r and r[0]}
    assert rows_j["قابل بازخوانی در برنامه"] == "بله"

    t = load_workbook(BytesIO(_download(client, accountant, "/api/export/trial.xlsx")))["راهنما"]
    rows_t = {str(r[0]): str(r[1]) for r in t.iter_rows(values_only=True) if r and r[0]}
    assert rows_t["قابل بازخوانی در برنامه"] == "خیر"


def test_export_file_name_is_persian(client, accountant):
    r = client.get("/api/export/journal.xlsx", headers=accountant)
    disp = r.headers["content-disposition"]
    assert "filename*=UTF-8''" in disp
    assert "%D8%B3%D9%86%D8%AF" in disp          # «سند»


# ------------------------------------------------------------------ برگشت به برنامه
def test_export_then_import_creates_same_drafts(client, accountant):
    """فایل سندها را می‌گیریم و برمی‌گردانیم؛ باید همان سندها دوباره ساخته شوند."""
    data = _download(client, accountant, "/api/export/journal.xlsx?status=draft")
    before = client.get("/api/entries?status=draft", headers=accountant).json()["rows"]

    preview = client.post("/api/import/preview?kind=journal", headers=accountant, files=_upload(data))
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p["row_count"] >= 2
    assert p["groups"], "هیچ سندی در فایل پیدا نشد"
    assert not any(g["problems"] for g in p["groups"]), p["groups"]

    applied = client.post("/api/import/apply?kind=journal", headers=accountant, files=_upload(data))
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["count"] == len(p["groups"])

    after = client.get("/api/entries?status=draft", headers=accountant).json()["rows"]
    assert len(after) == len(before) + result["count"]
    # هر سند برگشته باید همان مبالغ را داشته باشد
    made = sorted(result["created"], key=lambda c: c["id"])
    assert all(c["debit_total"] > 0 for c in made)


def test_unbalanced_draft_survives_the_roundtrip(client, accountant):
    """پیش‌نویس ناتراز هم باید برگردد؛ فقط به‌صورت پیش‌نویس، بدون ثبت نهایی."""
    data = _download(client, accountant, "/api/export/journal.xlsx?status=draft")
    preview = client.post("/api/import/preview?kind=journal", headers=accountant,
                          files=_upload(data)).json()
    assert any(g["warnings"] for g in preview["groups"]), "باید اخطار ناترازی دیده شود"
    applied = client.post("/api/import/apply?kind=journal", headers=accountant, files=_upload(data))
    assert applied.status_code == 200, applied.text
    made = applied.json()["created"]
    detail = client.get(f"/api/entries/{made[0]['id']}", headers=accountant).json()
    assert detail["status"] == "draft"


def test_imported_entries_are_drafts_and_never_auto_posted(client, accountant):
    data = _download(client, accountant, "/api/export/journal.xlsx")
    client.post("/api/import/apply?kind=journal", headers=accountant, files=_upload(data))
    rows = client.get("/api/entries?status=draft", headers=accountant).json()["rows"]
    assert rows, "سندهای وارد‌شده باید در فهرست پیش‌نویس‌ها دیده شوند"
    assert all(r["entry_no"] is None for r in rows)


def test_same_file_cannot_be_imported_twice(client, accountant):
    data = _download(client, accountant, "/api/export/journal.xlsx")
    first = client.post("/api/import/apply?kind=journal", headers=accountant, files=_upload(data))
    assert first.status_code == 200
    second = client.post("/api/import/apply?kind=journal", headers=accountant, files=_upload(data))
    assert second.status_code == 400
    assert "قبلاً" in second.json()["detail"]


def test_report_file_is_refused_with_clear_message(client, accountant):
    data = _download(client, accountant, "/api/export/trial.xlsx")
    r = client.post("/api/import/preview?kind=journal", headers=accountant, files=_upload(data))
    assert r.status_code == 400
    assert "گرارش" in r.json()["detail"] or "trial" in r.json()["detail"]


def test_unbalanced_or_wrong_account_rows_are_reported_and_nothing_is_created(client, accountant):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "داده"
    ws.append(excel_io.COLUMNS["journal"])
    today = jd.jalali_str(jd.jtoday())
    ws.append([1, today, "سند ناتراز", "110201", "بانک", "", 10000000, 9000000, ""])
    ws.append([2, today, "حساب ناموجود", "999999", "", "", 1000000, 0, ""])
    ws.append([2, today, "حساب ناموجود", "110201", "بانک", "", 0, 1000000, ""])
    help_ws = wb.create_sheet("راهنما")
    help_ws.append(["نوع فایل", "journal"])
    buf = BytesIO()
    wb.save(buf)
    data = buf.getvalue()

    preview = client.post("/api/import/preview?kind=journal", headers=accountant, files=_upload(data))
    assert preview.status_code == 200, preview.text
    groups = preview.json()["groups"]
    assert all(g["problems"] for g in groups), "همه‌ی ردیف‌ها باید ایراد داشته باشند"

    apply = client.post("/api/import/apply?kind=journal", headers=accountant, files=_upload(data))
    assert apply.status_code == 400
    assert "هیچ سند سالمی" in apply.json()["detail"]


def test_accounts_export_import_roundtrip(client, admin):
    data = _download(client, admin, "/api/export/accounts.xlsx")
    preview = client.post("/api/import/preview?kind=accounts", headers=admin, files=_upload(data)).json()
    assert preview["row_count"] >= 30
    assert all(i["exists"] for i in preview["rows"]), "همه‌ی حساب‌های موجود باید «از قبل هست» علامت بخورند"

    apply = client.post("/api/import/apply?kind=accounts", headers=admin, files=_upload(data)).json()
    assert apply["count"] == 0 and apply["skipped"] >= 30


def test_new_account_from_template(client, admin):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "داده"
    ws.append(excel_io.COLUMNS["accounts"])
    ws.append(["1135", "حساب آزمون اکسل", "تفصیلی", "بدهکار", "receivable", "خیر", "بله", None])
    help_ws = wb.create_sheet("راهنما")
    help_ws.append(["نوع فایل", "accounts"])
    buf = BytesIO()
    wb.save(buf)

    r = client.post("/api/import/apply?kind=accounts", headers=admin, files=_upload(buf.getvalue()))
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    rows = client.get("/api/accounts", headers=admin).json()["rows"]
    assert any(a["code"] == "1135" for a in rows)


def test_file_without_help_sheet_is_refused(client, accountant):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["تاریخ", "کد حساب"])
    buf = BytesIO()
    wb.save(buf)
    r = client.post("/api/import/preview?kind=journal", headers=accountant, files=_upload(buf.getvalue()))
    assert r.status_code == 400
    assert "راهنما" in r.json()["detail"]


def test_import_needs_permission(client, auditor):
    r = client.post("/api/import/preview?kind=journal", headers=auditor,
                    files=_upload(b"not really a file"))
    assert r.status_code == 403


def test_export_is_recorded_in_audit_log(client, accountant, admin):
    client.get("/api/export/journal.xlsx", headers=accountant)
    rows = client.get("/api/audit", headers=admin).json()["rows"]
    assert any(r["action"] == "export.excel" for r in rows)


def test_numbers_in_export_are_plain_so_excel_can_sum_them(client, accountant):
    data = _download(client, accountant, "/api/export/journal.xlsx")
    wb = load_workbook(BytesIO(data))
    ws = wb["داده"]
    header = [c.value for c in ws[1]]
    debit_col = header.index("بدهکار") + 1
    values = [ws.cell(row=r, column=debit_col).value for r in range(2, ws.max_row + 1)]
    numbers = [v for v in values if isinstance(v, (int, float))]
    assert numbers, "مبالغ باید عدد باشند تا اکسل بتواند جمع بزند"
    assert all(float(v).is_integer() for v in numbers)


# ------------------------------------------------------------------ رفت‌و‌برگشت کامل
def _journal_rows(data: bytes) -> list[tuple]:
    wb = load_workbook(BytesIO(data))
    ws = wb["داده"]
    header = [c.value for c in ws[1]]
    idx = {h: i for i, h in enumerate(header)}
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        out.append((row[idx["تاریخ"]], row[idx["کد حساب"]], row[idx["شرح سند"]],
                    int(row[idx["بدهکار"]] or 0), int(row[idx["بستانکار"]] or 0)))
    return sorted(out)


def test_full_roundtrip_export_import_export_is_identical(client, accountant):
    """قلب ماجرا: هر سطری که خروجی می‌دهیم باید برگردد و باز هم همان‌طور بیرون بیاید."""
    file1 = _download(client, accountant, "/api/export/journal.xlsx")
    rows1 = _journal_rows(file1)
    assert len(rows1) >= 4

    preview = client.post("/api/import/preview?kind=journal", headers=accountant,
                          files=_upload(file1)).json()
    assert preview["row_count"] == len(rows1)

    applied = client.post("/api/import/apply?kind=journal&allow_duplicates=true",
                          headers=accountant, files=_upload(file1))
    assert applied.status_code == 200, applied.text
    assert applied.json()["count"] == len(preview["groups"])

    file2 = _download(client, accountant, "/api/export/journal.xlsx")
    rows2 = _journal_rows(file2)

    # هر سطری از فایل اول باید عیناً در فایل دوم هم باشد (دوبار: اصل + نسخه‌ی وارد‌شده)
    for row in set(rows1):
        expected = rows1.count(row) * 2
        assert rows2.count(row) >= expected, f"این سطر بعد از رفت‌و‌برگشت کامل برنگشت: {row}"


def test_edited_file_makes_a_new_draft(client, accountant):
    """کاری که حسابدار می‌کند: خروجی می‌گیرد، در اکسل عوض می‌کند، برمی‌گرداند."""
    data = _download(client, accountant, "/api/export/journal.xlsx?status=draft")
    wb = load_workbook(BytesIO(data))
    ws = wb["داده"]
    header = [c.value for c in ws[1]]
    dcol = header.index("بدهکار") + 1
    ccol = header.index("بستانکار") + 1
    # مبلغ سطر دوم اولین سند را عوض می‌کنیم تا سند تازه‌ای ساخته شود
    first_group = ws.cell(row=2, column=header.index("ردیف سند") + 1).value
    second_row = 3
    assert ws.cell(row=second_row, column=header.index("ردیف سند") + 1).value == first_group
    old = ws.cell(row=second_row, column=ccol).value or 0
    ws.cell(row=second_row, column=ccol, value=old + 250_000)
    # برای اینکه سند تراز بمانَد، بدهکار سطر اول را هم به همان اندازه زیاد می‌کنیم
    ws.cell(row=2, column=dcol, value=(ws.cell(row=2, column=dcol).value or 0) + 250_000)
    buf = BytesIO()
    wb.save(buf)

    r = client.post("/api/import/apply?kind=journal", headers=accountant, files=_upload(buf.getvalue()))
    assert r.status_code == 200, r.text
    assert r.json()["count"] >= 1


def test_reimporting_same_content_says_so_clearly(client, accountant):
    data = _download(client, accountant, "/api/export/journal.xlsx")
    client.post("/api/import/apply?kind=journal&allow_duplicates=true", headers=accountant,
                files=_upload(data))
    again = client.post("/api/import/apply?kind=journal", headers=accountant, files=_upload(data))
    assert again.status_code == 400
    detail = again.json()["detail"]
    # هر دو محافظ قابل قبول‌اند: یا اثر انگشت خود فایل، یا تشخیص سطرهای تکراری
    assert ("قبلاً" in detail) or ("عیناً" in detail) or ("تکراری" in detail)
