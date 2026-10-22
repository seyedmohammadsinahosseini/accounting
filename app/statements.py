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

from sqlalchemy import func, select

from . import dates as jd
from . import services as svc
from .models import (Account, FiscalClosing, FiscalYear, JournalEntry, JournalLine, Period)
from .money import format_amount_fa
from .rules import SYSTEM_CODES, DomainError

GROUP_OF_PREFIX = {"1": "asset", "2": "liability", "3": "equity", "4": "income", "5": "expense"}
GROUP_TITLES = {"asset": "دارایی‌ها", "liability": "بدهی‌ها", "equity": "حقوق مالکانه",
                "income": "درآمدها", "expense": "هزینه‌ها"}


def _accounts(sess) -> dict[int, Account]:
    return {a.id: a for a in sess.execute(select(Account)).scalars()}


def _flow(sess, frm: date | None, to: date) -> list[tuple[Account, int, int]]:
    """گردش هر حساب تا تاریخ «to» (و از «frm» اگر بدهید): (حساب، جمع بدهکار، جمع بستانکار)."""
    q = (select(JournalLine, JournalEntry)
         .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
         .where(JournalEntry.status.in_(svc.BOOKED_STATUSES), JournalEntry.entry_date <= to))
    if frm:
        q = q.where(JournalEntry.entry_date >= frm)
    sums: dict[int, list[int]] = {}
    for line, _entry in sess.execute(q):
        row = sums.setdefault(line.account_id, [0, 0])
        row[0] += line.debit_rial
        row[1] += line.credit_rial
    accounts = _accounts(sess)
    return [(accounts[aid], v[0], v[1]) for aid, v in sums.items() if aid in accounts]


def _signed(account: Account, debit: int, credit: int) -> int:
    """مانده‌ی حساب با علامت: دارایی و هزینه مثبت = بدهکار، بقیه مثبت = بستانکار."""
    group = GROUP_OF_PREFIX.get(account.code[:1], "asset")
    if group in ("asset", "expense"):
        return debit - credit
    return credit - debit


