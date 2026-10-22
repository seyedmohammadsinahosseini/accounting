"""مسافران و محافظت از اطلاعات هویتی (فاز ۲ — بخشی که جا مانده بود).

قاعده‌های این بخش، ساده و صریح:
۱) شماره‌ی ملی و گذرنامه در فایل داده **رمزنگاری‌شده** ذخیره می‌شود؛ متن ساده هیچ‌جا نوشته نمی‌شود.
۲) در فهرست‌ها فقط **ماسک** نشان داده می‌شود (مثل «00••••••78»).
۳) دیدن شماره‌ی کامل، دسترسی جدا دارد و **هر بار** در «ردّ کار» ثبت می‌شود.
۴) بعد از تمام‌شدن سفر، می‌شود اطلاعات هویتی مسافران را **پاک** کرد و فقط نام بماند.
"""
from __future__ import annotations


from sqlalchemy import func, select

from . import dates as jd
from . import services as svc
from .models import Invoice, Passenger, Trip, User
from .rules import DomainError
from .security import role_has
from . import vault

KINDS = {"adult": "بزرگسال", "child": "کودک", "infant": "نوزاد"}
PERSON_TYPES = {1: "ایرانی", 2: "غیرایرانی"}


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _clean_name(name: str, index: int = 0) -> str:
    text = (name or "").strip()
    if len(text) < 3:
        raise DomainError("نام مسافر را کامل بنویسید."
                          if not index else f"نام مسافر ردیف {index} را کامل بنویسید.")
    return text[:160]


def _row(p: Passenger, reveal: bool = False) -> dict:
    """ردیف مسافر برای نمایش؛ پیش‌فرض ماسک‌شده."""
    national = vault.decrypt(p.national_id)
    passport = vault.decrypt(p.passport_no)
    expiry = p.passport_expiry
    out = {
        "id": p.id, "name": p.full_name, "kind": p.kind, "kind_label": KINDS.get(p.kind, ""),
        "person_type": p.person_type, "person_type_label": PERSON_TYPES.get(p.person_type, ""),
        "national_id": national if reveal else vault.mask(national),
        "passport_no": passport if reveal else vault.mask(passport),
        "has_national_id": bool(national), "has_passport": bool(passport),
        "passport_expiry": jd.jalali_str(expiry) if expiry else "",
        "passport_state": vault.expiry_state(expiry),
        "passport_state_label": vault.EXPIRY_LABELS[vault.expiry_state(expiry)],
        "birth_date": jd.jalali_str(p.birth_date) if p.birth_date else "",
        "phone": p.phone, "note": p.note,
        "trip_id": p.trip_id, "invoice_id": p.invoice_id,
    }
    if reveal:
        out["encrypted_at_rest"] = vault.is_encrypted(p.national_id) or vault.is_encrypted(p.passport_no)
        out["stored_in_clear"] = vault.stored_plain(p.national_id) or vault.stored_plain(p.passport_no)
    return out


def _get(sess, passenger_id: int) -> Passenger:
    row = sess.get(Passenger, passenger_id)
    if row is None:
        raise DomainError("این مسافر پیدا نشد.")
    return row


def _owner_check(sess, trip_id: int | None, invoice_id: int | None) -> None:
    if trip_id and sess.get(Trip, trip_id) is None:
        raise DomainError("پرونده‌ی سفر انتخاب‌شده پیدا نشد.")
    if invoice_id and sess.get(Invoice, invoice_id) is None:
        raise DomainError("فاکتور انتخاب‌شده پیدا نشد.")
    if not trip_id and not invoice_id:
        raise DomainError("هر مسافر باید به یک سفر یا یک فاکتور وصل باشد.")


