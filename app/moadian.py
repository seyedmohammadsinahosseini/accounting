"""سامانه‌ی مودیان — ساخت و نگهداری صورتحساب الکترونیکی (فاز ۵).

چه چیزی اینجا انجام می‌شود:
۱) از هر فاکتور فروشِ «صادرشده»، یک «صورتحساب الکترونیکی» با ساختار رسمی سامانه‌ی مودیان ساخته می‌شود
   (سرآمد + بدنه، همان فیلدهای دستورالعمل: taxid, indatim, inty, inno, inp, ins, tins, tob, bid, tinb, ...).
۲) پیش از هر چیز، خودِ برنامه صورتحساب را کنترل می‌کند: شناسه‌های ۱۳ رقمی، جمع‌ها، مالیات هر ردیف.
   اگر ایرادی باشد، صورتحساب «ناقص» می‌ماند و به زبان ساده می‌گوید کدام قلم کم است.
۳) صورتحساب‌های آماده در یک صف می‌مانند (کارپوشه‌ی محلی) تا یا برای شرکت معتمد فرستاده شوند،
   یا وقتی اتصال مستقیم برقرار شد، از همین‌جا ارسال شوند. وضعیت هر صورتحساب ثبت می‌شود.

نکته‌ی مهم: هیچ شناسه‌ی کالا/خدمتی در کد نیست. شناسه‌ها را سازمان مالیاتی می‌دهد؛
حسابدار آن‌ها را از صفحه‌ی «سامانه‌ی مودیان» وارد می‌کند و تأیید می‌کند.
"""
from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import func, select

from . import dates as jd
from . import services as svc
from .models import (Invoice, MoadianInvoice, MoadianServiceMap, MoadianSettings,
                     Party, Passenger, Settlement, SettlementAllocation, User)
from . import vault
from .money import format_amount_fa
from .rules import DomainError

# ---------------------------------------------------------------- فهرست‌های رسمی
# این فهرست‌ها از دستورالعمل سامانه‌ی مودیان می‌آید و در برنامه فقط برای نمایش و کنترل است.
PATTERNS = {1: "فروش", 2: "فروش ارزی", 3: "طلا، جواهر و پلاتین", 4: "قرارداد پیمانکاری",
            5: "قبوض خدماتی", 6: "بلیت هواپیما", 7: "صادرات", 8: "بارنامه"}
SUBJECTS = {1: "اصلی", 2: "اصلاحی", 3: "ابطالی", 4: "برگشت از فروش"}
INVOICE_TYPES = {1: "نوع اول (با اطلاعات خریدار)", 2: "نوع دوم (بدون اطلاعات کامل خریدار)"}
SETTLEMENTS = {1: "نقدی", 2: "نسیه", 3: "نقدی/نسیه"}
FLIGHT_TYPES = {1: "داخلی", 2: "خارجی"}
PERSON_TYPES = {1: "حقیقی", 2: "حقوقی"}
SELLER_PERSON_TYPES = PERSON_TYPES
# واحد اندازه‌گیری «خدمت» در سند واحدهای اندازه‌گیری سامانه — کد پیش‌فرض برای خدمات
DEFAULT_UNIT_CODE = 1627

SERVICE_TITLES = {"tour": "تور و سفر", "ticket": "بلیت", "visa": "ویزا",
                  "hotel": "اقامت و هتل", "insurance": "بیمه مسافرتی", "other": "سایر خدمات"}

STATUS_LABELS = {"draft": "ناقص", "ready": "آماده‌ی ارسال", "sent": "ارسال‌شده",
                 "accepted": "پذیرفته‌شده", "rejected": "مردود"}

SOURCE_NOTE = ("شناسه‌ی کالا/خدمت را از سامانه‌ی رسمی «stuffid.tax.gov.ir» یا از شرکت معتمد بگیرید "
               "و همین‌جا وارد کنید. تا وقتی وارد و تأیید نشود، صورتحساب ساخته نمی‌شود.")


def _ms(when: date) -> int:
    """تاریخ شمسی برنامه → عدد میلی‌ثانیه‌ی میلادی، همان‌طور که سامانه می‌خواهد."""
    moment = datetime(when.year, when.month, when.day, 12, 0, 0)
    return int(moment.timestamp() * 1000)


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


# ---------------------------------------------------------------- تنظیمات
def get_settings(sess) -> MoadianSettings:
    row = sess.execute(select(MoadianSettings).order_by(MoadianSettings.id)).scalars().first()
    if row is None:
        row = MoadianSettings(seller_name="", is_active=False)
        sess.add(row)
        sess.flush()
    return row


