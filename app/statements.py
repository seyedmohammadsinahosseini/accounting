"""صورت‌های مالی و بستن سال.

منطق دسته‌بندی، ساده و قابل توضیح است: کد حساب‌ها با ۱ شروع شود = دارایی، ۲ = بدهی،
۳ = حقوق مالکانه، ۴ = درآمد، ۵ = هزینه. (همه‌ی کدینگ برنامه همین قاعده را دارد.)

سه صورت:
۱) **سود و زیان** — درآمدها و هزینه‌های یک بازه، و سود (زیان) همان بازه.
۲) **ترازنامه** — وضعیت تا یک تاریخ: دارایی‌ها در یک طرف، بدهی‌ها و حقوق مالکانه در طرف دیگر.
   سودِ سالِ بسته‌نشده هم در طرف حقوق مالکانه می‌آید تا دو طرف برابر بمانند.
۳) **جریان نقدی** — پول چه طور آمد و کجا رفت، در سه سبد: عملیاتی، سرمایه‌گذاری، تأمین مالی.

بستن سال: حساب‌های درآمد و هزینه‌ی آن سال صفر می‌شوند (سند اختتامیه) و سود (زیان) به
«سود و زیان انباشته» می‌رود. بعد از آن، سند جدید در آن سال ثبت نمی‌شود تا بازش کنید.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json

from sqlalchemy import func, or_, select

from . import dates as jd
from . import services as svc
from .models import (Account, DepreciationRun, FiscalClosing, FiscalYear, Invoice, JournalEntry,
                     JournalLine, Party, PayrollRun, Period, SupplierBill)
from .money import format_amount_fa
from .rules import SYSTEM_CODES, DomainError

GROUP_OF_PREFIX = {"1": "asset", "2": "liability", "3": "equity", "4": "income", "5": "expense"}
GROUP_TITLES = {"asset": "دارایی‌ها", "liability": "بدهی‌ها", "equity": "حقوق مالکانه",
                "income": "درآمدها", "expense": "هزینه‌ها"}


def _accounts(sess) -> dict[int, Account]:
    return {a.id: a for a in sess.execute(select(Account)).scalars()}


def _flow(sess, frm: date | None, to: date, *, exclude_closing: bool = False,
          exclude_opening: bool = False) -> list[tuple[Account, int, int]]:
    """گردش حساب‌ها از دفتر ثبت‌شده؛ در صورت نیاز اسناد بستن/افتتاح را از گزارش عملیاتی کنار می‌گذارد."""
    q = (select(JournalLine, JournalEntry)
         .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
         .where(JournalEntry.status.in_(svc.BOOKED_STATUSES), JournalEntry.entry_date <= to))
    if frm:
        q = q.where(JournalEntry.entry_date >= frm)
    excluded_sources = []
    if exclude_closing:
        excluded_sources.append("closing")
    if exclude_opening:
        excluded_sources.append("opening")
    if excluded_sources:
        original_ids = select(JournalEntry.id).where(JournalEntry.source.in_(excluded_sources))
        q = q.where(JournalEntry.source.notin_(excluded_sources),
                    or_(JournalEntry.reversal_of_id.is_(None),
                        JournalEntry.reversal_of_id.not_in(original_ids)))
    sums: dict[int, list[int]] = {}
    for line, _entry in sess.execute(q):
        row = sums.setdefault(line.account_id, [0, 0])
        row[0] += line.debit_rial
        row[1] += line.credit_rial
    accounts = _accounts(sess)
    return [(accounts[aid], v[0], v[1]) for aid, v in sums.items() if aid in accounts]


def _account_group(account: Account) -> str | None:
    """طبقه‌ی گزارش از تنظیم حساب؛ پیشوند فقط برای دسته‌های قدیمی/مبهم fallback است."""
    group = account.report_group
    if group in {"asset", "cash", "bank", "receivable", "cheque_received", "prepaid_supplier", "employee_loan"}:
        return "asset"
    if group in {"liability", "payable", "cheque_paid", "advance_customer", "tax"}:
        return "liability"
    if group == "equity":
        return "equity"
    if group == "income":
        return "income"
    if group == "expense":
        return "expense"
    # گروه VAT در کدینگ پایه هم دارایی و هم بدهی است؛ برای سازگاری، کد فعلی تعیین‌کننده است.
    return GROUP_OF_PREFIX.get(account.code[:1])


def _signed(account: Account, debit: int, credit: int) -> int:
    """مانده‌ی گزارش: دارایی/هزینه مثبت بدهکار؛ بدهی/حقوق/درآمد مثبت بستانکار."""
    group = _account_group(account) or "asset"
    if group in ("asset", "expense"):
        return debit - credit
    return credit - debit


def _year_for_date(sess, d: date) -> FiscalYear | None:
    jy, _, _ = jd.jalali_parts(d)
    return sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()


def _active_opening_entry(sess, jy: int) -> JournalEntry | None:
    """افتتاحیه‌ی مؤثر: فقط سند افتتاحیه‌ی ثبت‌شده و ابطال‌نشده مرز دفتر را تعیین می‌کند."""
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    if year is None:
        return None
    return sess.execute(select(JournalEntry).where(
        JournalEntry.source == "opening", JournalEntry.jy == jy,
        JournalEntry.entry_date == year.start_date, JournalEntry.status == "posted",
    ).order_by(JournalEntry.id.desc())).scalars().first()


def _opening_scope_start(sess, d: date) -> date | None:
    # آخرین افتتاحیه‌ی فعال تا تاریخ گزارش، مبنای مانده‌ی جاری است؛ حتی اگر در سالی پیش‌تر ثبت شده باشد.
    return svc._opening_boundary(sess, d)


# ------------------------------------------------------------------ سود و زیان
def income_statement(sess, frm: date, to: date) -> dict:
    revenues, expenses = [], []
    for account, debit, credit in _flow(sess, frm, to, exclude_closing=True):
        group = _account_group(account)
        if group not in ("income", "expense"):
            continue
        amount = _signed(account, debit, credit)
        if amount == 0:
            continue
        (revenues if group == "income" else expenses).append(
            {"code": account.code, "name": account.name, "amount": amount})
    revenues.sort(key=lambda r: r["code"])
    expenses.sort(key=lambda r: r["code"])
    total_revenue = sum(r["amount"] for r in revenues)
    total_expense = sum(r["amount"] for r in expenses)
    profit = total_revenue - total_expense
    return {"from": jd.jalali_str(frm), "to": jd.jalali_str(to),
            "revenue_rows": revenues, "expense_rows": expenses,
            "total_revenue": total_revenue, "total_expense": total_expense,
            "gross_profit": total_revenue - total_expense, "profit": profit,
            "profit_margin_percent": round(profit * 100 / total_revenue, 1) if total_revenue else 0,
            "is_profit": profit >= 0}


# ------------------------------------------------------------------ ترازنامه
def balance_sheet(sess, to: date) -> dict:
    """دارایی‌ها یک طرف؛ بدهی‌ها + حقوق مالکانه طرف دیگر. سود سال بسته‌نشده در حقوق مالکانه می‌آید."""
    buckets: dict[str, list[dict]] = {"asset": [], "liability": [], "equity": []}
    open_profit = 0
    scope_start = _opening_scope_start(sess, to)
    for account, debit, credit in _flow(sess, scope_start, to):
        group = _account_group(account)
        if group is None:
            continue
        amount = _signed(account, debit, credit)
        if group in ("income", "expense"):
            open_profit += amount if group == "income" else -amount
            continue
        if amount != 0:
            buckets[group].append({"code": account.code, "name": account.name, "amount": amount})

    for rows in buckets.values():
        rows.sort(key=lambda r: r["code"])
    total_assets = sum(r["amount"] for r in buckets["asset"])
    total_liabilities = sum(r["amount"] for r in buckets["liability"])
    equity_rows = list(buckets["equity"])
    if open_profit:
        equity_rows.append({"code": "—", "name": "(سود) زیان سال جاری — بسته‌نشده",
                            "amount": open_profit})
        equity_rows.sort(key=lambda r: r["code"])
    total_equity = sum(r["amount"] for r in equity_rows)
    difference = total_assets - (total_liabilities + total_equity)
    return {"to": jd.jalali_str(to), "assets": buckets["asset"], "liabilities": buckets["liability"],
            "equity": equity_rows, "total_assets": total_assets,
            "total_liabilities": total_liabilities, "total_equity": total_equity,
            "open_profit": open_profit, "difference": difference, "balanced": difference == 0}


# ------------------------------------------------------------------ جریان نقدی
CASH_GROUPS = ("cash", "bank")
INVESTING = ("1109", "1110")          # دارایی ثابت و استهلاک انباشته
FINANCING = ("3101", "3102", "3103")  # سرمایه، سود و زیان انباشته، برداشت مالک


def _flow_bucket(account: Account) -> str:
    code = account.code
    if code.startswith(INVESTING):
        return "investing"
    if code.startswith(FINANCING) or code[:4] in FINANCING:
        return "financing"
    return "operating"


def cash_flow(sess, frm: date, to: date) -> dict:
    """روش مستقیم: هر سند که پول در آن دخیل است، به همان اندازه بین سبدها تقسیم می‌شود."""
    accounts = _accounts(sess)
    scope_start = _opening_scope_start(sess, to)
    day_before = frm - timedelta(days=1)
    opening = closing = 0
    if scope_start:
        if frm == scope_start:
            opening_entry = _active_opening_entry(sess, int(jd.jalali_parts(to)[0]))
            if opening_entry:
                for line in opening_entry.lines:
                    account = accounts.get(line.account_id)
                    if account and account.report_group in CASH_GROUPS:
                        opening += line.debit_rial - line.credit_rial
        else:
            for account, debit, credit in _flow(sess, scope_start, day_before):
                if account.report_group in CASH_GROUPS:
                    opening += debit - credit
        for account, debit, credit in _flow(sess, scope_start, to):
            if account.report_group in CASH_GROUPS:
                closing += debit - credit
    else:
        for account, debit, credit in _flow(sess, None, to):
            if account.report_group in CASH_GROUPS:
                closing += debit - credit
        for account, debit, credit in _flow(sess, None, day_before):
            if account.report_group in CASH_GROUPS:
                opening += debit - credit

    buckets: dict[str, list[dict]] = {"operating": [], "investing": [], "financing": []}
    totals: dict[str, int] = {"operating": 0, "investing": 0, "financing": 0}
    opening_ids = select(JournalEntry.id).where(JournalEntry.source == "opening")
    q = (select(JournalEntry).where(JournalEntry.status.in_(svc.BOOKED_STATUSES),
                                    JournalEntry.entry_date >= frm,
                                    JournalEntry.entry_date <= to,
                                    JournalEntry.source != "opening",
                                    or_(JournalEntry.reversal_of_id.is_(None),
                                        JournalEntry.reversal_of_id.not_in(opening_ids)))
         .order_by(JournalEntry.entry_date, JournalEntry.id))
    for entry in sess.execute(q).scalars():
        cash_lines = [l for l in entry.lines if l.account_id in accounts
                      and accounts[l.account_id].report_group in CASH_GROUPS]
        if not cash_lines:
            continue
        others = [l for l in entry.lines if l not in cash_lines]
        cash_amount = sum(l.debit_rial - l.credit_rial for l in cash_lines)
        if cash_amount == 0 or not others:
            continue
        # تقسیم پول بین سبدها به نسبت سهم هر سطر مقابل — همیشه جمعش برابر مبلغ سند می‌شود
        weights = [abs(l.debit_rial - l.credit_rial) for l in others]
        total_weight = sum(weights) or 1
        remaining = cash_amount
        for index, (line, weight) in enumerate(zip(others, weights)):
            share = remaining if index == len(others) - 1 else int(round(cash_amount * weight / total_weight))
            remaining -= share
            if share == 0:
                continue
            bucket = _flow_bucket(accounts.get(line.account_id, Account(code="9")))
            direction = "in" if share > 0 else "out"
            buckets[bucket].append({
                "date": jd.jalali_str(entry.entry_date), "entry_no": entry.entry_no,
                "description": entry.description, "direction": direction, "amount": abs(share),
                "counter_account": accounts[line.account_id].name if line.account_id in accounts else "?",
            })
            totals[bucket] += share

    net = sum(totals.values())
    return {"from": jd.jalali_str(frm), "to": jd.jalali_str(to),
            "opening_cash": opening, "closing_cash": closing, "change": closing - opening,
            "operating": buckets["operating"], "investing": buckets["investing"],
            "financing": buckets["financing"],
            "total_operating": totals["operating"], "total_investing": totals["investing"],
            "total_financing": totals["financing"], "net": net,
            "matches_cash": net == closing - opening}


# ------------------------------------------------------------------ بستن و افتتاح سال مالی

def fiscal_years(sess) -> list[dict]:
    closings = {c.jy: c for c in sess.execute(select(FiscalClosing)).scalars()}
    periods_by_year: dict[int, list[Period]] = {}
    for period in sess.execute(select(Period).order_by(Period.jy, Period.jm)).scalars():
        periods_by_year.setdefault(period.jy, []).append(period)
    out = []
    for year in sess.execute(select(FiscalYear).order_by(FiscalYear.title.desc())).scalars():
        jy = int(year.title) if year.title.isdigit() else 0
        closing = closings.get(jy)
        draft_count = sess.execute(select(func.count(JournalEntry.id)).where(
            JournalEntry.jy == jy, JournalEntry.status == "draft")).scalar() or 0
        opening_entries = list(sess.execute(select(JournalEntry).where(
            JournalEntry.source == "opening", JournalEntry.jy == jy,
            JournalEntry.entry_date == year.start_date,
            JournalEntry.status.in_(("draft", "posted", "reversed")),
        ).order_by(JournalEntry.id.desc())).scalars()) if jy else []
        opening = next((entry for entry in opening_entries if entry.status == "posted"), None)
        opening_draft = next((entry for entry in opening_entries if entry.status == "draft"), None)
        opening_status = ("posted" if opening else "draft" if opening_draft else
                          "reversed" if any(entry.status == "reversed" for entry in opening_entries)
                          else "missing")
        out.append({
            "title": year.title, "jy": jy, "start": jd.jalali_str(year.start_date),
            "end": jd.jalali_str(year.end_date), "status": year.status,
            "profit": closing.profit_rial if closing else None,
            "closed_at": closing.created_at.strftime("%Y-%m-%d %H:%M") if closing else "",
            "closed_by": closing.created_by if closing else None,
            "draft_count": draft_count,
            "closing_entry_id": closing.entry_id if closing else None,
            "closing_status": closing.status if closing else "not_started",
            "opening_entry_id": opening.id if opening else None,
            "opening_draft_entry_id": opening_draft.id if opening_draft else None,
            "opening_status": opening_status,
            "periods": [{"jm": p.jm, "status": p.status,
                         "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                         "closed_by": p.closed_by}
                        for p in periods_by_year.get(jy, [])],
        })
    return out


def _stable_hash(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _year_entries(sess, year: FiscalYear) -> list[JournalEntry]:
    return list(sess.execute(select(JournalEntry).where(
        JournalEntry.entry_date >= year.start_date,
        JournalEntry.entry_date <= year.end_date,
        JournalEntry.status.in_(svc.BOOKED_STATUSES),
    ).order_by(JournalEntry.id)).scalars())


def _entry_snapshot(entries: list[JournalEntry]) -> list[dict]:
    return [{
        "id": entry.id, "status": entry.status, "source": entry.source,
        "source_ref": entry.source_ref, "entry_date": entry.entry_date.isoformat(),
        "reversed_by_id": entry.reversed_by_id, "reversal_of_id": entry.reversal_of_id,
        "lines": [{"id": line.id, "account_id": line.account_id,
                   "debit": line.debit_rial, "credit": line.credit_rial}
                  for line in entry.lines],
    } for entry in entries]


def _closing_calculation(sess, year: FiscalYear) -> dict:
    accounts = _accounts(sess)
    turnover = _flow(sess, year.start_date, year.end_date)
    lines: list[dict] = []
    rows: list[dict] = []
    revenue_total = expense_total = 0
    issues: list[str] = []

    for account, debit, credit in turnover:
        is_candidate = (account.group_type == "temporary"
                        and account.report_type == "income_statement"
                        and account.report_group in ("income", "expense"))
        classification_conflict = (
            (account.report_group in ("income", "expense")
             and (account.group_type != "temporary" or account.report_type != "income_statement"))
            or (account.group_type == "temporary" and account.report_type == "income_statement"
                and account.report_group not in ("income", "expense"))
        )
        if classification_conflict and (debit or credit):
            issues.append(f"حساب {account.code} ({account.name}) گردش دارد اما طبقه‌بندی دائمی/موقت و گزارش آن سازگار نیست.")
        if not is_candidate:
            continue
        amount = _signed(account, debit, credit)
        if amount == 0:
            continue
        if not account.is_active:
            issues.append(f"حساب موقت {account.code} مانده دارد اما غیرفعال است و در سند قابل استفاده نیست.")
        if account.report_group == "income":
            revenue_total += amount
            line = ({"account_id": account.id, "debit": amount, "credit": 0,
                     "description": f"بستن حساب درآمد {account.name}"} if amount > 0 else
                    {"account_id": account.id, "debit": 0, "credit": -amount,
                     "description": f"بستن حساب درآمد {account.name}"})
            rows.append({"account_id": account.id, "code": account.code, "name": account.name,
                         "group": "income", "balance": amount, "debit": line["debit"],
                         "credit": line["credit"], "reason": "صفرکردن مانده‌ی درآمد"})
            lines.append(line)
        else:
            expense_total += amount
            line = ({"account_id": account.id, "debit": 0, "credit": amount,
                     "description": f"بستن حساب هزینه {account.name}"} if amount > 0 else
                    {"account_id": account.id, "debit": -amount, "credit": 0,
                     "description": f"بستن حساب هزینه {account.name}"})
            rows.append({"account_id": account.id, "code": account.code, "name": account.name,
                         "group": "expense", "balance": amount, "debit": line["debit"],
                         "credit": line["credit"], "reason": "صفرکردن مانده‌ی هزینه"})
            lines.append(line)

    profit = revenue_total - expense_total
    retained = svc.account_by_code(sess, SYSTEM_CODES["retained"])
    if (retained is None or retained.kind != "ledger" or not retained.is_active
            or retained.group_type != "permanent" or retained.report_type != "balance_sheet"
            or retained.report_group != "equity"):
        issues.append("حساب سیستمی سود و زیان انباشته پیدا نشد یا طبقه‌بندی/وضعیت آن معتبر نیست.")
        retained_before = 0
    else:
        raw = svc.balance_of_code(sess, retained.code, year.end_date)
        retained_before = -raw if retained.nature == "credit" else raw
        if profit > 0:
            line = {"account_id": retained.id, "debit": 0, "credit": profit,
                    "description": f"انتقال سود سال {year.title} به سود و زیان انباشته"}
            lines.append(line)
            rows.append({"account_id": retained.id, "code": retained.code, "name": retained.name,
                         "group": "equity", "balance": retained_before, "debit": 0,
                         "credit": profit, "reason": "انتقال سود خالص سال"})
        elif profit < 0:
            line = {"account_id": retained.id, "debit": -profit, "credit": 0,
                    "description": f"انتقال زیان سال {year.title} به سود و زیان انباشته"}
            lines.append(line)
            rows.append({"account_id": retained.id, "code": retained.code, "name": retained.name,
                         "group": "equity", "balance": retained_before, "debit": -profit,
                         "credit": 0, "reason": "انتقال زیان خالص سال"})

    entry_debit = sum(line["debit"] for line in lines)
    entry_credit = sum(line["credit"] for line in lines)
    if entry_debit != entry_credit:
        issues.append(f"پیش‌نویس اختتامیه تراز نیست: بدهکار {entry_debit} و بستانکار {entry_credit} ریال.")
    return {"lines": lines, "rows": rows, "revenue": revenue_total,
            "expense": expense_total, "profit": profit,
            "retained_before": retained_before, "retained_after": retained_before + profit,
            "debit": entry_debit, "credit": entry_credit, "issues": issues,
            "accounts": accounts}


def closing_precheck(sess, jy: int, ignore_draft_id: int | None = None) -> dict:
    """پیش‌بررسی مستقل و بدون تغییر دفتر؛ خطاهای قطعی با هشدارهای تکمیلی جدا هستند."""
    errors: list[dict] = []
    warnings: list[dict] = []
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    if year is None:
        return {"jy": jy, "calculated_at": datetime.now().isoformat(timespec="seconds"),
                "ready": False, "errors": [{"code": "year_missing", "message": f"سال مالی {jy} در برنامه ثبت نشده است."}],
                "warnings": [], "rows": [], "fingerprint": "", "totals": {}}
    if year.status != "open":
        errors.append({"code": "year_closed", "message": f"سال {jy} باز نیست و بستن دوباره مجاز نیست."})

    periods = list(sess.execute(select(Period).where(Period.jy == jy).order_by(Period.jm)).scalars())
    periods_by_month = {p.jm: p for p in periods}
    missing_months = [month for month in range(1, 13) if month not in periods_by_month]
    if missing_months:
        errors.append({"code": "periods_missing", "message": "دوره‌های ماهانه‌ی سال کامل نیستند: "
                       + "، ".join(jd.MONTHS[m - 1] for m in missing_months)})
    es_fand = periods_by_month.get(12)
    if es_fand is None:
        errors.append({"code": "year_end_period_missing", "message": "دوره‌ی اسفند ساخته نشده است."})
    elif es_fand.status == "closed":
        errors.append({"code": "year_end_period_closed",
                       "message": "طبق سیاست انتخاب‌شده، ابتدا دوره‌ی اسفند را از صفحه‌ی دوره‌های مالی باز کنید؛ سپس اختتامیه را بسازید."})
    open_months = [p.jm for p in periods if p.status == "open"]
    if open_months:
        warnings.append({"code": "periods_open", "message": "دوره‌های باز هنگام نهایی‌سازی سال بسته می‌شوند: "
                         + "، ".join(jd.MONTHS[m - 1] for m in open_months)})

    draft_entries = list(sess.execute(select(JournalEntry).where(
        JournalEntry.jy == jy, JournalEntry.status == "draft").order_by(JournalEntry.id)).scalars())
    blocking_drafts = []
    for entry in draft_entries:
        if ignore_draft_id and entry.id == ignore_draft_id:
            continue
        # پیش‌نویس‌های اختتامیه‌ی قبلی قابل بازتولیدند؛ پیش‌نویس‌های عملیاتی باید تعیین تکلیف شوند.
        if entry.source == "closing" and (ignore_draft_id is None or entry.id == ignore_draft_id):
            continue
        blocking_drafts.append(entry)
    if blocking_drafts:
        errors.append({"code": "journal_drafts", "message": f"{len(blocking_drafts)} پیش‌نویس حسابداری در سال مانده است؛ ابتدا تعیین تکلیف کنید."})

    pending_counts = {
        "invoices": sess.execute(select(func.count(Invoice.id)).where(
            Invoice.doc_date >= year.start_date, Invoice.doc_date <= year.end_date,
            Invoice.status == "draft")).scalar() or 0,
        "supplier_bills": sess.execute(select(func.count(SupplierBill.id)).where(
            SupplierBill.doc_date >= year.start_date, SupplierBill.doc_date <= year.end_date,
            SupplierBill.status == "draft")).scalar() or 0,
        "payroll": sess.execute(select(func.count(PayrollRun.id)).where(
            PayrollRun.jy == jy, PayrollRun.status == "draft")).scalar() or 0,
        "depreciation": sess.execute(select(func.count(DepreciationRun.id)).where(
            DepreciationRun.jy == jy, DepreciationRun.status == "draft")).scalar() or 0,
    }
    for key, label in (("invoices", "فاکتور فروش"), ("supplier_bills", "صورتحساب تأمین‌کننده"),
                       ("payroll", "لیست حقوق"), ("depreciation", "محاسبه‌ی استهلاک")):
        if pending_counts[key]:
            warnings.append({"code": f"pending_{key}", "message": f"{pending_counts[key]} مورد {label} پیش‌نویس/ناتمام است؛ وضعیت زیرسیستم را بازبینی کنید."})

    entries = _year_entries(sess, year)
    for entry in entries:
        debit = sum(line.debit_rial for line in entry.lines)
        credit = sum(line.credit_rial for line in entry.lines)
        if debit != credit:
            errors.append({"code": "unbalanced_posted_entry",
                           "message": f"سند ثبت‌شده‌ی شناسه {entry.id} تراز نیست ({debit} در برابر {credit})."})
        if entry.status == "reversed":
            reversal = sess.get(JournalEntry, entry.reversed_by_id) if entry.reversed_by_id else None
            if reversal is None or reversal.reversal_of_id != entry.id:
                errors.append({"code": "broken_reversal", "message": f"رابطه‌ی ابطال سند شناسه {entry.id} ناسازگار است."})
        if entry.reversal_of_id:
            original = sess.get(JournalEntry, entry.reversal_of_id)
            if original is None or original.reversed_by_id != entry.id:
                errors.append({"code": "broken_reversal", "message": f"ارجاع سند معکوس شناسه {entry.id} ناسازگار است."})

    calc = _closing_calculation(sess, year)
    for issue in calc["issues"]:
        errors.append({"code": "closing_calculation", "message": issue})
    # مانده‌ی باقیمانده در حساب موقت پس از ثبت‌های موجود، مانع بستن سالم است اگر طبقه‌بندی موقت باشد.
    for account, debit, credit in _flow(sess, year.start_date, year.end_date):
        if account.group_type == "temporary" and account.report_type == "income_statement" \
                and account.report_group in ("income", "expense") and _signed(account, debit, credit) != 0 \
                and not account.is_active:
            continue  # خطای غیرفعال‌بودن بالا علت دقیق را گزارش می‌کند.

    closing_row = sess.execute(select(FiscalClosing).where(FiscalClosing.jy == jy)).scalar_one_or_none()
    if closing_row and closing_row.status == "closed" and year.status != "closed":
        errors.append({"code": "closing_state_mismatch", "message": "وضعیت رکورد اختتامیه با وضعیت سال مالی سازگار نیست."})

    # اثر انگشت داده‌های مؤثر؛ با تغییر سند، سطر، طبقه‌بندی یا دوره، پیش‌نمایش قبلی باطل می‌شود.
    account_snapshot = [{"id": a.id, "code": a.code, "name": a.name, "active": a.is_active,
                         "kind": a.kind, "group_type": a.group_type,
                         "report_type": a.report_type, "report_group": a.report_group,
                         "nature": a.nature}
                        for a in sorted(calc["accounts"].values(), key=lambda item: item.id)]
    fingerprint = _stable_hash({
        "jy": jy, "start": year.start_date.isoformat(), "end": year.end_date.isoformat(),
        "year_status": year.status,
        "periods": [(p.jm, p.status) for p in periods],
        "entries": _entry_snapshot(entries), "accounts": account_snapshot,
        "calc": {k: calc[k] for k in ("revenue", "expense", "profit", "retained_before", "lines")},
        "pending": pending_counts,
    })
    return {"jy": jy, "calculated_at": datetime.now().isoformat(timespec="seconds"),
            "ready": not errors, "errors": errors, "warnings": warnings,
            "periods": [{"jm": p.jm, "status": p.status} for p in periods],
            "pending": pending_counts, "rows": calc["rows"], "fingerprint": fingerprint,
            "totals": {"revenue": calc["revenue"], "expense": calc["expense"],
                       "profit": calc["profit"], "debit": calc["debit"], "credit": calc["credit"],
                       "difference": calc["debit"] - calc["credit"],
                       "retained_before": calc["retained_before"],
                       "retained_after": calc["retained_after"]}}


def create_closing_draft(sess, user, jy: int, fingerprint: str, ip: str = "") -> JournalEntry | None:
    precheck = closing_precheck(sess, jy)
    if not precheck["ready"]:
        raise DomainError("پیش‌بررسی بستن سال مانع ثبت پیش‌نویس است: "
                          + "؛ ".join(item["message"] for item in precheck["errors"]))
    if fingerprint != precheck["fingerprint"]:
        raise DomainError("داده‌های سال پس از پیش‌نمایش تغییر کرده‌اند؛ پیش‌بررسی را دوباره اجرا کنید.")
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one()
    calc = _closing_calculation(sess, year)
    source_ref = f"{jy}:{fingerprint[:40]}"
    existing = sess.execute(select(JournalEntry).where(
        JournalEntry.source == "closing", JournalEntry.jy == jy,
        JournalEntry.status == "draft").order_by(JournalEntry.id.desc())).scalars().first()
    if not calc["lines"]:
        svc.audit(sess, user, "fiscal.closing_preview", "fiscal_year", str(jy),
                  "سال بدون مانده‌ی موقت؛ پیش‌نویس روزنامه لازم نیست.", ip)
        return None
    if existing:
        svc.update_draft(sess, user, existing.id, entry_date=year.end_date,
                         description=f"سند اختتامیه‌ی سال مالی {jy}", raw_lines=calc["lines"], ip=ip)
        existing.source_ref = source_ref
        entry = existing
    else:
        prior_count = sess.execute(select(func.count(JournalEntry.id)).where(
            JournalEntry.source == "closing", JournalEntry.jy == jy)).scalar() or 0
        entry = svc.create_draft(sess, user, year.end_date,
                                 f"سند اختتامیه‌ی سال مالی {jy}", calc["lines"],
                                 source="closing", source_ref=source_ref,
                                 idempotency_key=f"fclose-{jy}-{prior_count + 1}", ip=ip)
    svc.audit(sess, user, "fiscal.closing_draft", "fiscal_year", str(jy),
              f"پیش‌نویس سند اختتامیه با شناسه {entry.id} ساخته/به‌روز شد.", ip)
    return entry


def finalize_close_year(sess, user, jy: int, fingerprint: str,
                        draft_entry_id: int | None, note: str = "", ip: str = "") -> FiscalClosing:
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    if year is None:
        raise DomainError(f"سال مالی {jy} در برنامه ثبت نشده است.")
    existing_closing = sess.execute(select(FiscalClosing).where(FiscalClosing.jy == jy)).scalar_one_or_none()
    if year.status == "closed":
        if (existing_closing and existing_closing.status == "closed"
                and existing_closing.entry_id == draft_entry_id):
            return existing_closing  # ارسال مجدد همان درخواست موفق، بی‌اثر است.
        raise DomainError(f"سال {jy} قبلاً بسته شده است.")

    precheck = closing_precheck(sess, jy, ignore_draft_id=draft_entry_id)
    if not precheck["ready"]:
        raise DomainError("پیش‌بررسی نهایی بستن سال ناموفق است: "
                          + "؛ ".join(item["message"] for item in precheck["errors"]))
    if fingerprint != precheck["fingerprint"]:
        raise DomainError("اطلاعات مالی از زمان بازبینی تغییر کرده است؛ پیش‌نویس را دوباره محاسبه و بازبینی کنید.")
    calc = _closing_calculation(sess, year)
    entry = None
    if calc["lines"]:
        entry = sess.get(JournalEntry, draft_entry_id) if draft_entry_id else None
        expected_ref = f"{jy}:{fingerprint[:40]}"
        if (entry is None or entry.source != "closing" or entry.source_ref != expected_ref
                or entry.jy != jy or entry.status != "draft"):
            raise DomainError("پیش‌نویس اختتامیه معتبر یا قابل ثبت نیست؛ آن را دوباره تولید کنید.")
        expected = sorted((line["account_id"], line["debit"], line["credit"], line["description"])
                          for line in calc["lines"])
        actual = sorted((line.account_id, line.debit_rial, line.credit_rial, line.description)
                        for line in entry.lines)
        if expected != actual:
            raise DomainError("سطرهای پیش‌نویس با محاسبه‌ی تازه یکسان نیست؛ پیش‌نویس را دوباره بسازید.")
        svc.post_entry(sess, user, entry.id, ip, allow_fiscal_document=True)

    # فقط بعد از ثبت موفق سند، وضعیت دوره‌ها و سال در همین تراکنش نهایی می‌شود.
    for period in sess.execute(select(Period).where(Period.jy == jy).order_by(Period.jm)).scalars():
        if period.status != "closed":
            period.status = "closed"
            period.closed_at = datetime.now()
            period.closed_by = user.id
            svc.audit(sess, user, "period.close", "period", f"{jy}-{period.jm}",
                      f"بستن دوره در نهایی‌سازی سال مالی {jy}؛ وضعیت قبل: باز؛ وضعیت بعد: بسته.", ip)
    year.status = "closed"
    closing = existing_closing or FiscalClosing(jy=jy)
    if existing_closing is None:
        sess.add(closing)
    closing.status = "closed"
    closing.entry_id = entry.id if entry else None
    closing.revenue_rial = calc["revenue"]
    closing.expense_rial = calc["expense"]
    closing.profit_rial = calc["profit"]
    closing.retained_before_rial = calc["retained_before"]
    closing.retained_after_rial = calc["retained_after"]
    closing.note = str(note or "")[:255]
    closing.created_by = user.id
    closing.created_at = datetime.now()
    sess.flush()
    svc.audit(sess, user, "fiscal.close_year", "fiscal_year", str(jy),
              f"وضعیت سال: باز→بسته؛ سود/زیان {format_amount_fa(calc['profit'])} ریال؛ "
              f"سند اختتامیه {entry.id if entry else 'بدون سطر مالی'}؛ fingerprint {fingerprint[:16]}", ip)
    return closing


def close_year(sess, user, jy: int, note: str = "", ip: str = "") -> FiscalClosing:
    """سازگاری داخلی: بستن بدون بازبینی مجزا مجاز نیست؛ API جدید شناسه‌ی پیش‌نویس می‌خواهد."""
    raise DomainError("برای بستن سال، ابتدا پیش‌بررسی و پیش‌نویس اختتامیه را اجرا و بازبینی کنید.")


def _fiscal_dates(jy: int) -> tuple[date, date]:
    start = date(*jd.jalali_to_gregorian(jy, 1, 1))
    next_start = date(*jd.jalali_to_gregorian(jy + 1, 1, 1))
    return start, date.fromordinal(next_start.toordinal() - 1)


def create_fiscal_year(sess, user, jy: int, ip: str = "") -> FiscalYear:
    """ایجاد صریح یک سال شمسی باز، بدون ایجاد خودکار سند افتتاحیه یا اختتامیه."""
    if not 1390 <= int(jy) <= 1450:
        raise DomainError("سال مالی خارج از محدوده‌ی مجاز برنامه است.")
    title = str(int(jy))
    existing = sess.execute(select(FiscalYear).where(FiscalYear.title == title)).scalar_one_or_none()
    if existing is not None:
        raise DomainError(f"سال مالی {title} از قبل ایجاد شده است.", "year_exists")
    start, end = _fiscal_dates(int(jy))
    overlapping = sess.execute(select(FiscalYear).where(
        FiscalYear.start_date <= end, FiscalYear.end_date >= start)).scalars().first()
    if overlapping is not None:
        raise DomainError(f"بازه‌ی سال {title} با سال مالی {overlapping.title} هم‌پوشانی دارد.")
    stray_period = sess.execute(select(Period.id).where(Period.jy == int(jy)).limit(1)).first()
    if stray_period:
        raise DomainError(f"برای سال {title} دوره‌هایی بدون رکورد سال مالی پیدا شد؛ ایجاد متوقف شد تا داده بررسی شود.")
    year = svc.ensure_fiscal_year(sess, start)
    if year.start_date != start or year.end_date != end or year.status != "open":
        raise DomainError(f"اطلاعات سال {title} با تقویم مورد انتظار سازگار نیست.")
    periods = list(sess.execute(select(Period).where(Period.jy == int(jy)).order_by(Period.jm)).scalars())
    if len(periods) != 12 or [p.jm for p in periods] != list(range(1, 13)):
        raise DomainError(f"دوره‌های ماهانه‌ی سال {title} کامل یا یکتا نیستند.")
    svc.audit(sess, user, "fiscal.create_year", "fiscal_year", title,
              f"سال باز ایجاد شد؛ شروع {jd.jalali_str(start)}؛ پایان {jd.jalali_str(end)}؛ ۱۲ دوره‌ی باز.", ip)
    return year


def _year_end_balances(sess, year: FiscalYear) -> dict[int, int]:
    """مانده‌ی حساب تا پایان سال؛ در صورت وجود افتتاحیه‌ی فعال، از ابتدای همان سال می‌شمارد."""
    start = year.start_date if _active_opening_entry(sess, int(year.title)) else None
    return {account.id: debit - credit for account, debit, credit in _flow(sess, start, year.end_date)}


def _permanent_account(account: Account) -> bool:
    return (account.kind == "ledger" and account.group_type == "permanent"
            and account.report_type == "balance_sheet")


def _opening_source_ref(source_jy: int | None, destination_jy: int) -> str:
    return f"{source_jy if source_jy is not None else 'external'}->{destination_jy}"


def _detail_for_account(sess, account_id: int) -> dict:
    party = sess.execute(select(Party).where(
        or_(Party.account_id == account_id, Party.credit_account_id == account_id)
    ).limit(1)).scalars().first()
    if party is None:
        return {"detail_id": None, "detail_type": None, "detail_code": "", "detail_name": ""}
    detail_type = party.kind if party.account_id == account_id else "customer_credit"
    return {"detail_id": party.id, "detail_type": detail_type,
            "detail_code": party.code, "detail_name": party.name}


def _opening_entries(sess, destination_jy: int) -> list[JournalEntry]:
    start, _ = _fiscal_dates(destination_jy)
    return list(sess.execute(select(JournalEntry).where(
        JournalEntry.source == "opening", JournalEntry.jy == destination_jy,
        JournalEntry.entry_date == start, JournalEntry.status == "posted",
    ).order_by(JournalEntry.id)).scalars())


def opening_reconciliation(sess, source_jy: int, destination_jy: int) -> dict:
    """تطبیق مستقل پایان سال مبدأ با سطرهای خود سند افتتاحیه‌ی مقصد."""
    src = sess.execute(select(FiscalYear).where(FiscalYear.title == str(source_jy))).scalar_one_or_none()
    dest_start, dest_end = _fiscal_dates(destination_jy)
    if src is None:
        raise DomainError(f"سال مبدأ {source_jy} در برنامه ثبت نشده است.")
    entries = _opening_entries(sess, destination_jy)
    matches = [entry for entry in entries if entry.source_ref == _opening_source_ref(source_jy, destination_jy)]
    target_entry = matches[0] if len(matches) == 1 else None
    if len(matches) > 1:
        raise DomainError("چند سند افتتاحیه‌ی فعال برای همین سال مبدأ و مقصد پیدا شد؛ ثبت جدید متوقف شد.")
    expected_balances = _year_end_balances(sess, src)
    actual: dict[int, int] = {}
    if target_entry:
        for line in target_entry.lines:
            actual[line.account_id] = actual.get(line.account_id, 0) + line.debit_rial - line.credit_rial
    accounts = _accounts(sess)
    rows = []
    ids = set(expected_balances) | set(actual)
    for account_id in sorted(ids, key=lambda aid: accounts[aid].code if aid in accounts else "~"):
        account = accounts.get(account_id)
        if account is None:
            continue
        expected = expected_balances.get(account_id, 0)
        transferred = actual.get(account_id, 0)
        if not _permanent_account(account) and (expected or transferred):
            continue
        difference = transferred - expected
        detail = _detail_for_account(sess, account_id)
        rows.append({"account_id": account_id, "code": account.code, "name": account.name,
                     "level": account.level, **detail,
                     "source_net": expected, "opening_net": transferred,
                     "difference_debit": max(difference, 0),
                     "difference_credit": max(-difference, 0),
                     "matched": difference == 0,
                     "reason": "منطبق" if difference == 0 else "افتتاحیه با مانده‌ی پایان مبدأ برابر نیست"})
    debit_difference = sum(row["difference_debit"] for row in rows)
    credit_difference = sum(row["difference_credit"] for row in rows)
    no_transfer_required = not any(value for account_id, value in expected_balances.items()
                                   if account_id in accounts and _permanent_account(accounts[account_id]))
    return {"source_jy": source_jy, "destination_jy": destination_jy,
            "source_end": jd.jalali_str(src.end_date),
            "destination_start": jd.jalali_str(dest_start), "destination_end": jd.jalali_str(dest_end),
            "entry_id": target_entry.id if target_entry else None,
            "rows": rows, "matched_count": sum(1 for row in rows if row["matched"]),
            "unmatched_count": sum(1 for row in rows if not row["matched"]),
            "difference_debit": debit_difference, "difference_credit": credit_difference,
            "balanced": bool(target_entry and sum(l.debit_rial for l in target_entry.lines)
                              == sum(l.credit_rial for l in target_entry.lines)),
            "ready": bool((target_entry or no_transfer_required) and all(row["matched"] for row in rows)
                          and debit_difference == 0 and credit_difference == 0)}


def opening_preview(sess, destination_jy: int, mode: str = "transfer",
                    source_jy: int | None = None, raw_lines: list[dict] | None = None) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    if mode not in ("transfer", "initial"):
        raise DomainError("روش افتتاحیه باید انتقال از سال قبل یا ورود اولیه باشد.")
    if mode == "transfer" and source_jy != destination_jy - 1:
        errors.append({"code": "source_year_invalid", "message": "سال مبدأ باید سال مالی بلافاصله قبل از مقصد باشد."})
    dest = sess.execute(select(FiscalYear).where(FiscalYear.title == str(destination_jy))).scalar_one_or_none()
    dest_start, dest_end = _fiscal_dates(destination_jy)
    if dest and dest.status == "closed":
        errors.append({"code": "destination_closed", "message": f"سال مقصد {destination_jy} بسته است."})
    destination_periods = list(sess.execute(select(Period).where(
        Period.jy == destination_jy).order_by(Period.jm)).scalars())
    destination_opening_period = next((period for period in destination_periods if period.jm == 1), None)
    if destination_opening_period and destination_opening_period.status == "closed":
        errors.append({"code": "destination_opening_period_closed",
                       "message": "دوره‌ی فروردین سال مقصد بسته است؛ برای ثبت افتتاحیه باید ابتدا آن را باز کنید."})
    active_entries = _opening_entries(sess, destination_jy)
    if active_entries:
        errors.append({"code": "opening_exists", "message": f"برای سال مقصد {destination_jy} افتتاحیه‌ی ثبت‌شده وجود دارد؛ برای بررسی، گزارش تطبیق را باز کنید."})
    preexisting = list(sess.execute(select(JournalEntry).where(
        JournalEntry.jy == destination_jy, JournalEntry.status.in_(svc.BOOKED_STATUSES),
        JournalEntry.source != "opening")).scalars())
    if preexisting:
        warnings.append({"code": "destination_activity_before_opening",
                         "message": f"سال مقصد {destination_jy} پیش از افتتاحیه {len(preexisting)} سند ثبت‌شده/معکوس‌شده دارد؛ اسناد حفظ می‌شوند و مانده‌ی افتتاحیه در تاریخ شروع سال ثبت و مستقل تطبیق می‌شود."})

    accounts = _accounts(sess)
    lines: list[dict] = []
    rows: list[dict] = []
    source_balances: dict[int, int] = {}
    if mode == "transfer":
        src = sess.execute(select(FiscalYear).where(FiscalYear.title == str(source_jy))).scalar_one_or_none()
        if src is None:
            errors.append({"code": "source_missing", "message": f"سال مبدأ {source_jy} در سامانه نیست."})
        elif src.status != "closed":
            errors.append({"code": "source_not_closed", "message": f"سال مبدأ {source_jy} بسته نشده است."})
        closing = sess.execute(select(FiscalClosing).where(FiscalClosing.jy == source_jy)).scalar_one_or_none()
        if src and (closing is None or closing.status != "closed"):
            errors.append({"code": "source_closing_missing", "message": "اختتامیه‌ی معتبر برای سال مبدأ ثبت نشده است."})
        if src:
            source_periods = list(sess.execute(select(Period).where(
                Period.jy == source_jy).order_by(Period.jm)).scalars())
            if len(source_periods) != 12 or any(period.status != "closed" for period in source_periods):
                errors.append({"code": "source_periods_open",
                               "message": "همه‌ی ۱۲ دوره‌ی سال مبدأ باید طبق فرایند مجاز بسته باشند."})
            draft_count = sess.execute(select(func.count(JournalEntry.id)).where(
                JournalEntry.jy == source_jy, JournalEntry.status == "draft")).scalar_one()
            if draft_count:
                errors.append({"code": "source_drafts_exist",
                               "message": f"سال مبدأ {draft_count} سند پیش‌نویس دارد؛ پیش از انتقال تعیین تکلیف کنید."})
            if closing and not closing.entry_id and (closing.revenue_rial or closing.expense_rial):
                errors.append({"code": "source_closing_entry_missing",
                               "message": "رکورد اختتامیه گردش درآمد/هزینه دارد اما شناسه‌ی سند ثبت‌شده ندارد."})
            if closing and closing.entry_id:
                closing_entry = sess.get(JournalEntry, closing.entry_id)
                if (closing_entry is None or closing_entry.status != "posted"
                        or closing_entry.source != "closing" or closing_entry.jy != source_jy
                        or closing_entry.entry_date != src.end_date):
                    errors.append({"code": "source_closing_entry_invalid",
                                   "message": "سند اختتامیه‌ی سال مبدأ معتبر، ثبت‌شده یا مربوط به پایان همان سال نیست."})
            source_balances = _year_end_balances(sess, src)
            for account_id, net in source_balances.items():
                account = accounts.get(account_id)
                if account is None or net == 0:
                    continue
                if _permanent_account(account):
                    if not account.is_active:
                        errors.append({"code": "inactive_account", "message": f"حساب دائمی {account.code} مانده دارد اما غیرفعال است."})
                        continue
                    line = ({"account_id": account.id, "debit": net, "credit": 0,
                             "description": f"انتقال مانده‌ی افتتاحیه از سال {source_jy}"} if net > 0 else
                            {"account_id": account.id, "debit": 0, "credit": -net,
                             "description": f"انتقال مانده‌ی افتتاحیه از سال {source_jy}"})
                    lines.append(line)
                else:
                    errors.append({"code": "nonpermanent_balance", "message": f"حساب غیر دائمی {account.code} پس از اختتامیه مانده دارد؛ مانده نباید مخفی یا منتقل شود."})
            # ماتریس پیش‌نمایش تمام حساب‌های دائمیِ دفترپذیر را نشان می‌دهد، حتی با مانده‌ی صفر.
            for account in sorted((a for a in accounts.values() if _permanent_account(a)), key=lambda item: item.code):
                net = source_balances.get(account.id, 0)
                mapped = account.is_active
                if not mapped and net:
                    continue
                rows.append({"account_id": account.id, "code": account.code, "name": account.name,
                             "level": account.level, **_detail_for_account(sess, account.id), "source_net": net,
                             "debit": max(net, 0), "credit": max(-net, 0),
                             "mapped": mapped, "difference": 0 if mapped else net,
                             "reason": "حساب مقصد فعال است" if mapped else "حساب مقصد غیرفعال است"})
    else:
        raw_lines = raw_lines or []
        if not raw_lines:
            errors.append({"code": "initial_lines_missing", "message": "برای افتتاحیه‌ی اولیه دست‌کم یک سطر لازم است."})
        validation = svc._validated_lines(sess, raw_lines, require_balanced=True)
        if validation.problems:
            errors.append({"code": "initial_lines_invalid", "message": "؛ ".join(validation.problems)})
        else:
            for line in validation.lines:
                account = accounts.get(line.account_id)
                if account is None or not _permanent_account(account):
                    errors.append({"code": "initial_nonpermanent_account",
                                   "message": f"حساب {getattr(account, 'code', line.account_id)} دائمیِ ترازنامه‌ای نیست."})
                    continue
                lines.append({"account_id": line.account_id, "debit": line.debit, "credit": line.credit,
                              "description": line.description or "مانده‌ی افتتاحیه‌ی اولیه — منبع بیرونی"})
                rows.append({"account_id": account.id, "code": account.code, "name": account.name,
                             "level": account.level, **_detail_for_account(sess, account.id),
                             "source_net": line.debit - line.credit,
                             "debit": line.debit, "credit": line.credit, "mapped": account.is_active,
                             "difference": 0, "reason": "ورود اولیه از منبع بیرونی"})

    total_debit = sum(line["debit"] for line in lines)
    total_credit = sum(line["credit"] for line in lines)
    if total_debit != total_credit:
        errors.append({"code": "opening_unbalanced", "message": f"جمع افتتاحیه متوازن نیست: بدهکار {total_debit}، بستانکار {total_credit} ریال."})
    destination_period_map = {period.jm: period.status for period in destination_periods}
    destination_period_snapshot = [(month, destination_period_map.get(month, "open"))
                                   for month in range(1, 13)]
    fingerprint = _stable_hash({
        "mode": mode, "source_jy": source_jy, "destination_jy": destination_jy,
        "destination_start": dest_start.isoformat(), "destination_end": dest_end.isoformat(),
        "destination_status": dest.status if dest else "open",
        "destination_periods": destination_period_snapshot,
        "source_status": getattr(locals().get("src", None), "status", None),
        "source_balances": sorted(source_balances.items()),
        "source_entries": _entry_snapshot(_year_entries(sess, src)) if mode == "transfer" and locals().get("src") else [],
        "destination_entries": _entry_snapshot(_year_entries(sess, dest)) if dest else [],
        "accounts": [{"id": a.id, "code": a.code, "name": a.name, "active": a.is_active,
                      "kind": a.kind, "level": a.level, "nature": a.nature,
                      "group_type": a.group_type, "report_type": a.report_type,
                      "report_group": a.report_group, "is_system": a.is_system}
                     for a in sorted(accounts.values(), key=lambda item: item.id)],
        "lines": lines,
    })
    return {"mode": mode, "source_jy": source_jy, "destination_jy": destination_jy,
            "calculated_at": datetime.now().isoformat(timespec="seconds"),
            "source_end": jd.jalali_str(src.end_date) if mode == "transfer" and locals().get("src") else None,
            "destination_start": jd.jalali_str(dest_start), "destination_end": jd.jalali_str(dest_end),
            "ready": not errors, "errors": errors, "warnings": warnings,
            "rows": rows, "lines": lines, "fingerprint": fingerprint,
            "totals": {"debit": total_debit, "credit": total_credit,
                       "difference": total_debit - total_credit},
            "origin_label": "انتقال از سال مالی قبل در سامانه" if mode == "transfer"
                            else "ورود اولیه از منبع بیرونی؛ نه استخراج از سال قبل"}


def create_opening_draft(sess, user, destination_jy: int, mode: str, fingerprint: str,
                         source_jy: int | None = None, raw_lines: list[dict] | None = None,
                         ip: str = "") -> JournalEntry | None:
    preview = opening_preview(sess, destination_jy, mode, source_jy, raw_lines)
    if not preview["ready"]:
        raise DomainError("پیش‌بررسی افتتاحیه مانع ساخت پیش‌نویس است: "
                          + "؛ ".join(item["message"] for item in preview["errors"]))
    if preview["fingerprint"] != fingerprint:
        raise DomainError("داده‌های مبدأ یا کدینگ پس از پیش‌نمایش تغییر کرده‌اند؛ دوباره پیش‌نمایش بگیرید.")
    start, _ = _fiscal_dates(destination_jy)
    # با یک تاریخ در مقصد، helper موجود سال و دوازده دوره را در صورت نیاز ایجاد می‌کند.
    destination = svc.ensure_fiscal_year(sess, start)
    if destination.status != "open":
        raise DomainError("سال مقصد بسته است و امکان ساخت افتتاحیه ندارد.")
    source_ref = _opening_source_ref(source_jy if mode == "transfer" else None, destination_jy)
    if not preview["lines"]:
        svc.audit(sess, user, "fiscal.opening_draft", "fiscal_year", str(destination_jy),
                  "مانده‌ی دائمی برای انتقال وجود ندارد؛ سند صفرسطر ساخته نشد.", ip)
        return None
    key = f"fopen-{destination_jy}-{fingerprint[:20]}"
    existing = sess.execute(select(JournalEntry).where(
        JournalEntry.idempotency_key == key)).scalar_one_or_none()
    if existing is None:
        existing = sess.execute(select(JournalEntry).where(
            JournalEntry.source == "opening", JournalEntry.source_ref == source_ref,
            JournalEntry.status == "draft").order_by(JournalEntry.id.desc())).scalars().first()
    if existing and existing.status == "draft":
        svc.update_draft(sess, user, existing.id, entry_date=start,
                         description=f"سند افتتاحیه‌ی سال مالی {destination_jy}",
                         raw_lines=preview["lines"], ip=ip)
        existing.idempotency_key = key
        entry = existing
    elif existing:
        raise DomainError("این درخواست افتتاحیه قبلاً نهایی شده یا برگشت خورده است؛ پیش‌نمایش تازه بسازید.")
    else:
        entry = svc.create_draft(sess, user, start,
                                 f"سند افتتاحیه‌ی سال مالی {destination_jy}", preview["lines"],
                                 source="opening", source_ref=source_ref,
                                 idempotency_key=key, ip=ip)
    svc.audit(sess, user, "fiscal.opening_draft", "fiscal_year", str(destination_jy),
              f"پیش‌نویس افتتاحیه {mode} با شناسه {entry.id} ساخته شد؛ مبدأ {source_ref}.", ip)
    return entry


def finalize_opening(sess, user, destination_jy: int, mode: str, fingerprint: str,
                     draft_entry_id: int | None, source_jy: int | None = None, raw_lines=None,
                     ip: str = "") -> dict:
    expected_ref = _opening_source_ref(source_jy if mode == "transfer" else None, destination_jy)
    already_posted = sess.get(JournalEntry, draft_entry_id)
    expected_key = f"fopen-{destination_jy}-{fingerprint[:20]}"
    if (already_posted and already_posted.source == "opening"
            and already_posted.source_ref == expected_ref
            and already_posted.idempotency_key == expected_key
            and already_posted.status == "posted"):
        reconciliation = (opening_reconciliation(sess, source_jy, destination_jy)
                          if mode == "transfer" and source_jy is not None else None)
        return {"entry": already_posted, "preview": None, "reconciliation": reconciliation}
    preview = opening_preview(sess, destination_jy, mode, source_jy, raw_lines)
    if not preview["ready"]:
        raise DomainError("پیش‌بررسی نهایی افتتاحیه ناموفق است: "
                          + "؛ ".join(item["message"] for item in preview["errors"]))
    if preview["fingerprint"] != fingerprint:
        raise DomainError("داده‌های مبدأ یا مقصد بعد از بازبینی تغییر کرده‌اند؛ پیش‌نویس را دوباره بسازید.")
    if not preview["lines"] and draft_entry_id is None:
        svc.audit(sess, user, "fiscal.opening_post", "fiscal_year", str(destination_jy),
                  "مانده‌ی دائمی برای انتقال وجود نداشت؛ بدون سند، فرآیند افتتاحیه تأیید شد.", ip)
        return {"entry": None, "preview": preview,
                "reconciliation": opening_reconciliation(sess, source_jy, destination_jy)
                if mode == "transfer" and source_jy is not None else None}
    entry = sess.get(JournalEntry, draft_entry_id)
    expected_ref = _opening_source_ref(source_jy if mode == "transfer" else None, destination_jy)
    if (entry is None or entry.source != "opening" or entry.source_ref != expected_ref
            or entry.jy != destination_jy or entry.status != "draft"):
        raise DomainError("پیش‌نویس افتتاحیه معتبر یا قابل ثبت نیست.")
    expected = sorted((line["account_id"], line["debit"], line["credit"], line["description"])
                      for line in preview["lines"])
    actual = sorted((line.account_id, line.debit_rial, line.credit_rial, line.description)
                    for line in entry.lines)
    if expected != actual:
        raise DomainError("سطرهای پیش‌نویس با محاسبه‌ی تازه یکسان نیست؛ آن را دوباره بسازید.")
    svc.post_entry(sess, user, entry.id, ip, allow_fiscal_document=True)
    svc.audit(sess, user, "fiscal.opening_post", "fiscal_year", str(destination_jy),
              f"افتتاحیه‌ی سال {destination_jy} با شناسه سند {entry.id} ثبت شد؛ مبدأ {expected_ref}.", ip)
    if mode == "transfer" and source_jy is not None:
        reconciliation = opening_reconciliation(sess, source_jy, destination_jy)
        if not reconciliation["ready"]:
            raise DomainError("سند ثبت شد اما تطبیق پایان/ابتدای سال ناموفق است؛ تراکنش برای حفظ سازگاری rollback می‌شود.")
    return {"entry": entry, "preview": preview,
            "reconciliation": opening_reconciliation(sess, source_jy, destination_jy)
            if mode == "transfer" and source_jy is not None else None}


def _all_downstream_openings(sess, jy: int) -> list[JournalEntry]:
    """افتتاحیه‌های انتقالی از این سال و سال‌های بعد که زنجیره‌شان به این مبدأ وابسته است."""
    candidates = sess.execute(select(JournalEntry).where(
        JournalEntry.source == "opening", JournalEntry.status.in_(("draft", "posted", "reversed")),
        JournalEntry.jy > jy).order_by(JournalEntry.jy, JournalEntry.id)).scalars()
    rows = []
    for entry in candidates:
        source_ref = entry.source_ref or ""
        source = source_ref.split("->", 1)[0]
        if source.isdigit() and int(source) >= jy:
            rows.append(entry)
    return rows


def _downstream_openings(sess, jy: int) -> list[JournalEntry]:
    return [entry for entry in _all_downstream_openings(sess, jy) if entry.status == "posted"]


def reopening_precheck(sess, jy: int) -> dict:
    """پیش‌نمایش وابستگی‌های بعد از سال؛ هیچ سند یا وضعیتی را تغییر نمی‌دهد."""
    errors: list[dict] = []
    warnings: list[dict] = []
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    if year is None:
        errors.append({"code": "year_missing", "message": f"سال مالی {jy} پیدا نشد."})
        return {"jy": jy, "ready": False, "requires_acknowledgement": False,
                "calculated_at": datetime.now().isoformat(timespec="seconds"),
                "errors": errors, "warnings": warnings, "downstream": [], "fingerprint": ""}
    if year.status != "closed":
        errors.append({"code": "year_not_closed", "message": f"سال {jy} بسته نیست."})
    closing = sess.execute(select(FiscalClosing).where(FiscalClosing.jy == jy)).scalar_one_or_none()
    if closing is None or closing.status != "closed":
        errors.append({"code": "closing_missing", "message": "رکورد اختتامیه‌ی بسته‌شده برای این سال پیدا نشد."})
    elif closing.entry_id:
        closing_entry = sess.get(JournalEntry, closing.entry_id)
        if (closing_entry is None or closing_entry.status != "posted"
                or closing_entry.source != "closing" or closing_entry.jy != jy):
            errors.append({"code": "closing_entry_invalid", "message": "سند اختتامیه‌ی سال معتبر یا ثبت‌شده نیست."})
    elif closing.revenue_rial or closing.expense_rial:
        errors.append({"code": "closing_entry_missing", "message": "اختتامیه گردش درآمد/هزینه دارد اما سند ثبت‌شده ندارد."})

    all_openings = _all_downstream_openings(sess, jy)
    openings = [entry for entry in all_openings if entry.status == "posted"]
    opening_dependencies = []
    for entry in all_openings:
        source_text = (entry.source_ref or "").split("->", 1)[0]
        source_jy = int(source_text) if source_text.isdigit() else None
        reversal = entry.reversed_by_id or sess.execute(select(JournalEntry.id).where(
            JournalEntry.reversal_of_id == entry.id, JournalEntry.status == "posted").limit(1)
        ).scalar_one_or_none()
        reconciliation = None
        if entry.status == "posted" and source_jy is not None:
            try:
                reconciliation = opening_reconciliation(sess, source_jy, entry.jy)
            except DomainError as exc:
                reconciliation = {"ready": False, "issue": exc.message}
        destination_year = sess.execute(select(FiscalYear).where(
            FiscalYear.title == str(entry.jy))).scalar_one_or_none()
        destination_period = sess.execute(select(Period).where(
            Period.jy == entry.jy, Period.jm == 1)).scalar_one_or_none()
        opening_dependencies.append({"entry_id": entry.id, "destination_jy": entry.jy,
                                     "source_ref": entry.source_ref or "", "status": entry.status,
                                     "destination_year_status": destination_year.status if destination_year else "missing",
                                     "destination_opening_period_status": destination_period.status if destination_period else "missing",
                                     "reversal_entry_id": reversal,
                                     "reconciliation_ready": reconciliation.get("ready") if reconciliation else None,
                                     "reconciliation_issue": reconciliation.get("issue") if reconciliation else None})
    if openings:
        ids = "، ".join(f"سال مقصد {entry.jy}: سند {entry.id}" for entry in openings)
        errors.append({"code": "downstream_openings_active",
                       "message": "بازگشایی متوقف شد؛ ابتدا افتتاحیه‌های انتقالی سال‌های وابسته را با دلیل از مسیر معکوس‌سازی استاندارد باطل کنید: " + ids})

    opening_ids = select(JournalEntry.id).where(
        JournalEntry.source == "opening", JournalEntry.jy > jy)
    later_entries = list(sess.execute(select(JournalEntry).where(
        JournalEntry.jy > jy, JournalEntry.status.in_(svc.BOOKED_STATUSES),
        JournalEntry.source.notin_(("opening", "closing")),
        or_(JournalEntry.reversal_of_id.is_(None), JournalEntry.reversal_of_id.not_in(opening_ids)),
    ).order_by(JournalEntry.jy, JournalEntry.entry_date, JournalEntry.id)).scalars())
    later_drafts = list(sess.execute(select(JournalEntry).where(
        JournalEntry.jy > jy, JournalEntry.status == "draft").order_by(
            JournalEntry.jy, JournalEntry.id)).scalars())
    counts: dict[int, dict] = {}
    for entry in later_entries:
        item = counts.setdefault(entry.jy, {"jy": entry.jy, "posted_count": 0, "draft_count": 0,
                                            "entry_ids": []})
        item["posted_count"] += 1
        item["entry_ids"].append(entry.id)
    for entry in later_drafts:
        item = counts.setdefault(entry.jy, {"jy": entry.jy, "posted_count": 0, "draft_count": 0,
                                            "entry_ids": []})
        item["draft_count"] += 1
    downstream = [counts[year_key] for year_key in sorted(counts)]
    plan = []
    active_opening_dependencies = [item for item in opening_dependencies if item["status"] == "posted"]
    if openings:
        closed_destination_years = [item["destination_jy"] for item in active_opening_dependencies
                                    if item["destination_year_status"] == "closed"]
        closed_destination_periods = [item["destination_jy"] for item in active_opening_dependencies
                                      if item["destination_year_status"] == "open"
                                      and item["destination_opening_period_status"] == "closed"]
        if closed_destination_years:
            plan.append("پیش از ابطال افتتاحیه، سال‌های مقصدِ بسته‌شده را یکی‌یکی از گردش بازگشایی کنترل‌شده‌ی خودشان باز کنید؛ وابستگی‌های بعد از هر مقصد نیز باید همان‌جا بررسی شوند: "
                        + "، ".join(map(str, sorted(set(closed_destination_years)))) + ".")
        if closed_destination_periods:
            plan.append("پیش از ابطال افتتاحیه، دوره‌ی فروردینِ سال‌های مقصدِ باز اما دارای دوره‌ی بسته را از مدیریت دوره‌ها باز کنید: "
                        + "، ".join(map(str, sorted(set(closed_destination_periods)))) + ".")
        plan.append("سپس افتتاحیه‌های انتقالی فعالِ فهرست‌شده را با دلیل از مسیر معکوس‌سازی استاندارد باطل کنید و دوباره پیش‌نمایش بگیرید.")
    elif all_openings:
        plan.append("افتتاحیه‌های وابسته‌ی معکوس‌شده را پس از اصلاح و بستن مجدد مبدأ، برای مقصدهای فهرست‌شده دوباره بسازید و تطبیق کنید.")
    if later_entries or later_drafts:
        plan.append("اسناد ثبت‌شده و پیش‌نویس‌های سال‌های بعد را حفظ کنید؛ حذف یا ویرایش نمی‌شوند و اثر مانده‌های آن‌ها را پس از اصلاح تطبیق دهید.")
    if not plan:
        plan.append("پس از بازگشایی، اصلاحات را فقط با سندهای استاندارد و دارای دلیل انجام دهید.")
    plan.append("پس از پایان اصلاح، سال را دوباره از گردش پیش‌بررسی، پیش‌نویس و تأیید اختتامیه ببندید.")
    if later_entries or later_drafts or all_openings:
        plan.append("پس از بسته‌شدن مجدد مبدأ، افتتاحیه‌های انتقالیِ متأثر را از مانده‌های نهایی بسازید یا به‌روزرسانی کنید و تطبیق را نهایی کنید.")
    if later_entries:
        warnings.append({"code": "downstream_posted_activity",
                         "message": f"در سال‌های بعد {len(later_entries)} سند ثبت‌شده وجود دارد؛ سندها حذف یا ویرایش نمی‌شوند و پس از بازگشایی باید مانده‌ها/افتتاحیه‌ها دوباره تطبیق شوند."})
    if later_drafts:
        warnings.append({"code": "downstream_drafts",
                         "message": f"در سال‌های بعد {len(later_drafts)} پیش‌نویس وجود دارد؛ پیش از تأیید برنامه‌ی اصلاح، تاریخ و اثر آن‌ها را بررسی کنید."})

    closing_entries = [sess.get(JournalEntry, closing.entry_id)] if closing and closing.entry_id else []
    snapshot_entries = [entry for entry in closing_entries if entry is not None] + openings + later_entries + later_drafts
    fingerprint = _stable_hash({
        "jy": jy, "year_status": year.status,
        "closing": {"status": closing.status, "entry_id": closing.entry_id,
                    "profit": closing.profit_rial, "revenue": closing.revenue_rial,
                    "expense": closing.expense_rial} if closing else None,
        "periods": [(p.jm, p.status) for p in sess.execute(
            select(Period).where(Period.jy == jy).order_by(Period.jm)).scalars()],
        "entries": _entry_snapshot(snapshot_entries + all_openings),
        "downstream": downstream, "opening_dependencies": opening_dependencies,
    })
    return {"jy": jy, "ready": not errors,
            "requires_acknowledgement": bool(all_openings or later_entries or later_drafts),
            "calculated_at": datetime.now().isoformat(timespec="seconds"),
            "errors": errors, "warnings": warnings, "downstream": downstream,
            "opening_dependencies": opening_dependencies,
            "active_opening_ids": [entry.id for entry in openings],
            "closing_entry_id": closing.entry_id if closing else None,
            "fingerprint": fingerprint, "plan": plan}


def reverse_dependent_opening(sess, user, source_jy: int, entry_id: int, reason: str,
                              fingerprint: str, acknowledge_dependencies: bool = False,
                              ip: str = "") -> JournalEntry:
    """ابطال یک افتتاحیه‌ی وابسته فقط بر اساس پیش‌نمایش/برنامه‌ی تازه و با سند معکوس."""
    reason = str(reason or "").strip()
    if len(reason) < 5:
        raise DomainError("دلیل ابطال افتتاحیه اجباری است (حداقل ۵ نویسه).")
    preview = reopening_precheck(sess, source_jy)
    blocking = [item for item in preview["errors"]
                if item["code"] != "downstream_openings_active"]
    if blocking:
        raise DomainError("برنامه‌ی اصلاح قابل اجرا نیست: " + "؛ ".join(item["message"] for item in blocking))
    if preview["fingerprint"] != fingerprint:
        raise DomainError("وابستگی‌ها پس از پیش‌نمایش تغییر کرده‌اند؛ پیش‌نمایش را دوباره اجرا کنید.")
    if preview["requires_acknowledgement"] and not acknowledge_dependencies:
        raise DomainError("ابتدا وابستگی‌ها و برنامه‌ی اصلاح را بررسی و صریحاً تأیید کنید.")
    if entry_id not in preview["active_opening_ids"]:
        raise DomainError("این سند افتتاحیه در پیش‌نمایش فعلی به‌عنوان وابستگی فعال شناسایی نشد.")
    entry = sess.get(JournalEntry, entry_id)
    if entry is None or entry.source != "opening" or entry.status != "posted":
        raise DomainError("سند افتتاحیه دیگر فعال نیست؛ پیش‌نمایش را دوباره اجرا کنید.")
    dependency = next((item for item in preview["opening_dependencies"]
                       if item["entry_id"] == entry.id), None)
    if dependency is None:
        raise DomainError("وابستگی این افتتاحیه در پیش‌نمایش پیدا نشد؛ پیش‌نمایش را دوباره اجرا کنید.")
    if dependency["destination_year_status"] == "closed":
        raise DomainError(f"ابتدا سال مقصد {entry.jy} را با گردش بازگشایی کنترل‌شده‌ی خودش باز کنید؛ سپس پیش‌نمایش سال {source_jy} را تازه کنید.")
    if dependency["destination_year_status"] != "open":
        raise DomainError(f"وضعیت سال مقصد {entry.jy} معتبر نیست؛ ابتدا آن را از گردش مدیریت سال مالی تعیین تکلیف کنید.")
    if dependency["destination_opening_period_status"] == "closed":
        raise DomainError(f"ابتدا دوره‌ی فروردین سال مقصد {entry.jy} را از مدیریت دوره‌ها باز کنید؛ سپس پیش‌نمایش سال {source_jy} را تازه کنید.")
    if dependency["destination_opening_period_status"] != "open":
        raise DomainError(f"دوره‌ی فروردین سال مقصد {entry.jy} پیدا نشد یا معتبر نیست؛ ابتدا دوره‌ها را تعیین تکلیف کنید.")
    reversal = svc.reverse_entry(sess, user, entry.id, reason, ip, allow_fiscal_document=True)
    svc.audit(sess, user, "fiscal.reverse_dependent_opening", "fiscal_year", str(source_jy),
              f"افتتاحیه‌ی مقصد {entry.jy}؛ سند {entry.id} معکوس شد با سند {reversal.id}; "
              f"دلیل: {reason}; fingerprint {fingerprint[:16]}", ip)
    return reversal


def reopen_year(sess, user, jy: int, reason: str, fingerprint: str,
                acknowledge_dependencies: bool = False, ip: str = "") -> FiscalClosing:
    reason = str(reason or "").strip()
    if len(reason) < 5:
        raise DomainError("دلیل بازگشایی اجباری است (حداقل ۵ نویسه).")
    preview = reopening_precheck(sess, jy)
    if not preview["ready"]:
        raise DomainError("بازگشایی مجاز نیست: " + "؛ ".join(item["message"] for item in preview["errors"]))
    if preview["fingerprint"] != fingerprint:
        raise DomainError("وابستگی‌ها یا وضعیت سال پس از پیش‌نمایش تغییر کرده‌اند؛ پیش‌نمایش را دوباره اجرا کنید.")
    if preview["requires_acknowledgement"] and not acknowledge_dependencies:
        raise DomainError("سال‌های بعد سند یا پیش‌نویس دارند؛ ابتدا برنامه‌ی اصلاح را بررسی و صریحاً تأیید کنید.")
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one()
    closing = sess.execute(select(FiscalClosing).where(FiscalClosing.jy == jy)).scalar_one()
    before = year.status
    year.status = "open"
    for period in sess.execute(select(Period).where(Period.jy == jy).order_by(Period.jm)).scalars():
        if period.status == "closed":
            period.status = "open"
            period.closed_at = None
            period.closed_by = None
            svc.audit(sess, user, "period.reopen", "period", f"{jy}-{period.jm}",
                      f"بازگشایی دوره در فرایند کنترل‌شده‌ی سال {jy}؛ بسته→باز.", ip)
    sess.flush()
    reversal_id = None
    if closing.entry_id:
        reversal = svc.reverse_entry(sess, user, closing.entry_id, reason, ip,
                                     allow_fiscal_document=True)
        reversal_id = reversal.id
    closing.status = "reopened"
    sess.flush()
    svc.audit(sess, user, "fiscal.reopen_year", "fiscal_year", str(jy),
              f"وضعیت سال {before}→{year.status}؛ اختتامیه {closing.entry_id}; "
              f"سند معکوس {reversal_id}; وابستگی‌ها {preview['downstream']}; "
              f"تأیید وابستگی={preview['requires_acknowledgement']}; دلیل: {reason}; "
              f"fingerprint {fingerprint[:16]}", ip)
    return closing


def is_year_closed(sess, jy: int) -> bool:
    row = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    return bool(row and row.status == "closed")