# ------------------------------------------------------------------ سود و زیان
def income_statement(sess, frm: date, to: date) -> dict:
    revenues, expenses = [], []
    for account, debit, credit in _flow(sess, frm, to):
        group = GROUP_OF_PREFIX.get(account.code[:1])
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
    for account, debit, credit in _flow(sess, None, to):
        group = GROUP_OF_PREFIX.get(account.code[:1])
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
    opening = closing = 0
    for account, debit, credit in _flow(sess, None, to):
        if account.report_group in CASH_GROUPS:
            closing += debit - credit
    day_before = frm - timedelta(days=1)
    for account, debit, credit in _flow(sess, None, day_before):
        if account.report_group in CASH_GROUPS:
            opening += debit - credit

    buckets: dict[str, list[dict]] = {"operating": [], "investing": [], "financing": []}
    totals: dict[str, int] = {"operating": 0, "investing": 0, "financing": 0}
    q = (select(JournalEntry).where(JournalEntry.status.in_(svc.BOOKED_STATUSES),
                                    JournalEntry.entry_date >= frm,
                                    JournalEntry.entry_date <= to)
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


# ------------------------------------------------------------------ بستن سال
def fiscal_years(sess) -> list[dict]:
    closings = {c.jy: c for c in sess.execute(select(FiscalClosing)).scalars()}
    out = []
    for year in sess.execute(select(FiscalYear).order_by(FiscalYear.title.desc())).scalars():
        jy = int(year.title) if year.title.isdigit() else 0
        closing = closings.get(jy)
        draft_count = sess.execute(select(func.count(JournalEntry.id)).where(
            JournalEntry.jy == jy, JournalEntry.status == "draft")).scalar() or 0
        out.append({
            "title": year.title, "jy": jy, "start": jd.jalali_str(year.start_date),
            "end": jd.jalali_str(year.end_date), "status": year.status,
            "profit": closing.profit_rial if closing else None,
            "closed_at": closing.created_at.strftime("%Y-%m-%d %H:%M") if closing else "",
            "draft_count": draft_count,
        })
    return out


def close_year(sess, user, jy: int, note: str = "", ip: str = "") -> FiscalClosing:
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    if year is None:
        raise DomainError(f"سال مالی {jy} در برنامه ثبت نشده است.")
    if year.status == "closed":
        raise DomainError(f"سال {jy} قبلاً بسته شده است.")
    drafts = sess.execute(select(func.count(JournalEntry.id)).where(
        JournalEntry.jy == jy, JournalEntry.status == "draft")).scalar() or 0
    if drafts:
        raise DomainError(f"{drafts} سند پیش‌نویس در سال {jy} مانده است؛ اول تعیین تکلیفشان کنید "
                          f"(ثبت نهایی یا حذف)، بعد سال را ببندید.")
    start, end = year.start_date, year.end_date
    lines = []
    revenue_total = expense_total = 0
    for account, debit, credit in _flow(sess, start, end):
        group = GROUP_OF_PREFIX.get(account.code[:1])
        if group not in ("income", "expense"):
            continue
        amount = _signed(account, debit, credit)
        if amount == 0:
            continue
        if group == "income":
            revenue_total += amount
            # درآمد بستانکار بوده؛ برای صفر کردنش بدهکار می‌شود
            lines.append({"account_id": account.id, "debit": amount, "credit": 0,
                          "description": f"بستن حساب درآمد {account.name}"}
                         if amount > 0 else
                         {"account_id": account.id, "debit": 0, "credit": -amount,
                          "description": f"بستن حساب درآمد {account.name}"})
        else:
            expense_total += amount
            lines.append({"account_id": account.id, "debit": 0, "credit": amount,
                          "description": f"بستن حساب هزینه {account.name}"}
                         if amount > 0 else
                         {"account_id": account.id, "debit": -amount, "credit": 0,
                          "description": f"بستن حساب هزینه {account.name}"})
    profit = revenue_total - expense_total
    retained = svc.account_by_code(sess, SYSTEM_CODES["retained"])
    if retained is None:
        raise DomainError("حساب «سود و زیان انباشته» در کدینگ پیدا نشد.")
    raw = svc.balance_of_code(sess, retained.code, end)
    retained_before = -raw if retained.nature == "credit" else raw
    if profit > 0:
        lines.append({"account_id": retained.id, "debit": 0, "credit": profit,
                      "description": f"انتقال سود سال {jy} به سود و زیان انباشته"})
    elif profit < 0:
        lines.append({"account_id": retained.id, "debit": -profit, "credit": 0,
                      "description": f"انتقال زیان سال {jy} به سود و زیان انباشته"})

    if not lines:
        entry_id = None            # سال بدون درآمد و هزینه: سندی برای بستن لازم نیست
    else:
        entry = svc.create_draft(sess, user, end, f"سند اختتامیه‌ی سال مالی {jy}", lines,
                                source="closing", source_ref=str(jy), ip=ip)
        svc.post_entry(sess, user, entry.id, ip)
        entry_id = entry.id
    for period in sess.execute(select(Period).where(Period.jy == jy)).scalars():
        period.status = "closed"
        period.closed_at = datetime.now()
        period.closed_by = user.id
    year.status = "closed"
    closing = sess.execute(select(FiscalClosing).where(FiscalClosing.jy == jy)).scalar_one_or_none()
    if closing is None:
        closing = FiscalClosing(jy=jy)
        sess.add(closing)
    closing.status = "closed"
    closing.entry_id = entry_id
    closing.revenue_rial = revenue_total
    closing.expense_rial = expense_total
    closing.profit_rial = profit
    closing.retained_before_rial = retained_before
    closing.retained_after_rial = retained_before + profit
    closing.note = note[:255]
    closing.created_by = user.id
    closing.created_at = datetime.now()
    sess.flush()
    svc.audit(sess, user, "fiscal.close_year", "fiscal_year", str(jy),
              f"سود/زیان {format_amount_fa(profit)} ریال", ip)
    return closing


def reopen_year(sess, user, jy: int, reason: str = "", ip: str = "") -> FiscalClosing:
    year = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    if year is None:
        raise DomainError(f"سال مالی {jy} پیدا نشد.")
    if year.status != "closed":
        raise DomainError(f"سال {jy} بسته نیست.")
    closing = sess.execute(select(FiscalClosing).where(FiscalClosing.jy == jy)).scalar_one_or_none()
    # اول قفل سال را باز می‌کنیم، بعد سند اختتامیه را ابطال می‌کنیم
    # (تا وقتی سال بسته است، سند جدید — حتی سند ابطال — در آن ساخته نمی‌شود).
    year.status = "open"
    for period in sess.execute(select(Period).where(Period.jy == jy)).scalars():
        period.status = "open"
        period.closed_at = None
        period.closed_by = None
    sess.flush()
    if closing and closing.entry_id:
        svc.reverse_entry(sess, user, closing.entry_id, reason or "بازکردن سال مالی", ip)
    if closing:
        closing.status = "reopened"
    sess.flush()
    svc.audit(sess, user, "fiscal.reopen_year", "fiscal_year", str(jy), reason, ip)
    return closing


def is_year_closed(sess, jy: int) -> bool:
    row = sess.execute(select(FiscalYear).where(FiscalYear.title == str(jy))).scalar_one_or_none()
    return bool(row and row.status == "closed")