def settings_dict(sess) -> dict:
    s = get_settings(sess)
    return {"taxid": s.taxid, "economic_code": s.economic_code, "seller_name": s.seller_name,
            "person_type": s.person_type, "person_type_label": SELLER_PERSON_TYPES.get(s.person_type, ""),
            "branch_code": s.branch_code, "postal_code": s.postal_code, "phone": s.phone,
            "address": s.address, "send_method": s.send_method,
            "send_method_label": {"motamed": "شرکت معتمد مالیاتی", "direct": "ارسال مستقیم"}.get(s.send_method, ""),
            "motamed_name": s.motamed_name, "motamed_id": s.motamed_id,
            "environment": s.environment,
            "environment_label": {"sandbox": "محیط آزمایشی", "production": "محیط واقعی"}.get(s.environment, ""),
            "is_active": bool(s.is_active), "note": s.note,
            "updated_at": s.updated_at.strftime("%Y-%m-%d %H:%M") if s.updated_at else ""}


def save_settings(sess, user: User, payload: dict, ip: str = "") -> MoadianSettings:
    s = get_settings(sess)
    taxid = "".join((payload.get("taxid") or "").split())
    if taxid and len(taxid) != 22:
        raise DomainError("شناسه‌ی یکتای حافظه‌ی مالیاتی ۲۲ نویسه است (حرف و رقم). چیزی که وارد "
                          f"کردید {len(taxid)} نویسه است؛ از کارپوشه‌ی مالیاتی کپی کنید.")
    econ = _digits(payload.get("economic_code"))
    person_type = int(payload.get("person_type") or 2)
    if econ and person_type == 2 and len(econ) != 11:
        raise DomainError("شماره اقتصادی شخص حقوقی ۱۱ رقم است (همان شناسه‌ی ملی).")
    if econ and person_type == 1 and len(econ) not in (11, 14):
        raise DomainError("شماره اقتصادی شخص حقیقی ۱۴ رقم است: ده رقم کد ملی + چهار رقم از ۰۰۰۱.")
    method = payload.get("send_method") or "motamed"
    if method not in ("motamed", "direct"):
        raise DomainError("روش ارسال باید «شرکت معتمد» یا «ارسال مستقیم» باشد.")
    if method == "motamed" and not (payload.get("motamed_name") or "").strip():
        raise DomainError("نام شرکت معتمد را بنویسید، یا روش ارسال را «مستقیم» بگذارید.")

    s.taxid = taxid
    s.economic_code = econ
    s.seller_name = (payload.get("seller_name") or "").strip()[:160]
    s.person_type = person_type
    s.branch_code = _digits(payload.get("branch_code"))[:10]
    s.postal_code = _digits(payload.get("postal_code"))[:15]
    s.phone = (payload.get("phone") or "").strip()[:30]
    s.address = (payload.get("address") or "").strip()[:300]
    s.send_method = method
    s.motamed_name = (payload.get("motamed_name") or "").strip()[:160]
    s.motamed_id = (payload.get("motamed_id") or "").strip()[:60]
    s.environment = "production" if payload.get("environment") == "production" else "sandbox"
    s.is_active = bool(payload.get("is_active"))
    s.note = (payload.get("note") or "").strip()[:400]
    s.updated_by = user.id
    s.updated_at = datetime.now()
    sess.flush()
    svc.audit(sess, user, "moadian.save_settings", "moadian_settings", str(s.id),
              ("فعال" if s.is_active else "غیرفعال") + f" — {s.send_method} — {s.environment}", ip)
    return s


# ---------------------------------------------------------------- شناسه‌های کالا/خدمت
def seed_service_map(sess) -> None:
    """ردیف‌های پیش‌فرض نگاشت خدمات. شناسه‌ها عمداً خالی‌اند تا حسابدار واردش کند."""
    existing = {m.service_type for m in sess.execute(select(MoadianServiceMap)).scalars()}
    patterns = {"ticket": 6, "tour": 1, "visa": 1, "hotel": 1, "insurance": 1, "other": 1}
    for index, (code, title) in enumerate(zip(SERVICE_TITLES, SERVICE_TITLES.values()), start=1):
        if code in existing:
            continue
        sess.add(MoadianServiceMap(service_type=code, title=title, stuff_id="",
                                   unit_code=DEFAULT_UNIT_CODE, invoice_pattern=patterns.get(code, 1),
                                   is_confirmed=False, note=SOURCE_NOTE, sort_order=index * 10))
    sess.flush()