def create_passenger(sess, user: User, payload: dict, ip: str = "") -> Passenger:
    trip_id = payload.get("trip_id")
    invoice_id = payload.get("invoice_id")
    _owner_check(sess, trip_id, invoice_id)
    kind = payload.get("kind") or "adult"
    if kind not in KINDS:
        raise DomainError("نوع مسافر باید بزرگسال، کودک یا نوزاد باشد.")
    person_type = int(payload.get("person_type") or 1)
    if person_type not in (1, 2):
        raise DomainError("مسافر یا ایرانی است یا غیرایرانی.")
    national = _digits(payload.get("national_id"))
    passport = "".join(str(payload.get("passport_no") or "").split()).upper()
    if person_type == 1 and national and len(national) != 10:
        raise DomainError("کد ملی مسافر ۱۰ رقم است.")
    if person_type == 2 and not passport:
        raise DomainError("برای مسافر غیرایرانی، شماره‌ی گذرنامه لازم است.")
    if passport and len(passport) < 5:
        raise DomainError("شماره‌ی گذرنامه کوتاه است؛ شماره‌ی کامل را وارد کنید.")
    expiry = payload.get("passport_expiry")
    expiry_date = jd.parse_date_any(expiry) if expiry else None
    if person_type == 2 and expiry_date is None:
        raise DomainError("برای مسافر غیرایرانی، تاریخ انقضای گذرنامه لازم است.")

    row = Passenger(
        full_name=_clean_name(payload.get("full_name") or payload.get("name")),
        kind=kind, person_type=person_type,
        national_id=vault.encrypt(national), passport_no=vault.encrypt(passport),
        passport_expiry=expiry_date,
        birth_date=jd.parse_date_any(payload["birth_date"]) if payload.get("birth_date") else None,
        phone=str(payload.get("phone") or "")[:30], note=str(payload.get("note") or "")[:255],
        trip_id=trip_id, invoice_id=invoice_id, created_by=user.id)
    sess.add(row)
    sess.flush()
    svc.audit(sess, user, "passenger.create", "passenger", str(row.id),
              row.full_name + (" — شماره‌ی هویتی ثبت شد" if (national or passport) else ""), ip)
    return row


def update_passenger(sess, user: User, passenger_id: int, payload: dict, ip: str = "") -> Passenger:
    row = _get(sess, passenger_id)
    if payload.get("full_name"):
        row.full_name = _clean_name(payload["full_name"])
    if payload.get("kind"):
        if payload["kind"] not in KINDS:
            raise DomainError("نوع مسافر نامعتبر است.")
        row.kind = payload["kind"]
    if payload.get("person_type"):
        row.person_type = int(payload["person_type"])
    if payload.get("national_id") is not None:
        national = _digits(payload["national_id"])
        if national and len(national) != 10:
            raise DomainError("کد ملی مسافر ۱۰ رقم است.")
        row.national_id = vault.encrypt(national)
    if payload.get("passport_no") is not None:
        passport = "".join(str(payload["passport_no"]).split()).upper()
        if passport and len(passport) < 5:
            raise DomainError("شماره‌ی گذرنامه کوتاه است.")
        row.passport_no = vault.encrypt(passport)
    if payload.get("passport_expiry") is not None:
        row.passport_expiry = (jd.parse_date_any(payload["passport_expiry"])
                               if payload["passport_expiry"] else None)
    if payload.get("phone") is not None:
        row.phone = str(payload["phone"])[:30]
    if payload.get("note") is not None:
        row.note = str(payload["note"])[:255]
    if row.person_type == 2 and not vault.decrypt(row.passport_no):
        raise DomainError("برای مسافر غیرایرانی، شماره‌ی گذرنامه لازم است.")
    sess.flush()
    svc.audit(sess, user, "passenger.update", "passenger", str(row.id), row.full_name, ip)
    return row


def delete_passenger(sess, user: User, passenger_id: int, ip: str = "") -> None:
    row = _get(sess, passenger_id)
    svc.audit(sess, user, "passenger.delete", "passenger", str(row.id),
              f"{row.full_name} — حذف شد", ip)
    sess.delete(row)
    sess.flush()


def list_passengers(sess, user: User, trip_id: int | None = None,
                    invoice_id: int | None = None, reveal: bool = False) -> list[dict]:
    q = select(Passenger).order_by(Passenger.kind, Passenger.full_name)
    if trip_id is not None:
        q = q.where(Passenger.trip_id == trip_id)
    if invoice_id is not None:
        q = q.where(Passenger.invoice_id == invoice_id)
    rows = [_row(p, reveal) for p in sess.execute(q).scalars()]
    if reveal:
        # دیدن فهرست کامل هم در «ردّ کار» ثبت می‌شود (نه فقط دیدن یک نفر)
        svc.audit(sess, user, "passenger.view_full", "passengers",
                  f"trip={trip_id} invoice={invoice_id}",
                  f"{len(rows)} مسافر — شماره‌ی کامل دیده شد")
    return rows


