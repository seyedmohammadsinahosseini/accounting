"""خروجی و ورودی اکسل.

قاعده‌ی این بخش: هر فایلی که برنامه می‌دهد، خودش می‌داند چیست. داخل هر فایل یک برگ
«راهنما» هست که می‌گوید این فایل چه نوعی است و آیا می‌شود دوباره به برنامه برگرداند یا نه.
پس کاربر می‌تواند هر خروجی را در اکسل باز کند، دست بزند، و همان فایل را برگرداند.

تصمیم‌های مهم:
- حساب‌ها با «کد حساب» شناخته می‌شوند، نه با شناسه‌ی داخلی. یعنی فایل خروجی روی هر نسخه‌ای
  از برنامه قابل بازخوانی است.
- مبالغ به شکل عدد ساده (بدون جداکننده) نوشته می‌شوند تا اکسل بتواند جمع بزند و برگشت
  فایل هم دقیق باشد.
- تاریخ‌ها شمسی و به شکل 1405/07/23 هستند.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import dates as jd
from .money import format_amount, parse_amount
from .rules import DomainError

FILE_VERSION = 1
HELP_SHEET = "راهنما"
DATA_SHEET = "داده"

COLUMNS = {
    "journal": ["ردیف سند", "تاریخ", "شرح سند", "کد حساب", "نام حساب", "شرح سطر",
                "بدهکار", "بستانکار", "وضعیت"],
    "accounts": ["کد حساب", "نام حساب", "گروهی یا تفصیلی", "ماهیت", "دسته‌ی گزارشی",
                 "حساب اصلی برنامه", "فعال", "مانده", "سطح حساب", "کد حساب والد",
                 "نوع گروه", "نوع گزارش"],
    "trial": ["کد حساب", "نام حساب", "بدهکار", "بستانکار"],
    "ledger": ["تاریخ", "شماره سند", "شرح", "بدهکار", "بستانکار", "مانده"],
    "treasury": ["کد حساب", "نام حساب", "نوع", "مانده"],
    "receivables": ["کد حساب", "نام حساب", "نوع", "مانده", "به نفع"],
}

# ورودی‌پذیرها: فایل‌هایی که می‌شود همان‌ها را برگرداند. بقیه گزارش‌اند و برگشتشان معنا ندارد.
IMPORTABLE = {"journal": "سند روزنامه", "accounts": "کدینگ حساب‌ها"}

# Exports are always paper-light; the application theme must not leak into workbook headers.
HEADER_FILL = PatternFill("solid", fgColor="E2F0EA")
HEADER_FONT = Font(color="174A3E", bold=True, size=11)


class ExcelError(DomainError):
    pass


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


# ------------------------------------------------------------------ ساخت فایل
def _help_rows(kind: str, generated_by: str, extra: list[tuple[str, str]] | None = None):
    rows = [
        ("برنامه", "سامانه‌ی حسابداری آژانس مسافرتی"),
        ("نوع فایل", kind),
        ("نسخه‌ی قالب", str(FILE_VERSION)),
        ("ساخته‌شده در", jd.jalali_str(jd.jtoday()) + " " + datetime.now().strftime("%H:%M")),
        ("سازنده", generated_by),
        ("قابل بازخوانی در برنامه", "بله" if kind in IMPORTABLE else "خیر"),
        ("توضیح", IMPORTABLE.get(kind, "این فایل گزارش است؛ برای دیدن و چاپ. ورودی برنامه نیست.")),
        ("", ""),
        ("قاعده‌ی مهم", "مبالغ به ریال و بدون جداکننده نوشته می‌شوند. تاریخ‌ها شمسی‌اند (مثل 1405/07/23)."),
        ("قاعده‌ی مهم", "ردیف‌های یک سند با یک شماره‌ی «ردیف سند» به هم وصل می‌شوند؛ جمع بدهکار و بستانکار هر ردیف سند باید برابر باشد."),
        ("قاعده‌ی مهم", "هر ورودی از فایل، اول به‌صورت «پیش‌نویس» ساخته می‌شود؛ ثبت نهایی همیشه دست حسابدار است."),
    ]
    for k, v in (extra or []):
        rows.append((k, v))
    return rows


def _new_wb(kind: str, generated_by: str, extra=None) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = DATA_SHEET
    return wb


def _write_help(wb: Workbook, kind: str, generated_by: str, extra=None) -> None:
    ws = wb.create_sheet(HELP_SHEET)
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 90
    for i, (k, v) in enumerate(_help_rows(kind, generated_by, extra), start=1):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        c = ws.cell(row=i, column=2, value=v)
        c.alignment = Alignment(wrap_text=True, vertical="top")


def _write_table(ws, headers: list[str], rows: list[list], widths: list[int] | None = None,
                 money_columns: set[int] | None = None) -> None:
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append(r)
    money_columns = money_columns or set()
    for col in money_columns:
        for row in range(2, ws.max_row + 1):
            ws.cell(row=row, column=col).number_format = "#,##0"
    for i, w in enumerate(widths or [], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(ws.max_row, 1)}"


def _to_bytes(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_journal(entries, labels: dict[int, str], account_codes: dict[int, str],
                   generated_by: str = "") -> bytes:
    wb = _new_wb("journal", generated_by)
    ws = wb[DATA_SHEET]
    rows = []
    for e in entries:
        group = e.entry_no or f"پیش‌نویس-{e.id}"
        for line in e.lines:
            rows.append([
                group,
                jd.jalali_str(e.entry_date),
                e.description,
                account_codes.get(line.account_id, ""),
                labels.get(line.account_id, ""),
                line.description,
                line.debit_rial or 0,
                line.credit_rial or 0,
                {"draft": "پیش‌نویس", "posted": "ثبت نهایی", "reversed": "ابطال‌شده"}.get(e.status, e.status),
            ])
    _write_table(ws, COLUMNS["journal"], rows,
                 widths=[16, 12, 38, 12, 32, 30, 16, 16, 12],
                 money_columns={7, 8})
    _write_help(wb, "journal", generated_by,
                extra=[("تعداد سطر", str(len(rows)))])
    return _to_bytes(wb)


def export_accounts(accounts, generated_by: str = "") -> bytes:
    wb = _new_wb("accounts", generated_by)
    ws = wb[DATA_SHEET]
    level_labels = {"group": "گروه", "general": "کل", "subsidiary": "معین", "detail": "تفصیلی"}
    group_labels = {"permanent": "دائمی", "temporary": "موقت", "other": "سایر"}
    report_labels = {"balance_sheet": "ترازنامه", "income_statement": "سود و زیان",
                     "cash_flow": "جریان وجوه نقد", "other": "سایر"}
    rows = [[a.code, a.name, "گروهی" if a.kind == "group" else "تفصیلی",
             "بدهکار" if a.nature == "debit" else "بستانکار", a.report_group,
             "بله" if a.is_system else "خیر", "بله" if a.is_active else "خیر", None,
             level_labels.get(a.level, a.level), a.parent_code or "",
             group_labels.get(a.group_type, a.group_type),
             report_labels.get(a.report_type, a.report_type)]
            for a in accounts]
    _write_table(ws, COLUMNS["accounts"], rows,
                 widths=[14, 40, 16, 12, 18, 16, 10, 18, 14, 18, 16, 20], money_columns={8})
    _write_help(wb, "accounts", generated_by, extra=[("تعداد حساب", str(len(rows))),
        ("ساختار", "گروه ← کل ← معین ← تفصیلی؛ «کد حساب والد» زنجیره‌ی درخت کدینگ را نگه می‌دارد."),
        ("نوع گزارش", "ترازنامه، سود و زیان، جریان وجوه نقد یا سایر؛ تغییر طبقه‌بندی را حسابدار تأیید کند.")])
    return _to_bytes(wb)


def export_table(kind: str, headers: list[str], rows: list[list], generated_by: str = "",
                 money_columns: set[int] | None = None, widths: list[int] | None = None,
                 extra=None) -> bytes:
    wb = _new_wb(kind, generated_by)
    _write_table(wb[DATA_SHEET], headers, rows, widths=widths, money_columns=money_columns)
    _write_help(wb, kind, generated_by, extra=extra)
    return _to_bytes(wb)


# شناسه‌های فنی سرآمد/بدنه/پرداخت در قالب صورتحساب الکترونیکی. نام ستون‌ها را عمداً
# با نام فیلدهای پروتکل نگه می‌داریم تا حسابدار یا شرکت معتمد بتواند آن‌ها را تطبیق دهد.
MOADIAN_HEADER_FIELDS = [
    "taxid", "indatim", "indati2m", "inty", "inno", "irtaxid", "inp", "ins", "tins",
    "tob", "bid", "tinb", "sbc", "bpc", "bbc", "ft", "bpn", "scln", "scc", "crn",
    "billid", "tprdis", "tdis", "tadis", "tvam", "todam", "tbill", "setm", "cap", "insp",
    "tvop", "tax17",
]
MOADIAN_BODY_FIELDS = ["sstid", "sstt", "mu", "am", "fee", "prdis", "dis", "adis", "vra", "vam",
                       "tsstam", "flight_type"]
MOADIAN_PAYMENT_FIELDS = ["pmt", "pv", "pdt"]
MOADIAN_AMOUNT_FIELDS = {"tprdis", "tdis", "tadis", "tvam", "todam", "tbill", "cap", "insp", "tvop",
                         "fee", "prdis", "dis", "adis", "vam", "tsstam", "pv"}


def export_moadian_report(records: list[dict], generated_by: str = "") -> bytes:
    """گزارش چندبرگیِ قابل تطبیق با فیلدهای فنی صورتحساب؛ این فایل، فایل ارسال نیست."""
    wb = Workbook()
    heads = ["سریال داخلی", "شماره فاکتور برنامه", "وضعیت صف"] + MOADIAN_HEADER_FIELDS
    invoices_ws = wb.active
    invoices_ws.title = "سرآمد"
    invoice_rows = []
    line_rows = []
    payment_rows = []
    check_rows = []
    status_labels = {"draft": "ناقص", "ready": "آماده", "sent": "ارسال‌شده",
                     "accepted": "پذیرفته‌شده", "rejected": "مردود"}
    for rec in records:
        payload = rec.get("payload") or {}
        header = payload.get("header") or {}
        serial = rec.get("serial", "")
        invoice_number = rec.get("invoice_number", "")
        invoice_rows.append([serial, invoice_number, status_labels.get(rec.get("status"), rec.get("status", ""))]
                            + [header.get(field) for field in MOADIAN_HEADER_FIELDS])
        for index, line in enumerate(payload.get("body") or [], start=1):
            line_rows.append([serial, invoice_number, index] + [line.get(field) for field in MOADIAN_BODY_FIELDS])
        for index, payment in enumerate(payload.get("payment") or [], start=1):
            payment_rows.append([serial, invoice_number, index] + [payment.get(field) for field in MOADIAN_PAYMENT_FIELDS])
        problems = rec.get("problems") or []
        check_rows.append([serial, invoice_number, status_labels.get(rec.get("status"), rec.get("status", "")),
                           len(problems), " | ".join(str(x) for x in problems), rec.get("error", ""),
                           rec.get("reference_number", ""), rec.get("taxid", "")])

    invoice_money = {heads.index(field) + 1 for field in MOADIAN_HEADER_FIELDS if field in MOADIAN_AMOUNT_FIELDS}
    _write_table(invoices_ws, heads, invoice_rows, money_columns=invoice_money)
    invoices_ws.sheet_view.rightToLeft = True
    invoices_ws.column_dimensions["A"].width = 17
    invoices_ws.column_dimensions["B"].width = 22
    invoices_ws.column_dimensions["C"].width = 18
    for col in range(4, len(heads) + 1):
        invoices_ws.column_dimensions[get_column_letter(col)].width = 16

    line_ws = wb.create_sheet("اقلام")
    line_headers = ["سریال داخلی", "شماره فاکتور برنامه", "ردیف"] + MOADIAN_BODY_FIELDS
    line_money = {line_headers.index(field) + 1 for field in MOADIAN_BODY_FIELDS if field in MOADIAN_AMOUNT_FIELDS}
    _write_table(line_ws, line_headers, line_rows, money_columns=line_money)
    line_ws.sheet_view.rightToLeft = True
    for col in range(1, len(line_headers) + 1):
        line_ws.column_dimensions[get_column_letter(col)].width = 20 if col < 4 else 18

    payment_ws = wb.create_sheet("پرداخت")
    payment_headers = ["سریال داخلی", "شماره فاکتور برنامه", "ردیف"] + MOADIAN_PAYMENT_FIELDS
    payment_money = {payment_headers.index(field) + 1 for field in MOADIAN_PAYMENT_FIELDS
                     if field in MOADIAN_AMOUNT_FIELDS}
    _write_table(payment_ws, payment_headers, payment_rows, money_columns=payment_money)
    payment_ws.sheet_view.rightToLeft = True
    for col in range(1, len(payment_headers) + 1):
        payment_ws.column_dimensions[get_column_letter(col)].width = 24

    check_ws = wb.create_sheet("کنترل")
    check_headers = ["سریال داخلی", "شماره فاکتور برنامه", "وضعیت", "تعداد ایراد", "ایرادهای اعتبارسنجی",
                     "خطای ارسال", "شماره ارجاع", "شماره مالیاتی"]
    _write_table(check_ws, check_headers, check_rows, widths=[18, 22, 18, 14, 72, 50, 24, 24])
    check_ws.sheet_view.rightToLeft = True
    for row in check_ws.iter_rows(min_row=2):
        row[4].alignment = Alignment(wrap_text=True, vertical="top")
        row[5].alignment = Alignment(wrap_text=True, vertical="top")

    guide = wb.create_sheet(HELP_SHEET)
    guide.sheet_view.rightToLeft = True
    guide.column_dimensions["A"].width = 25
    guide.column_dimensions["B"].width = 45
    guide.column_dimensions["C"].width = 94
    guide.append(["نام فیلد", "معنا", "یادداشت"])
    for cell in guide[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
    explanations = {
        "taxid": "شناسه‌ی یکتای حافظه‌ی مالیاتی فروشنده", "indatim": "زمان صدور صورتحساب (میلی‌ثانیه)",
        "indati2m": "زمان ایجاد/ثبت صورتحساب (میلی‌ثانیه)", "inty": "نوع صورتحساب",
        "inno": "شماره‌ی صورتحساب", "irtaxid": "شناسه‌ی صورتحساب مرجع", "inp": "الگوی صورتحساب",
        "ins": "موضوع صورتحساب", "tins": "شماره‌ی اقتصادی فروشنده", "tob": "نوع خریدار",
        "bid": "کد ملی/شناسه‌ی ملی خریدار", "tinb": "شماره‌ی اقتصادی خریدار", "sstid": "شناسه‌ی کالا/خدمت",
        "sstt": "شرح کالا/خدمت", "mu": "کد واحد اندازه‌گیری", "am": "تعداد/مقدار",
        "fee": "فی واحد", "prdis": "مبلغ قبل از تخفیف", "dis": "تخفیف", "adis": "مبلغ پس از تخفیف",
        "vra": "نرخ مالیات", "vam": "مالیات ردیف", "tbill": "جمع صورتحساب",
        "setm": "روش تسویه", "cap": "مبلغ نقدی", "insp": "مبلغ نسیه", "pmt": "نوع پرداخت",
        "pv": "مبلغ پرداخت", "pdt": "تاریخ پرداخت (میلی‌ثانیه)",
    }
    fields = list(dict.fromkeys(MOADIAN_HEADER_FIELDS + MOADIAN_BODY_FIELDS + MOADIAN_PAYMENT_FIELDS))
    for field in fields:
        guide.append([field, explanations.get(field, "فیلد فنی صورتحساب"),
                      "مقدار عیناً از پرونده‌ی صورتحساب برنامه استخراج شده است؛ مبالغ به ریال هستند."])
    guide.append(["ساخته‌شده توسط", generated_by, "گزارش ساختاریافته برای بررسی و تطبیق حسابدار."])
    guide.append(["محدودیت مهم", "این فایل اکسل خروجی کنترل است، نه قالب رسمی بارگذاری یا ارسال به سازمان.",
                  "ارسال صورتحساب از فایل JSON ساختاریافته و از مسیر سامانه/شرکت معتمد انجام می‌شود. قالب اکسلِ ورود شرکت معتمد ممکن است اختصاصی باشد؛ برای تطبیق قطعی، فایل نمونه‌ی همان معتمد لازم است."])
    for row in guide.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    return _to_bytes(wb)


def template_journal(generated_by: str = "") -> bytes:
    """فایل نمونه‌ی خالی برای اینکه حسابدار سندها را در اکسل آماده کند."""
    wb = _new_wb("journal", generated_by)
    ws = wb[DATA_SHEET]
    demo_rows = [
        [1, jd.jalali_str(jd.jtoday()), "نمونه: فروش تور", "1103", "حساب‌های دریافتنی", "طلب از آقای نمونه", 100000000, 0, ""],
        [1, jd.jalali_str(jd.jtoday()), "نمونه: فروش تور", "4101", "درآمد فروش تور", "", 0, 100000000, ""],
        [2, jd.jalali_str(jd.jtoday()), "نمونه: دریافت از مشتری", "110201", "بانک ملت", "", 100000000, 0, ""],
        [2, jd.jalali_str(jd.jtoday()), "نمونه: دریافت از مشتری", "1103", "حساب‌های دریافتنی", "", 0, 100000000, ""],
    ]
    _write_table(ws, COLUMNS["journal"], demo_rows,
                 widths=[16, 12, 38, 12, 32, 30, 16, 16, 12], money_columns={7, 8})
    _write_help(wb, "journal", generated_by,
                extra=[("توضیح", "ردیف‌های نمونه را پاک کنید و سندهای خودتان را بنویسید. "
                                 "کد حساب را از برگ «کدینگ حساب‌ها» بردارید.")])
    return _to_bytes(wb)


def template_accounts(generated_by: str = "") -> bytes:
    wb = _new_wb("accounts", generated_by)
    ws = wb[DATA_SHEET]
    demo = [["1108-0002", "نمونه: تفصیلی مساعده کارکنان", "تفصیلی", "بدهکار", "receivable",
             "خیر", "بله", None, "تفصیلی", "1108", "دائمی", "ترازنامه"]]
    _write_table(ws, COLUMNS["accounts"], demo,
                 widths=[14, 40, 16, 12, 18, 16, 10, 18, 14, 18, 16, 20], money_columns={8})
    _write_help(wb, "accounts", generated_by,
                extra=[("توضیح", "ردیف نمونه را پاک کنید و حساب‌های جدید را بنویسید. "
                                 "ماهیت و دسته‌ی گزارشی را می‌توانید خالی بگذارید تا پیش‌فرض بگیرد.")])
    return _to_bytes(wb)


# ------------------------------------------------------------------ خواندن فایل
async def read_upload(upload) -> bytes:
    data = await upload.read()
    if not data:
        raise ExcelError("فایل خالی است.")
    if len(data) > 8 * 1024 * 1024:
        raise ExcelError("حجم فایل بیشتر از ۸ مگابایت است؛ فایل را کوچک‌تر کنید.")
    return data


def open_workbook(data: bytes) -> Workbook:
    try:
        return load_workbook(BytesIO(data), data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ExcelError("این فایل یک فایل اکسل سالم نیست. فایل را با اکسل ذخیره کنید و دوباره بفرستید.") from exc


def detect_kind(wb: Workbook) -> str:
    if HELP_SHEET not in wb.sheetnames:
        raise ExcelError("این فایل از برنامه‌ی ما نیست: برگ «راهنما» در آن پیدا نشد. "
                         "فایل نمونه را از همین صفحه دانلود کنید و اطلاعات را داخل آن بنویسید.")
    ws = wb[HELP_SHEET]
    for row in ws.iter_rows(values_only=True):
        if row and row[0] and str(row[0]).strip() == "نوع فایل":
            return str(row[1]).strip()
    raise ExcelError("نوع این فایل در برگ «راهنما» نوشته نشده است.")


def read_rows(wb: Workbook, kind: str) -> list[dict]:
    if DATA_SHEET not in wb.sheetnames:
        raise ExcelError("برگ «داده» در این فایل نیست.")
    ws = wb[DATA_SHEET]
    header = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
    needed = [c for c in COLUMNS[kind] if c in header]
    missing = [c for c in COLUMNS[kind] if c not in header]
    if kind == "journal" and ("تاریخ" not in header or "کد حساب" not in header):
        raise ExcelError(f"ستون‌های لازم در فایل نیست: {', '.join(missing)}")
    out = []
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if row is None or all(v is None or str(v).strip() == "" for v in row):
            continue
        item = {h: row[header.index(h)] if h in header else None for h in needed}
        item["_row"] = i
        out.append(item)
    return out


def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _amount(value) -> int:
    if value is None or _text(value) == "":
        return 0
    if isinstance(value, (int, float)):
        n = int(round(float(value)))
        if n < 0:
            raise ExcelError("مبلغ منفی نمی‌تواند باشد.")
        return n
    return parse_amount(value)


def _parse_journal_groups(rows: list[dict], account_by_code: dict, existing_signatures: set[str]) -> dict:
    """ردیف‌های اکسل را به سندهای آماده‌ی ساختن تبدیل و ایرادها را جمع می‌کند."""
    groups: dict[str, dict] = {}
    for r in rows:
        key = _text(r.get("ردیف سند")) or "1"
        g = groups.setdefault(key, {"key": key, "date_raw": None, "description": "", "lines": [],
                                    "problems": [], "warnings": [], "rows": []})
        g["rows"].append(r["_row"])
        date_raw = _text(r.get("تاریخ"))
        if date_raw:
            if g["date_raw"] is None:
                g["date_raw"] = date_raw
            elif g["date_raw"] != date_raw:
                g["problems"].append(f"ردیف سند {key}: تاریخ‌های مختلف در یک سند.")
        desc = _text(r.get("شرح سند"))
        if desc and not g["description"]:
            g["description"] = desc
        code = _text(r.get("کد حساب"))
        account = account_by_code.get(code)
        if not code:
            g["problems"].append(f"ردیف سند {key}، سطر {r['_row']}: کد حساب خالی است.")
        elif account is None:
            g["problems"].append(f"ردیف سند {key}، سطر {r['_row']}: حسابی با کد {code} در برنامه نیست.")
        elif account.kind != "ledger":
            g["problems"].append(f"ردیف سند {key}، سطر {r['_row']}: «{account.name}» حساب گروهی است.")
        try:
            debit = _amount(r.get("بدهکار"))
            credit = _amount(r.get("بستانکار"))
        except Exception as exc:  # noqa: BLE001
            g["problems"].append(f"ردیف سند {key}، سطر {r['_row']}: {exc}")
            continue
        if debit and credit:
            g["problems"].append(f"ردیف سند {key}، سطر {r['_row']}: هم بدهکار پر است هم بستانکار.")
        if not debit and not credit:
            g["problems"].append(f"ردیف سند {key}، سطر {r['_row']}: مبلغ خالی است.")
        if account is not None:
            g["lines"].append({"account_id": account.id, "debit": debit, "credit": credit,
                               "description": _text(r.get("شرح سطر"))[:300]})

    out = []
    for g in groups.values():
        debit = sum(l["debit"] for l in g["lines"])
        credit = sum(l["credit"] for l in g["lines"])
        g["debit_total"], g["credit_total"] = debit, credit
        if len(g["lines"]) < 2:
            g["problems"].append(f"ردیف سند {g['key']}: سند باید حداقل دو سطر داشته باشد.")
        if debit != credit:
            # ناترازی «ایراد» نیست، «اخطار» است: سند ساخته می‌شود ولی پیش‌نویس می‌ماند و تا
            # تراز نشود ثبت نهایی نمی‌شود. این‌طور خروجی و ورودی کاملاً رفت‌و‌برگشت‌پذیر است.
            g["warnings"].append(
                f"ردیف سند {g['key']}: سند تراز نیست — بدهکار {format_amount(debit)} و "
                f"بستانکار {format_amount(credit)} ریال؛ به‌صورت پیش‌نویس ساخته می‌شود.")
        try:
            g["date"] = jd.parse_jalali(g["date_raw"]) if g["date_raw"] else None
        except Exception:  # noqa: BLE001
            g["date"] = None
            g["problems"].append(f"ردیف سند {g['key']}: تاریخ «{g['date_raw']}» خوانده نشد.")
        if g["date"] is None and g["date_raw"] in (None, ""):
            g["problems"].append(f"ردیف سند {g['key']}: تاریخ خالی است.")
        signature = None
        if g["date"] and not g["problems"] and debit == credit:
            signature = _signature(g["date"].isoformat(), g["lines"])
        g["signature"] = signature
        g["duplicate"] = bool(signature and signature in existing_signatures)
        out.append(g)
    out.sort(key=lambda g: (g["date"] or jd.jtoday(), g["key"]))
    return {"groups": out,
            "has_problems": any(g["problems"] for g in out),
            "warning_count": sum(len(g["warnings"]) for g in out)}


def _signature(iso_date: str, lines: list[dict]) -> str:
    body = "|".join(f"{l['account_id']}:{l['debit']}:{l['credit']}" for l in lines)
    return hashlib.sha256((iso_date + "||" + body).encode()).hexdigest()[:24]


def existing_entry_signatures(sess) -> set[str]:
    from sqlalchemy import select

    from .models import JournalEntry

    out = set()
    rows = sess.execute(select(JournalEntry).where(JournalEntry.status.in_(("posted", "reversed")))).scalars()
    for e in rows:
        lines = [{"account_id": l.account_id, "debit": l.debit_rial, "credit": l.credit_rial}
                 for l in e.lines]
        out.add(_signature(e.entry_date.isoformat(), lines))
    return out


def prepare_journal_import(sess, data: bytes) -> dict:
    from . import services as svc

    wb = open_workbook(data)
    kind = detect_kind(wb)
    if kind != "journal":
        raise ExcelError(f"این فایل از نوع «{kind}» است و به‌عنوان سند روزنامه خوانده نمی‌شود. "
                         f"برای سند، فایل «سند روزنامه» یا فایل نمونه را بفرستید.")
    rows = read_rows(wb, "journal")
    if not rows:
        raise ExcelError("این فایل هیچ سطری برای خواندن ندارد.")
    accounts = {a.code: a for a in svc.list_accounts(sess)}
    result = _parse_journal_groups(rows, accounts, existing_entry_signatures(sess))
    result.update({"kind": kind, "row_count": len(rows), "fingerprint": fingerprint(data)})
    return result


def prepare_accounts_import(sess, data: bytes) -> dict:
    from . import services as svc

    wb = open_workbook(data)
    kind = detect_kind(wb)
    if kind != "accounts":
        raise ExcelError(f"این فایل از نوع «{kind}» است؛ برای کدینگ حساب‌ها فایل «کدینگ حساب‌ها» را بفرستید.")
    rows = read_rows(wb, "accounts")
    existing = {a.code: a for a in svc.list_accounts(sess)}
    levels = {"group": "group", "گروه": "group", "general": "general", "کل": "general",
              "subsidiary": "subsidiary", "معین": "subsidiary", "detail": "detail", "تفصیلی": "detail"}
    group_types = {"permanent": "permanent", "دائمی": "permanent", "temporary": "temporary",
                   "موقت": "temporary", "other": "other", "سایر": "other"}
    report_types = {"balance_sheet": "balance_sheet", "ترازنامه": "balance_sheet",
                    "income_statement": "income_statement", "سود و زیان": "income_statement",
                    "cash_flow": "cash_flow", "جریان وجوه نقد": "cash_flow", "other": "other", "سایر": "other"}
    report_groups = {"other", "asset", "liability", "equity", "income", "expense", "cash", "bank",
                     "receivable", "payable", "vat", "tax", "equity_draw", "cheque_received",
                     "cheque_paid", "prepaid_supplier", "advance_customer", "employee_loan"}
    prepared = []
    seen: set[str] = set()
    input_levels: dict[str, str] = {a.code: a.level for a in existing.values()}
    for r in rows:
        code = _text(r.get("کد حساب"))
        name = _text(r.get("نام حساب"))
        item = {"row": r["_row"], "code": code, "name": name, "problems": [], "exists": code in existing}
        if not code or not name:
            item["problems"].append("کد و نام حساب هر دو لازم است.")
        if code in seen:
            item["problems"].append(f"کد حساب {code} در همین فایل بیش از یک‌بار آمده است.")
        if code:
            seen.add(code)

        nature_raw = _text(r.get("ماهیت")) or "debit"
        nature_map = {"بدهکار": "debit", "debit": "debit", "بستانکار": "credit", "credit": "credit"}
        item["nature"] = nature_map.get(nature_raw, "debit")
        if nature_raw not in nature_map:
            item["problems"].append(f"ماهیت «{nature_raw}» شناخته‌شده نیست.")

        kind_raw = _text(r.get("گروهی یا تفصیلی"))
        item["account_kind"] = "group" if kind_raw in ("گروهی", "group") else "ledger"
        level_raw = _text(r.get("سطح حساب"))
        if level_raw:
            level = levels.get(level_raw.lower(), levels.get(level_raw))
            if not level:
                item["problems"].append(f"سطح حساب «{level_raw}» نامعتبر است.")
                level = "detail"
        elif item["account_kind"] == "group":
            level = "group" if len(code) <= 1 else ("general" if len(code) <= 2 else "subsidiary")
        else:
            level = "detail"      # قالب قدیمی فقط «گروهی/تفصیلی» داشت
        item["level"] = level
        item["explicit_level"] = bool(level_raw)
        item["parent_code"] = _text(r.get("کد حساب والد")) or None

        item["report_group"] = _text(r.get("دسته‌ی گزارشی")) or "other"
        if item["report_group"] not in report_groups:
            item["problems"].append(f"دسته‌ی گزارشی «{item['report_group']}» شناخته‌شده نیست.")
        group_raw = _text(r.get("نوع گروه"))
        item["group_type"] = group_types.get(group_raw, svc.group_type_for(item["report_group"]))
        if group_raw and group_raw not in group_types:
            item["problems"].append(f"نوع گروه «{group_raw}» نامعتبر است.")
        report_raw = _text(r.get("نوع گزارش"))
        item["report_type"] = report_types.get(report_raw, svc.report_type_for(item["report_group"]))
        if report_raw and report_raw not in report_types:
            item["problems"].append(f"نوع گزارش «{report_raw}» نامعتبر است.")
        if code:
            input_levels[code] = level
        if code in existing:
            item["problems"].append(f"حساب با کد {code} از قبل در برنامه هست (ساخته نمی‌شود).")
        prepared.append(item)

    expected_parent = {"general": "group", "subsidiary": "general", "detail": "subsidiary"}
    by_file = {i["code"]: i for i in prepared if i["code"]}
    for item in prepared:
        if item["exists"]:
            continue
        parent_code = item["parent_code"]
        if item["level"] == "group":
            if parent_code:
                item["problems"].append("حساب سطح گروه نباید حساب والد داشته باشد.")
            continue
        if not parent_code:
            # قالب‌های قدیمی یا کدهای تفصیلی مستقل برای سازگاری پذیرفته می‌شوند؛
            # قالب جدید، هر سطح پایین‌تر از گروه را با والد مشخص می‌کند.
            if item["explicit_level"]:
                item["problems"].append("برای حساب کل، معین یا تفصیلی، کد حساب والد لازم است.")
            continue
        parent = existing.get(parent_code)
        parent_item = by_file.get(parent_code)
        parent_level = parent.level if parent else (parent_item or {}).get("level")
        if parent is None and parent_item is None:
            item["problems"].append(f"حساب والد {parent_code} در برنامه یا فایل پیدا نشد.")
        elif parent_level != expected_parent[item["level"]]:
            item["problems"].append(f"حساب والد {parent_code} باید سطح «{svc.ACCOUNT_LEVELS[expected_parent[item['level']]]}» داشته باشد.")

    return {"kind": kind, "rows": prepared, "fingerprint": fingerprint(data),
            "row_count": len(rows),
            "has_problems": any(i["problems"] and not i["exists"] for i in prepared)}


# ------------------------------------------------------------------ اجرای ورود
def apply_journal_import(sess, user, data: bytes, allow_duplicates: bool = False,
                         ip: str = "") -> dict:
    """سندهای فایل را به‌صورت پیش‌نویس می‌سازد. هیچ سندی خودکار ثبت نهایی نمی‌شود."""
    from . import services as svc
    from .models import ImportRecord

    prepared = prepare_journal_import(sess, data)
    fp = prepared["fingerprint"]
    from sqlalchemy import select

    already = sess.execute(select(ImportRecord).where(ImportRecord.fingerprint == fp)).scalar_one_or_none()
    if already is not None:
        raise ExcelError(
            "این فایل قبلاً یک‌بار وارد شده است. برای اینکه سندها دوباره ساخته نشوند، وارد کردن "
            "متوقف شد. اگر عمداً می‌خواهید دوباره وارد شود، در اکسل یک تغییر کوچک بدهید و ذخیره کنید."
        )

    good = [g for g in prepared["groups"] if not g["problems"] and (allow_duplicates or not g["duplicate"])]
    skipped_dup = [g for g in prepared["groups"] if not g["problems"] and g["duplicate"] and not allow_duplicates]
    bad = [g for g in prepared["groups"] if g["problems"]]
    if not good:
        if skipped_dup and not bad:
            raise ExcelError(
                f"همه‌ی {len(skipped_dup)} سند این فایل عیناً در دفتر هست؛ چیزی ساخته نشد. "
                "اگر می‌خواهید نسخه‌ی تازه‌ای هم ساخته شود، در فایل تغییری بدهید یا تیک "
                "«سندهای تکراری را هم بساز» را بزنید."
            )
        raise ExcelError("هیچ سند سالمی در این فایل نبود؛ چیزی وارد نشد. ایرادها را در فهرست بالا ببینید.")

    created = []
    for g in good:
        entry = svc.create_draft(sess, user, g["date"], g["description"], g["lines"],
                                 source="excel", source_ref="اکسل", ip=ip)
        created.append({"id": entry.id, "date": jd.jalali_str(g["date"]),
                        "debit_total": g["debit_total"], "rows": g["rows"]})
    record = ImportRecord(fingerprint=fp, kind="journal", user_id=user.id,
                          rows=prepared["row_count"], created=len(created),
                          skipped=len(bad) + len(skipped_dup), note="فایل سند روزنامه")
    sess.add(record)
    svc.audit(sess, user, "import.journal", "import", str(record.id),
              f"{len(created)} سند وارد شد، {len(bad)} ناسالم، {len(skipped_dup)} تکراری", ip)
    return {"created": created, "count": len(created), "problems": len(bad),
            "skipped_duplicates": len(skipped_dup), "message":
            f"{len(created)} سند به‌صورت پیش‌نویس ساخته شد. "
            f"{len(bad)} ردیف ایراد داشت و وارد نشد." +
            (f" {len(skipped_dup)} ردیف تکراری بود و وارد نشد." if skipped_dup else "")}


def apply_accounts_import(sess, user, data: bytes, ip: str = "") -> dict:
    from . import services as svc
    from .models import ImportRecord
    from sqlalchemy import select

    prepared = prepare_accounts_import(sess, data)
    fp = prepared["fingerprint"]
    if sess.execute(select(ImportRecord).where(ImportRecord.fingerprint == fp)).scalar_one_or_none():
        raise ExcelError("این فایل قبلاً وارد شده است؛ برای جلوگیری از تکرار، متوقف شد.")
    created, skipped = [], []
    pending = []
    for item in prepared["rows"]:
        if item["exists"] or item["problems"]:
            skipped.append(item)
        else:
            pending.append(item)
    # والد باید پیش از فرزند ساخته شود؛ ترتیب ردیف‌های فایل را به ساختار درخت وابسته نمی‌کنیم.
    while pending:
        progressed = False
        for item in pending[:]:
            if item["parent_code"] and svc.account_by_code(sess, item["parent_code"]) is None:
                continue
            acc = svc.create_account(
                sess, user, item["code"], item["name"], item["nature"], item["parent_code"],
                item["report_group"], ip, level=item["level"], group_type=item["group_type"],
                report_type=item["report_type"], enforce_hierarchy=item["explicit_level"])
            created.append({"code": acc.code, "name": acc.name, "level": acc.level,
                            "parent_code": acc.parent_code})
            pending.remove(item)
            progressed = True
        if not progressed:
            skipped.extend(pending)
            break
    record = ImportRecord(fingerprint=fp, kind="accounts", user_id=user.id,
                          rows=prepared["row_count"], created=len(created),
                          skipped=len(skipped), note="فایل کدینگ حساب‌ها")
    sess.add(record)
    svc.audit(sess, user, "import.accounts", "import", str(record.id),
              f"{len(created)} حساب ساخته شد، {len(skipped)} رد شد", ip)
    return {"created": created, "count": len(created), "skipped": len(skipped),
            "message": f"{len(created)} حساب ساخته شد؛ {len(skipped)} ردیف ساخته نشد."}


# ------------------------------------------------------------------ قالب‌های تازه: اشخاص، فاکتور، سفر، تقویم پول
COLUMNS.update({
    "parties": ["کد شخص", "نام", "نوع", "تلفن", "یادداشت", "مانده"],
    "invoices": ["شماره فاکتور", "تاریخ", "سررسید", "مشتری", "سفر", "شرح", "نوع خدمت",
                 "مبلغ بدون مالیات", "مالیات", "جمع", "دریافت‌شده", "مانده", "وضعیت"],
    "settlements": ["شماره", "تاریخ", "نوع", "شخص", "حساب", "مبلغ", "شرح", "وضعیت"],
    "trips": ["کد سفر", "عنوان", "مقصد", "شروع", "پایان", "فروش", "هزینه", "سود",
              "درصد سود", "مانده دریافتنی", "مانده پرداختنی"],
    "calendar": ["تاریخ", "چه چیزی", "مرجع", "طرف حساب", "ورودی", "خروجی", "روزهای مانده"],
})

IMPORTABLE.update({"parties": "اشخاص (مشتریان و تأمین‌کنندگان)"})


def export_parties(rows: list[dict], generated_by: str = "") -> bytes:
    wb = _new_wb("parties", generated_by)
    ws = wb[DATA_SHEET]
    kind_label = {"customer": "مشتری", "supplier": "تأمین‌کننده", "both": "مشتری و تأمین‌کننده"}
    data = [[r["code"], r["name"], kind_label.get(r["kind"], r["kind"]), r.get("phone", ""),
             r.get("note", ""), r["balance"]] for r in rows]
    _write_table(ws, COLUMNS["parties"], data, widths=[14, 36, 24, 20, 30, 20], money_columns={6})
    _write_help(wb, "parties", generated_by,
                extra=[("تعداد شخص", str(len(data))),
                       ("نکته", "برای ساختن شخص تازه، یک سطر با نام و نوع بنویسید؛ «کد شخص» را خالی بگذارید.")])
    return _to_bytes(wb)


def export_invoices(rows: list[dict], generated_by: str = "") -> bytes:
    status = {"draft": "پیش‌نویس", "issued": "صادرشده", "cancelled": "ابطال‌شده"}
    labels = {"tour": "تور", "ticket": "بلیت", "visa": "ویزا", "hotel": "اقامت",
              "transfer": "ترانسفر", "insurance": "بیمه مسافرتی", "commission": "کمیسیون",
              "other": "سایر خدمات"}
    data = [[r["number"] or "—", r["date"], r["due"], r["party"], r["trip"], r["description"],
             ", ".join(labels.get(s, s) for s in r["service_types"]),
             r["subtotal"] - r["discount"], r["vat"], r["total"], r["paid"], r["open"],
             status.get(r["status"], r["status"])] for r in rows]
    wb = _new_wb("invoices", generated_by)
    _write_table(wb[DATA_SHEET], COLUMNS["invoices"], data,
                 widths=[16, 12, 12, 26, 22, 34, 20, 18, 14, 18, 18, 18, 12],
                 money_columns={8, 9, 10, 11, 12})
    _write_help(wb, "invoices", generated_by, extra=[("تعداد فاکتور", str(len(data)))])
    return _to_bytes(wb)


def export_settlements(rows: list[dict], generated_by: str = "") -> bytes:
    status = {"draft": "پیش‌نویس", "issued": "ثبت‌شده", "cancelled": "ابطال‌شده"}
    data = [[r["number"] or "—", r["date"], "دریافت" if r["direction"] == "in" else "پرداخت",
             r["party"], r["account"], r["amount"], r["description"],
             status.get(r["status"], r["status"])] for r in rows]
    wb = _new_wb("settlements", generated_by)
    _write_table(wb[DATA_SHEET], COLUMNS["settlements"], data,
                 widths=[16, 12, 12, 26, 24, 18, 34, 12], money_columns={6})
    _write_help(wb, "settlements", generated_by, extra=[("تعداد ردیف", str(len(data)))])
    return _to_bytes(wb)


def export_trips(rows: list[dict], generated_by: str = "") -> bytes:
    data = [[r["code"], r["title"], r["destination"], r["start"], r["end"],
             r["revenue"], r["cost"], r["profit"], r["margin_percent"],
             r["receivable_open"], r["payable_open"]] for r in rows]
    wb = _new_wb("trips", generated_by)
    _write_table(wb[DATA_SHEET], COLUMNS["trips"], data,
                 widths=[16, 30, 18, 12, 12, 18, 18, 18, 12, 20, 20],
                 money_columns={6, 7, 8, 10, 11})
    _write_help(wb, "trips", generated_by,
                extra=[("تعداد سفر", str(len(data))),
                       ("نکته", "سود = فروش بدون مالیات − هزینه‌ی سفر بدون مالیات.")])
    return _to_bytes(wb)


def export_calendar(rows: list[dict], generated_by: str = "", period: str = "") -> bytes:
    data = [[r["date"], r["title"], r["ref"], r["party"],
             r["amount"] if r["direction"] == "in" else "",
             r["amount"] if r["direction"] == "out" else "", r["days_left"]] for r in rows]
    wb = _new_wb("calendar", generated_by)
    _write_table(wb[DATA_SHEET], COLUMNS["calendar"], data,
                 widths=[12, 46, 16, 26, 18, 18, 14], money_columns={5, 6})
    _write_help(wb, "calendar", generated_by, extra=[("بازه", period)])
    return _to_bytes(wb)


def prepare_parties_import(sess, data: bytes) -> dict:
    from sqlalchemy import select

    from .models import Party

    wb = open_workbook(data)
    kind = detect_kind(wb)
    if kind != "parties":
        raise ExcelError(f"این فایل از نوع «{kind}» است؛ برای اشخاص فایل «اشخاص» را بفرستید.")
    rows = read_rows(wb, "parties")
    existing_codes = {p.code for p in sess.execute(select(Party)).scalars()}
    kind_map = {"مشتری": "customer", "تأمین‌کننده": "supplier",
                "مشتری و تأمین‌کننده": "both", "customer": "customer",
                "supplier": "supplier", "both": "both"}
    prepared = []
    for r in rows:
        code = _text(r.get("کد شخص"))
        name = _text(r.get("نام"))
        kind_raw = _text(r.get("نوع")) or "مشتری"
        item = {"row": r["_row"], "code": code, "name": name,
                "kind": kind_map.get(kind_raw, "customer"), "phone": _text(r.get("تلفن")),
                "note": _text(r.get("یادداشت")), "problems": [], "exists": code in existing_codes}
        if len(name) < 2:
            item["problems"].append("نام شخص را کامل بنویسید.")
        if item["exists"]:
            item["problems"].append(f"شخصی با کد {code} از قبل هست (ساخته نمی‌شود).")
        prepared.append(item)
    return {"kind": kind, "rows": prepared, "row_count": len(rows), "fingerprint": fingerprint(data),
            "has_problems": any(i["problems"] and not i["exists"] for i in prepared)}


def apply_parties_import(sess, user, data: bytes, ip: str = "") -> dict:
    from sqlalchemy import select

    from . import sales, services as svc
    from .models import ImportRecord

    prepared = prepare_parties_import(sess, data)
    fp = prepared["fingerprint"]
    if sess.execute(select(ImportRecord).where(ImportRecord.fingerprint == fp)).scalar_one_or_none():
        raise ExcelError("این فایل قبلاً وارد شده است؛ برای جلوگیری از تکرار، متوقف شد.")
    created, skipped = [], []
    for item in prepared["rows"]:
        if item["exists"] or item["problems"]:
            skipped.append(item)
            continue
        party = sales.create_party(sess, user, item["name"], item["kind"], item["phone"],
                                   item["note"], ip)
        created.append({"code": party.code, "name": party.name})
    record = ImportRecord(fingerprint=fp, kind="parties", user_id=user.id,
                          rows=prepared["row_count"], created=len(created),
                          skipped=len(skipped), note="فایل اشخاص")
    sess.add(record)
    svc.audit(sess, user, "import.parties", "import", str(record.id),
              f"{len(created)} شخص ساخته شد، {len(skipped)} رد شد", ip)
    return {"created": created, "count": len(created), "skipped": len(skipped),
            "message": f"{len(created)} شخص ساخته شد؛ {len(skipped)} ردیف ساخته نشد."}