def list_service_map(sess) -> list[dict]:
    rows = sess.execute(select(MoadianServiceMap).order_by(MoadianServiceMap.sort_order,
                                                           MoadianServiceMap.id)).scalars()
    out = []
    for m in rows:
        used = sess.execute(select(func.count(MoadianInvoice.id)).where(
            MoadianInvoice.pattern == m.invoice_pattern)).scalar() or 0
        out.append({"id": m.id, "service_type": m.service_type, "title": m.title,
                    "stuff_id": m.stuff_id, "unit_code": m.unit_code,
                    "invoice_pattern": m.invoice_pattern,
                    "pattern_label": PATTERNS.get(m.invoice_pattern, ""),
                    "is_confirmed": bool(m.is_confirmed), "note": m.note,
                    "ready": bool(len(_digits(m.stuff_id)) == 13 and m.is_confirmed),
                    "used_invoices": used})
    return out


def update_service_map(sess, user: User, row_id: int, payload: dict, ip: str = "") -> MoadianServiceMap:
    m = sess.get(MoadianServiceMap, row_id)
    if m is None:
        raise DomainError("این ردیف شناسه پیدا نشد.")
    if "stuff_id" in payload:
        stuff = _digits(payload.get("stuff_id"))
        if stuff and len(stuff) != 13:
            raise DomainError(f"شناسه‌ی کالا/خدمت «{m.title}» باید ۱۳ رقم باشد؛ {len(stuff)} رقم وارد شده است.")
        if stuff:
            twin = sess.execute(select(MoadianServiceMap).where(
                MoadianServiceMap.stuff_id == stuff, MoadianServiceMap.id != m.id)).scalar_one_or_none()
            if twin is not None:
                raise DomainError(f"این شناسه قبلاً برای «{twin.title}» ثبت شده است.")
        m.stuff_id = stuff
    if payload.get("unit_code") is not None:
        m.unit_code = int(payload["unit_code"])
    if payload.get("invoice_pattern") is not None:
        pattern = int(payload["invoice_pattern"])
        if pattern not in PATTERNS:
            raise DomainError("الگوی صورتحساب نامعتبر است.")
        m.invoice_pattern = pattern
    if payload.get("is_confirmed") is not None:
        if payload["is_confirmed"] and len(_digits(m.stuff_id)) != 13:
            raise DomainError("تا وقتی شناسه‌ی کالا/خدمت وارد نشده، نمی‌شود تأییدش کرد.")
        m.is_confirmed = bool(payload["is_confirmed"])
    if payload.get("note") is not None:
        m.note = str(payload["note"])[:400]
    m.updated_at = datetime.now()
    sess.flush()
    svc.audit(sess, user, "moadian.update_service", "moadian_service_map", str(m.id),
              f"{m.title} — شناسه {m.stuff_id or 'ندارد'} — {'تأییدشده' if m.is_confirmed else 'تأییدنشده'}", ip)
    return m


# ---------------------------------------------------------------- ساخت صورتحساب
def _invoice_type_for(sess, invoice: Invoice, pattern: int) -> int:
    """نوع صورتحساب: بلیت همیشه نوع اول؛ بقیه اگر خریدار شناسه/شماره اقتصادی داشته باشد نوع اول."""
    if pattern == 6:
        return 1
    party = sess.get(Party, invoice.party_id)
    if party and (_digits(party.national_id) or _digits(party.economic_code)):
        return 1
    return 2