def reveal_passenger(sess, user: User, passenger_id: int, reason: str = "",
                     ip: str = "") -> dict:
    """نمایش شماره‌ی کامل یک مسافر — با ثبت دلیل و تاریخچه."""
    row = _get(sess, passenger_id)
    svc.audit(sess, user, "passenger.reveal", "passenger", str(row.id),
              f"{row.full_name} — {reason or 'بدون توضیح'}", ip)
    return _row(row, reveal=True)


def passengers_of_invoice(sess, user: User, invoice_id: int) -> list[dict]:
    return list_passengers(sess, user, invoice_id=invoice_id,
                           reveal=role_has(user.role, "passengers.view_full"))


def set_invoice_passengers(sess, user: User, invoice_id: int, passenger_ids: list[int],
                           ip: str = "") -> dict:
    """مسافران یک فاکتور (مثلاً بلیت‌ها) را تعیین می‌کند."""
    invoice = sess.get(Invoice, invoice_id)
    if invoice is None:
        raise DomainError("فاکتور پیدا نشد.")
    wanted = set(passenger_ids or [])
    current = list(sess.execute(select(Passenger).where(Passenger.invoice_id == invoice.id)).scalars())
    for row in current:
        if row.id not in wanted:
            if row.trip_id is None:          # مسافری که فقط به این فاکتور وصل بود، حذف می‌شود
                sess.delete(row)
            else:
                row.invoice_id = None
    for pid in wanted:
        row = _get(sess, pid)
        if row.invoice_id not in (None, invoice.id):
            raise DomainError(f"مسافر «{row.full_name}» به فاکتور دیگری وصل است.")
        row.invoice_id = invoice.id
    sess.flush()
    count = sess.execute(select(func.count(Passenger.id)).where(
        Passenger.invoice_id == invoice.id)).scalar() or 0
    svc.audit(sess, user, "invoice.passengers", "invoice", str(invoice.id),
              f"{count} مسافر به فاکتور {invoice.number or invoice.id} وصل شد", ip)
    return {"count": count}


def purge_trip_passengers(sess, user: User, trip_id: int, ip: str = "") -> dict:
    """بعد از پایان سفر: شماره‌ی هویتی مسافران پاک می‌شود، نام و بقیه می‌ماند."""
    trip = sess.get(Trip, trip_id)
    if trip is None:
        raise DomainError("پرونده‌ی سفر پیدا نشد.")
    rows = list(sess.execute(select(Passenger).where(Passenger.trip_id == trip.id)).scalars())
    cleared = 0
    for row in rows:
        if vault.decrypt(row.national_id) or vault.decrypt(row.passport_no):
            row.national_id = ""
            row.passport_no = ""
            row.passport_expiry = None
            cleared += 1
    sess.flush()
    svc.audit(sess, user, "passenger.purge", "trip", str(trip.id),
              f"{trip.code}: اطلاعات هویتی {cleared} مسافر پاک شد", ip)
    return {"cleared": cleared, "kept": len(rows)}


def summary(sess, user: User) -> dict:
    """خلاصه‌ی مسافران: چند نفر، چند تا گذرنامه کمتر از ۶ ماه، چند تا بدون کد ملی."""
    rows = list(sess.execute(select(Passenger)).scalars())
    soon = expired = missing_id = foreigners = 0
    for p in rows:
        if p.person_type == 2:
            foreigners += 1
        state = vault.expiry_state(p.passport_expiry)
        if state == "soon":
            soon += 1
        elif state == "expired":
            expired += 1
        if not vault.decrypt(p.national_id) and p.person_type == 1:
            missing_id += 1
    return {"total": len(rows), "foreigners": foreigners, "expiry_soon": soon,
            "expired": expired, "missing_national_id": missing_id,
            "encryption": {"available": vault.crypto_available(),
                           "problem": vault.crypto_problem()}}


def passengers_map(sess) -> dict[int, Passenger]:
    return {p.id: p for p in sess.execute(select(Passenger)).scalars()}
