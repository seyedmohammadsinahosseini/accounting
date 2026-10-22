"""فروش خدمات آژانس، پرونده‌ی سفر، دریافت و پرداخت.

قاعده‌های رعایت‌شده (از فهرست خودت):
- خدمات آژانس: تور، بلیت، ویزا و سایر — نرخ مالیات از جدول نرخ‌ها و بر اساس تاریخ اجرا می‌آید،
  نه از یک عدد ثابت در کد.
- هر فاکتور و هر صورتحساب، خودش یک سند متوازن می‌سازد؛ هیچ منطق حساب‌داری در جای دیگری نیست.
- هیچ سند مالی خودکار «ثبت نهایی» نمی‌شود، مگر از راه خودِ عملیات که حسابدار تأیید می‌کند.
- هر پول دریافتی/پرداختی به فاکتورهای همان مشتری (یا تأمین‌کننده) نسبت داده می‌شود، از قدیمی‌تر.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import threading

from sqlalchemy import func, select

from . import dates as jd

# قفل درون‌فرآیندی برای لغو جزئی — برای SQLite که FOR UPDATE ندارد
_partial_cancel_locks: dict[int, threading.Lock] = {}
_partial_cancel_locks_lock = threading.Lock()
from . import services as svc
from .models import (Account, Invoice, InvoiceLine, JournalEntry, Party, RecurringEntry,
                     Settlement, SettlementAllocation, SupplierBill, TaxRate, Trip)
from .money import MoneyError, parse_amount
from .rules import SYSTEM_CODES, DomainError

SERVICE_TYPES = {
    "tour": {"label": "تور", "account": "revenue_tour"},
    "ticket": {"label": "بلیت", "account": "revenue_ticket"},
    "visa": {"label": "ویزا", "account": "revenue_other"},
    "hotel": {"label": "اقامت", "account": "revenue_other"},
    "transfer": {"label": "ترانسفر", "account": "revenue_other"},
    "insurance": {"label": "بیمه مسافرتی", "account": "revenue_other"},
    "commission": {"label": "کمیسیون", "account": "revenue_commission"},
    "other": {"label": "سایر خدمات", "account": "revenue_other"},
}

PARTY_CODES = {"customer": SYSTEM_CODES["receivable"], "supplier": SYSTEM_CODES["payable"],
               "both": SYSTEM_CODES["receivable"], "employee": SYSTEM_CODES["employee_loan"]}


# ------------------------------------------------------------------ نرخ مالیات
VAT_SOURCE_1404 = ("نرخ ۱۰٪ از سال ۱۴۰۴ به بعد (احکام بودجه‌ی ۱۴۰۴ و ۱۴۰۵: نرخ عمومی ۱۰٪) — "
                    "تأییدنشده تا حسابدار تأیید کند")


def seed_tax_rates(sess) -> None:
    """نرخ‌های مالیات، تاریخ‌دار.

    نرخ داخل کد قفل نیست: هر نرخ یک بازه‌ی اجرا دارد و با عوض شدن قانون، یک نرخ تازه با
    تاریخ اجرای خودش اضافه می‌شود؛ اسناد قدیمی با نرخ خودشان می‌مانند.
    این تابع هر بار اجرا می‌شود (نه فقط بار اول) تا برنامه‌های به‌روزرسانی‌شده هم نرخ تازه را بگیرند.
    """
    def d(y, m, day):
        return date(*jd.jalali_to_gregorian(y, m, day))

    if not sess.execute(select(TaxRate)).first():
        sess.add(TaxRate(kind="vat", title="مالیات بر ارزش افزوده ۶٪", rate_bp=600,
                         effective_from=d(1397, 1, 1),
                         effective_to=date.fromordinal(d(1399, 1, 1).toordinal() - 1),
                         note="نرخ قدیمی‌تر"))
        sess.add(TaxRate(kind="vat", title="مالیات بر ارزش افزوده ۹٪", rate_bp=900,
                         effective_from=d(1399, 1, 1),
                         effective_to=date.fromordinal(d(1404, 1, 1).toordinal() - 1),
                         note="نرخ سال‌های ۱۳۹۹ تا ۱۴۰۳"))

    has_10 = sess.execute(select(TaxRate).where(TaxRate.kind == "vat",
                                                TaxRate.rate_bp == 1000)).scalar_one_or_none()
    if has_10 is None:
        nine = sess.execute(select(TaxRate).where(TaxRate.kind == "vat", TaxRate.rate_bp == 900)
                            ).scalar_one_or_none()
        if nine is not None and nine.effective_to is None:
            nine.effective_to = date.fromordinal(d(1404, 1, 1).toordinal() - 1)
        sess.add(TaxRate(kind="vat", title="مالیات بر ارزش افزوده ۱۰٪", rate_bp=1000,
                         effective_from=d(1404, 1, 1), effective_to=None, note=VAT_SOURCE_1404))
    sess.flush()


def vat_rate_for(sess, when: date, service_type: str | None = None) -> tuple[int, TaxRate | None]:
    """نرخ مالیات آن روز. نرخ ویژه‌ی یک خدمت بر نرخ عمومی مقدم است."""
    q = select(TaxRate).where(TaxRate.kind == "vat", TaxRate.effective_from <= when)
    rows = list(sess.execute(q).scalars())
    live = [r for r in rows if r.effective_to is None or r.effective_to >= when]
    specific = [r for r in live if r.service_type and r.service_type == (service_type or "")]
    pool = specific or [r for r in live if not r.service_type]
    if not pool:
        return 0, None
    best = max(pool, key=lambda r: r.effective_from)
    return int(best.rate_bp), best


def _check_tax_rate(title: str, rate_percent: float, service_type: str | None) -> tuple[str, int, str | None]:
    title = str(title or "").strip()
    if not title:
        raise DomainError("عنوان نرخ را بنویسید.")
    try:
        value = float(rate_percent)
    except (TypeError, ValueError) as exc:
        raise DomainError("درصد مالیات باید یک عدد باشد.") from exc
    if not 0 <= value <= 100 or round(value * 100) != value * 100:
        raise DomainError("درصد مالیات باید بین ۰ و ۱۰۰ و حداکثر با دو رقم اعشار باشد.")
    scope = str(service_type or "").strip() or None
    if scope and scope not in SERVICE_TYPES:
        raise DomainError("نوع خدمت برای نرخ مالیات شناخته‌شده نیست.")
    return title[:120], int(round(value * 100)), scope


def _normalize_tax_schedule(sess, scope: str | None) -> None:
    rows = list(sess.execute(select(TaxRate).where(
        TaxRate.kind == "vat", TaxRate.service_type == scope
    ).order_by(TaxRate.effective_from, TaxRate.id)).scalars())
    seen_dates: set[date] = set()
    for index, row in enumerate(rows):
        if row.effective_from in seen_dates:
            raise DomainError("برای همین نوع خدمت، دو نرخ از یک تاریخ ثبت شده‌اند.")
        seen_dates.add(row.effective_from)
        next_start = rows[index + 1].effective_from if index + 1 < len(rows) else None
        row.effective_to = date.fromordinal(next_start.toordinal() - 1) if next_start else None


def list_tax_rates(sess, kind: str = "vat") -> list[dict]:
    rows = sess.execute(select(TaxRate).where(TaxRate.kind == kind)
                        .order_by(TaxRate.effective_from.desc())).scalars()
    return [{"id": r.id, "title": r.title, "rate_bp": r.rate_bp, "rate_percent": r.rate_bp / 100,
             "from": jd.jalali_str(r.effective_from),
             "to": jd.jalali_str(r.effective_to) if r.effective_to else "—",
             "service_type": r.service_type or "",
             "service_type_label": SERVICE_TYPES.get(r.service_type or "", {}).get("label", "همه‌ی خدمات"),
             "note": r.note} for r in rows]


def create_tax_rate(sess, user, title: str, rate_percent: float, from_date: date,
                    service_type: str | None = None, note: str = "", ip: str = "") -> TaxRate:
    title, rate_bp, scope = _check_tax_rate(title, rate_percent, service_type)
    duplicate = sess.execute(select(TaxRate).where(
        TaxRate.kind == "vat", TaxRate.service_type == scope,
        TaxRate.effective_from == from_date)).scalar_one_or_none()
    if duplicate is not None:
        raise DomainError("برای همین نوع خدمت، از این تاریخ نرخ ثبت شده است؛ همان ردیف را ویرایش کنید.")
    rate = TaxRate(kind="vat", title=title, rate_bp=rate_bp,
                   effective_from=from_date, service_type=scope, note=str(note or "").strip()[:255])
    sess.add(rate)
    sess.flush()
    _normalize_tax_schedule(sess, scope)
    svc.audit(sess, user, "tax_rate.create", "tax_rate", str(rate.id),
              f"{title} = {rate_bp / 100}% از {jd.jalali_str(from_date)}", ip)
    return rate


def update_tax_rate(sess, user, rate_id: int, payload: dict, ip: str = "") -> TaxRate:
    rate = sess.get(TaxRate, rate_id)
    if rate is None:
        raise DomainError("این نرخ مالیات پیدا نشد.")
    old_scope = rate.service_type
    title, rate_bp, scope = _check_tax_rate(
        payload.get("title", rate.title), payload.get("rate_percent", rate.rate_bp / 100),
        payload.get("service_type", rate.service_type))
    when = payload.get("from_date")
    effective_from = jd.parse_date_any(when) if when else rate.effective_from
    duplicate = sess.execute(select(TaxRate).where(
        TaxRate.kind == "vat", TaxRate.service_type == scope,
        TaxRate.effective_from == effective_from, TaxRate.id != rate.id)).scalar_one_or_none()
    if duplicate is not None:
        raise DomainError("برای همین نوع خدمت، از این تاریخ نرخ دیگری ثبت شده است.")
    rate.title, rate.rate_bp, rate.service_type = title, rate_bp, scope
    rate.effective_from = effective_from
    rate.note = str(payload.get("note", rate.note) or "").strip()[:255]
    sess.flush()
    _normalize_tax_schedule(sess, old_scope)
    if scope != old_scope:
        _normalize_tax_schedule(sess, scope)
    else:
        _normalize_tax_schedule(sess, scope)
    svc.audit(sess, user, "tax_rate.update", "tax_rate", str(rate.id),
              f"{title} = {rate_bp / 100}% از {jd.jalali_str(effective_from)}", ip)
    return rate


# ------------------------------------------------------------------ اشخاص
def list_parties(sess, kind: str | None = None, text: str | None = None) -> list[dict]:
    q = select(Party).order_by(Party.name)
    if kind and kind != "all":
        q = q.where(Party.kind.in_((kind, "both")))
    if text:
        q = q.where(Party.name.ilike(f"%{text.strip()}%") | Party.code.ilike(f"%{text.strip()}%"))
    out = []
    balances = svc.account_balances(sess)
    for p in sess.execute(q).scalars():
        s = balances.get(p.account_id, {"debit": 0, "credit": 0}) if p.account_id else {"debit": 0, "credit": 0}
        net = s["debit"] - s["credit"]
        credit_balance = 0
        if p.credit_account_id:
            cs = balances.get(p.credit_account_id, {"debit": 0, "credit": 0})
            credit_balance = cs["credit"] - cs["debit"]   # قابل مصرف = بستانکاری مانده اعتبار
        out.append({"id": p.id, "code": p.code, "name": p.name, "kind": p.kind,
                    "group_name": p.group_name,
                    "phone": p.phone, "note": p.note, "is_active": p.is_active,
                    "person_type": p.person_type, "national_id": p.national_id,
                    "economic_code": p.economic_code, "postal_code": p.postal_code,
                    "passport_no": p.passport_no, "tax_ready": bool(_digits(p.national_id)),
                    "account_id": p.account_id, "balance": net,
                    "credit_account_id": p.credit_account_id, "credit_balance": credit_balance,
                    "balance_label": "طلب ما" if net > 0 else ("بدهی ما" if net < 0 else "تسویه")})
    return out


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _fill_party_tax(p: Party, payload: dict) -> None:
    """اطلاعات مالیاتی شخص را با کنترل درست بودن می‌نویسد (برای صورتحساب سامانه‌ی مودیان)."""
    if payload.get("person_type") is not None:
        ptype = int(payload["person_type"])
        if ptype not in (1, 2):
            raise DomainError("نوع شخص باید «حقیقی» یا «حقوقی» باشد.")
        p.person_type = ptype
    if payload.get("national_id") is not None:
        nid = _digits(payload["national_id"])
        if nid and len(nid) not in (10, 11):
            raise DomainError("کد ملی ۱۰ رقم و شناسه‌ی ملی شرکت ۱۱ رقم است؛ "
                              f"چیزی که وارد کردید {len(nid)} رقم است.")
        p.national_id = nid[:20]
    if payload.get("economic_code") is not None:
        eco = _digits(payload["economic_code"])
        if eco and len(eco) not in (11, 14):
            raise DomainError("شماره‌ی اقتصادی ۱۱ رقم (اشخاص حقوقی) یا ۱۴ رقم (اشخاص حقیقی) است؛ "
                              f"چیزی که وارد کردید {len(eco)} رقم است.")
        p.economic_code = eco[:20]
    if payload.get("postal_code") is not None:
        pc = _digits(payload["postal_code"])
        if pc and len(pc) != 10:
            raise DomainError("کد پستی ۱۰ رقم است.")
        p.postal_code = pc[:15]
    if payload.get("passport_no") is not None:
        p.passport_no = "".join(str(payload["passport_no"]).split())[:30]


def create_party(sess, user, name: str, kind: str = "customer", phone: str = "",
                 note: str = "", ip: str = "", tax: dict | None = None) -> Party:
    """شخص تازه می‌سازد. ترتیب پارامترها عوض نشده؛ اطلاعات مالیاتی با tax فرستاده می‌شود."""
    name = (name or "").strip()
    if len(name) < 2:
        raise DomainError("نام شخص را کامل بنویسید.")
    if kind not in PARTY_CODES:
        raise DomainError("نوع شخص باید مشتری، تأمین‌کننده، هر دو یا کارمند/کانتر باشد.")
    if kind == "supplier" and tax and any(tax.get(k) for k in
                                          ("national_id", "economic_code", "postal_code")):
        pass      # اطلاعات مالیاتی تأمین‌کننده هم نگه داشته می‌شود (برای اعتبار مالیاتی خرید)
    parent_code = PARTY_CODES[kind]
    parent = svc.account_by_code(sess, parent_code)
    if parent is None:
        raise DomainError(f"حساب والد {parent_code} در کدینگ پیدا نشد.")
    prefix = {"customer": "م", "supplier": "ت", "both": "م", "employee": "ک"}[kind]
    same = sess.execute(select(func.count(Party.id)).where(Party.code.like(f"{prefix}-%"))).scalar() or 0
    seq = same + 1
    code = f"{prefix}-{seq:05d}"
    while sess.execute(select(Party).where(Party.code == code)).scalar_one_or_none():
        seq += 1
        code = f"{prefix}-{seq:05d}"
    acc_seq = len([a for a in svc.list_accounts(sess) if a.parent_code == parent_code]) + 1
    # کد حساب زیرمجموعه: کد والد + شماره، تا در کدینگ زیر همان حساب بنشیند
    while svc.account_by_code(sess, f"{parent_code}-{acc_seq:04d}"):
        acc_seq += 1
    acc = svc.create_account(sess, user, f"{parent_code}-{acc_seq:04d}", f"{name}",
                             parent.nature, parent_code,
                             {"customer": "receivable", "both": "receivable",
                              "supplier": "payable", "employee": "employee_loan"}[kind], ip)
    party = Party(code=code, name=name, kind=kind, phone=phone.strip()[:30], note=note[:255],
                  group_name=(tax or {}).get("group_name", "")[:80].strip(),
                  account_id=acc.id, created_by=user.id)
    _fill_party_tax(party, tax or {})
    sess.add(party)
    sess.flush()
    svc.audit(sess, user, "party.create", "party", str(party.id), f"{code} {name}", ip)
    return party


def update_party(sess, user, party_id: int, payload: dict, ip: str = "") -> Party:
    p = sess.get(Party, party_id)
    if p is None:
        raise DomainError("این شخص پیدا نشد.")
    if payload.get("name"):
        p.name = str(payload["name"]).strip()[:160]
        if p.account_id:
            acc = sess.get(Account, p.account_id)
            if acc:
                acc.name = p.name
    if "phone" in payload and payload["phone"] is not None:
        p.phone = str(payload["phone"])[:30]
    if "note" in payload and payload["note"] is not None:
        p.note = str(payload["note"])[:255]
    if "is_active" in payload and payload["is_active"] is not None:
        p.is_active = bool(payload["is_active"])
    if "group_name" in payload and payload["group_name"] is not None:
        p.group_name = str(payload["group_name"]).strip()[:80]
    _fill_party_tax(p, payload)
    svc.audit(sess, user, "party.update", "party", str(p.id),
              " | ".join(f"{k}={v}" for k, v in payload.items())[:255], ip)
    return p


# ------------------------------------------------------------------ پرونده‌ی سفر
def create_trip(sess, user, title: str, start_date: date, end_date: date | None = None,
                destination: str = "", note: str = "", ip: str = "") -> Trip:
    title = (title or "").strip()
    if len(title) < 2:
        raise DomainError("عنوان سفر را بنویسید.")
    jy = jd.jalali_parts(start_date)[0]
    seq = sess.execute(select(func.count(Trip.id))).scalar() or 0
    code = f"سفر-{jy}-{seq + 1:04d}"
    while sess.execute(select(Trip).where(Trip.code == code)).scalar_one_or_none():
        seq += 1
        code = f"سفر-{jy}-{seq + 1:04d}"
    if end_date and end_date < start_date:
        raise DomainError("تاریخ پایان نمی‌تواند قبل از تاریخ شروع باشد.")
    trip = Trip(code=code, title=title, destination=destination.strip()[:120],
                start_date=start_date, end_date=end_date, note=note[:400], created_by=user.id)
    sess.add(trip)
    sess.flush()
    svc.audit(sess, user, "trip.create", "trip", str(trip.id), f"{code} {title}", ip)
    return trip


def list_trips(sess, include_closed: bool = True) -> list[dict]:
    q = select(Trip).order_by(Trip.start_date.desc(), Trip.id.desc())
    if not include_closed:
        q = q.where(Trip.status == "open")
    out = []
    for t in sess.execute(q).scalars():
        s = trip_totals(sess, t.id)
        out.append({"id": t.id, "code": t.code, "title": t.title, "destination": t.destination,
                    "start": jd.jalali_str(t.start_date),
                    "end": jd.jalali_str(t.end_date) if t.end_date else "—",
                    "status": t.status, **s})
    return out


def trip_totals(sess, trip_id: int) -> dict:
    inv = sess.execute(
        select(func.coalesce(func.sum(Invoice.subtotal_rial - Invoice.discount_rial), 0),
               func.coalesce(func.sum(Invoice.total_rial), 0),
               func.coalesce(func.sum(Invoice.total_rial - Invoice.paid_rial), 0),
               func.count(Invoice.id))
        .where(Invoice.trip_id == trip_id, Invoice.status == "issued")).one()
    bills = sess.execute(
        select(func.coalesce(func.sum(SupplierBill.net_rial), 0),
               func.coalesce(func.sum(SupplierBill.total_rial), 0),
               func.coalesce(func.sum(SupplierBill.total_rial - SupplierBill.paid_rial), 0),
               func.count(SupplierBill.id))
        .where(SupplierBill.trip_id == trip_id, SupplierBill.status == "issued")).one()
    revenue = int(inv[0] or 0)
    cost = int(bills[0] or 0)
    profit = revenue - cost
    return {
        "revenue": revenue, "cost": cost, "profit": profit,
        "margin_percent": round(profit / revenue * 100, 1) if revenue else 0,
        "receivable_open": int(inv[2] or 0), "payable_open": int(bills[2] or 0),
        "invoice_count": int(inv[3] or 0), "bill_count": int(bills[3] or 0),
        "lost": profit < 0,
    }


def trip_detail(sess, trip_id: int) -> dict:
    trip = sess.get(Trip, trip_id)
    if trip is None:
        raise DomainError("این سفر پیدا نشود.")
    invoices = sess.execute(select(Invoice).where(Invoice.trip_id == trip_id)
                            .order_by(Invoice.doc_date)).scalars()
    bills = sess.execute(select(SupplierBill).where(SupplierBill.trip_id == trip_id)
                         .order_by(SupplierBill.doc_date)).scalars()
    settlements = sess.execute(select(Settlement).where(Settlement.trip_id == trip_id)
                               .order_by(Settlement.doc_date)).scalars()
    parties = {p.id: p.name for p in sess.execute(select(Party)).scalars()}
    return {
        "trip": {"id": trip.id, "code": trip.code, "title": trip.title,
                 "destination": trip.destination, "start": jd.jalali_str(trip.start_date),
                 "end": jd.jalali_str(trip.end_date) if trip.end_date else "—",
                 "status": trip.status, "note": trip.note},
        "totals": trip_totals(sess, trip_id),
        "invoices": [{"id": i.id, "number": i.number, "date": jd.jalali_str(i.doc_date),
                      "party": parties.get(i.party_id, "?"), "total": i.total_rial,
                      "paid": i.paid_rial, "status": i.status} for i in invoices],
        "bills": [{"id": b.id, "number": b.number, "date": jd.jalali_str(b.doc_date),
                   "party": parties.get(b.party_id, "?"), "total": b.total_rial,
                   "paid": b.paid_rial, "status": b.status} for b in bills],
        "settlements": [{"id": s.id, "number": s.number, "date": jd.jalali_str(s.doc_date),
                         "direction": s.direction, "party": parties.get(s.party_id, "?"),
                         "amount": s.amount_rial, "status": s.status} for s in settlements],
    }


# ------------------------------------------------------------------ شماره‌گذاری
def _next_number(sess, model, jy: int, marker: str) -> str:
    last = sess.execute(select(func.max(model.number)).where(model.number.is_not(None),
                                                             model.number.like(f"{jy}-{marker}-%"))
                        ).scalar()
    seq = int(last.split("-")[-1]) + 1 if last else 1
    return f"{jy}-{marker}-{seq:06d}"


# ------------------------------------------------------------------ فاکتور فروش
def _money(value) -> int:
    if value is None or str(value).strip() == "":
        return 0
    try:
        amount = parse_amount(value)
    except MoneyError as exc:
        raise DomainError(str(exc)) from exc
    if amount < 0:
        raise DomainError("مبلغ نمی‌تواند منفی باشد.")
    return amount


def _party(sess, party_id: int, need: str) -> Party:
    p = sess.get(Party, party_id)
    if p is None:
        raise DomainError("مشتری/تأمین‌کننده انتخاب نشده است.")
    if not p.is_active:
        raise DomainError(f"«{p.name}» غیرفعال است.")
    if p.kind not in (need, "both"):
        label = "مشتری" if need == "customer" else "تأمین‌کننده"
        raise DomainError(f"«{p.name}» {label} نیست؛ از فهرست درست انتخاب کنید.")
    return p


def compute_invoice(sess, doc_date: date, raw_lines: list[dict]) -> dict:
    """فاکتور را می‌سازد و مالیات هر سطر را با نرخ همان تاریخ حساب می‌کند."""
    if not raw_lines:
        raise DomainError("فاکتور باید حداقل یک سطر داشته باشد.")
    lines, subtotal, discount_total, vat_total = [], 0, 0, 0
    for i, raw in enumerate(raw_lines, start=1):
        stype = str(raw.get("service_type") or "other")
        if stype not in SERVICE_TYPES:
            raise DomainError(f"سطر {i}: نوع خدمت نامعتبر است.")
        try:
            qty = int(raw.get("qty") or 1)
        except (TypeError, ValueError) as exc:
            raise DomainError(f"سطر {i}: تعداد باید عدد صحیح باشد.") from exc
        if qty <= 0:
            raise DomainError(f"سطر {i}: تعداد باید بزرگ‌تر از صفر باشد.")
        unit = _money(raw.get("unit_price"))
        disc = _money(raw.get("discount"))
        gross = qty * unit
        if disc > gross:
            raise DomainError(f"سطر {i}: تخفیف بیشتر از مبلغ سطر است.")
        net = gross - disc
        taxable = bool(raw.get("taxable", True))
        rate_bp, _rate = vat_rate_for(sess, doc_date, stype) if taxable else (0, None)
        vat = (net * rate_bp) // 10000 if rate_bp else 0
        flight = raw.get("flight_type")
        if stype == "ticket" and flight not in (None, "", 0):
            try:
                flight = int(flight)
            except (TypeError, ValueError) as exc:
                raise DomainError(f"سطر {i}: نوع پرواز باید «داخلی» یا «خارجی» باشد.") from exc
            if flight not in (1, 2):
                raise DomainError(f"سطر {i}: نوع پرواز باید «داخلی» یا «خارجی» باشد.")
        elif stype == "ticket":
            flight = None     # نامعلوم؛ در سامانه‌ی مودیان گفته می‌شود که انتخاب نشده است
        else:
            flight = None

        revenue_model = str(raw.get("revenue_model") or "gross").strip().lower()
        if revenue_model not in ("gross", "commission"):
            raise DomainError(f"سطر {i}: مدل درآمدی نامعتبر است.")
        purchase_cost = _money(raw.get("purchase_cost"))
        supplier_id = raw.get("supplier_party_id")
        if supplier_id in (None, "", 0, "0"):
            supplier_id = None
        else:
            try:
                supplier_id = int(supplier_id)
            except (TypeError, ValueError) as exc:
                raise DomainError(f"سطر {i}: تأمین‌کننده‌ی انتخاب‌شده معتبر نیست.") from exc
            _party(sess, supplier_id, "supplier")
        if purchase_cost and supplier_id is None:
            raise DomainError(f"سطر {i}: برای ثبت مبلغ خرید، تأمین‌کننده را انتخاب کنید.")
        if revenue_model == "commission":
            if supplier_id is None:
                raise DomainError(f"سطر {i}: برای مدل امانی/کمیسیون، تأمین‌کننده لازم است.")
            if purchase_cost <= 0:
                raise DomainError(f"سطر {i}: مبلغ خرید ایرلاین/تأمین‌کننده را برای مدل امانی وارد کنید.")
            if purchase_cost > net:
                raise DomainError(f"سطر {i}: مبلغ خرید نمی‌تواند از مبلغ فروش پس از تخفیف بیشتر باشد؛ "
                                  "برای فروش زیان‌ده، مدل «فروش مستقیم» را انتخاب کنید.")

        lines.append({"line_no": i, "service_type": stype,
                      "description": str(raw.get("description") or "")[:300],
                      "qty": qty, "unit_price": unit, "discount": disc, "taxable": taxable,
                      "flight_type": flight, "revenue_model": revenue_model,
                      "purchase_cost": purchase_cost, "supplier_party_id": supplier_id,
                      "net": net, "vat": vat, "rate_bp": rate_bp})
        subtotal += gross
        discount_total += disc
        vat_total += vat
    if not lines:
        raise DomainError("فاکتور سطر سالمی ندارد.")
    return {"lines": lines, "subtotal": subtotal, "discount": discount_total, "vat": vat_total,
            "total": subtotal - discount_total + vat_total}


def create_invoice(sess, user, party_id: int, doc_date: date, raw_lines: list[dict],
                   trip_id: int | None = None, description: str = "", due_date: date | None = None,
                   issue: bool = False, ip: str = "") -> Invoice:
    party = _party(sess, party_id, "customer")
    if trip_id and sess.get(Trip, trip_id) is None:
        raise DomainError("پرونده‌ی سفر انتخاب‌شده پیدا نشد.")
    doc = compute_invoice(sess, doc_date, raw_lines)
    inv = Invoice(doc_date=doc_date, due_date=due_date or doc_date, party_id=party.id,
                  trip_id=trip_id, created_by=user.id, description=str(description)[:300],
                  subtotal_rial=doc["subtotal"], discount_rial=doc["discount"],
                  vat_rial=doc["vat"], total_rial=doc["total"], status="draft")
    for l in doc["lines"]:
        inv.lines.append(InvoiceLine(line_no=l["line_no"], service_type=l["service_type"],
                                     description=l["description"], qty=l["qty"],
                                     unit_price_rial=l["unit_price"], discount_rial=l["discount"],
                                     taxable=l["taxable"], flight_type=l.get("flight_type"),
                                     revenue_model=l["revenue_model"],
                                     purchase_cost_rial=l["purchase_cost"],
                                     supplier_party_id=l["supplier_party_id"],
                                     net_rial=l["net"], vat_rial=l["vat"],
                                     rate_bp=l["rate_bp"]))
    sess.add(inv)
    sess.flush()
    svc.audit(sess, user, "invoice.draft", "invoice", str(inv.id),
              f"{party.name} — {inv.total_rial:,} ریال", ip)
    if issue:
        issue_invoice(sess, user, inv.id, ip)
    return inv


def set_flight_type(sess, user, invoice_id: int, line_id: int, flight_type: int, ip: str = "") -> InvoiceLine:
    """نوع پرواز یک ردیف بلیت را ثبت/اصلاح می‌کند (تغییر مالی نیست، برچسب اطلاعاتی است).

    اگر صورتحساب الکترونیکی همان فاکتور ارسال یا پذیرفته شده باشد، اجازه‌ی تغییر نمی‌دهیم؛
    چون آن‌وقت اطلاعات ما با اطلاعات سازمان مالیاتی نمی‌خواند.
    """
    from .models import MoadianInvoice

    inv = sess.get(Invoice, invoice_id)
    if inv is None:
        raise DomainError("فاکتور پیدا نشد.")
    line = sess.get(InvoiceLine, line_id)
    if line is None or line.invoice_id != inv.id:
        raise DomainError("این ردیف به این فاکتور تعلق ندارد.")
    if line.service_type != "ticket":
        raise DomainError("نوع پرواز فقط برای ردیف بلیت معنی دارد.")
    if int(flight_type) not in (1, 2):
        raise DomainError("نوع پرواز باید «داخلی» یا «خارجی» باشد.")
    busy = sess.execute(select(MoadianInvoice).where(
        MoadianInvoice.invoice_id == inv.id,
        MoadianInvoice.status.in_(("sent", "accepted")))).scalars().first()
    if busy is not None:
        raise DomainError("صورتحساب الکترونیکی این فاکتور ارسال شده است؛ تغییر نوع پرواز "
                          "باعث اختلاف با سازمان مالیاتی می‌شود.")
    line.flight_type = int(flight_type)
    sess.flush()
    svc.audit(sess, user, "invoice.flight_type", "invoice_line", str(line.id),
              f"فاکتور {inv.number or inv.id} — ردیف {line.line_no} → "
              + ("داخلی" if int(flight_type) == 1 else "خارجی"), ip)
    return line


def _invoice_entry_lines(sess, inv: Invoice) -> list[dict]:
    party = sess.get(Party, inv.party_id)
    by_service: dict[str, int] = {}
    airline_clearing = 0
    for line in inv.lines:
        if line.revenue_model == "commission":
            # بهای بلیت/خدمت متعلق به ایرلاین در حساب بدهی امانی می‌نشیند؛ فقط
            # مابه‌التفاوت پس از خرید، درآمد کمیسیون آژانس است.
            commission = line.net_rial - line.purchase_cost_rial
            airline_clearing += line.purchase_cost_rial
            by_service["revenue_commission"] = by_service.get("revenue_commission", 0) + commission
        else:
            key = SERVICE_TYPES[line.service_type]["account"]
            by_service[key] = by_service.get(key, 0) + line.net_rial
    lines = [{"account_id": party.account_id, "debit": inv.total_rial, "credit": 0,
              "description": f"فاکتور فروش {inv.number or ''} — {party.name}"}]
    for key, amount in by_service.items():
        if amount:
            acc = svc.account_by_code(sess, SYSTEM_CODES[key])
            lines.append({"account_id": acc.id, "debit": 0, "credit": amount,
                          "description": "درآمد کمیسیون آژانس" if key == "revenue_commission"
                          else f"درآمد {key}"})
    if airline_clearing:
        clear = svc.account_by_code(sess, SYSTEM_CODES["airline_clearing"])
        lines.append({"account_id": clear.id, "debit": 0, "credit": airline_clearing,
                      "description": "سهم امانی / بدهی به ایرلاین"})
    if inv.vat_rial:
        vat = svc.account_by_code(sess, SYSTEM_CODES["vat_payable"])
        lines.append({"account_id": vat.id, "debit": 0, "credit": inv.vat_rial,
                      "description": "مالیات بر ارزش افزوده فروش"})
    return lines


def issue_invoice(sess, user, invoice_id: int, ip: str = "") -> Invoice:
    inv = sess.get(Invoice, invoice_id)
    if inv is None:
        raise DomainError("این فاکتور پیدا نشد.")
    if inv.status == "issued":
        return inv
    if inv.status == "cancelled":
        raise DomainError("فاکتور ابطال‌شده قابل صدور نیست.")
    if inv.total_rial <= 0:
        raise DomainError("فاکتور بدون مبلغ قابل صدور نیست.")
    jy = jd.jalali_parts(inv.doc_date)[0]
    number = _next_number(sess, Invoice, jy, "ف")
    inv.number = number
    entry = svc.create_draft(sess, user, inv.doc_date,
                             f"فاکتور فروش {number} — {sess.get(Party, inv.party_id).name}",
                             _invoice_entry_lines(sess, inv), source="system",
                             source_ref=number, ip=ip)
    svc.post_entry(sess, user, entry.id, ip)
    inv.entry_id = entry.id
    inv.status = "issued"
    sess.flush()
    svc.audit(sess, user, "invoice.issue", "invoice", str(inv.id), f"{number}", ip)
    return inv


def cancel_invoice(sess, user, invoice_id: int, reason: str, ip: str = "") -> Invoice:
    inv = sess.get(Invoice, invoice_id)
    if inv is None:
        raise DomainError("این فاکتور پیدا نشد.")
    if inv.status == "cancelled":
        return inv
    # اگر فاکتور کاملاً تسویه شده است، لغو را به‌عنوان اعتبار مشتری بپذیر
    # (درآمد معکوس می‌شود ولی وجه واقعی باقی می‌ماند — بستانکاری 50M)
    # اما اگر فقط جزئی پرداخت شده، اول باید تسویه‌ها برگردند تا ابهام نماند
    if inv.paid_rial > 0:
        if inv.paid_rial != inv.total_rial:
            raise DomainError("برای این فاکتور پول دریافت شده است؛ اول تسویه‌ها را برگردانید.")
        to_clear = sess.execute(select(SettlementAllocation).where(
            SettlementAllocation.target_type == "invoice",
            SettlementAllocation.target_id == inv.id)).scalars().all()
        for a in to_clear:
            sess.delete(a)
        inv.paid_rial = 0
    if inv.status == "issued" and inv.entry_id:
        svc.reverse_entry(sess, user, inv.entry_id, reason or "ابطال فاکتور", ip)
    inv.status = "cancelled"
    svc.audit(sess, user, "invoice.cancel", "invoice", str(inv.id), reason, ip)
    return inv


# ------------------------------------------------------------------ لغو جزئی (مرحله اصلاح اصولی)
PC_STATUS_FA = {"draft": "پیش‌نویس", "pending": "در انتظار تأیید", "confirmed": "تأییدشده",
                "rejected": "ردشده", "cancelled": "ابطال‌شده"}
REFUND_MODE_FA = {"immediate": "پرداخت فوری", "deferred": "پرداخت در آینده", "credit": "اعتبار خرید بعدی"}


def _pc_lock(invoice_id: int):
    with _partial_cancel_locks_lock:
        return _partial_cancel_locks.setdefault(invoice_id, threading.Lock())


def _pc_dup_exists(sess, invoice_id: int, doc_date: date, amount: int, reason: str):
    from .models import InvoicePartialCancel
    return sess.execute(select(InvoicePartialCancel).where(
        InvoicePartialCancel.invoice_id == invoice_id,
        InvoicePartialCancel.doc_date == doc_date,
        InvoicePartialCancel.amount_rial == amount,
        InvoicePartialCancel.reason == reason.strip(),
        InvoicePartialCancel.status.notin_(("cancelled", "rejected")))).scalar_one_or_none()


def _moadian_sent(sess, invoice_id: int) -> bool:
    from .models import MoadianInvoice
    return sess.execute(select(MoadianInvoice).where(
        MoadianInvoice.invoice_id == invoice_id,
        MoadianInvoice.status.in_(("sent", "accepted")))).scalars().first() is not None


def _pc_selected_lines(sess, inv: Invoice, line_id: int | None):
    if line_id is not None:
        line = sess.get(InvoiceLine, int(line_id))
        if line is None or line.invoice_id != inv.id:
            raise DomainError("ردیف خدمت انتخاب‌شده به این فاکتور تعلق ندارد.")
        return [line]
    return list(inv.lines)


def _pc_cost_share(lines, cancel_amount: int, inv_total: int, single: bool) -> int:
    """سهم هزینه‌ی ثبت‌شده‌ی بخش لغوشده؛ صفر یعنی هزینه‌ای در ردیف‌ها ثبت نشده (سقف‌گذاری ممکن نیست)."""
    if not lines:
        return 0
    if single and lines[0].net_rial > 0:
        return min(lines[0].purchase_cost_rial,
                   lines[0].purchase_cost_rial * cancel_amount // max(1, lines[0].net_rial))
    if inv_total <= 0:
        return 0
    return sum(l.purchase_cost_rial for l in lines) * cancel_amount // inv_total


def _pc_debit_parts(sess, inv: Invoice, lines, cancel_amount: int):
    """بدهکار سند برگشت بر اساس ثبت اولیه‌ی همان فاکتور: gross → درآمد خود خدمت،
    commission → کمیسیون (4105) + سهم امانی ایرلاین (2108)؛ به‌علاوه‌ی برگشت مالیات فروش (2104)."""
    vat_share = (inv.vat_rial * cancel_amount) // inv.total_rial if inv.vat_rial and inv.total_rial else 0
    base = cancel_amount - vat_share
    if not lines:
        return [("4101", base, "برگشت درآمد")], vat_share
    total_net = sum(max(0, l.net_rial) for l in lines) or 1
    shares = [[l, base * max(0, l.net_rial) // total_net] for l in lines]
    shares[-1][1] += base - sum(a for _, a in shares)      # باقی‌مانده‌ی گردکردن روی آخرین ردیف
    group: dict[str, list] = {}
    for l, share in shares:
        if share <= 0:
            continue
        if l.revenue_model == "commission" and l.net_rial > 0:
            cost = share * l.purchase_cost_rial // l.net_rial
            comm = share - cost
            if comm:
                group.setdefault(SYSTEM_CODES["revenue_commission"], [0, "برگشت کمیسیون آژانس"])[0] += comm
            if cost:
                group.setdefault(SYSTEM_CODES["airline_clearing"], [0, "برگشت سهم امانی ایرلاین"])[0] += cost
        else:
            st = SERVICE_TYPES.get(l.service_type, SERVICE_TYPES["other"])
            code = SYSTEM_CODES.get(st["account"], "4101")
            group.setdefault(code, [0, "برگشت درآمد خدمت لغوشده"])[0] += share
    return [(code, amt, desc) for code, (amt, desc) in group.items() if amt > 0], vat_share


def _io_locked_invoice(sess, invoice_id: int) -> Invoice | None:
    try:
        return sess.execute(select(Invoice).where(Invoice.id == invoice_id)
                            .with_for_update()).scalar_one_or_none()
    except Exception:
        return sess.get(Invoice, invoice_id)


def create_partial_cancel(sess, user, invoice_id: int, cancel_amount: int, reason: str,
                          doc_date: date | None = None, penalty_customer: int = 0,
                          agency_fee: int = 0, supplier_refund: int = 0, penalty_supplier: int = 0,
                          supplier_party_id: int | None = None, supplier_refund_vat: int = 0,
                          line_id: int | None = None, refund_mode: str = "deferred",
                          submit: bool = True, allow_over_cost: bool = False,
                          allow_moadian: bool = False, ip: str = "",
                          counter_penalty: int = 0, counter_party_id: int | None = None,
                          hold_lock: bool = True):
    """ثبت لغو جزئی — جدا از پرداخت واقعی. فقط اسناد حسابداری زده می‌شود؛ جابه‌جایی وجه
    طبق refund_mode: immediate (همان لحظه تسویه بانکی)، deferred (بدهی باز)، credit (اعتبار مشتری).
    هشدار قفل: برای اتمیک‌بودن واقعی زیر بار هم‌زمان، لایه‌ی API قفل را تا بعد از commit نگه می‌دارد
    (hold_lock=False در مسیر API)؛ فراخوانی مستقیم با hold_lock=True فقط دور محاسبه/ثبت را پوشش می‌دهد."""
    from .models import InvoicePartialCancel
    if refund_mode not in REFUND_MODE_FA:
        raise DomainError("حالت تسویه استرداد باید «فوری»، «بعداً» یا «اعتبار» باشد.")
    lock = _pc_lock(invoice_id)
    if hold_lock:
        lock.acquire()
    try:
        inv = _io_locked_invoice(sess, invoice_id)
        if inv is None:
            raise DomainError("این فاکتور پیدا نشد.")
        if inv.status != "issued":
            raise DomainError("فقط فاکتور صادرشده را می‌توان به‌صورت جزئی لغو کرد.")
        if not reason or len(reason.strip()) < 8:
            raise DomainError("دلیل لغو را کامل بنویسید (حداقل ۸ نویسه).")
        if cancel_amount is None or cancel_amount <= 0:
            raise DomainError("مبلغ لغو باید بزرگ‌تر از صفر باشد.")
        doc_date = doc_date or jd.jtoday()
        from .models import InvoicePartialCancel as _PC
        prev = sess.execute(select(func.coalesce(func.sum(_PC.amount_rial), 0)).where(
            _PC.invoice_id == inv.id, _PC.status.notin_(("cancelled", "rejected")))).scalar() or 0
        cancellable = inv.total_rial - prev
        if cancel_amount > cancellable:
            raise DomainError(f"مبلغ لغو از مبلغ قابل‌لغو بیشتر است؛ قابل‌لغو {cancellable:,} ریال است.")
        for nm, v in (("جریمه مشتری", penalty_customer), ("کارمزد آژانس", agency_fee),
                      ("برگشت تامین", supplier_refund), ("جریمه تامین", penalty_supplier),
                      ("مالیات برگشتی", supplier_refund_vat), ("جریمه بر عهده کانتر", counter_penalty)):
            if v < 0:
                raise DomainError(f"مبلغ {nm} نمی‌تواند منفی باشد.")
        if penalty_customer > cancel_amount:
            raise DomainError("جریمه مشتری نمی‌تواند از مبلغ لغو بیشتر باشد.")
        if agency_fee + penalty_customer > cancel_amount:
            raise DomainError("جمع جریمه مشتری و کارمزد آژانس نمی‌تواند از مبلغ لغو بیشتر باشد.")
        if supplier_refund_vat > 0 and supplier_refund == 0:
            raise DomainError("برای برگشت مالیات، مبلغ برگشت تامین هم باید مشخص باشد.")
        if supplier_refund_vat > supplier_refund:
            raise DomainError("مالیات برگشتی نمی‌تواند از مبلغ برگشت تامین بیشتر باشد.")
        lines = _pc_selected_lines(sess, inv, line_id)
        known_cost = _pc_cost_share(lines, cancel_amount, inv.total_rial, line_id is not None)
        if known_cost and supplier_refund > known_cost:
            if not allow_over_cost:
                raise DomainError(
                    f"برگشت تامین ({supplier_refund:,}) از هزینه‌ی ثبت‌شده‌ی این بخش ({known_cost:,} ریال) بیشتر است؛ "
                    f"در غیر این صورت با مجوز ویژه و دلیل مستند ثبت کنید.", "over_cost")
            if len(reason.strip()) < 15:
                raise DomainError("برگشت تامین بالاتر از هزینه نیازمند دلیل مستند (حداقل ۱۵ نویسه) است.")
        if _pc_dup_exists(sess, inv.id, doc_date, cancel_amount, reason):
            raise DomainError("این لغو جزئی قبلاً ثبت شده است.")
        sup_id = supplier_party_id
        if (supplier_refund or penalty_supplier) and not sup_id:
            for l in lines:
                if l.supplier_party_id:
                    sup_id = l.supplier_party_id
                    break
        moadian_sent = _moadian_sent(sess, inv.id)
        if moadian_sent and not allow_moadian:
            raise DomainError(
                "صورتحساب الکترونیکی این فاکتور ارسال/پذیرفته شده؛ ثبت لغو مستلزم اقدام مالیاتی "
                "(صدور صورتحساب ابطالی/اصلاحی طبق ضوابط سامانه مودیان) است. "
                "اگر آن گردش کار انجام شده، لغو را با تأکید ثبت کنید تا «در انتظار تأیید» بماند.",
                "moadian_required")
        refund_due = cancel_amount - penalty_customer - agency_fee
        # جریمه بر عهده کانتر: مسئولیت را به حساب کانتر می‌بریم و سوخت آژانس را برمی‌گردانیم
        burn = max(0, known_cost - supplier_refund) if known_cost else 0
        if counter_penalty:
            counter = sess.get(Party, counter_party_id) if counter_party_id else None
            if counter is None or counter.kind != "employee":
                raise DomainError("برای جریمه بر عهده کانتر، یک شخص با نوع «کارمند/کانتر» را انتخاب کنید.")
            if not counter.is_active or not counter.account_id:
                raise DomainError("حساب تفصیلی کانتر فعال نیست یا ساخته نشده است.")
            if counter_penalty > burn:
                calc = (f"خسارت برنگشتی = قیمت خرید ثبت‌شده‌ی این بخش ({known_cost:,}) − "
                        f"پولی که تأمین برمی‌گرداند ({supplier_refund:,}) = {burn:,}")
                hint = ("برای این بخش از فاکتور قیمت خریدی در سیستم ثبت نشده؛ اول «قیمت خرید/سهم "
                        "تأمین‌کننده» را در فاکتور کامل کنید تا خسارت محاسبه شود، یا مبلغ کانتر را کمتر کنید."
                        if not known_cost else
                        "خسارت کانتر نمی‌تواند از خسارت واقعی آژانس بیشتر باشد؛ مازادش «درآمد» می‌شد، نه جبران خسارت.")
                raise DomainError(f"مبلغ خسارت کانتر ({counter_penalty:,}) از خسارت برنگشتی بیشتر است. {calc}. {hint}")
        pc = InvoicePartialCancel(
            invoice_id=inv.id, doc_date=doc_date, amount_rial=cancel_amount,
            penalty_customer_rial=penalty_customer, agency_fee_rial=agency_fee,
            supplier_refund_rial=supplier_refund, penalty_supplier_rial=penalty_supplier,
            supplier_refund_vat_rial=supplier_refund_vat, reason=reason.strip(),
            refund_mode=refund_mode, refund_due_rial=refund_due,
            supplier_claim_rial=supplier_refund + penalty_supplier,
            supplier_party_id=sup_id, line_id=lines[0].id if line_id is not None else None,
            nonrefundable_cost_rial=burn,
            counter_penalty_rial=counter_penalty,
            counter_party_id=counter_party_id if counter_penalty else None,
            profit_impact_rial=penalty_customer + agency_fee + supplier_refund + penalty_supplier
            - cancel_amount + counter_penalty,
            status="pending" if moadian_sent else ("confirmed" if submit else "draft"),
            created_by=user.id)
        sess.add(pc)
        sess.flush()
        if pc.status == "confirmed":
            _confirm_partial_cancel(sess, user, inv, pc, lines, ip)
        sess.flush()
        svc.audit(sess, user, "invoice.partial_cancel", "invoice", str(inv.id),
                  f"لغو {cancel_amount:,} حالت {refund_mode} وضعیت {pc.status}", ip)
        return pc
    finally:
        if hold_lock:
            lock.release()


def _confirm_partial_cancel(sess, user, inv: Invoice, pc, lines, ip: str = "") -> None:
    """صدور اسناد لغو — اتمیک داخل همان تراکنش. فرض: pc به‌تازگی ساخته و اعتبارسنجی شده."""
    party = sess.get(Party, inv.party_id)
    freed: list[dict] = []
    allocs = sess.execute(select(SettlementAllocation).where(
        SettlementAllocation.target_type == "invoice",
        SettlementAllocation.target_id == inv.id).order_by(SettlementAllocation.id)).scalars().all()
    if allocs:
        to_free = pc.amount_rial
        for a in allocs:
            if to_free <= 0:
                break
            take = min(a.amount_rial, to_free)
            freed.append({"settlement_id": a.settlement_id, "amount": take})
            if take == a.amount_rial:
                sess.delete(a)
            else:
                a.amount_rial -= take
            to_free -= take
        sess.flush()
        remaining = sum(a.amount_rial for a in sess.execute(select(SettlementAllocation).where(
            SettlementAllocation.target_type == "invoice",
            SettlementAllocation.target_id == inv.id)).scalars().all())
        inv.paid_rial = max(0, remaining)
    else:
        inv.paid_rial = max(0, inv.paid_rial - pc.amount_rial)
    # ۱) سند برگشت فروش — مطابق ثبت اولیه (gross/commission) + برگشت مالیات فروش
    parts, vat_share = _pc_debit_parts(sess, inv, lines, pc.amount_rial)
    e_lines = []
    for code, amt, desc in parts:
        acc = svc.account_by_code(sess, code)
        if acc is None:
            raise DomainError(f"حساب {code} در کدینگ پیدا نشد.")
        e_lines.append({"account_id": acc.id, "debit": amt, "credit": 0, "description": desc})
    if vat_share:
        vat_acc = svc.account_by_code(sess, SYSTEM_CODES["vat_payable"])
        e_lines.append({"account_id": vat_acc.id, "debit": vat_share, "credit": 0,
                        "description": "برگشت مالیات فروش — 2104"})
    e_lines.append({"account_id": party.account_id, "debit": 0, "credit": pc.amount_rial,
                    "description": f"لغو {inv.number}"})
    entry_cancel = svc.create_draft(sess, user, pc.doc_date,
        f"لغو جزئی {pc.amount_rial:,} از فاکتور {inv.number}", e_lines,
        source="system", source_ref=f"partial-cancel-{pc.id}", ip=ip)
    svc.post_entry(sess, user, entry_cancel.id, ip)
    pc.entry_id = entry_cancel.id
    # ۲) جریمه مشتری — 4107
    if pc.penalty_customer_rial > 0:
        penalty_cust_acc = svc.account_by_code(sess, "4107")
        entry_pen = svc.create_draft(sess, user, pc.doc_date,
            f"جریمه لغو مشتری {pc.penalty_customer_rial:,} فاکتور {inv.number}",
            [{"account_id": party.account_id, "debit": pc.penalty_customer_rial, "credit": 0, "description": "جریمه مشتری"},
             {"account_id": penalty_cust_acc.id, "debit": 0, "credit": pc.penalty_customer_rial, "description": "جریمه مشتری — 4107"}],
            source="system", source_ref=f"penalty-cust-{pc.id}", ip=ip)
        svc.post_entry(sess, user, entry_pen.id, ip)
        pc.penalty_entry_id = entry_pen.id
    # ۳) کارمزد خدمات کنسلی آژانس — 4105
    if pc.agency_fee_rial > 0:
        fee_acc = svc.account_by_code(sess, SYSTEM_CODES["revenue_commission"])
        entry_fee = svc.create_draft(sess, user, pc.doc_date,
            f"کارمزد کنسلی {pc.agency_fee_rial:,} فاکتور {inv.number}",
            [{"account_id": party.account_id, "debit": pc.agency_fee_rial, "credit": 0, "description": "کارمزد کنسلی"},
             {"account_id": fee_acc.id, "debit": 0, "credit": pc.agency_fee_rial, "description": "کارمزد کنسلی — 4105"}],
            source="system", source_ref=f"agency-fee-{pc.id}", ip=ip)
        svc.post_entry(sess, user, entry_fee.id, ip)
        pc.agency_fee_entry_id = entry_fee.id
    # ۴) برگشت از تامین (+ تفکیک مالیات خرید) و ۵) جریمه تامین — هر دو «طلب» روی تفصیلی تأمین
    sup = sess.get(Party, pc.supplier_party_id) if pc.supplier_party_id else None
    if (pc.supplier_refund_rial > 0 or pc.penalty_supplier_rial > 0) and sup:
        cogs_acc = svc.account_by_code(sess, "5101")
        if pc.supplier_refund_rial > 0:
            vat_acc = svc.account_by_code(sess, SYSTEM_CODES["vat_credit"])
            net_refund = pc.supplier_refund_rial - pc.supplier_refund_vat_rial
            s_lines = [{"account_id": sup.account_id, "debit": pc.supplier_refund_rial, "credit": 0,
                        "description": "برگشت تامین"}]
            if pc.supplier_refund_vat_rial > 0 and vat_acc:
                s_lines.append({"account_id": vat_acc.id, "debit": 0,
                                "credit": pc.supplier_refund_vat_rial, "description": "برگشت مالیات خرید — 1107"})
                s_lines.append({"account_id": cogs_acc.id, "debit": 0, "credit": net_refund,
                                "description": "برگشت تامین — خالص"})
            else:
                s_lines.append({"account_id": cogs_acc.id, "debit": 0, "credit": pc.supplier_refund_rial,
                                "description": "برگشت تامین"})
            entry_sup = svc.create_draft(sess, user, pc.doc_date,
                f"برگشت {pc.supplier_refund_rial:,} از تامین بابت لغو {inv.number}", s_lines,
                source="system", source_ref=f"supplier-refund-{pc.id}", ip=ip)
            svc.post_entry(sess, user, entry_sup.id, ip)
            pc.supplier_refund_entry_id = entry_sup.id
            pc.supplier_entry_id = entry_sup.id
        if pc.penalty_supplier_rial > 0:
            penalty_sup_acc = svc.account_by_code(sess, "4108")
            entry_sup_pen = svc.create_draft(sess, user, pc.doc_date,
                f"جریمه تامین {pc.penalty_supplier_rial:,} بابت لغو {inv.number}",
                [{"account_id": sup.account_id, "debit": pc.penalty_supplier_rial, "credit": 0, "description": "جریمه تامین"},
                 {"account_id": penalty_sup_acc.id, "debit": 0, "credit": pc.penalty_supplier_rial, "description": "جریمه تامین — 4108"}],
                source="system", source_ref=f"penalty-sup-{pc.id}", ip=ip)
            svc.post_entry(sess, user, entry_sup_pen.id, ip)
            pc.supplier_penalty_entry_id = entry_sup_pen.id
            if not pc.supplier_entry_id:
                pc.supplier_entry_id = entry_sup_pen.id
    # ۶) سوخت هزینه را از بهای تمام‌شده جدا می‌کنیم: ۵۱۰۱ ← ۵۱۰۹ (حساب مستقل کنسلی‌ها)
    if pc.nonrefundable_cost_rial > 0:
        burn_acc = svc.account_by_code(sess, SYSTEM_CODES["cancel_burn"])
        cogs_acc2 = svc.account_by_code(sess, SYSTEM_CODES["cogs"])
        if burn_acc and cogs_acc2:
            entry_burn = svc.create_draft(sess, user, pc.doc_date,
                f"تشخیص سوخت هزینهٔ لغو {pc.nonrefundable_cost_rial:,} فاکتور {inv.number}",
                [{"account_id": burn_acc.id, "debit": pc.nonrefundable_cost_rial, "credit": 0,
                  "description": "سوخت کنسلی — جریمه‌ای که تامین نگه داشت"},
                 {"account_id": cogs_acc2.id, "debit": 0, "credit": pc.nonrefundable_cost_rial,
                  "description": "خروج از بهای تمام‌شده‌ی خدمات"}],
                source="system", source_ref=f"cancel-burn-{pc.id}", ip=ip)
            svc.post_entry(sess, user, entry_burn.id, ip)
            pc.burn_entry_id = entry_burn.id
    # ۷) جریمه بر عهده کانتر: طلب از کانتر به‌جای سوخت آژانس (۵۱۰۹ ← مطالبهٔ کانتر)
    if pc.counter_penalty_rial > 0 and pc.counter_party_id:
        counter = sess.get(Party, pc.counter_party_id)
        burn_acc2 = svc.account_by_code(sess, SYSTEM_CODES["cancel_burn"])
        if counter and counter.account_id and burn_acc2:
            entry_ctr = svc.create_draft(sess, user, pc.doc_date,
                f"جریمه بر عهدهٔ کانتر {pc.counter_penalty_rial:,} بابت لغو فاکتور {inv.number}",
                [{"account_id": counter.account_id, "debit": pc.counter_penalty_rial, "credit": 0,
                  "description": f"جریمه خطای کانتر — {counter.name} — قابل وصول"},
                 {"account_id": burn_acc2.id, "debit": 0, "credit": pc.counter_penalty_rial,
                  "description": "کسر از سوخت کنسلی به حساب کانتر"}],
                source="system", source_ref=f"counter-penalty-{pc.id}", ip=ip)
            svc.post_entry(sess, user, entry_ctr.id, ip)
            pc.counter_penalty_entry_id = entry_ctr.id
    # حالت تسویه — جدا از ثبت اسناد لغو
    state: dict = {"freed": freed, "mode": pc.refund_mode, "credit_entry_ids": []}
    if pc.refund_due_rial > 0 and pc.refund_mode == "immediate":
        if party.kind == "customer":
            party.kind = "both"
        bank_acc = svc.account_by_code(sess, "110201") or svc.account_by_code(sess, "1101")
        refund = create_settlement(sess, user, "out", inv.party_id, bank_acc.id, pc.refund_due_rial,
                                   pc.doc_date, description=f"استرداد فوری لغو جزئی {inv.number}",
                                   issue=True, ip=ip, partial_cancel_id=pc.id)
        pc.refund_id = refund.id
        state["immediate_settlement_id"] = refund.id
    elif pc.refund_due_rial > 0 and pc.refund_mode == "credit":
        e = issue_partial_credit(sess, user, pc.id, pc.refund_due_rial, pc.doc_date, ip,
                                 hold_lock=False)   # قفل فاکتور در همین مسیر از قبل در دست است
        state["credit_entry_ids"] = [e.id]
    pc.reversal_state = json.dumps(state, ensure_ascii=False)


def confirm_partial_cancel(sess, user, pc_id: int, ip: str = "", hold_lock: bool = True):
    """تأیید پیش‌نویس/در انتظار تأیید — اسناد در همین‌جا صادر می‌شوند."""
    from .models import InvoicePartialCancel
    pc = sess.get(InvoicePartialCancel, pc_id)
    if pc is None:
        raise DomainError("این لغو پیدا نشد.")
    if pc.status not in ("draft", "pending"):
        raise DomainError(f"لغو در وضعیت «{PC_STATUS_FA.get(pc.status, pc.status)}» قابل تأیید نیست.")
    lock = _pc_lock(pc.invoice_id)
    if hold_lock:
        lock.acquire()
    try:
        inv = _io_locked_invoice(sess, pc.invoice_id)
        if inv is None or inv.status != "issued":
            raise DomainError("فاکتور دیگر صادرشده نیست.")
        # بازبینی سقف قابل‌لغو بدون خود این رکورد
        prev = sess.execute(select(func.coalesce(func.sum(InvoicePartialCancel.amount_rial), 0)).where(
            InvoicePartialCancel.invoice_id == inv.id,
            InvoicePartialCancel.status.notin_(("cancelled", "rejected")),
            InvoicePartialCancel.id != pc.id)).scalar() or 0
        if pc.amount_rial > inv.total_rial - prev:
            raise DomainError("از زمان ثبت، سقف قابل‌لغو تغییر کرده است؛ این لغو دیگر در سقف نمی‌گنجد.")
        lines = ([sess.get(InvoiceLine, pc.line_id)] if pc.line_id else list(inv.lines))
        pc.status = "confirmed"
        sess.flush()
        _confirm_partial_cancel(sess, user, inv, pc, lines, ip)
        sess.flush()
        svc.audit(sess, user, "invoice.partial_cancel.confirm", "invoice_partial_cancel",
                  str(pc.id), f"تأیید لغو {inv.number}", ip)
        return pc
    finally:
        if hold_lock:
            lock.release()


def reject_partial_cancel(sess, user, pc_id: int, reason: str, ip: str = ""):
    from .models import InvoicePartialCancel
    pc = sess.get(InvoicePartialCancel, pc_id)
    if pc is None:
        raise DomainError("این لغو پیدا نشد.")
    if pc.status not in ("draft", "pending"):
        raise DomainError("فقط پیش‌نویس/در انتظار تأیید رد می‌شود.")
    pc.status = "rejected"
    pc.cancel_reason = str(reason or "")[:400]
    if "— ردشده" not in pc.reason:
        pc.reason = (pc.reason + " — ردشده")[:400]   # آزادسازی کلید یکتا برای ثبت دوباره
    sess.flush()
    svc.audit(sess, user, "invoice.partial_cancel.reject", "invoice_partial_cancel", str(pc.id), reason, ip)
    return pc


def _credit_account_for(sess, user, party: Party, ip: str = "") -> Account:
    if party.credit_account_id:
        acc = sess.get(Account, party.credit_account_id)
        if acc is not None:
            return acc
    parent = svc.account_by_code(sess, "2103")
    if parent is None:
        raise DomainError("حساب ۲۱۰۳ (پیش‌دریافت از مشتریان) در کدینگ پیدا نشد.")
    seq = len([a for a in svc.list_accounts(sess) if a.parent_code == "2103"]) + 1
    while svc.account_by_code(sess, f"2103-{seq:04d}"):
        seq += 1
    acc = Account(code=f"2103-{seq:04d}", name=f"اعتبار مشتری — {party.name}", kind="ledger",
                  nature="credit", parent_code="2103", level="detail", group_type="permanent",
                  report_type="balance_sheet", report_group="advance_customer", is_system=False)
    sess.add(acc)
    sess.flush()
    party.credit_account_id = acc.id
    sess.flush()
    svc.audit(sess, user, "party.credit_account", "party", str(party.id),
              f"{acc.code} برای {party.name}", ip)
    return acc


def issue_partial_credit(sess, user, pc_id: int, amount: int, doc_date: date | None = None,
                         ip: str = "", hold_lock: bool = True) -> JournalEntry:
    """تبدیل مانده استرداد به اعتبار خرید بعدی — جابه‌جایی وجه رخ نمی‌دهد."""
    from .models import InvoicePartialCancel
    pc = sess.get(InvoicePartialCancel, pc_id)
    if pc is None:
        raise DomainError("این لغو پیدا نشد.")
    if pc.status != "confirmed":
        raise DomainError("اعتبار فقط برای لغوی تأییدشده صادر می‌شود.")
    if amount <= 0:
        raise DomainError("مبلغ اعتبار باید بزرگ‌تر از صفر باشد.")
    remaining = pc.refund_due_rial - pc.refund_paid_rial - pc.credit_issued_rial
    if amount > remaining:
        raise DomainError(f"از مانده قابل اعتبار ({remaining:,} ریال) بیشتر است.")
    inv = sess.get(Invoice, pc.invoice_id)
    lock = _pc_lock(inv.id)
    if hold_lock:
        lock.acquire()
    try:
        party = sess.get(Party, inv.party_id)
        acc = _credit_account_for(sess, user, party, ip)
        entry = svc.create_draft(sess, user, doc_date or pc.doc_date,
            f"اعتبار مشتری بابت لغو {inv.number} (لغو #{pc.id})",
            [{"account_id": party.account_id, "debit": amount, "credit": 0, "description": "تبدیل استرداد به اعتبار"},
             {"account_id": acc.id, "debit": 0, "credit": amount, "description": "اعتبار خرید بعدی مشتری"}],
            source="system", source_ref=f"credit-pc-{pc.id}-{pc.credit_issued_rial + amount}", ip=ip)
        svc.post_entry(sess, user, entry.id, ip)
        pc.credit_entry_id = entry.id
        pc.credit_account_id = acc.id
        pc.credit_issued_rial += amount
        state = json.loads(pc.reversal_state) if pc.reversal_state else {}
        state.setdefault("credit_entry_ids", []).append(entry.id)
        pc.reversal_state = json.dumps(state, ensure_ascii=False)
        sess.flush()
        svc.audit(sess, user, "invoice.partial_cancel.credit", "invoice_partial_cancel", str(pc.id),
                  f"اعتبار {amount:,}", ip)
        return entry
    finally:
        if hold_lock:
            lock.release()


def pay_refund(sess, user, pc_id: int, account_id: int, amount: int, doc_date: date | None = None,
               ip: str = "", method: str = "cash", hold_lock: bool = True) -> Settlement:
    """پرداخت واقعی استرداد به مشتری — فقط همین‌جا بانک/صندوق تغییر می‌کند. پرداخت جزئی هم مجاز است."""
    from .models import InvoicePartialCancel
    pc = sess.get(InvoicePartialCancel, pc_id)
    if pc is None:
        raise DomainError("این لغو پیدا نشد.")
    if pc.status != "confirmed":
        raise DomainError("پرداخت فقط برای لغوی تأییدشده انجام می‌شود.")
    inv = sess.get(Invoice, pc.invoice_id)
    lock = _pc_lock(inv.id)
    if hold_lock:
        lock.acquire()
    try:
        party = sess.get(Party, inv.party_id)
        if party.kind == "customer":
            party.kind = "both"
        return create_settlement(sess, user, "out", inv.party_id, account_id, amount,
                                 doc_date or jd.jtoday(), description=f"پرداخت استرداد لغو {inv.number} (#{pc.id})",
                                 issue=True, ip=ip, method=method, partial_cancel_id=pc.id)
    finally:
        if hold_lock:
            lock.release()


def receive_supplier_refund(sess, user, pc_id: int, account_id: int, amount: int,
                            doc_date: date | None = None, ip: str = "",
                            hold_lock: bool = True) -> Settlement:
    """وصول واقعی طلب از تأمین‌کننده — تخصیص مستقیم به همان لغو؛ تکرار بیش از طلب رد می‌شود."""
    from .models import InvoicePartialCancel
    pc = sess.get(InvoicePartialCancel, pc_id)
    if pc is None:
        raise DomainError("این لغو پیدا نشد.")
    if pc.status != "confirmed":
        raise DomainError("وصول فقط برای لغوی تأییدشده انجام می‌شود.")
    if pc.supplier_party_id is None:
        raise DomainError("این لغو تأمین‌کننده ندارد.")
    sup = sess.get(Party, pc.supplier_party_id)
    if sup.kind == "supplier":
        sup.kind = "both"
    inv = sess.get(Invoice, pc.invoice_id)
    lock = _pc_lock(inv.id)
    if hold_lock:
        lock.acquire()
    try:
        return create_settlement(sess, user, "in", sup.id, account_id, amount,
                                 doc_date or jd.jtoday(),
                                 description=f"وصول برگشت/جریمه لغو {inv.number} (#{pc.id})",
                                 issue=True, ip=ip, partial_cancel_id=pc.id)
    finally:
        if hold_lock:
            lock.release()


def cancel_partial_cancel(sess, user, pc_id: int, reason: str, ip: str = "", hold_lock: bool = True):
    """ابطال رسمی لغوی تأییدشده: ابطال اسناد، بازگردانی تخصیص‌ها و مانده‌ها — اتمیک."""
    from .models import InvoicePartialCancel
    pc = sess.get(InvoicePartialCancel, pc_id)
    if pc is None:
        raise DomainError("این لغو پیدا نشد.")
    if pc.status != "confirmed":
        raise DomainError("فقط لغوی تأییدشده قابل ابطال است.")
    reason = str(reason or "").strip()
    if len(reason) < 5:
        raise DomainError("دلیل ابطال لغو را بنویسید (حداقل چند کلمه).")
    lock = _pc_lock(pc.invoice_id)
    if hold_lock:
        lock.acquire()
    try:
        inv = _io_locked_invoice(sess, pc.invoice_id)
        # ۱) تسویه‌های مرتبط باید اول دستی ابطال شوند (ردیابی وجه حفظ می‌ماند)
        allocs = sess.execute(select(SettlementAllocation).where(
            SettlementAllocation.target_type == "partial_cancel",
            SettlementAllocation.target_id == pc.id)).scalars().all()
        live = []
        for a in allocs:
            s = sess.get(Settlement, a.settlement_id)
            if s and s.status == "issued":
                live.append(s)
        if live:
            nums = "، ".join(s.number or str(s.id) for s in live)
            raise DomainError(f"برای این لغو تسویه ثبت شده است؛ ابتدا آن‌ها را ابطال کنید: {nums}")
        for a in allocs:
            sess.delete(a)
        # ۲) اعتبار مصرف‌نشده باید برگردد
        state = json.loads(pc.reversal_state) if pc.reversal_state else {}
        if pc.credit_issued_rial > 0:
            spent = 0
            if pc.credit_account_id:
                spent = int(sess.execute(select(func.coalesce(func.sum(Settlement.amount_rial), 0)).where(
                    Settlement.account_id == pc.credit_account_id,
                    Settlement.status == "issued", Settlement.direction == "in")).scalar() or 0)
            if spent:
                raise DomainError(f"از اعتبار این لغو {spent:,} ریال مصرف شده؛ ابتدا تسویه‌های اعتباری را برگردانید.")
            for eid in state.get("credit_entry_ids", []) or ([pc.credit_entry_id] if pc.credit_entry_id else []):
                e = sess.get(JournalEntry, eid)
                if e and not e.reversed_by_id:
                    svc.reverse_entry(sess, user, eid, f"ابطال لغو #{pc.id} — اعتبار مشتری", ip)
        # ۳) ابطال اسناد لغو
        for eid in (pc.entry_id, pc.penalty_entry_id, pc.agency_fee_entry_id,
                    pc.supplier_refund_entry_id, pc.supplier_penalty_entry_id,
                    pc.burn_entry_id, pc.counter_penalty_entry_id):
            if eid:
                e = sess.get(JournalEntry, eid)
                if e and not e.reversed_by_id:
                    svc.reverse_entry(sess, user, eid, f"ابطال لغو جزئی — {reason}", ip)
        # ۴) بازگردانی تخصیص‌های آزادشده و مانده فاکتور
        if state.get("freed"):
            for f in state["freed"]:
                s = sess.get(Settlement, f["settlement_id"])
                if s is None or s.status == "cancelled":
                    raise DomainError("یکی از دریافت‌های مرتبط ابطال شده است؛ بازگردانی خودکار مانده "
                                      "فاکتور ممکن نیست و نیازمند اصلاح دستی حسابدار است.")
                cur = sum(a.amount_rial for a in sess.execute(select(SettlementAllocation).where(
                    SettlementAllocation.settlement_id == s.id)).scalars().all())
                if s.amount_rial - cur < f["amount"]:
                    raise DomainError("ظرفیت تخصیص دریافت مرتبط دوباره مصرف شده است؛ ابتدا تخصیص "
                                      "سپرده‌ها را اصلاح کنید.")
                sess.add(SettlementAllocation(settlement_id=s.id, target_type="invoice",
                                              target_id=inv.id, amount_rial=f["amount"]))
            sess.flush()
            inv.paid_rial = sum(a.amount_rial for a in sess.execute(select(SettlementAllocation).where(
                SettlementAllocation.target_type == "invoice",
                SettlementAllocation.target_id == inv.id)).scalars().all())
        else:
            inv.paid_rial = min(inv.total_rial, inv.paid_rial + pc.amount_rial)
        pc.status = "cancelled"
        pc.cancel_reason = reason
        if "— ابطال‌شده" not in pc.reason:
            pc.reason = (pc.reason + " — ابطال‌شده")[:400]
        sess.flush()
        svc.audit(sess, user, "invoice.partial_cancel.cancel", "invoice_partial_cancel",
                  str(pc.id), reason, ip)
        return pc
    finally:
        if hold_lock:
            lock.release()


def partial_cancel_view(sess, pc) -> dict:
    """نمای محاسباتی یک لغو: وضعیت‌های استرداد/تأمین از روی اعداد همگام."""
    inv = sess.get(Invoice, pc.invoice_id)
    due, paid, issued = pc.refund_due_rial, pc.refund_paid_rial, pc.credit_issued_rial
    covered = paid + issued
    credit_spent = 0
    if pc.credit_account_id:
        credit_spent = int(sess.execute(select(func.coalesce(func.sum(Settlement.amount_rial), 0)).where(
            Settlement.account_id == pc.credit_account_id,
            Settlement.status == "issued", Settlement.direction == "in")).scalar() or 0)
    if pc.status != "confirmed":
        refund_status = "—"
    elif covered <= 0:
        refund_status = "unpaid"
    elif covered < due:
        refund_status = "partial"
    else:
        refund_status = "credited" if issued > 0 else "paid"
    claim, got = pc.supplier_claim_rial, pc.supplier_received_rial
    if claim <= 0:
        supplier_status = "none"
    elif got <= 0:
        supplier_status = "pending"
    elif got < claim:
        supplier_status = "partial"
    else:
        supplier_status = "settled"
    return {"invoice_number": inv.number if inv else "", "refund_due": due,
            "refund_paid": paid, "refund_remaining": max(0, due - paid - issued),
            "credit_issued": issued, "credit_spent": credit_spent,
            "credit_remaining": max(0, issued - credit_spent),
            "refund_status": refund_status, "supplier_claim": claim,
            "supplier_received": got, "supplier_remaining": max(0, claim - got),
            "supplier_status": supplier_status, "profit_impact": pc.profit_impact_rial,
            "nonrefundable_cost": pc.nonrefundable_cost_rial}


def partial_cancel_invoice(sess, user, invoice_id: int, cancel_amount: int, reason: str,
                           doc_date: date | None = None, penalty_customer: int = 0,
                           supplier_refund: int = 0, penalty_supplier: int = 0,
                           supplier_party_id: int | None = None, ip: str = "",
                           supplier_refund_vat: int = 0, **kw):
    """پوشش سازگاری با امضای قدیمی — منطق در create_partial_cancel است."""
    return create_partial_cancel(sess, user, invoice_id, cancel_amount, reason, doc_date,
                                 penalty_customer=penalty_customer,
                                 agency_fee=kw.get("agency_fee", 0),
                                 supplier_refund=supplier_refund, penalty_supplier=penalty_supplier,
                                 supplier_party_id=supplier_party_id,
                                 supplier_refund_vat=supplier_refund_vat,
                                 line_id=kw.get("line_id"),
                                 refund_mode=kw.get("refund_mode", "deferred"),
                                 submit=kw.get("submit", True),
                                 allow_over_cost=kw.get("allow_over_cost", False),
                                 allow_moadian=kw.get("allow_moadian", False), ip=ip)


def list_invoices(sess, party_id: int | None = None, trip_id: int | None = None,
                  status: str | None = None, text: str | None = None,
                  limit: int = 300, only_creator: int | None = None) -> list[dict]:
    q = select(Invoice).order_by(Invoice.doc_date.desc(), Invoice.id.desc())
    if only_creator is not None:
        # کارمند فروش فقط فاکتورهای خودش را می‌بیند (همان قاعده‌ی پیش‌نویس‌های سند روزنامه)
        q = q.where(Invoice.created_by == only_creator)
    if party_id:
        q = q.where(Invoice.party_id == party_id)
    if trip_id:
        q = q.where(Invoice.trip_id == trip_id)
    if status and status != "all":
        q = q.where(Invoice.status == status)
    if text:
        q = q.where(Invoice.description.ilike(f"%{text.strip()}%") | Invoice.number.ilike(f"%{text.strip()}%"))
    parties = {p.id: p.name for p in sess.execute(select(Party)).scalars()}
    trips = {t.id: t.title for t in sess.execute(select(Trip)).scalars()}
    out = []
    for i in sess.execute(q.limit(limit)).scalars():
        out.append({"id": i.id, "number": i.number, "date": jd.jalali_str(i.doc_date),
                    "date_iso": i.doc_date.isoformat(),
                    "due": jd.jalali_str(i.due_date) if i.due_date else "—",
                    "party_id": i.party_id, "party": parties.get(i.party_id, "?"),
                    "trip": trips.get(i.trip_id, "—") if i.trip_id else "—", "trip_id": i.trip_id,
                    "description": i.description, "status": i.status,
                    "subtotal": i.subtotal_rial, "discount": i.discount_rial,
                    "vat": i.vat_rial, "total": i.total_rial, "paid": i.paid_rial,
                    "open": i.total_rial - i.paid_rial,
                    "service_types": sorted({l.service_type for l in i.lines}),
                    "lines": [{"service_type": l.service_type,
                               "service_label": SERVICE_TYPES[l.service_type]["label"],
                               "description": l.description, "qty": l.qty,
                               "unit_price": l.unit_price_rial, "discount": l.discount_rial,
                               "net": l.net_rial, "vat": l.vat_rial, "taxable": l.taxable,
                               "revenue_model": l.revenue_model,
                               "purchase_cost": l.purchase_cost_rial,
                               "supplier_party_id": l.supplier_party_id,
                               "rate_percent": l.rate_bp / 100} for l in i.lines]})
    return out


# ------------------------------------------------------------------ صورتحساب تأمین‌کننده
def create_bill(sess, user, party_id: int, doc_date: date, net: int, vat: int | None = None,
                expense_account_id: int | None = None, trip_id: int | None = None,
                description: str = "", due_date: date | None = None, issue: bool = False,
                ip: str = "", bill_type: str = "expense",
                source_invoice_id: int | None = None) -> SupplierBill:
    party = _party(sess, party_id, "supplier")
    if net <= 0:
        raise DomainError("مبلغ صورتحساب باید بزرگ‌تر از صفر باشد.")
    if bill_type not in ("expense", "consignment"):
        raise DomainError("نوع صورتحساب تأمین‌کننده نامعتبر است.")
    if trip_id and sess.get(Trip, trip_id) is None:
        raise DomainError("پرونده‌ی سفر انتخاب‌شده پیدا نشد.")
    if source_invoice_id and sess.get(Invoice, source_invoice_id) is None:
        raise DomainError("فاکتور مبدأ صورتحساب پیدا نشد.")
    if expense_account_id is None:
        code = SYSTEM_CODES["airline_clearing"] if bill_type == "consignment" else SYSTEM_CODES["cogs"]
        acc = svc.account_by_code(sess, code)
        expense_account_id = acc.id
    acc = sess.get(Account, expense_account_id)
    if acc is None or acc.kind != "ledger":
        raise DomainError("حساب هزینه/تسویه انتخاب‌شده درست نیست.")
    if bill_type == "consignment":
        clearing = svc.account_by_code(sess, SYSTEM_CODES["airline_clearing"])
        if acc.id != clearing.id:
            raise DomainError("بدهی امانی باید به حساب تسویه‌نشده‌ی ایرلاین ثبت شود.")
        if vat not in (None, 0):
            raise DomainError("برای بدهی امانی، مالیات خرید را جداگانه و پس از بررسی حسابدار ثبت کنید.")
        vat = 0
    elif vat is None:
        # مالیات را خودمان حساب می‌کنیم؛ اگر ۰ داده شود یعنی «معاف» و دست نمی‌زنیم
        rate_bp, _ = vat_rate_for(sess, doc_date)
        vat = (net * rate_bp) // 10000 if rate_bp else 0
    jy = jd.jalali_parts(doc_date)[0]
    bill = SupplierBill(doc_date=doc_date, due_date=due_date or doc_date, party_id=party.id,
                        trip_id=trip_id, created_by=user.id, expense_account_id=acc.id,
                        bill_type=bill_type, source_invoice_id=source_invoice_id,
                        description=str(description)[:300], net_rial=net, vat_rial=vat,
                        total_rial=net + vat, status="draft")
    sess.add(bill)
    sess.flush()
    bill.number = _next_number(sess, SupplierBill, jy, "خ")
    sess.flush()
    svc.audit(sess, user, "bill.draft", "bill", str(bill.id),
              f"{party.name} — {bill.total_rial:,} ریال", ip)
    if issue:
        issue_bill(sess, user, bill.id, ip)
    return bill


def _bill_entry_lines(sess, bill: SupplierBill) -> list[dict]:
    party = sess.get(Party, bill.party_id)
    debit_label = ("بدهی امانی / تسویه‌ی سهم ایرلاین" if bill.bill_type == "consignment"
                   else f"هزینه {bill.description or ''}".strip()[:300])
    lines = [{"account_id": bill.expense_account_id, "debit": bill.net_rial, "credit": 0,
              "description": debit_label}]
    if bill.vat_rial:
        vat = svc.account_by_code(sess, SYSTEM_CODES["vat_credit"])
        lines.append({"account_id": vat.id, "debit": bill.vat_rial, "credit": 0,
                      "description": "مالیات بر ارزش افزوده خرید — اعتبار مالیاتی"})
    lines.append({"account_id": party.account_id, "debit": 0, "credit": bill.total_rial,
                  "description": f"{'تسویه‌ی امانی' if bill.bill_type == 'consignment' else 'صورتحساب'} "
                                 f"{bill.number} — {party.name}"})
    return lines


def issue_bill(sess, user, bill_id: int, ip: str = "") -> SupplierBill:
    bill = sess.get(SupplierBill, bill_id)
    if bill is None:
        raise DomainError("این صورتحساب پیدا نشد.")
    if bill.status == "issued":
        return bill
    if bill.status == "cancelled":
        raise DomainError("صورتحساب ابطال‌شده قابل صدور نیست.")
    party = sess.get(Party, bill.party_id)
    entry = svc.create_draft(sess, user, bill.doc_date,
                             f"صورتحساب تأمین‌کننده {bill.number} — {party.name}",
                             _bill_entry_lines(sess, bill), source="system",
                             source_ref=bill.number, ip=ip)
    svc.post_entry(sess, user, entry.id, ip)
    bill.entry_id = entry.id
    bill.status = "issued"
    sess.flush()
    svc.audit(sess, user, "bill.issue", "bill", str(bill.id), bill.number, ip)
    return bill


def cancel_bill(sess, user, bill_id: int, reason: str, ip: str = "") -> SupplierBill:
    bill = sess.get(SupplierBill, bill_id)
    if bill is None:
        raise DomainError("این صورتحساب پیدا نشد.")
    if bill.paid_rial > 0:
        raise DomainError("برای این صورتحساب پول پرداخت شده است؛ اول تسویه‌ها را برگردانید.")
    if bill.status == "issued" and bill.entry_id:
        svc.reverse_entry(sess, user, bill.entry_id, reason or "ابطال صورتحساب", ip)
    bill.status = "cancelled"
    svc.audit(sess, user, "bill.cancel", "bill", str(bill.id), reason, ip)
    return bill


def list_bills(sess, party_id: int | None = None, trip_id: int | None = None,
               status: str | None = None, limit: int = 300,
               only_creator: int | None = None) -> list[dict]:
    q = select(SupplierBill).order_by(SupplierBill.doc_date.desc(), SupplierBill.id.desc())
    if only_creator is not None:
        q = q.where(SupplierBill.created_by == only_creator)
    if party_id:
        q = q.where(SupplierBill.party_id == party_id)
    if trip_id:
        q = q.where(SupplierBill.trip_id == trip_id)
    if status and status != "all":
        q = q.where(SupplierBill.status == status)
    parties = {p.id: p.name for p in sess.execute(select(Party)).scalars()}
    trips = {t.id: t.title for t in sess.execute(select(Trip)).scalars()}
    accounts = svc.account_map(sess)
    return [{"id": b.id, "number": b.number, "date": jd.jalali_str(b.doc_date),
             "date_iso": b.doc_date.isoformat(),
             "due": jd.jalali_str(b.due_date) if b.due_date else "—",
             "party_id": b.party_id, "party": parties.get(b.party_id, "?"),
             "trip": trips.get(b.trip_id, "—") if b.trip_id else "—", "trip_id": b.trip_id,
             "description": b.description, "status": b.status,
             "bill_type": b.bill_type, "source_invoice_id": b.source_invoice_id,
             "net": b.net_rial, "vat": b.vat_rial, "total": b.total_rial, "paid": b.paid_rial,
             "open": b.total_rial - b.paid_rial,
             "expense_account": accounts[b.expense_account_id].name if b.expense_account_id in accounts else ""}
            for b in sess.execute(q.limit(limit)).scalars()]


# ------------------------------------------------------------------ دریافت و پرداخت
def _allocate(sess, settlement: Settlement) -> None:
    """پول را به هدف صریح یا در گردش عادی به قدیمی‌ترین سند باز نسبت می‌دهد."""
    # پیوند به لغو جزئی: تخصیص صریح به استرداد مشتری (out) یا وصول طلب تأمین (in) —
    # اعتبارسنجی مبالغ در create_settlement انجام شده؛ این‌جا فقط ثبت و همگام‌سازی
    if settlement.partial_cancel_id:
        from .models import InvoicePartialCancel
        pc = sess.get(InvoicePartialCancel, settlement.partial_cancel_id)
        if settlement.direction == "out":
            pc.refund_paid_rial += settlement.amount_rial
        else:
            pc.supplier_received_rial += settlement.amount_rial
        settlement.allocations.append(SettlementAllocation(
            target_type="partial_cancel", target_id=pc.id, amount_rial=settlement.amount_rial))
        return
    remaining = settlement.amount_rial
    if settlement.direction == "in":
        if settlement.target_invoice_id:
            target = sess.get(Invoice, settlement.target_invoice_id)
            if target is None or target.status != "issued" or target.party_id != settlement.party_id:
                raise DomainError("فاکتور هدف دریافت صادر نشده یا به این مشتری تعلق ندارد.")
            rows = [target]
        else:
            rows = sess.execute(select(Invoice).where(Invoice.party_id == settlement.party_id,
                                                      Invoice.status == "issued",
                                                      Invoice.total_rial > Invoice.paid_rial)
                                .order_by(Invoice.due_date, Invoice.id)).scalars()
        target_type = "invoice"
    else:
        if settlement.target_invoice_id:
            raise DomainError("دریافتِ هدفمند به فاکتور فقط برای جهت «دریافت» مجاز است.")
        rows = sess.execute(select(SupplierBill).where(SupplierBill.party_id == settlement.party_id,
                                                       SupplierBill.status == "issued",
                                                       SupplierBill.total_rial > SupplierBill.paid_rial)
                            .order_by(SupplierBill.due_date, SupplierBill.id)).scalars()
        target_type = "bill"
    for doc in rows:
        if remaining <= 0:
            break
        need = doc.total_rial - doc.paid_rial
        amount = min(need, remaining)
        doc.paid_rial += amount
        remaining -= amount
        settlement.allocations.append(SettlementAllocation(
            target_type=target_type, target_id=doc.id, amount_rial=amount))
    if settlement.target_invoice_id and remaining:
        raise DomainError("مبلغ دریافت از مانده‌ی فاکتور انتخاب‌شده بیشتر است؛ مازاد خودکار به فاکتور دیگری نمی‌رود.")


def create_settlement(sess, user, direction: str, party_id: int, account_id: int, amount: int,
                      doc_date: date, trip_id: int | None = None, description: str = "",
                      issue: bool = False, ip: str = "", method: str = "cash",
                      cheque_number: str = "", cheque_bank: str = "", cheque_branch: str = "",
                      cheque_due_date: date | None = None,
                      ref_no: str = "", batch_no: str = "",
                      cheque_account_number: str = "", cheque_sheba: str = "", cheque_sayad_id: str = "",
                      target_invoice_id: int | None = None,
                      partial_cancel_id: int | None = None) -> Settlement:
    if direction not in ("in", "out"):
        raise DomainError("نوع عملیات باید دریافت یا پرداخت باشد.")
    party = _party(sess, party_id, "customer" if direction == "in" else "supplier")
    acc = sess.get(Account, account_id) if account_id else None
    if acc is None and method != "cheque":
        # برای چک حساب صندوق/بانک رای ندارد — حساب چک ۱۱۰۴/۲۱۰۲ پایین‌تر گذاشته می‌شود
        raise DomainError("حساب صندوق یا بانک انتخاب‌شده درست نیست.")
    if amount <= 0:
        raise DomainError("مبلغ باید بزرگ‌تر از صفر باشد.")
    if method == "discount":
        # تخفیف تسویه پول جابه‌جا نمی‌کند؛ فقط بدهی طرف را با سند خاص کم می‌کند:
        # در دریافت → تخفیف داده‌ایم (۴۱۰۴، کسر از درآمد) / در پرداخت → تخفیف گرفته‌ایم (۴۱۰۹، درآمد)
        if acc.id != party.account_id:
            raise DomainError("ردیف تخفیف باید به حساب تفصیلی خود طرف‌حساب برسد.")
        disc_code = "4104" if direction == "in" else "4109"
        if svc.account_by_code(sess, disc_code) is None:
            raise DomainError(f"حساب {disc_code} (تخفیف تسویه) در کدینگ پیدا نشد.")
    if partial_cancel_id is not None:
        from .models import InvoicePartialCancel
        pc = sess.get(InvoicePartialCancel, int(partial_cancel_id))
        if pc is None or pc.status != "confirmed":
            raise DomainError("لغو جزئی مرتبط پیدا نشد یا هنوز تأیید نشده است.")
        inv_pc = sess.get(Invoice, pc.invoice_id)
        if direction == "out":
            if inv_pc is None or party.id != inv_pc.party_id:
                raise DomainError("استرداد فقط به مشتری همان فاکتور پرداخت می‌شود.")
            if amount > pc.refund_due_rial - pc.refund_paid_rial:
                raise DomainError(
                    f"پرداخت از مانده استرداد ({pc.refund_due_rial - pc.refund_paid_rial:,} ریال) بیشتر است.")
        else:
            if pc.supplier_party_id is None or party.id != pc.supplier_party_id:
                raise DomainError("وصول فقط از تأمین‌کننده‌ی همان لغو مجاز است.")
            if amount > pc.supplier_claim_rial - pc.supplier_received_rial:
                raise DomainError(
                    f"دریافت از مانده طلب تأمین ({pc.supplier_claim_rial - pc.supplier_received_rial:,} ریال) بیشتر است.")
    if target_invoice_id is not None:
        if direction != "in":
            raise DomainError("فقط دریافت مشتری را می‌توان به فاکتور فروش تخصیص داد.")
        target = sess.get(Invoice, int(target_invoice_id))
        if target is None or target.party_id != party.id or target.status == "cancelled":
            raise DomainError("فاکتور هدف دریافت برای این مشتری معتبر نیست.")
        if target.status == "issued" and amount > target.total_rial - target.paid_rial:
            raise DomainError("دریافت از مانده‌ی فاکتور انتخاب‌شده بیشتر است.")
    if method == "cheque":
        if not cheque_number or not cheque_due_date:
            raise DomainError("برای روش چک، شماره چک و سررسید را وارد کنید.")
        # حساب چک را — فارغ از انتخاب کاربر — به حساب درست (۱۱۰۴/۲۱۰۲) می‌گذاریم
        cheque_acc_code = "1104" if direction == "in" else "2102"
        acc = sess.query(Account).filter(Account.code == cheque_acc_code).first()
        if acc is None:
            raise DomainError(f"حساب چک‌های {'دریافتی' if direction == 'in' else 'پرداختی'} ({cheque_acc_code}) در کدینگ پیدا نشد.")
    if method != "discount":
        # برای چک، حساب چک 1104/2102 هم مجاز است؛ حساب اعتبار مشتری (2103) فقط برای مصرف اعتبار
        allowed = ("cash", "bank", "cheque_received", "cheque_paid", "advance_customer")
        if acc.report_group not in allowed:
            raise DomainError("حساب انتخاب‌شده باید صندوق یا بانک باشد، یا حساب چک معتبر باشد.")
        if acc.report_group == "advance_customer":
            if direction == "out":
                raise DomainError("اعتبار مشتری به پرداخت نقدی تبدیل نمی‌شود؛ فقط برای تسویه‌ی خرید بعدی مصرف می‌شود.")
            if party.credit_account_id != acc.id:
                raise DomainError("تسویه با اعتبار فقط از حساب اعتبار خودِ همین مشتری مجاز است.")
            s = svc.account_balances(sess).get(acc.id, {"debit": 0, "credit": 0})
            available = s["credit"] - s["debit"]
            if amount > available:
                raise DomainError(f"از مانده اعتبار مشتری ({available:,} ریال) بیشتر است؛ "
                                  "مازاد باید با روش دیگری دریافت شود.")
    jy = jd.jalali_parts(doc_date)[0]
    s = Settlement(direction=direction, doc_date=doc_date, party_id=party.id, trip_id=trip_id,
                   target_invoice_id=target_invoice_id, partial_cancel_id=partial_cancel_id,
                   account_id=acc.id, amount_rial=amount, created_by=user.id,
                   description=str(description)[:300], status="draft", method=method,
                   ref_no=str(ref_no or "")[:60], batch_no=str(batch_no or "")[:40],
                   cheque_number=cheque_number or "", cheque_bank=cheque_bank or "",
                   cheque_branch=cheque_branch or "", cheque_due_date=cheque_due_date)
    sess.add(s)
    sess.flush()
    s.number = _next_number(sess, Settlement, jy, "د" if direction == "in" else "پ")
    # اگر چک بود، رکورد چک هم بساز
    if method == "cheque" and cheque_number:
        from .models import Cheque
        ch = Cheque(number=cheque_number, bank_name=cheque_bank or "", branch=cheque_branch or "",
                    account_number=cheque_account_number or "", sheba=cheque_sheba or "",
                    sayad_id=cheque_sayad_id or "",
                    amount_rial=amount, issue_date=doc_date, due_date=cheque_due_date or doc_date,
                    direction=direction, status="in_hand" if direction == "in" else "issued",
                    party_id=party.id, account_id=acc.id if direction == "in" else None,
                    description=description, created_by=user.id)
        sess.add(ch)
        sess.flush()
        s.cheque_id = ch.id
    if issue:
        issue_settlement(sess, user, s.id, ip)
    sess.flush()
    svc.audit(sess, user, "settlement.draft", "settlement", str(s.id),
              f"{'دریافت' if direction == 'in' else 'پرداخت'} {party.name} {amount:,} [{method}]", ip)
    return s


def issue_settlement(sess, user, settlement_id: int, ip: str = "") -> Settlement:
    s = sess.get(Settlement, settlement_id)
    if s is None:
        raise DomainError("این دریافت/پرداخت پیدا نشد.")
    if s.status == "issued":
        return s
    if s.status == "cancelled":
        raise DomainError("این عملیات ابطال شده است.")
    party = sess.get(Party, s.party_id)
    acc = sess.get(Account, s.account_id)
    if s.method == "discount":
        # ردیف تخفیف: پول جابه‌جا نمی‌شود؛ فقط بدهی طرف با سند تخفیف صفر می‌شود
        disc_code = "4104" if s.direction == "in" else "4109"
        dacc = svc.account_by_code(sess, disc_code)
        if dacc is None:
            raise DomainError(f"حساب {disc_code} در کدینگ پیدا نشد؛ تخفیف تسویه قابل صدور نیست.")
        label = "تخفیف داده‌شده به" if s.direction == "in" else "تخفیف گرفته‌شده از"
        if s.direction == "in":
            lines = [{"account_id": dacc.id, "debit": s.amount_rial, "credit": 0,
                      "description": f"تخفیف نقدی تسویه — {party.name}"},
                     {"account_id": party.account_id, "debit": 0, "credit": s.amount_rial,
                      "description": f"تسویه تخفیفی {party.name}"}]
        else:
            lines = [{"account_id": party.account_id, "debit": s.amount_rial, "credit": 0,
                      "description": f"تسویه تخفیفی {party.name}"},
                     {"account_id": dacc.id, "debit": 0, "credit": s.amount_rial,
                      "description": f"تخفیف نقدی خرید — {party.name}"}]
        title = f"تخفیف تسویه {s.number} — {label} {party.name}"
    elif s.direction == "in":
        lines = [{"account_id": acc.id, "debit": s.amount_rial, "credit": 0,
                  "description": f"دریافت از {party.name}"},
                 {"account_id": party.account_id, "debit": 0, "credit": s.amount_rial,
                  "description": f"تسویه حساب {party.name}"}]
        title = f"دریافت {s.number} از {party.name}"
    else:
        lines = [{"account_id": party.account_id, "debit": s.amount_rial, "credit": 0,
                  "description": f"پرداخت به {party.name}"},
                 {"account_id": acc.id, "debit": 0, "credit": s.amount_rial,
                  "description": f"پرداخت از {acc.name}"}]
        title = f"پرداخت {s.number} به {party.name}"
    entry = svc.create_draft(sess, user, s.doc_date, title, lines, source="system",
                             source_ref=s.number, ip=ip)
    svc.post_entry(sess, user, entry.id, ip)
    s.entry_id = entry.id
    s.status = "issued"
    _allocate(sess, s)
    sess.flush()
    svc.audit(sess, user, "settlement.issue", "settlement", str(s.id), s.number, ip)
    return s


def settlement_available_amount(sess, settlement: Settlement) -> int:
    allocated = sum(a.amount_rial for a in settlement.allocations)
    return max(0, settlement.amount_rial - allocated)


def create_settlement_batch(sess, user, direction: str, party_id: int, doc_date: date,
                            trip_id: int | None, description: str,
                            items: list[dict], discount: int = 0,
                            target_invoice_id: int | None = None,
                            partial_cancel_id: int | None = None,
                            issue: bool = True, ip: str = "") -> dict:
    """یک دریافت/پرداخت چندابزاری (نقد+کارت+حواله+چک+اعتبار) + تخفیف تسویه — اتمیک.

    هر ابزار یک رکورد تسویه‌ی مستقل است تا ابطال جزئی ممکن بماند، ولی همه با برچسب
    `batch_no` مشترک به‌عنوان «یک سند» دیده می‌شوند. «تخفیف» هم یک ردیف تسویه با
    method=discount است که فقط بدهی طرف را سندی کم می‌کند (در را بدون تغییر خزانه).
    """
    if direction not in ("in", "out"):
        raise DomainError("نوع عملیات باید دریافت یا پرداخت باشد.")
    items = [dict(it) for it in items]
    amounts = [_money(it.get("amount")) for it in items]
    discount = _money(discount)
    if discount < 0:
        raise DomainError("تخفیف نمی‌تواند منفی باشد.")
    if not items and discount == 0:
        raise DomainError("دست‌کم یک ردیف پرداخت یا تخفیف لازم است.")
    if any(it.get("method") == "discount" for it in items):
        raise DomainError("تخفیف را در همان فیلد مخصوصش وارد کنید، نه داخل ردیف‌های پرداخت.")
    party = _party(sess, party_id, "customer" if direction == "in" else "supplier")

    # اعتبار مشتری: جمع ردیف‌های اعتباری از مانده‌ی اعتبار بیشتر نباشد (قبل از نوشتن هر سند)
    credit_total = sum(a for a, it in zip(amounts, items)
                       if str(it.get("method") or "").strip().lower() == "credit")
    if credit_total:
        if not party.credit_account_id:
            raise DomainError("این مشتری اعتبار خرید بعدی ندارد.")
        s = svc.account_balances(sess).get(party.credit_account_id, {"debit": 0, "credit": 0})
        available = s["credit"] - s["debit"]
        if credit_total > available:
            raise DomainError(f"جمع ردیف‌های اعتباری از مانده اعتبار مشتری ({available:,} ریال) بیشتر است.")

    # اگر کل سند به یک فاکتور تخصیص می‌یابد، جمع پرداخت‌ها + تخفیف نباید از ماندهٔ آن بیشتر باشد
    if target_invoice_id is not None:
        target = sess.get(Invoice, int(target_invoice_id))
        if target is None or target.party_id != party.id or target.status == "cancelled":
            raise DomainError("فاکتور هدف این دریافت برای این مشتری معتبر نیست.")
        if target.status == "issued" and (sum(amounts) + discount) > target.total_rial - target.paid_rial:
            raise DomainError(
                f"جمع ردیف‌ها و تخفیف از مانده‌ی فاکتور ({target.total_rial - target.paid_rial:,} ریال) بیشتر است؛ "
                "بقیه را بدون تخصیص ثبت کنید تا پیش‌دریافت شود.")

    rows = []
    for it, amount in zip(items, amounts):
        method = str(it.get("method") or "").strip().lower() or "cash"
        account_id = it.get("account_id")
        if method == "credit":
            account_id = party.credit_account_id
        cheque_due = it.get("cheque_due_date")
        if isinstance(cheque_due, str) and cheque_due.strip():
            cheque_due = jd.parse_date_any(cheque_due)
        else:
            cheque_due = None
        rows.append(create_settlement(
            sess, user, direction, party_id, int(account_id) if account_id else 0, amount,
            doc_date, trip_id=trip_id, description=str(it.get("description") or description),
            issue=issue, ip=ip, method=method,
            cheque_number=str(it.get("cheque_number") or "").strip(),
            cheque_bank=str(it.get("cheque_bank") or "").strip(),
            cheque_branch=str(it.get("cheque_branch") or "").strip(),
            cheque_due_date=cheque_due,
            cheque_account_number=str(it.get("cheque_account_number") or "").strip(),
            cheque_sheba=str(it.get("cheque_sheba") or "").strip(),
            cheque_sayad_id=str(it.get("cheque_sayad_id") or "").strip(),
            ref_no=str(it.get("ref_no") or "").strip(),
            target_invoice_id=target_invoice_id,
            partial_cancel_id=partial_cancel_id))
    if discount > 0:
        rows.append(create_settlement(
            sess, user, direction, party_id, party.account_id, discount,
            doc_date, trip_id=trip_id,
            description=f"تخفیف تسویه — {description}" if description else "تخفیف تسویه",
            issue=issue, ip=ip, method="discount",
            target_invoice_id=target_invoice_id,
            partial_cancel_id=partial_cancel_id))
    batch_no = rows[0].number or str(rows[0].id)
    for row in rows:
        row.batch_no = batch_no
    sess.flush()
    svc.audit(sess, user, "settlement.batch", "party", str(party.id),
              f"سند چندابزاری {batch_no}: {len(rows)} ردیف، جمع {sum(amounts):,} + تخفیف {discount:,}", ip)
    return {"batch_no": batch_no, "rows": rows, "total": sum(amounts), "discount": discount}


def allocate_deposit(sess, user, settlement_id: int, invoice_id: int, amount: int, ip: str = "") -> Settlement:
    s = sess.get(Settlement, settlement_id)
    if s is None:
        raise DomainError("این دریافت پیدا نشد.")
    if s.direction != "in":
        raise DomainError("فقط دریافت از مشتری را می‌توان به فاکتور تخصیص داد.")
    if s.status != "issued":
        raise DomainError("فقط دریافت صادرشده را می‌توان تخصیص داد.")
    if amount is None or amount <= 0:
        raise DomainError("مبلغ تخصیص باید بزرگ‌تر از صفر باشد.")
    inv = sess.get(Invoice, invoice_id)
    if inv is None:
        raise DomainError("فاکتور هدف پیدا نشد.")
    if inv.status == "cancelled":
        raise DomainError("به فاکتور ابطال‌شده نمی‌توان تخصیص داد.")
    if inv.status != "issued":
        raise DomainError("فقط به فاکتور صادرشده می‌توان تخصیص داد.")
    if inv.party_id != s.party_id:
        raise DomainError("فاکتور به همین مشتری تعلق ندارد.")
    available = settlement_available_amount(sess, s)
    if amount > available:
        raise DomainError(f"مبلغ تخصیص از پیش‌دریافت قابل استفاده بیشتر است؛ باقی‌مانده {available:,} ریال است.")
    open_inv = inv.total_rial - inv.paid_rial
    if amount > open_inv:
        raise DomainError(f"مبلغ تخصیص از مانده‌ی باز فاکتور بیشتر است؛ مانده {open_inv:,} ریال است.")
    # جلوگیری از تخصیص تکراری بیش از حد — چک بالا کافی است، ولی برای شفافیت:
    # اگر همین فاکتور قبلاً از همین دریافت تخصیص گرفته، مجموع نباید از سقف بگذرد (بالا چک می‌شود)
    alloc = SettlementAllocation(settlement_id=s.id, target_type="invoice", target_id=inv.id, amount_rial=amount)
    s.allocations.append(alloc)
    inv.paid_rial += amount
    sess.flush()
    svc.audit(sess, user, "settlement.allocate", "settlement", str(s.id),
              f"تخصیص {amount:,} از {s.number} به فاکتور {inv.number}", ip)
    return s


def unallocate_deposit(sess, user, settlement_id: int, allocation_id: int, ip: str = "") -> Settlement:
    s = sess.get(Settlement, settlement_id)
    if s is None:
        raise DomainError("این دریافت پیدا نشد.")
    alloc = sess.get(SettlementAllocation, allocation_id)
    if alloc is None or alloc.settlement_id != s.id:
        raise DomainError("این تخصیص پیدا نشد یا به همین دریافت تعلق ندارد.")
    if alloc.target_type != "invoice":
        raise DomainError("فقط تخصیص فاکتور قابل لغو است.")
    inv = sess.get(Invoice, alloc.target_id)
    if inv is None:
        # حتی اگر فاکتور حذف شده باشد، تخصیص را پاک می‌کنیم
        sess.delete(alloc)
        sess.flush()
        return s
    # برگرداندن مانده
    inv.paid_rial = max(0, inv.paid_rial - alloc.amount_rial)
    sess.delete(alloc)
    sess.flush()
    svc.audit(sess, user, "settlement.unallocate", "settlement", str(s.id),
              f"لغو تخصیص {alloc.amount_rial:,} از {s.number} فاکتور {inv.number}", ip)
    return s


def cancel_settlement(sess, user, settlement_id: int, reason: str, ip: str = "") -> Settlement:
    s = sess.get(Settlement, settlement_id)
    if s is None:
        raise DomainError("این عملیات پیدا نشد.")
    if s.status == "issued" and s.entry_id:
        svc.reverse_entry(sess, user, s.entry_id, reason or "ابطال دریافت/پرداخت", ip)
        for a in s.allocations:
            if a.target_type == "invoice":
                doc = sess.get(Invoice, a.target_id)
            elif a.target_type == "partial_cancel":
                from .models import InvoicePartialCancel
                doc = None
                pc = sess.get(InvoicePartialCancel, a.target_id)
                if pc is not None:
                    if s.direction == "out":
                        pc.refund_paid_rial = max(0, pc.refund_paid_rial - a.amount_rial)
                    else:
                        pc.supplier_received_rial = max(0, pc.supplier_received_rial - a.amount_rial)
            else:
                doc = sess.get(SupplierBill, a.target_id)
            if doc:
                doc.paid_rial = max(0, doc.paid_rial - a.amount_rial)
        s.allocations.clear()
    # اگر ردیف چکی بود، خود برگ چک هم باطل شود تا بعداً دوباره به جریان نیفتد
    if getattr(s, "cheque_id", None):
        from .models import Cheque as _Cheque
        ch = sess.get(_Cheque, s.cheque_id)
        if ch is not None and ch.status in ("in_hand", "issued", "deposited"):
            ch.status = "cancelled"
    s.status = "cancelled"
    svc.audit(sess, user, "settlement.cancel", "settlement", str(s.id), reason, ip)
    return s


def list_settlements(sess, direction: str | None = None, party_id: int | None = None,
                     trip_id: int | None = None, limit: int = 300,
                     only_creator: int | None = None) -> list[dict]:
    q = select(Settlement).order_by(Settlement.doc_date.desc(), Settlement.id.desc())
    if only_creator is not None:
        q = q.where(Settlement.created_by == only_creator)
    if direction and direction != "all":
        q = q.where(Settlement.direction == direction)
    if party_id:
        q = q.where(Settlement.party_id == party_id)
    if trip_id:
        q = q.where(Settlement.trip_id == trip_id)
    parties = {p.id: p.name for p in sess.execute(select(Party)).scalars()}
    accounts = svc.account_map(sess)
    out = []
    for s in sess.execute(q.limit(limit)).scalars():
        allocated = sum(a.amount_rial for a in s.allocations)
        out.append({"id": s.id, "number": s.number, "date": jd.jalali_str(s.doc_date),
                    "direction": s.direction, "party_id": s.party_id,
                    "party": parties.get(s.party_id, "?"),
                    "method": s.method, "ref_no": s.ref_no, "batch_no": s.batch_no,
                    "account": accounts[s.account_id].name if s.account_id in accounts else "",
                    "amount": s.amount_rial, "description": s.description, "status": s.status,
                    "trip_id": s.trip_id,
                    "allocated": allocated,
                    "unallocated": s.amount_rial - allocated,
                    "allocations": [{"id": a.id, "target_type": a.target_type, "target_id": a.target_id,
                                     "amount": a.amount_rial} for a in s.allocations]})
    return out


# ------------------------------------------------------------------ تقویم پول و صف تأیید
def cash_calendar(sess, days: int = 30, today: date | None = None) -> dict:
    today = today or jd.jtoday()
    horizon = today + timedelta(days=days)
    parties = {p.id: p.name for p in sess.execute(select(Party)).scalars()}
    items, overdue = [], []

    def push(kind: str, doc, direction: str, title: str, when: date, amount: int,
             ref: str, party_id: int):
        if amount <= 0:
            return
        row = {"kind": kind, "title": title, "ref": ref, "date": jd.jalali_str(when),
               "date_iso": when.isoformat(), "direction": direction, "amount": amount,
               "party": parties.get(party_id, "?"), "days_left": (when - today).days}
        (overdue if when < today else items).append(row)

    for i in sess.execute(select(Invoice).where(Invoice.status == "issued",
                                                Invoice.total_rial > Invoice.paid_rial)).scalars():
        push("invoice", i, "in", f"مانده فاکتور {i.number} — {i.description or ''}".strip(),
             i.due_date or i.doc_date, i.total_rial - i.paid_rial, i.number, i.party_id)
    for b in sess.execute(select(SupplierBill).where(SupplierBill.status == "issued",
                                                     SupplierBill.total_rial > SupplierBill.paid_rial)).scalars():
        push("bill", b, "out", f"مانده صورتحساب {b.number} — {b.description or ''}".strip(),
             b.due_date or b.doc_date, b.total_rial - b.paid_rial, b.number, b.party_id)
    for r in sess.execute(select(RecurringEntry).where(RecurringEntry.is_active.is_(True))).scalars():
        try:
            lines = json.loads(r.lines_json or "[]")
            amount = sum(int(l.get("debit") or 0) for l in lines)
        except Exception:  # noqa: BLE001
            amount = 0
        if amount <= 0:
            continue
        for offset in (0, 1):
            jy, jm, _ = jd.jalali_parts(today)
            jm += offset
            if jm > 12:
                jy, jm = jy + 1, 1
            first, last = jd.daterange_month(jy, jm)
            day = min(max(1, r.day_of_month), last.day)
            when = date.fromordinal(first.toordinal() + day - 1)
            if when < today or when > horizon:
                continue
            items.append({"kind": "recurring", "title": f"سند تکراری: {r.title}", "ref": r.title,
                          "date": jd.jalali_str(when), "date_iso": when.isoformat(),
                          "direction": "out", "amount": amount, "party": "—",
                          "days_left": (when - today).days})

    items.sort(key=lambda r: r["date_iso"])
    overdue.sort(key=lambda r: r["date_iso"])
    return {
        "from": jd.jalali_str(today), "to": jd.jalali_str(horizon), "days": days,
        "items": [i for i in items if i["date_iso"] <= horizon.isoformat()],
        "overdue": overdue,
        "total_in": sum(i["amount"] for i in items if i["direction"] == "in"),
        "total_out": sum(i["amount"] for i in items if i["direction"] == "out"),
        "overdue_in": sum(i["amount"] for i in overdue if i["direction"] == "in"),
        "overdue_out": sum(i["amount"] for i in overdue if i["direction"] == "out"),
    }


def approvals(sess) -> dict:
    """صف تأیید: چیزهایی که کارمند ثبت کرده و منتظر حسابدار است."""
    rows = sess.execute(
        select(JournalEntry).where(JournalEntry.status == "draft")
        .order_by(JournalEntry.entry_date, JournalEntry.id)).scalars()
    users = {u.id: u for u in sess.execute(select(__import__("app.models", fromlist=["User"]).User)).scalars()}
    items = []
    for e in rows:
        owner = users.get(e.created_by)
        items.append({"id": e.id, "date": jd.jalali_str(e.entry_date), "description": e.description,
                      "by": owner.full_name or owner.username if owner else "—",
                      "role": owner.role if owner else "", "amount": sum(l.debit_rial for l in e.lines),
                      "balanced": sum(l.debit_rial for l in e.lines) == sum(l.credit_rial for l in e.lines),
                      "source": e.source})
    draft_inv = sess.execute(select(func.count(Invoice.id)).where(Invoice.status == "draft")).scalar() or 0
    draft_bill = sess.execute(select(func.count(SupplierBill.id)).where(SupplierBill.status == "draft")).scalar() or 0
    draft_settle = sess.execute(select(func.count(Settlement.id)).where(Settlement.status == "draft")).scalar() or 0
    return {"entries": items, "count": len(items), "draft_invoices": draft_inv,
            "draft_bills": draft_bill, "draft_settlements": draft_settle}


# ------------------------------------------------------------------ سندهای تکراری
def create_recurring(sess, user, title: str, day_of_month: int, raw_lines: list[dict],
                     description: str = "", ip: str = "") -> RecurringEntry:
    if len((title or "").strip()) < 2:
        raise DomainError("عنوان سند تکراری را بنویسید.")
    if not (1 <= int(day_of_month) <= 31):
        raise DomainError("روز ماه باید بین ۱ و ۳۱ باشد.")
    check = [{"account_id": l.get("account_id"), "debit": l.get("debit"), "credit": l.get("credit"),
              "description": l.get("description", "")} for l in raw_lines]
    v = svc._validated_lines(sess, check)          # همان بررسی سند معمولی
    if v.problems:
        raise DomainError(v.message(), "invalid_lines")
    lines = [{"account_id": l.account_id, "debit": l.debit, "credit": l.credit,
              "description": l.description} for l in v.lines]
    r = RecurringEntry(title=title.strip()[:120], day_of_month=int(day_of_month),
                       description=description[:300], lines_json=json.dumps(lines, ensure_ascii=False),
                       created_by=user.id)
    sess.add(r)
    sess.flush()
    svc.audit(sess, user, "recurring.create", "recurring", str(r.id), r.title, ip)
    return r


def list_recurring(sess) -> list[dict]:
    out = []
    for r in sess.execute(select(RecurringEntry).order_by(RecurringEntry.day_of_month)).scalars():
        try:
            lines = json.loads(r.lines_json or "[]")
        except Exception:  # noqa: BLE001
            lines = []
        labels = svc.label_map(sess)
        out.append({"id": r.id, "title": r.title, "day": r.day_of_month,
                    "description": r.description, "is_active": r.is_active,
                    "last_period": r.last_period,
                    "amount": sum(int(l.get("debit") or 0) for l in lines),
                    "lines": [{"account_id": l["account_id"],
                               "account": labels.get(l["account_id"], "?"),
                               "debit": l.get("debit", 0), "credit": l.get("credit", 0)} for l in lines]})
    return out


def generate_recurring(sess, user, recurring_id: int, jy: int, jm: int, ip: str = "") -> JournalEntry:
    r = sess.get(RecurringEntry, recurring_id)
    if r is None:
        raise DomainError("این سند تکراری پیدا نشد.")
    period_key = f"{jy}-{jm:02d}"
    if r.last_period == period_key:
        raise DomainError(f"سند «{r.title}» برای {jd.MONTHS[jm - 1]} {jy} از قبل ساخته شده است.")
    first, last = jd.daterange_month(jy, jm)
    day = min(max(1, r.day_of_month), last.day)
    when = date.fromordinal(first.toordinal() + day - 1)
    lines = json.loads(r.lines_json or "[]")
    entry = svc.create_draft(sess, user, when, r.description or r.title, lines,
                             source="system", source_ref=f"تکراری {r.title}", ip=ip)
    r.last_period = period_key
    sess.flush()
    svc.audit(sess, user, "recurring.generate", "recurring", str(r.id),
              f"{r.title} — {period_key}", ip)
    return entry


def toggle_recurring(sess, user, recurring_id: int, is_active: bool, ip: str = "") -> RecurringEntry:
    r = sess.get(RecurringEntry, recurring_id)
    if r is None:
        raise DomainError("این سند تکراری پیدا نشد.")
    r.is_active = bool(is_active)
    svc.audit(sess, user, "recurring.toggle", "recurring", str(r.id), str(is_active), ip)
    return r