def build_payload(sess, invoice: Invoice, settings: MoadianSettings,
                  pattern_override: int | None = None) -> tuple[dict, list[str]]:
    """صورتحساب الکترونیکی یک فاکتور را می‌سازد و ایرادهایش را برمی‌گرداند."""
    problems: list[str] = []
    party = sess.get(Party, invoice.party_id)
    maps = {m.service_type: m for m in sess.execute(select(MoadianServiceMap)).scalars()}

    if invoice.status != "issued":
        problems.append("فاکتور صادر نشده است؛ اول در صفحه‌ی فاکتورها صادرش کنید.")
    if settings.taxid and len("".join(settings.taxid.split())) != 22:
        problems.append("شناسه‌ی یکتای حافظه‌ی مالیاتی در تنظیمات ۲۲ نویسه نیست.")
    if len(_digits(settings.economic_code)) not in (11, 14):
        problems.append("شماره اقتصادی فروشنده در تنظیمات کامل نیست (۱۱ رقم حقوقی یا ۱۴ رقم حقیقی).")

    first_pattern = pattern_override
    if first_pattern is None:
        line_maps = [maps.get(l.service_type) for l in invoice.lines]
        first_pattern = line_maps[0].invoice_pattern if line_maps and line_maps[0] else 1

    invoice_type = _invoice_type_for(sess, invoice, first_pattern)

    body = []
    tprdis = tdis = tadis = tvam = 0
    for line in invoice.lines:
        mapping = maps.get(line.service_type)
        if mapping is None:
            problems.append(f"نوع خدمت «{line.service_type}» نگاشت ندارد.")
            continue
        if line.revenue_model == "commission":
            problems.append("این فاکتور ردیف امانی/کمیسیونی دارد؛ تفکیک مبلغ ایرلاین از کمیسیون در "
                            "صورتحساب رسمی باید پیش از ارسال با حسابدار و نگاشت مالیاتی تأیید شود.")
        stuff = _digits(mapping.stuff_id)
        if len(stuff) != 13:
            problems.append(f"برای «{mapping.title}» شناسه‌ی کالا/خدمت ۱۳ رقمی وارد نشده است.")
        elif not mapping.is_confirmed:
            problems.append(f"شناسه‌ی «{mapping.title}» وارد شده ولی هنوز تأیید نشده است.")
        # الگوی صورتحساب همه‌ی ردیف‌ها باید یکی باشد (در برنامه هر خدمت الگوی خودش را دارد)
        if mapping.invoice_pattern != first_pattern:
            problems.append(f"در یک فاکتور نمی‌شود «{PATTERNS.get(first_pattern, '')}» و "
                            f"«{PATTERNS.get(mapping.invoice_pattern, '')}» را با هم آورد؛ "
                            f"ردیف «{mapping.title}» را جداگانه فاکتور کنید.")

        fee = line.unit_price_rial
        prdis = fee * line.qty
        dis = line.discount_rial
        adis = line.net_rial
        vra = round(line.rate_bp / 10000, 4)
        vam = line.vat_rial
        if dis and prdis - dis != adis:
            problems.append(f"ردیف {line.line_no}: مبلغ بعد از تخفیف با فی و تخفیف نمی‌خواند.")
        if vam != int(round(adis * vra)):
            problems.append(f"ردیف {line.line_no}: مالیات با نرخ {vra} نمی‌خواند.")
        tprdis += prdis
        tdis += dis
        tadis += adis
        tvam += vam

        row = {"sstid": stuff, "sstt": (line.description or mapping.title)[:200],
               "mu": mapping.unit_code, "am": line.qty, "fee": fee, "prdis": prdis,
               "dis": dis, "adis": adis, "vra": vra, "vam": vam, "tsstam": adis}
        if first_pattern == 6:
            if not line.flight_type:
                problems.append(f"ردیف {line.line_no}: نوع پرواز (داخلی/خارجی) انتخاب نشده است.")
            row["flight_type"] = line.flight_type
        body.append(row)

    if invoice.total_rial != tadis + tvam:
        problems.append("جمع فاکتور با مبلغ ردیف‌ها و مالیات نمی‌خواند.")
    if not body:
        problems.append("فاکتور هیچ ردیف قابل ارسالی ندارد.")

    # خریدار
    tob = int(party.person_type or 1) if party else 1
    bid = _digits(party.national_id) if party else ""
    tinb = _digits(party.economic_code) if party else ""
    # شماره‌ی گذرنامه می‌تواند حرف داشته باشد؛ فقط فاصله‌ها برداشته می‌شود
    bpn = "".join((party.passport_no or "").split()) if party else ""
    if int(tob) not in (1, 2):
        tob = 1

    # در بلیت، خریدار همان مسافر است؛ اگر مسافر ثبت شده باشد، اطلاعات هویتی او مقدم است
    ticket_passengers = list(sess.execute(select(Passenger).where(
        Passenger.invoice_id == invoice.id)).scalars())
    if first_pattern == 6 and ticket_passengers:
        chosen = next((p for p in ticket_passengers if vault.decrypt(p.national_id)),
                      ticket_passengers[0])
        pax_national = _digits(vault.decrypt(chosen.national_id))
        pax_passport = "".join(vault.decrypt(chosen.passport_no).split())
        if pax_national:
            bid = pax_national
        if pax_passport:
            bpn = pax_passport
        if chosen.person_type == 2:
            tob = 1                      # مسافر غیرایرانی هم شخص حقیقی است
        expiry_state = vault.expiry_state(chosen.passport_expiry)
        if expiry_state == "expired":
            problems.append(f"گذرنامه‌ی مسافر «{chosen.full_name}» منقضی شده است.")
        elif expiry_state == "soon":
            problems.append(f"گذرنامه‌ی مسافر «{chosen.full_name}» کمتر از ۶ ماه اعتبار دارد "
                            "(برای پرواز خارجی مشکل‌ساز می‌شود).")
        if len(ticket_passengers) > 1:
            problems.append(f"این فاکتور {len(ticket_passengers)} مسافر دارد؛ اطلاعات هویتی مسافر "
                            "اول در صورتحساب آمده. اگر سامانه برای هر بلیت صورتحساب جدا می‌خواهد، "
                            "برای هر مسافر یک فاکتور جدا صادر کنید.")
    if invoice_type == 1:
        if not bid and not bpn:
            # خریدار خارجی فقط گذرنامه دارد؛ سامانه هم برای او کد ملی نمی‌خواهد
            problems.append("برای صورتحساب نوع اول، کد ملی/شناسه‌ی ملی خریدار (یا شماره‌ی "
                            "گذرنامه برای خریدار خارجی) لازم است (در صفحه‌ی مشتری‌ها "
                            "واردش کنید).")
        if tob == 2 and not tinb:
            problems.append("خریدار شخص حقوقی است؛ شماره‌ی اقتصادی‌اش را وارد کنید.")

    # payload فعلی فقط یک روش پرداخت را در فیلد رسمی می‌سازد؛ دریافت کارت/چک یا چندروش
    # باید در صف بماند تا نگاشت رسمی روش‌های پرداخت قبل از ارسال بررسی شود.
    settlement_rows = list(sess.execute(
        select(Settlement).join(SettlementAllocation, SettlementAllocation.settlement_id == Settlement.id)
        .where(SettlementAllocation.target_type == "invoice",
               SettlementAllocation.target_id == invoice.id,
               Settlement.direction == "in", Settlement.status == "issued")
        .order_by(Settlement.doc_date, Settlement.id)
    ).scalars())
    payment_methods = {str(s.method or "cash") for s in settlement_rows}
    if payment_methods - {"cash"} or len(payment_methods) > 1:
        problems.append("این فاکتور دریافت کارت/چک یا چند روش پرداخت دارد؛ payload فعلی جزئیات "
                        "چند روش را منتقل نمی‌کند و باید پیش از ارسال بازبینی شود.")

    # روش تسویه
    paid = min(invoice.paid_rial, invoice.total_rial)
    if paid >= invoice.total_rial and invoice.total_rial > 0:
        setm, cap, insp = 1, invoice.total_rial, 0
    elif paid == 0:
        setm, cap, insp = 2, 0, invoice.total_rial
    else:
        setm, cap, insp = 3, paid, invoice.total_rial - paid
    tvop = int(round(tvam * cap / invoice.total_rial)) if invoice.total_rial else 0

    header = {
        "taxid": settings.taxid, "indatim": _ms(invoice.doc_date), "indati2m": _ms(invoice.doc_date),
        "inty": invoice_type, "inno": invoice.number or "", "irtaxid": None, "inp": first_pattern,
        "ins": 1, "tins": _digits(settings.economic_code),
        "tob": tob, "bid": bid or None, "tinb": tinb or None,
        "sbc": _digits(settings.branch_code) or None,
        "bpc": _digits(party.postal_code) if party and party.postal_code else None,
        "bbc": None, "ft": None,
        "bpn": bpn or None, "scln": None, "scc": None, "crn": None, "billid": None,
        "tprdis": tprdis, "tdis": tdis, "tadis": tadis, "tvam": tvam, "todam": 0,
        "tbill": tadis + tvam, "setm": setm, "cap": cap, "insp": insp, "tvop": tvop, "tax17": None,
    }
    if first_pattern == 6:
        flight = next((l.flight_type for l in invoice.lines if l.flight_type), None)
        header["ft"] = flight
        if not bid and not bpn:
            problems.append("برای بلیت به کد ملی یا شماره‌ی گذرنامه‌ی مسافر نیاز است "
                            "(در صفحه‌ی پرونده‌ی سفر، مسافر را ثبت کنید یا اطلاعات مالیاتی "
                            "مشتری را کامل کنید).")
        if flight == 2 and not (bid or bpn):
            problems.append("برای بلیت پرواز خارجی، شماره‌ی گذرنامه‌ی مسافر لازم است.")
        header["bpn"] = bpn or None
    payload = {"header": header, "body": body,
               "payment": [] if not cap else [{"pmt": 1, "pv": cap, "pdt": _ms(invoice.doc_date)}]}
    return payload, problems


