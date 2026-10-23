"""کارهای اصلی برنامه: ساختن سند، ثبت نهایی، ابطال، تراز، دفتر و انتقال وجه.

هر تابع این فایل مستقل از صفحه‌ها کار می‌کند؛ یعنی هم سایت از آن استفاده می‌کند و هم
آزمون‌های خودکار. این باعث می‌شود چیزی که تست کرده‌ام دقیقاً همان چیزی باشد که کاربر
روی صفحه می‌بیند.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select

from . import dates as jd
from .models import (Account, AuditLog, FiscalYear, JournalEntry, JournalLine, Period, User)
from .money import format_amount_fa
from .rules import (DomainError, Validation, build_validation)


# --------------------------------------------------------------------- سال و دوره
def ensure_fiscal_year(sess, d: date) -> FiscalYear:
    title, start, end = jd.fiscal_year_of(d)
    fy = sess.execute(select(FiscalYear).where(FiscalYear.title == title)).scalar_one_or_none()
    if fy is None:
        fy = FiscalYear(title=title, start_date=start, end_date=end, status="open")
        sess.add(fy)
        sess.flush()
        for m in range(1, 13):
            sess.add(Period(fiscal_year_id=fy.id, jy=int(title), jm=m, status="open"))
        sess.flush()
    return fy


def period_for(sess, d: date) -> Period:
    jy, jm = jd.period_of(d)
    ensure_fiscal_year(sess, d)
    p = sess.execute(select(Period).where(Period.jy == jy, Period.jm == jm)).scalar_one_or_none()
    if p is None:
        fy = ensure_fiscal_year(sess, d)
        p = Period(fiscal_year_id=fy.id, jy=jy, jm=jm, status="open")
        sess.add(p)
        sess.flush()
    return p


def _validate_period_key(jy: int, jm: int) -> None:
    if not 1390 <= int(jy) <= 1450:
        raise DomainError("سال مالی خارج از محدوده‌ی مجاز است.")
    if not 1 <= int(jm) <= 12:
        raise DomainError("شماره‌ی دوره باید بین ۱ تا ۱۲ باشد.")


def close_period(sess, user: User, jy: int, jm: int, ip: str = "") -> Period:
    _validate_period_key(jy, jm)
    p = sess.execute(select(Period).where(Period.jy == jy, Period.jm == jm)).scalar_one_or_none()
    if p is None:
        raise DomainError(f"دوره‌ی {jd.MONTHS[jm - 1]} {jy} ساخته نشده است.")
    year = sess.get(FiscalYear, p.fiscal_year_id)
    if year and year.status == "closed":
        raise DomainError(f"سال مالی {jy} بسته است؛ تغییر دوره فقط از فرایند مجاز بازگشایی سال انجام می‌شود.")
    if p.status == "closed":
        raise DomainError(f"دوره‌ی {jd.MONTHS[jm - 1]} {jy} از قبل بسته شده است.")
    p.status = "closed"
    p.closed_at = datetime.now()
    p.closed_by = user.id
    audit(sess, user, "period.close", "period", f"{jy}-{jm}", jd.MONTHS[jm - 1] + f" {jy}", ip)
    return p


def reopen_period(sess, user: User, jy: int, jm: int, ip: str = "") -> Period:
    _validate_period_key(jy, jm)
    p = sess.execute(select(Period).where(Period.jy == jy, Period.jm == jm)).scalar_one_or_none()
    if p is None:
        raise DomainError("این دوره وجود ندارد.")
    year = sess.get(FiscalYear, p.fiscal_year_id)
    if year and year.status == "closed":
        raise DomainError(f"سال مالی {jy} بسته است؛ برای بازکردن دوره، ابتدا فرایند بازگشایی سال را تکمیل کنید.")
    if p.status == "open":
        raise DomainError(f"دوره‌ی {jd.MONTHS[jm - 1]} {jy} از قبل باز است.")
    p.status = "open"
    p.closed_at = None
    p.closed_by = None
    audit(sess, user, "period.reopen", "period", f"{jy}-{jm}", jd.MONTHS[jm - 1] + f" {jy}", ip)
    return p


def list_periods(sess, limit: int = 24) -> list[Period]:
    return list(sess.execute(select(Period).order_by(Period.jy.desc(), Period.jm.desc()).limit(limit)).scalars())


# --------------------------------------------------------------------- کدینگ حساب‌ها
ACCOUNT_LEVELS = {"group": "گروه", "general": "کل", "subsidiary": "معین", "detail": "تفصیلی"}
GROUP_TYPES = {"permanent": "دائمی", "temporary": "موقت", "other": "سایر"}
REPORT_TYPES = {"balance_sheet": "ترازنامه", "income_statement": "سود و زیان",
                "cash_flow": "جریان وجوه نقد", "other": "سایر"}
_PARENT_LEVEL = {"general": "group", "subsidiary": "general", "detail": "subsidiary"}


def report_type_for(report_group: str) -> str:
    if report_group in {"income", "expense", "equity_draw"}:
        return "income_statement"
    if report_group in {"asset", "liability", "equity", "cash", "bank", "receivable", "payable",
                        "vat", "tax", "cheque_received", "cheque_paid", "prepaid_supplier",
                        "advance_customer", "employee_loan"}:
        return "balance_sheet"
    return "other"


def group_type_for(report_group: str) -> str:
    if report_group in {"income", "expense", "equity_draw"}:
        return "temporary"
    if report_type_for(report_group) == "balance_sheet":
        return "permanent"
    return "other"


def list_accounts(sess, include_groups: bool = True) -> list[Account]:
    q = select(Account).order_by(Account.code)
    if not include_groups:
        q = q.where(Account.kind == "ledger")
    return list(sess.execute(q).scalars())


def account_map(sess) -> dict[int, Account]:
    return {a.id: a for a in list_accounts(sess)}


def account_by_code(sess, code: str) -> Account | None:
    return sess.execute(select(Account).where(Account.code == code)).scalar_one_or_none()


def label_map(sess) -> dict[int, str]:
    return {a.id: f"{a.code} — {a.name}" for a in list_accounts(sess)}


def create_account(sess, user: User, code: str, name: str, nature: str, parent_code: str | None,
                   report_group: str, ip: str = "", *, level: str | None = None,
                   group_type: str | None = None, report_type: str | None = None,
                   enforce_hierarchy: bool = False) -> Account:
    code = str(code).strip()
    name = str(name).strip()
    parent_code = str(parent_code).strip() if parent_code else None
    if not code or not name:
        raise DomainError("کد و نام حساب هر دو لازم است.")
    if len(code) > 20 or len(name) > 160:
        raise DomainError("طول کد یا عنوان حساب از حد مجاز بیشتر است.")
    if nature not in ("debit", "credit"):
        raise DomainError("ماهیت حساب باید بدهکار یا بستانکار باشد.")
    level = level or "detail"       # سازگاری با فرم/اکسل قدیمی: حساب تازه، تفصیلی است
    if level not in ACCOUNT_LEVELS:
        raise DomainError("سطح حساب باید گروه، کل، معین یا تفصیلی باشد.")
    kind = "ledger" if level == "detail" else "group"
    if account_by_code(sess, code):
        raise DomainError(f"حسابی با کد {code} از قبل وجود دارد.")
    parent = account_by_code(sess, parent_code) if parent_code else None
    if parent_code and parent is None:
        raise DomainError(f"حساب والد با کد {parent_code} پیدا نشد.")
    if enforce_hierarchy:
        required_parent_level = _PARENT_LEVEL.get(level)
        if required_parent_level is None and parent_code:
            raise DomainError("حساب گروه نباید حساب والد داشته باشد.")
        if required_parent_level is not None and parent is None:
            raise DomainError(f"برای حساب {ACCOUNT_LEVELS[level]}، حساب والد از نوع "
                              f"{ACCOUNT_LEVELS[required_parent_level]} را انتخاب کنید.")
        if parent is not None and parent.level != required_parent_level:
            raise DomainError(f"حساب والد باید از نوع {ACCOUNT_LEVELS[required_parent_level]} باشد.")
    report_group = str(report_group or "other").strip()[:20]
    group_type = group_type or group_type_for(report_group)
    report_type = report_type or report_type_for(report_group)
    if group_type not in GROUP_TYPES:
        raise DomainError("نوع گروه حساب نامعتبر است.")
    if report_type not in REPORT_TYPES:
        raise DomainError("نوع گزارش حساب نامعتبر است.")
    acc = Account(code=code, name=name, kind=kind, level=level, nature=nature,
                  parent_code=parent_code, report_group=report_group,
                  group_type=group_type, report_type=report_type)
    sess.add(acc)
    sess.flush()
    audit(sess, user, "account.create", "account", str(acc.id),
          f"{ACCOUNT_LEVELS[level]} {code} {name}", ip)
    return acc


def update_account(sess, user: User, account_id: int, name=None, is_active=None,
                   report_group=None, ip: str = "", group_type=None, report_type=None,
                   nature=None) -> Account:
    acc = sess.get(Account, account_id)
    if acc is None:
        raise DomainError("این حساب پیدا نشد.")
    if acc.is_system and is_active is False:
        raise DomainError("حساب‌های اصلی برنامه را نمی‌توان غیرفعال کرد.")
    if name is not None:
        value = str(name).strip()
        if not value or len(value) > 160:
            raise DomainError("عنوان حساب باید بین ۱ تا ۱۶۰ نویسه باشد.")
        acc.name = value
    if is_active is not None:
        if is_active is False and sess.execute(select(Account).where(Account.parent_code == acc.code,
                                                                      Account.is_active.is_(True))).first():
            raise DomainError("این حساب زیرحساب فعال دارد؛ ابتدا زیرحساب‌ها را غیرفعال کنید.")
        acc.is_active = bool(is_active)
    if report_group is not None:
        acc.report_group = str(report_group).strip()[:20] or "other"
        if group_type is None:
            acc.group_type = group_type_for(acc.report_group)
        if report_type is None:
            acc.report_type = report_type_for(acc.report_group)
    if group_type is not None:
        if group_type not in GROUP_TYPES:
            raise DomainError("نوع گروه حساب نامعتبر است.")
        acc.group_type = group_type
    if report_type is not None:
        if report_type not in REPORT_TYPES:
            raise DomainError("نوع گزارش حساب نامعتبر است.")
        acc.report_type = report_type
    if nature is not None:
        if nature not in ("debit", "credit"):
            raise DomainError("ماهیت حساب باید بدهکار یا بستانکار باشد.")
        acc.nature = nature
    audit(sess, user, "account.update", "account", str(acc.id), acc.code, ip)
    return acc


# --------------------------------------------------------------------- سند روزنامه
def _validated_lines(sess, raw_lines, require_balanced: bool = True) -> Validation:
    v = build_validation(raw_lines, require_balanced=require_balanced)
    if v.problems:
        return v
    accounts = {a.id: a for a in list_accounts(sess)}
    for i, line in enumerate(v.lines, start=1):
        acc = accounts.get(line.account_id)
        if acc is None:
            v.problems.append(f"سطر {i}: حسابی با این شناسه پیدا نشد.")
            continue
        if acc.kind != "ledger":
            v.problems.append(f"سطر {i}: «{acc.name}» یک حساب گروهی است و سند روی آن ثبت نمی‌شود؛ "
                              "یکی از حساب‌های زیرمجموعه‌اش را انتخاب کنید.")
        elif not acc.is_active:
            v.problems.append(f"سطر {i}: حساب «{acc.name}» غیرفعال است.")
    return v


def _assert_entry_date_open(sess, entry_date: date) -> tuple[FiscalYear, Period]:
    jy, jm = jd.period_of(entry_date)
    if not 1390 <= jy <= 1450:
        raise DomainError("تاریخ سند خارج از محدوده‌ی مجاز است.")
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    if year is not None and year.status == "closed":
        raise DomainError(
            f"سال مالی {jy} بسته است و ثبت سند در آن مجاز نیست؛ از مسیر مجاز بازگشایی سال اقدام کنید.",
            "year_closed",
        )
    period = period_for(sess, entry_date)
    if period.status == "closed":
        raise DomainError(
            f"دوره‌ی {jd.MONTHS[jm - 1]} {jy} بسته است و ثبت یا تأیید سند در آن مجاز نیست؛ "
            "تاریخ سند را بررسی کنید یا دوره را طبق فرایند مجاز باز کنید.",
            "period_closed",
        )
    year = sess.get(FiscalYear, period.fiscal_year_id)
    return year, period


def create_draft(sess, user: User, entry_date: date, description: str, raw_lines,
                 source: str = "manual", source_ref: str | None = None,
                 idempotency_key: str | None = None, ip: str = "") -> JournalEntry:
    if idempotency_key:
        existing = sess.execute(
            select(JournalEntry).where(JournalEntry.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    v = _validated_lines(sess, raw_lines, require_balanced=False)
    if v.problems:
        raise DomainError(v.message(), "invalid_lines")

    _assert_entry_date_open(sess, entry_date)

    entry = JournalEntry(
        entry_date=entry_date,
        jy=jd.jalali_parts(entry_date)[0],
        jm=jd.jalali_parts(entry_date)[1],
        description=str(description or "")[:400],
        status="draft",
        source=source,
        source_ref=source_ref,
        idempotency_key=idempotency_key,
        created_by=user.id,
    )
    for i, line in enumerate(v.lines, start=1):
        entry.lines.append(JournalLine(line_no=i, account_id=line.account_id,
                                       debit_rial=line.debit, credit_rial=line.credit,
                                       description=line.description))
    sess.add(entry)
    sess.flush()
    audit(sess, user, "entry.draft", "entry", str(entry.id),
          f"{jd.jalali_str(entry_date)} — {format_amount_fa(v.debit_total)} ریال", ip)
    return entry


def update_draft(sess, user: User, entry_id: int, entry_date: date | None = None,
                 description: str | None = None, raw_lines=None, ip: str = "") -> JournalEntry:
    entry = sess.get(JournalEntry, entry_id)
    if entry is None:
        raise DomainError("این سند پیدا نشد.")
    if entry.status != "draft":
        raise DomainError("سند ثبت‌شده قابل ویرایش نیست؛ باید ابطال و سند جدید ثبت شود.")
    if entry.created_by != user.id and not _can(user, "journal.edit"):
        raise DomainError("این پیش‌نویس را شما نساخته‌اید.", "forbidden")

    effective_date = entry_date or entry.entry_date
    _assert_entry_date_open(sess, effective_date)
    if raw_lines is not None:
        v = _validated_lines(sess, raw_lines, require_balanced=False)
        if v.problems:
            raise DomainError(v.message(), "invalid_lines")
        entry.lines.clear()
        sess.flush()
        for i, line in enumerate(v.lines, start=1):
            entry.lines.append(JournalLine(line_no=i, account_id=line.account_id,
                                           debit_rial=line.debit, credit_rial=line.credit,
                                           description=line.description))
    if entry_date is not None:
        entry.entry_date = entry_date
        entry.jy, entry.jm = jd.period_of(entry_date)
    if description is not None:
        entry.description = str(description)[:400]
    audit(sess, user, "entry.draft_update", "entry", str(entry.id), entry.description, ip)
    return entry


def _can(user: User, permission: str) -> bool:
    from .security import role_has

    return role_has(user.role, permission)


def post_entry(sess, user: User, entry_id: int, ip: str = "", *,
               allow_fiscal_document: bool = False) -> JournalEntry:
    """ثبت نهایی: تنها نقطه‌ای که یک سند وارد دفتر می‌شود.

    اختتامیه/افتتاحیه فقط پس از fingerprint و کنترل اختصاصی چرخه از مسیر fiscal ثبت می‌شوند.
    """
    entry = sess.get(JournalEntry, entry_id)
    if entry is None:
        raise DomainError("این سند پیدا نشد.")
    if entry.status == "posted":
        return entry
    if entry.status != "draft":
        raise DomainError("این سند در وضعیت قابل ثبت نیست (از قبل ابطال شده است).")
    if entry.source in {"opening", "closing"} and not allow_fiscal_document:
        raise DomainError("سند افتتاحیه/اختتامیه فقط از مسیر تأیید و نهایی‌سازی سال مالی قابل ثبت است.")

    raw = [{"account_id": l.account_id, "debit": l.debit_rial, "credit": l.credit_rial,
            "description": l.description} for l in entry.lines]
    v = _validated_lines(sess, raw)
    if v.problems:
        raise DomainError(v.message(), "invalid_lines")

    actual_jy, actual_jm = jd.period_of(entry.entry_date)
    if (entry.jy, entry.jm) != (actual_jy, actual_jm):
        raise DomainError("سال و دوره‌ی ذخیره‌شده با تاریخ سند یکسان نیست؛ پیش از ثبت، داده را اصلاح کنید.")
    _assert_entry_date_open(sess, entry.entry_date)

    last_no = sess.execute(
        select(func.max(JournalEntry.entry_no)).where(JournalEntry.entry_no.is_not(None),
                                                      JournalEntry.jy == entry.jy)
    ).scalar()
    seq = int(last_no.split("-")[1]) + 1 if last_no else 1
    entry.entry_no = f"{entry.jy}-{seq:06d}"
    entry.status = "posted"
    entry.posted_by = user.id
    entry.posted_at = datetime.now()
    sess.flush()   # شماره‌ی سند باید همان لحظه در محل داده‌ها بنشیند تا سند بعدی شماره‌ی تکراری نگیرد
    audit(sess, user, "entry.post", "entry", str(entry.id),
          f"{entry.entry_no} | {format_amount_fa(v.debit_total)} ریال", ip)
    return entry


def reverse_entry(sess, user: User, entry_id: int, reason: str, ip: str = "", *,
                  allow_fiscal_document: bool = False) -> JournalEntry:
    """ابطال سند: سند اصلی پاک نمی‌شود؛ یک سند معکوس ثبت می‌شود تا ردّ کار بماند."""
    entry = sess.get(JournalEntry, entry_id)
    if entry is None:
        raise DomainError("این سند پیدا نشد.")
    if entry.status != "posted":
        raise DomainError("فقط سند ثبت‌شده قابل ابطال است.")
    protected_fiscal_entry = (entry.source == "closing" or
                              (entry.source == "opening" and not
                               (entry.source_ref or "").startswith("external->")))
    if protected_fiscal_entry and not allow_fiscal_document:
        raise DomainError("سند اختتامیه و افتتاحیه‌ی انتقالی فقط از گردش کنترل‌شده‌ی سال مالی قابل ابطال است.")
    if entry.reversed_by_id:
        raise DomainError("این سند قبلاً ابطال شده است.")
    reason = str(reason or "").strip()
    if len(reason) < 5:
        raise DomainError("دلیل ابطال را بنویسید (حداقل چند کلمه).")

    rev = JournalEntry(
        entry_date=entry.entry_date,
        jy=entry.jy,
        jm=entry.jm,
        description=f"ابطال سند {entry.entry_no}: {reason}"[:400],
        status="draft",
        source="reversal",
        source_ref=entry.entry_no,
        created_by=user.id,
        reversal_of_id=entry.id,
    )
    for i, line in enumerate(entry.lines, start=1):
        rev.lines.append(JournalLine(
            line_no=i,
            account_id=line.account_id,
            debit_rial=line.credit_rial,
            credit_rial=line.debit_rial,
            description=f"ابطال: {line.description}"[:300],
        ))
    sess.add(rev)
    sess.flush()
    post_entry(sess, user, rev.id, ip)
    entry.status = "reversed"
    entry.reversed_by_id = rev.id
    audit(sess, user, "entry.reverse", "entry", str(entry.id),
          f"{entry.entry_no} → {rev.entry_no} | {reason}", ip)
    return rev


def list_entries(sess, frm: date | None = None, to: date | None = None,
                 status: str | None = None, text: str | None = None,
                 only_creator: int | None = None, limit: int = 200) -> list[JournalEntry]:
    q = select(JournalEntry).order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
    if frm:
        q = q.where(JournalEntry.entry_date >= frm)
    if to:
        q = q.where(JournalEntry.entry_date <= to)
    if status and status != "all":
        q = q.where(JournalEntry.status == status)
    if text:
        like = f"%{text.strip()}%"
        q = q.where(JournalEntry.description.ilike(like) | JournalEntry.entry_no.ilike(like))
    if only_creator is not None:
        q = q.where(JournalEntry.created_by == only_creator, JournalEntry.status == "draft")
    return list(sess.execute(q.limit(limit)).scalars())


def get_entry(sess, entry_id: int) -> JournalEntry:
    entry = sess.get(JournalEntry, entry_id)
    if entry is None:
        raise DomainError("این سند پیدا نشد.")
    return entry


# --------------------------------------------------------------------- مانده و گزارش
# سندهایی که در دفتر «هستند»: ثبت‌نهایی‌شده و ابطال‌شده. سند ابطال‌شده از دفتر پاک نمی‌شود؛
# سند معکوسش کنارش می‌ماند و اثر خالص صفر می‌شود (ردّ کار حفظ می‌شود).
BOOKED_STATUSES = ("posted", "reversed")


def _opening_boundary(sess, to: date | None) -> date | None:
    """آخرین افتتاحیه‌ی فعال تا تاریخ گزارش؛ مانده‌ی آن جایگزین سابقه‌ی قدیمی‌تر است."""
    q = select(JournalEntry.entry_date).where(
        JournalEntry.source == "opening", JournalEntry.status == "posted")
    if to is not None:
        q = q.where(JournalEntry.entry_date <= to)
    return sess.execute(q.order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
                        .limit(1)).scalar_one_or_none()


def posted_lines_query(sess, frm: date | None = None, to: date | None = None):
    # دفتر تاریخی نگه داشته می‌شود؛ اما افتتاحیه‌ی ثبت‌شده، مبنای مانده‌ی جاری است و نباید
    # با مانده‌ی پایان سال مبدأ جمع شود. گزارش‌های بازه‌ای قدیمی‌تر از افتتاحیه دست‌نخورده‌اند.
    boundary = _opening_boundary(sess, to)
    if boundary is not None and (frm is None or frm < boundary):
        frm = boundary
    q = (select(JournalLine.account_id,
                func.sum(JournalLine.debit_rial).label("debit"),
                func.sum(JournalLine.credit_rial).label("credit"))
         .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
         .where(JournalEntry.status.in_(BOOKED_STATUSES)))
    if frm:
        q = q.where(JournalEntry.entry_date >= frm)
    if to:
        q = q.where(JournalEntry.entry_date <= to)
    return q.group_by(JournalLine.account_id)


def account_balances(sess, frm: date | None = None, to: date | None = None) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for row in sess.execute(posted_lines_query(sess, frm, to)).all():
        out[row.account_id] = {"debit": int(row.debit or 0), "credit": int(row.credit or 0)}
    return out


def trial_balance(sess, frm: date, to: date) -> dict:
    accounts = account_map(sess)
    rows = []
    total_debit = total_credit = 0
    for acc_id, sums in account_balances(sess, frm, to).items():
        acc = accounts.get(acc_id)
        if acc is None:
            continue
        debit, credit = sums["debit"], sums["credit"]
        net = debit - credit
        if net == 0:
            continue
        rows.append({
            "account_id": acc_id,
            "code": acc.code,
            "name": acc.name,
            "debit": net if net > 0 else 0,
            "credit": -net if net < 0 else 0,
        })
        total_debit += net if net > 0 else 0
        total_credit += -net if net < 0 else 0
    rows.sort(key=lambda r: r["code"])
    return {"from": jd.jalali_str(frm), "to": jd.jalali_str(to), "rows": rows,
            "total_debit": total_debit, "total_credit": total_credit,
            "balanced": total_debit == total_credit}


# ---------- تراز ۴ و ۶ ستونی () ----------
def _balances_upto(sess, to: date | None) -> dict[int, int]:
    """مانده هر حساب تا تاریخ to (شامل افتتاحیه). net = debit - credit"""
    out: dict[int, int] = {}
    q = posted_lines_query(sess, None, to)
    for row in sess.execute(q).all():
        out[row.account_id] = int(row.debit or 0) - int(row.credit or 0)
    return out


def _turnover(sess, frm: date, to: date) -> dict[int, dict]:
    """گردش هر حساب فقط بین frm تا to: {'debit': x, 'credit': y}"""
    out: dict[int, dict] = {}
    q = posted_lines_query(sess, frm, to)
    for row in sess.execute(q).all():
        out[row.account_id] = {"debit": int(row.debit or 0), "credit": int(row.credit or 0)}
    return out


def trial_balance_4(sess, frm: date, to: date) -> dict:
    """۴ ستونی: گردش بدهکار/بستانکار طی دوره + مانده بدهکار/بستانکار پایان"""
    accounts = account_map(sess)
    turnover = _turnover(sess, frm, to)
    closing_net = _balances_upto(sess, to)
    # جمع همه حساب‌ها برای ردیف‌ها: هر حسابی که یا گردش دارد یا مانده
    all_ids = set(turnover.keys()) | set(closing_net.keys())
    rows = []
    t_deb = t_cre = c_deb = c_cre = 0
    for acc_id in all_ids:
        acc = accounts.get(acc_id)
        if acc is None:
            continue
        td = turnover.get(acc_id, {"debit": 0, "credit": 0})["debit"]
        tc = turnover.get(acc_id, {"debit": 0, "credit": 0})["credit"]
        net_close = closing_net.get(acc_id, 0)
        cd = net_close if net_close > 0 else 0
        cc = -net_close if net_close < 0 else 0
        if td == 0 and tc == 0 and cd == 0 and cc == 0:
            continue
        rows.append({
            "account_id": acc_id, "code": acc.code, "name": acc.name,
            "turn_debit": td, "turn_credit": tc,
            "close_debit": cd, "close_credit": cc,
        })
        t_deb += td
        t_cre += tc
        c_deb += cd
        c_cre += cc
    rows.sort(key=lambda r: r["code"])
    return {"from": jd.jalali_str(frm), "to": jd.jalali_str(to), "rows": rows,
            "total_turn_debit": t_deb, "total_turn_credit": t_cre,
            "total_close_debit": c_deb, "total_close_credit": c_cre,
            "balanced": t_deb == t_cre and c_deb == c_cre, "cols": 4}


def _opening_balance_for_period(sess, frm: date) -> dict[int, int]:
    """مانده‌ی ابتدای بازه؛ اگر بازه از روز ثبت افتتاحیه شروع شود، مانده‌ی قبلی صفر است."""
    day_before = date.fromordinal(frm.toordinal() - 1)
    jy = jd.jalali_parts(frm)[0]
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    if year and frm == year.start_date:
        opening = sess.execute(select(JournalEntry.id).where(
            JournalEntry.source == "opening", JournalEntry.jy == jy,
            JournalEntry.entry_date == year.start_date, JournalEntry.status == "posted")
        ).first()
        if opening:
            return {}
    return _balances_upto(sess, day_before)


def trial_balance_6(sess, frm: date, to: date) -> dict:
    """۶ ستونی: مانده اول + گردش + مانده آخر (هر کدام بدهکار/بستانکار)"""
    accounts = account_map(sess)
    opening_net = _opening_balance_for_period(sess, frm)
    turnover = _turnover(sess, frm, to)
    closing_net = _balances_upto(sess, to)
    all_ids = set(opening_net.keys()) | set(turnover.keys()) | set(closing_net.keys())
    rows = []
    o_deb = o_cre = t_deb = t_cre = c_deb = c_cre = 0
    for acc_id in all_ids:
        acc = accounts.get(acc_id)
        if acc is None:
            continue
        op = opening_net.get(acc_id, 0)
        td = turnover.get(acc_id, {"debit": 0, "credit": 0})["debit"]
        tc = turnover.get(acc_id, {"debit": 0, "credit": 0})["credit"]
        cl = closing_net.get(acc_id, 0)
        od = op if op > 0 else 0
        oc = -op if op < 0 else 0
        cd = cl if cl > 0 else 0
        cc = -cl if cl < 0 else 0
        if od==0 and oc==0 and td==0 and tc==0 and cd==0 and cc==0:
            continue
        rows.append({
            "account_id": acc_id, "code": acc.code, "name": acc.name,
            "open_debit": od, "open_credit": oc,
            "turn_debit": td, "turn_credit": tc,
            "close_debit": cd, "close_credit": cc,
        })
        o_deb += od
        o_cre += oc
        t_deb += td
        t_cre += tc
        c_deb += cd
        c_cre += cc
    rows.sort(key=lambda r: r["code"])
    return {"from": jd.jalali_str(frm), "to": jd.jalali_str(to), "rows": rows,
            "total_open_debit": o_deb, "total_open_credit": o_cre,
            "total_turn_debit": t_deb, "total_turn_credit": t_cre,
            "total_close_debit": c_deb, "total_close_credit": c_cre,
            "balanced": o_deb==o_cre and t_deb==t_cre and c_deb==c_cre, "cols": 6}


# ---------- گردش‌ها
def gerdash_account(sess, account_id: int, frm: date, to: date) -> dict:
    """گردش یک حساب خاص بین دو تاریخ — دقیقا دفتر با جزئیات بدهکار/بستانکار/مانده"""
    acc = sess.get(Account, account_id)
    if acc is None:
        raise DomainError("این حساب پیدا نشد.")
    # گردش‌ها؛ مانده‌ی ابتدای بازه در ledger محاسبه می‌شود.
    turn = _turnover(sess, frm, to).get(account_id, {"debit": 0, "credit": 0})
    # ریز گردش — همان ledger
    ld = ledger(sess, account_id, frm, to)
    return {
        "account": {"id": acc.id, "code": acc.code, "name": acc.name, "nature": acc.nature},
        "from": jd.jalali_str(frm), "to": jd.jalali_str(to),
        "opening": ld["opening"], "opening_label": "بدهکار" if ld["opening"]>0 else ("بستانکار" if ld["opening"]<0 else "صفر"),
        "turn_debit": turn["debit"], "turn_credit": turn["credit"],
        "closing": ld["closing"], "closing_label": "بدهکار" if ld["closing"]>0 else ("بستانکار" if ld["closing"]<0 else "صفر"),
        "rows": ld["rows"], # هر ردیف: date, entry_no, description, debit, credit, running
        "total_debit": ld["total_debit"], "total_credit": ld["total_credit"],
        "count": len(ld["rows"]),
    }


def gerdash_summary(sess, frm: date, to: date, search: str | None = None) -> dict:
    """فهرست همه حساب‌های قابل گردش با مانده و آخرین عملیات — برای حالت 'همه را ببینی'"""
    accounts = [a for a in list_accounts(sess) if getattr(a, "can_post", False) or getattr(a, "kind", "") == "ledger"]
    if search:
        s = search.strip().lower()
        accounts = [a for a in accounts if s in (a.code or "").lower() or s in (a.name or "").lower()]
    opening_map = _opening_balance_for_period(sess, frm)
    turn_map = _turnover(sess, frm, to)
    # تاریخ آخرین عملیات هر حساب
    last_map: dict[int, date | None] = {}
    if accounts:
        ids = [a.id for a in accounts]
        rows = sess.execute(
            select(JournalLine.account_id, func.max(JournalEntry.entry_date))
            .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
            .where(JournalLine.account_id.in_(ids), JournalEntry.status.in_(BOOKED_STATUSES),
                   JournalEntry.entry_date >= frm, JournalEntry.entry_date <= to)
            .group_by(JournalLine.account_id)
        ).all()
        for aid, d in rows:
            last_map[int(aid)] = d
    out = []
    for acc in sorted(accounts, key=lambda x: x.code):
        op = opening_map.get(acc.id, 0)
        tu = turn_map.get(acc.id, {"debit": 0, "credit": 0})
        closing = op + tu["debit"] - tu["credit"]
        label = "بدهکار" if closing > 0 else ("بستانکار" if closing < 0 else "صفر")
        last = last_map.get(acc.id)
        out.append({
            "account_id": acc.id, "code": acc.code, "name": acc.name,
            "report_group": acc.report_group,
            "opening": op, "turn_debit": tu["debit"], "turn_credit": tu["credit"],
            "closing": closing, "closing_label": label,
            "last_date": jd.jalali_str(last) if last else "—",
            "has_turn": bool(tu["debit"] or tu["credit"]),
        })
    return {"from": jd.jalali_str(frm), "to": jd.jalali_str(to), "rows": out, "count": len(out)}


def gerdash_group(sess, report_group: str, frm: date, to: date) -> dict:
    """گردش گروهی: مثلا همه حساب‌های درآمد یا هزینه"""
    accounts = [a for a in list_accounts(sess) if a.report_group == report_group]
    if not accounts:
        # fallback by nature
        if report_group in ("income", "revenue"):
            accounts = [a for a in list_accounts(sess) if a.report_group == "income"]
        elif report_group in ("expense", "cost"):
            accounts = [a for a in list_accounts(sess) if a.report_group == "expense"]
    rows = []
    total_debit = total_credit = 0
    for acc in accounts:
        turn = _turnover(sess, frm, to).get(acc.id, {"debit": 0, "credit": 0})
        net = turn["debit"] - turn["credit"]
        rows.append({"account_id": acc.id, "code": acc.code, "name": acc.name,
                     "debit": turn["debit"], "credit": turn["credit"], "net": net})
        total_debit += turn["debit"]
        total_credit += turn["credit"]
    return {"from": jd.jalali_str(frm), "to": jd.jalali_str(to), "group": report_group,
            "rows": rows, "total_debit": total_debit, "total_credit": total_credit}


def ledger(sess, account_id: int, frm: date, to: date) -> dict:
    acc = sess.get(Account, account_id)
    if acc is None:
        raise DomainError("این حساب پیدا نشد.")
    opening_net = _opening_balance_for_period(sess, frm).get(account_id, 0)
    opening = {"debit": max(opening_net, 0), "credit": max(-opening_net, 0)}
    running = opening["debit"] - opening["credit"]
    rows = sess.execute(
        select(JournalEntry.entry_date, JournalEntry.entry_no, JournalEntry.description,
               JournalLine.debit_rial, JournalLine.credit_rial, JournalLine.description)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .where(JournalLine.account_id == account_id, JournalEntry.status.in_(BOOKED_STATUSES),
               JournalEntry.entry_date >= frm, JournalEntry.entry_date <= to)
        .order_by(JournalEntry.entry_date, JournalEntry.id)
    ).all()
    out = []
    for r in rows:
        running += int(r.debit_rial) - int(r.credit_rial)
        out.append({
            "date": jd.jalali_str(r.entry_date),
            "entry_no": r.entry_no,
            "description": r.description or r[5],
            "debit": int(r.debit_rial),
            "credit": int(r.credit_rial),
            "running": running,
        })
    return {
        "account": {"id": acc.id, "code": acc.code, "name": acc.name, "nature": acc.nature},
        "opening": opening["debit"] - opening["credit"],
        "rows": out,
        "closing": running,
        "total_debit": sum(r["debit"] for r in out),
        "total_credit": sum(r["credit"] for r in out),
    }


def balance_of_code(sess, code: str, to: date | None = None) -> int:
    acc = account_by_code(sess, code)
    if acc is None:
        return 0
    sums = account_balances(sess, None, to).get(acc.id, {"debit": 0, "credit": 0})
    return sums["debit"] - sums["credit"]


def cash_and_bank(sess, to: date | None = None) -> dict:
    accounts = [a for a in list_accounts(sess) if a.report_group in ("cash", "bank")]
    sums = account_balances(sess, None, to)
    rows = []
    for a in accounts:
        s = sums.get(a.id, {"debit": 0, "credit": 0})
        rows.append({"id": a.id, "code": a.code, "name": a.name, "group": a.report_group,
                     "balance": s["debit"] - s["credit"]})
    return {"rows": rows, "total": sum(r["balance"] for r in rows)}


def receivables_payables(sess, to: date | None = None) -> dict:
    wanted = ("receivable", "payable", "cheque_received", "cheque_paid", "prepaid_supplier",
              "advance_customer", "employee_loan")
    accounts = [a for a in list_accounts(sess) if a.report_group in wanted]
    sums = account_balances(sess, None, to)
    rows = []
    for a in accounts:
        s = sums.get(a.id, {"debit": 0, "credit": 0})
        net = s["debit"] - s["credit"]
        rows.append({"id": a.id, "code": a.code, "name": a.name, "nature": a.nature,
                     "group": a.report_group, "balance": net,
                     "sign": "debit" if net > 0 else ("credit" if net < 0 else "zero")})
    return {"rows": rows}


# --------------------------------------------------------------------- انتقال وجه
def transfer_money(sess, user: User, from_account_id: int, to_account_id: int, amount_rial: int,
                   when: date, description: str = "", ip: str = "") -> JournalEntry:
    """انتقال وجه بین دو حساب خودمان: هیچ درآمد و هزینه‌ای ایجاد نمی‌کند.

    (سند از دو سطر ساخته می‌شود: مقصد بدهکار، مبدأ بستانکار — همان قاعده‌ی ماژول ۲.)
    """
    if from_account_id == to_account_id:
        raise DomainError("حساب مبدأ و مقصد یکی است.")
    accounts = {a.id: a for a in list_accounts(sess)}
    src, dst = accounts.get(from_account_id), accounts.get(to_account_id)
    if src is None or dst is None:
        raise DomainError("حساب مبدأ یا مقصد پیدا نشد.")
    for acc in (src, dst):
        if acc.report_group not in ("cash", "bank"):
            raise DomainError(f"«{acc.name}» از حساب‌های صندوق یا بانک نیست؛ انتقال وجه فقط بین آن‌ها انجام می‌شود.")
    if amount_rial <= 0:
        raise DomainError("مبلغ انتقال باید بزرگ‌تر از صفر باشد.")
    available = (account_balances(sess, None, None).get(src.id, {"debit": 0, "credit": 0}))
    balance = available["debit"] - available["credit"]
    entry = create_draft(
        sess, user, when,
        description or f"انتقال وجه از {src.name} به {dst.name}",
        [
            {"account_id": dst.id, "debit": amount_rial, "credit": 0,
             "description": f"واریز به {dst.name}"},
            {"account_id": src.id, "debit": 0, "credit": amount_rial,
             "description": f"برداشت از {src.name}"},
        ],
        source="transfer", ip=ip,
    )
    post_entry(sess, user, entry.id, ip)
    if balance - amount_rial < 0:
        # مانده منفی جلوی ثبت را نمی‌گیرد ولی به حسابدار اطلاع داده می‌شود.
        entry.description = (entry.description + " — توجه: مانده این حساب منفی شده است")[:400]
    return entry


# --------------------------------------------------------------------- داشبورد و ردّ کار
def audit(sess, user: User | None, action: str, entity: str = "", entity_id: str = "",
          detail: str = "", ip: str = "") -> None:
    sess.add(AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "system",
        action=action, entity=entity, entity_id=str(entity_id),
        detail=str(detail)[:2000], ip=ip[:60],
    ))


def dashboard(sess, to: date | None = None) -> dict:
    from .models import User as _User

    to = to or jd.jtoday()
    frm = jd.fiscal_year_of(to)[1]
    tb = trial_balance(sess, frm, to)
    draft_count = sess.execute(
        select(func.count(JournalEntry.id)).where(JournalEntry.status == "draft")
    ).scalar() or 0
    posted_count = sess.execute(
        select(func.count(JournalEntry.id)).where(JournalEntry.status.in_(BOOKED_STATUSES),
                                                 JournalEntry.entry_date >= frm,
                                                 JournalEntry.entry_date <= to)
    ).scalar() or 0
    cash = cash_and_bank(sess, to)
    # سود و زیان ساده از روی حساب‌های درآمد و هزینه
    income = expense = 0
    sums = account_balances(sess, frm, to)
    for acc in list_accounts(sess):
        s = sums.get(acc.id)
        if not s:
            continue
        if acc.report_group in ("income",):
            income += s["credit"] - s["debit"]
        elif acc.report_group == "expense":
            expense += s["debit"] - s["credit"]
    users_count = sess.execute(select(func.count(_User.id)).where(_User.is_active.is_(True))).scalar() or 0

    # چند سند آخر سال مالی، برای اینکه در نگاه اول معلوم باشد آخرین کارها چه بوده
    recent = []
    last = sess.execute(
        select(JournalEntry).where(JournalEntry.entry_date >= frm, JournalEntry.entry_date <= to)
        .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc()).limit(6)
    ).scalars()
    for e in last:
        amount = max(sum(l.debit_rial for l in e.lines), sum(l.credit_rial for l in e.lines))
        recent.append({"id": e.id, "entry_no": e.entry_no or "—", "date": jd.jalali_str(e.entry_date),
                       "description": e.description, "status": e.status, "amount": amount,
                       "amount_fa": format_amount_fa(amount)})

    return {
        "today": jd.jalali_long(to),
        "fiscal_year": jd.fiscal_year_of(to)[0],
        "cash": cash,
        "income": income,
        "expense": expense,
        "profit": income - expense,
        "draft_count": draft_count,
        "posted_count": posted_count,
        "trial": {"balanced": tb["balanced"], "total_debit": tb["total_debit"],
                  "total_credit": tb["total_credit"], "rows": len(tb["rows"])},
        "active_users": users_count,
        "recent": recent,
        "cash_rows": [{**r, "balance_fa": format_amount_fa(r["balance"])} for r in cash["rows"]],
    }