def _serial_for(sess, jy: int) -> str:
    last = sess.execute(select(func.max(MoadianInvoice.serial)).where(
        MoadianInvoice.serial.like(f"{jy}-%"))).scalar()
    seq = int(last.split("-")[-1]) + 1 if last else 1
    return f"{jy}-{seq:06d}"


def create_for_invoices(sess, user: User, invoice_ids: list[int] | None = None,
                        frm: date | None = None, to: date | None = None,
                        include_cancelled: bool = True, ip: str = "") -> dict:
    """از فاکتورهای صادرشده، صورتحساب الکترونیکی می‌سازد و در صف می‌گذارد."""
    settings = get_settings(sess)
    q = select(Invoice).order_by(Invoice.doc_date, Invoice.id)
    if invoice_ids:
        q = q.where(Invoice.id.in_(invoice_ids))
    else:
        statuses = ["issued"] + (["cancelled"] if include_cancelled else [])
        q = q.where(Invoice.status.in_(statuses))
        if frm:
            q = q.where(Invoice.doc_date >= frm)
        if to:
            q = q.where(Invoice.doc_date <= to)
    invoices = list(sess.execute(q).scalars())

    created = skipped = 0
    rows: list[dict] = []
    for invoice in invoices:
        subject = 3 if invoice.status == "cancelled" else 1
        existing = sess.execute(select(MoadianInvoice).where(
            MoadianInvoice.invoice_id == invoice.id,
            MoadianInvoice.subject == subject)).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue
        if subject == 3:
            original = sess.execute(select(MoadianInvoice).where(
                MoadianInvoice.invoice_id == invoice.id,
                MoadianInvoice.subject == 1)).scalar_one_or_none()
            if original is None:
                skipped += 1          # فاکتور ابطالی که اصلش هرگز فرستاده نشده، لازم نیست
                continue
        payload, problems = build_payload(sess, invoice, settings)
        if subject == 3:
            original = sess.execute(select(MoadianInvoice).where(
                MoadianInvoice.invoice_id == invoice.id,
                MoadianInvoice.subject == 1)).scalar_one_or_none()
            payload["header"]["ins"] = 3
            payload["header"]["irtaxid"] = (original.taxid if original and original.taxid else None)
            if original is None or not original.taxid:
                problems.append("برای صورتحساب ابطالی، اول باید صورتحساب اصلی پذیرفته شده باشد "
                                "(شماره‌ی مالیاتی اصل لازم است).")
        jy = jd.jalali_parts(invoice.doc_date)[0]
        row = MoadianInvoice(
            invoice_id=invoice.id, serial=_serial_for(sess, jy), doc_date=invoice.doc_date,
            invoice_type=payload["header"]["inty"], pattern=payload["header"]["inp"], subject=subject,
            status="ready" if not problems else "draft",
            payload_json=json.dumps(payload, ensure_ascii=False),
            problems_json=json.dumps(problems, ensure_ascii=False),
            total_rial=payload["header"]["tbill"], vat_rial=payload["header"]["tvam"],
            reference_taxid=payload["header"].get("irtaxid") or "", created_by=user.id)
        sess.add(row)
        sess.flush()
        created += 1
        rows.append({"id": row.id, "serial": row.serial, "invoice": invoice.number,
                     "status": row.status, "problems": problems,
                     "total": row.total_rial})
    svc.audit(sess, user, "moadian.build", "moadian_invoice", "", f"{created} صورتحساب ساخته شد", ip)
    return {"created": created, "skipped": skipped, "rows": rows}


def list_queue(sess, status: str | None = None, limit: int = 300) -> list[dict]:
    q = select(MoadianInvoice).order_by(MoadianInvoice.doc_date.desc(), MoadianInvoice.id.desc())
    if status and status != "all":
        q = q.where(MoadianInvoice.status == status)
    rows = []
    numbers = {i.id: (i.number or "") for i in sess.execute(select(Invoice)).scalars()}
    for m in sess.execute(q.limit(limit)).scalars():
        problems = []
        try:
            problems = json.loads(m.problems_json or "[]")
        except Exception:  # noqa: BLE001
            problems = []
        rows.append({
            "id": m.id, "serial": m.serial, "invoice_id": m.invoice_id,
            "invoice_number": numbers.get(m.invoice_id, ""), "date": jd.jalali_str(m.doc_date),
            "type": m.invoice_type, "type_label": INVOICE_TYPES.get(m.invoice_type, ""),
            "pattern": m.pattern, "pattern_label": PATTERNS.get(m.pattern, ""),
            "subject": m.subject, "subject_label": SUBJECTS.get(m.subject, ""),
            "status": m.status, "status_label": STATUS_LABELS.get(m.status, ""),
            "taxid": m.taxid, "reference_number": m.reference_number,
            "reference_taxid": m.reference_taxid, "total": m.total_rial, "vat": m.vat_rial,
            "total_fa": format_amount_fa(m.total_rial), "vat_fa": format_amount_fa(m.vat_rial),
            "problems": problems, "error": m.error_text,
            "sent_at": m.sent_at.strftime("%Y-%m-%d %H:%M") if m.sent_at else "",
        })
    return rows


def queue_detail(sess, row_id: int) -> dict:
    m = sess.get(MoadianInvoice, row_id)
    if m is None:
        raise DomainError("این صورتحساب پیدا نشد.")
    try:
        payload = json.loads(m.payload_json or "{}")
    except Exception:  # noqa: BLE001
        payload = {}
    try:
        problems = json.loads(m.problems_json or "[]")
    except Exception:  # noqa: BLE001
        problems = []
    invoice = sess.get(Invoice, m.invoice_id)
    ticket_lines = []
    if invoice is not None:
        ticket_lines = [{"line_id": l.id, "line_no": l.line_no, "description": l.description,
                         "flight_type": l.flight_type}
                        for l in invoice.lines if l.service_type == "ticket"]
    return {"id": m.id, "serial": m.serial, "invoice_id": m.invoice_id, "ticket_lines": ticket_lines,
            "status": m.status,
            "status_label": STATUS_LABELS.get(m.status, ""), "taxid": m.taxid,
            "reference_number": m.reference_number, "error": m.error_text,
            "problems": problems, "payload": payload,
            "total_fa": format_amount_fa(m.total_rial), "vat_fa": format_amount_fa(m.vat_rial)}


def mark_sent(sess, user: User, row_id: int, reference_number: str, taxid: str = "",
              status: str = "sent", error: str = "", ip: str = "") -> MoadianInvoice:
    """ثبت نتیجه‌ی ارسال: یا رفته و منتظر جواب است، یا پذیرفته/مردود شده."""
    m = sess.get(MoadianInvoice, row_id)
    if m is None:
        raise DomainError("این صورتحساب پیدا نشد.")
    if status not in ("sent", "accepted", "rejected"):
        raise DomainError("وضعیت ارسال نامعتبر است.")
    if status in ("sent", "accepted") and not (reference_number.strip() or taxid.strip()):
        raise DomainError("شماره‌ی ارجاع یا شماره‌ی مالیاتی را وارد کنید.")
    if status == "accepted" and len((taxid or "").strip()) < 8:
        raise DomainError("برای «پذیرفته‌شده» شدن، شماره‌ی مالیاتی صورتحساب لازم است؛ "
                          "همان شماره‌ای که سامانه‌ی مودیان در جواب می‌دهد (۲۲ نویسه).")
    if m.status in ("accepted",) and status != "accepted":
        raise DomainError("صورتحساب پذیرفته‌شده را نمی‌شود عقب برد.")
    m.status = status
    m.reference_number = reference_number.strip()[:60]
    if taxid:
        m.taxid = taxid.strip()[:30]
    m.error_text = (error or "").strip()[:500]
    m.sent_at = datetime.now() if status in ("sent", "accepted") else m.sent_at
    sess.flush()
    svc.audit(sess, user, "moadian.mark_result", "moadian_invoice", str(m.id),
              f"{m.serial} → {STATUS_LABELS.get(status, status)} ({reference_number or taxid})", ip)
    return m


def rebuild(sess, user: User, row_id: int, ip: str = "") -> MoadianInvoice:
    """صورتحساب ناقص را با اطلاعات تازه‌ی برنامه از نو می‌سازد (مثلاً بعد از وارد کردن شناسه‌ها)."""
    m = sess.get(MoadianInvoice, row_id)
    if m is None:
        raise DomainError("این صورتحساب پیدا نشد.")
    if m.status in ("sent", "accepted"):
        raise DomainError("صورتحساب ارسال‌شده را نمی‌شود از نو ساخت.")
    invoice = sess.get(Invoice, m.invoice_id)
    if invoice is None:
        raise DomainError("فاکتور مربوط به این صورتحساب پیدا نشد.")
    payload, problems = build_payload(sess, invoice, get_settings(sess))
    if m.subject == 3:
        payload["header"]["ins"] = 3
        original = sess.execute(select(MoadianInvoice).where(
            MoadianInvoice.invoice_id == m.invoice_id, MoadianInvoice.subject == 1)).scalar_one_or_none()
        payload["header"]["irtaxid"] = (original.taxid if original and original.taxid else None)
    m.payload_json = json.dumps(payload, ensure_ascii=False)
    m.problems_json = json.dumps(problems, ensure_ascii=False)
    m.total_rial = payload["header"]["tbill"]
    m.vat_rial = payload["header"]["tvam"]
    m.invoice_type = payload["header"]["inty"]
    m.pattern = payload["header"]["inp"]
    m.status = "ready" if not problems else "draft"
    sess.flush()
    svc.audit(sess, user, "moadian.rebuild", "moadian_invoice", str(m.id), m.serial, ip)
    return m


def summary(sess) -> dict:
    counts = {key: 0 for key in STATUS_LABELS}
    for m in sess.execute(select(MoadianInvoice)).scalars():
        counts[m.status] = counts.get(m.status, 0) + 1
    ready_total = sess.execute(select(func.coalesce(func.sum(MoadianInvoice.total_rial), 0)).where(
        MoadianInvoice.status == "ready")).scalar() or 0
    accepted_total = sess.execute(select(func.coalesce(func.sum(MoadianInvoice.total_rial), 0)).where(
        MoadianInvoice.status == "accepted")).scalar() or 0
    settings = get_settings(sess)
    maps = list_service_map(sess)
    return {"counts": counts, "ready_total": ready_total, "accepted_total": accepted_total,
            "ready_total_fa": format_amount_fa(ready_total),
            "accepted_total_fa": format_amount_fa(accepted_total),
            "is_active": bool(settings.is_active),
            "service_ready": sum(1 for m in maps if m["ready"]), "service_count": len(maps)}


# ---------------------------------------------------------------- فایل خروجی
def payload_file(sess, row_id: int) -> tuple[str, str]:
    """فایل JSON یک صورتحساب، آماده‌ی تحویل به شرکت معتمد."""
    m = sess.get(MoadianInvoice, row_id)
    if m is None:
        raise DomainError("این صورتحساب پیدا نشد.")
    if m.status in ("draft",):
        raise DomainError("این صورتحساب ناقص است؛ اول ایرادهایش را برطرف کنید.")
    pretty = json.dumps(json.loads(m.payload_json or "{}"), ensure_ascii=False, indent=2)
    return pretty, f"صورتحساب-{m.serial}.json"


def batch_file(sess, status: str = "ready") -> tuple[str, str]:
    """همه‌ی صورتحساب‌های یک وضعیت، در یک فایل بسته‌ای برای فرستادن یک‌جا."""
    rows = sess.execute(select(MoadianInvoice).where(MoadianInvoice.status == status)
                        .order_by(MoadianInvoice.id)).scalars()
    packets = []
    for m in rows:
        try:
            packets.append({"serial": m.serial, "packet": json.loads(m.payload_json or "{}")})
        except Exception:  # noqa: BLE001
            continue
    body = {"تعداد": len(packets), "وضعیت": STATUS_LABELS.get(status, status),
            "یادداشت": "این فایل برای شرکت معتمد مالیاتی یا برای ارسال مستقیم آماده شده است. "
                        "هر بسته یک صورتحساب الکترونیکی با ساختار سرآمد/بدنه/پرداخت است.",
            "بسته‌ها": packets}
    return json.dumps(body, ensure_ascii=False, indent=2), f"صورتحساب‌های-{STATUS_LABELS.get(status, status)}.json"


# ---------------------------------------------------------------- گزارش ارزش افزوده
def vat_report(sess, jy: int, jm: int) -> dict:
    """گزارش فصلی ارزش افزوده: فروش مشمول و معاف، مالیات فروش، اعتبار خرید، و مانده‌ی قابل پرداخت."""
    quarter = (jm - 1) // 3 + 1
    first_month = (quarter - 1) * 3 + 1
    frm = jd.daterange_month(jy, first_month)[0]
    to = jd.daterange_month(jy, first_month + 2)[1]

    taxable_sales = exempt_sales = output_vat = 0
    rows = []
    for invoice in sess.execute(select(Invoice).where(
            Invoice.doc_date >= frm, Invoice.doc_date <= to,
            Invoice.status.in_(("issued", "cancelled"))).order_by(Invoice.doc_date)).scalars():
        inv_taxable = sum(l.net_rial for l in invoice.lines if l.taxable and l.rate_bp)
        inv_exempt = sum(l.net_rial for l in invoice.lines if not (l.taxable and l.rate_bp))
        sign = -1 if invoice.status == "cancelled" else 1
        taxable_sales += sign * inv_taxable
        exempt_sales += sign * inv_exempt
        output_vat += sign * invoice.vat_rial
        rows.append({"number": invoice.number or "—", "date": jd.jalali_str(invoice.doc_date),
                     "status": invoice.status, "taxable": sign * inv_taxable,
                     "exempt": sign * inv_exempt, "vat": sign * invoice.vat_rial,
                     "total": sign * invoice.total_rial})
    # اعتبار مالیاتی: مالیات پرداختی بابت خریدها و هزینه‌ها
    credit = svc.balance_of_code(sess, "1107", to)
    credit = max(0, credit)
    moadian_ready = sess.execute(select(func.count(MoadianInvoice.id)).where(
        MoadianInvoice.doc_date >= frm, MoadianInvoice.doc_date <= to,
        MoadianInvoice.status.in_(("ready", "sent", "accepted")))).scalar() or 0
    return {"jy": jy, "quarter": quarter,
            "from": jd.jalali_str(frm), "to": jd.jalali_str(to),
            "rows": rows, "taxable_sales": taxable_sales, "exempt_sales": exempt_sales,
            "output_vat": output_vat, "credit_vat": credit,
            "payable_vat": max(0, output_vat - credit),
            "moadian_sent": moadian_ready,
            "taxable_sales_fa": format_amount_fa(taxable_sales),
            "exempt_sales_fa": format_amount_fa(exempt_sales),
            "output_vat_fa": format_amount_fa(output_vat),
            "credit_vat_fa": format_amount_fa(credit),
            "payable_vat_fa": format_amount_fa(max(0, output_vat - credit))}
