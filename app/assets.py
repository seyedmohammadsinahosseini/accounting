"""دارایی ثابت و استهلاک.

قاعده‌ی اصلی (مثل حقوق): **هیچ نرخ و روشی داخل کد قفل نیست.** روش استهلاک (نزولی یا مستقیم)،
نرخ سالانه، عمر مفید و حساب‌های هر گروه، همه در جدول «گروه دارایی» نگه داشته می‌شوند و
حسابدار می‌تواند از داخل برنامه عوضشان کند.

سه کار این ماژول:
۱) کارت دارایی: خرید (با سند خودکار)، ویرایش، فروش یا کنارگذاری (با سند و سود/زیان).
۲) استهلاک ماهانه: حساب می‌شود (خروجی می‌دهد)، بعد سندش ثبت می‌شود؛ برگرداندن هم دارد.
۳) گزارش دارایی‌ها و برنامه‌ی استهلاک ماه‌های آینده.
"""
from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import func, select

from . import dates as jd
from . import services as svc
from .models import (Account, AssetCategory, DepreciationRun, FixedAsset)
from .money import format_amount_fa
from .rules import SYSTEM_CODES, DomainError

METHOD_LABELS = {"declining": "نزولی (نرخ سالانه روی مانده)", "straight": "مستقیم (مساوی هر ماه)"}

# گروه‌های پیش‌فرض. نرخ‌ها از جدول استهلاکات قانون مالیات‌های مستقیم؛ حسابدار باید تأییدشان کند.
DEFAULT_CATEGORIES = [
    # کد، عنوان، روش، نرخ (درصد)، عمر مفید (ماه)، حساب دارایی، حساب انباشته، منبع
    ("EQUIP", "اثاثه و تجهیزات اداری", "declining", 10, 120, "110901", "111001",
     "جدول استهلاکات — پیش‌فرض راه‌اندازی، باید توسط حسابدار تأیید شود."),
    ("IT", "رایانه و تجهیزات رایانه‌ای", "declining", 33, 36, "110901", "111001",
     "جدول استهلاکات — پیش‌فرض راه‌اندازی، باید توسط حسابدار تأیید شود."),
    ("CAR", "خودرو", "declining", 25, 48, "110902", "111002",
     "جدول استهلاکات — پیش‌فرض راه‌اندازی، باید توسط حسابدار تأیید شود."),
    ("BUILD", "ساختمان و تأسیسات", "declining", 7, 180, "110903", "111003",
     "جدول استهلاکات — پیش‌فرض راه‌اندازی، باید توسط حسابدار تأیید شود."),
]


def seed_assets(sess) -> None:
    if sess.execute(select(AssetCategory)).first():
        return
    for code, title, method, rate, life, asset_code, acc_code, note in DEFAULT_CATEGORIES:
        asset_acc = svc.account_by_code(sess, asset_code)
        acc_acc = svc.account_by_code(sess, acc_code)
        exp_acc = svc.account_by_code(sess, SYSTEM_CODES["depreciation"])
        if asset_acc is None or acc_acc is None or exp_acc is None:
            continue
        sess.add(AssetCategory(
            code=code, title=title, method=method, annual_rate_bp=int(rate * 100),
            useful_life_months=life, asset_account_id=asset_acc.id,
            accumulation_account_id=acc_acc.id, expense_account_id=exp_acc.id,
            source_note=note, is_system=True,
            sort_order=10 * list(METHOD_LABELS).index(method) + len(title)))
    sess.flush()


# ------------------------------------------------------------------ گروه‌ها
def list_categories(sess, include_inactive: bool = False) -> list[dict]:
    from .models import Account as _A

    accounts = {a.id: a for a in sess.execute(select(_A)).scalars()}
    q = select(AssetCategory).order_by(AssetCategory.sort_order, AssetCategory.id)
    if not include_inactive:
        q = q.where(AssetCategory.is_active.is_(True))
    out = []
    for c in sess.execute(q).scalars():
        out.append({
            "id": c.id, "code": c.code, "title": c.title, "method": c.method,
            "method_label": METHOD_LABELS.get(c.method, c.method),
            "annual_rate_percent": c.annual_rate_bp / 100,
            "useful_life_months": c.useful_life_months,
            "asset_account": accounts[c.asset_account_id].name if c.asset_account_id in accounts else "",
            "accumulation_account": accounts[c.accumulation_account_id].name if c.accumulation_account_id in accounts else "",
            "expense_account": accounts[c.expense_account_id].name if c.expense_account_id in accounts else "",
            "source_note": c.source_note, "is_active": c.is_active,
        })
    return out


def create_category(sess, user, title: str, method: str, annual_rate_percent: float,
                    useful_life_months: int, asset_account_id: int, accumulation_account_id: int,
                    expense_account_id: int | None = None, source_note: str = "", ip: str = "") -> AssetCategory:
    title = (title or "").strip()
    if len(title) < 3:
        raise DomainError("عنوان گروه را کامل بنویسید.")
    if method not in METHOD_LABELS:
        raise DomainError("روش استهلاک باید نزولی یا مستقیم باشد.")
    if not (0 <= annual_rate_percent <= 100):
        raise DomainError("نرخ استهلاک باید بین ۰ و ۱۰۰ درصد باشد.")
    if not (1 <= useful_life_months <= 600):
        raise DomainError("عمر مفید باید بین ۱ و ۶۰۰ ماه باشد.")
    expense = expense_account_id or (svc.account_by_code(sess, SYSTEM_CODES["depreciation"]) or Account()).id
    n = len(sess.execute(select(AssetCategory)).scalars().all()) + 1
    cat = AssetCategory(code=f"C{n}", title=title, method=method,
                        annual_rate_bp=int(round(annual_rate_percent * 100)),
                        useful_life_months=useful_life_months, asset_account_id=asset_account_id,
                        accumulation_account_id=accumulation_account_id, expense_account_id=expense,
                        source_note=source_note[:400])
    sess.add(cat)
    sess.flush()
    svc.audit(sess, user, "asset_category.create", "asset_category", str(cat.id), title, ip)
    return cat


def update_category(sess, user, category_id: int, payload: dict, ip: str = "") -> AssetCategory:
    cat = sess.get(AssetCategory, category_id)
    if cat is None:
        raise DomainError("این گروه پیدا نشد.")
    if payload.get("title"):
        cat.title = str(payload["title"])[:120]
    if payload.get("method") in METHOD_LABELS:
        cat.method = payload["method"]
    if payload.get("annual_rate_percent") is not None:
        rate = float(payload["annual_rate_percent"])
        if not (0 <= rate <= 100):
            raise DomainError("نرخ استهلاک باید بین ۰ و ۱۰۰ درصد باشد.")
        cat.annual_rate_bp = int(round(rate * 100))
    if payload.get("useful_life_months") is not None:
        cat.useful_life_months = max(1, min(600, int(payload["useful_life_months"])))
    if payload.get("source_note") is not None:
        cat.source_note = str(payload["source_note"])[:400]
    if payload.get("is_active") is not None:
        cat.is_active = bool(payload["is_active"])
    sess.flush()
    svc.audit(sess, user, "asset_category.update", "asset_category", str(cat.id), str(payload), ip)
    return cat


# ------------------------------------------------------------------ کارت دارایی
def _next_asset_code(sess) -> str:
    n = sess.execute(select(func.count(FixedAsset.id))).scalar() or 0
    while True:
        n += 1
        code = f"ث-{n:04d}"
        if sess.execute(select(FixedAsset).where(FixedAsset.code == code)).scalar_one_or_none() is None:
            return code


def create_asset(sess, user, title: str, category_id: int, purchase_date: date,
                 in_service_date: date | None, cost_rial: int, salvage_rial: int = 0,
                 useful_life_months: int | None = None, method: str | None = None,
                 annual_rate_percent: float | None = None, note: str = "",
                 counter_account_id: int | None = None, ip: str = "") -> FixedAsset:
    """کارت دارایی تازه.

    اگر «حساب مقابل» را بدهید، سند خرید هم همان لحظه ثبت می‌شود: بدهکار دارایی، بستانکار
    همان حساب. حساب مقابل می‌تواند صندوق، بانک، سرمایه (آورده‌ی مالک) یا حساب‌های پرداختنی باشد.
    """
    title = (title or "").strip()
    if len(title) < 3:
        raise DomainError("عنوان دارایی را کامل بنویسید.")
    cat = sess.get(AssetCategory, category_id)
    if cat is None:
        raise DomainError("گروه دارایی را انتخاب کنید.")
    if cost_rial <= 0:
        raise DomainError("بهای تمام‌شده باید بزرگ‌تر از صفر باشد.")
    if salvage_rial < 0 or salvage_rial >= cost_rial:
        raise DomainError("ارزش اسقاط باید کمتر از بهای تمام‌شده باشد.")
    method = method or cat.method
    if method not in METHOD_LABELS:
        raise DomainError("روش استهلاک نامعتبر است.")

    asset = FixedAsset(
        code=_next_asset_code(sess), title=title, category_id=cat.id,
        purchase_date=purchase_date, in_service_date=in_service_date or purchase_date,
        cost_rial=cost_rial, salvage_rial=salvage_rial,
        useful_life_months=int(useful_life_months or cat.useful_life_months), method=method,
        annual_rate_bp=int(round((annual_rate_percent if annual_rate_percent is not None
                                  else cat.annual_rate_bp / 100) * 100)),
        asset_account_id=cat.asset_account_id, accumulation_account_id=cat.accumulation_account_id,
        expense_account_id=cat.expense_account_id, note=note[:255], created_by=user.id)
    sess.add(asset)
    sess.flush()

    if counter_account_id:
        acc = sess.get(Account, counter_account_id)
        if acc is None or acc.kind != "ledger":
            raise DomainError("حساب مقابل باید یکی از حساب‌های تفصیلی برنامه باشد.")
        entry = svc.create_draft(
            sess, user, purchase_date, f"خرید دارایی ثابت — {asset.title} ({asset.code})",
            [{"account_id": asset.asset_account_id, "debit": cost_rial, "credit": 0,
              "description": f"{asset.title}"},
             {"account_id": acc.id, "debit": 0, "credit": cost_rial,
              "description": f"از {acc.name}"}],
            source="asset", source_ref=asset.code, ip=ip)
        svc.post_entry(sess, user, entry.id, ip)
        asset.purchase_entry_id = entry.id
    sess.flush()
    svc.audit(sess, user, "asset.create", "asset", str(asset.id),
              f"{asset.code} {title} — {format_amount_fa(cost_rial)} ریال", ip)
    return asset


def update_asset(sess, user, asset_id: int, payload: dict, ip: str = "") -> FixedAsset:
    asset = sess.get(FixedAsset, asset_id)
    if asset is None:
        raise DomainError("این دارایی پیدا نشد.")
    if asset.status != "active" and ("cost_rial" in payload or "salvage_rial" in payload):
        raise DomainError("دارایی فروخته‌شده یا کنارگذاشته‌شده دیگر قابل تغییر نیست.")
    if payload.get("title"):
        asset.title = str(payload["title"])[:160]
    if payload.get("note") is not None:
        asset.note = str(payload["note"])[:255]
    if payload.get("cost_rial") is not None:
        cost = int(payload["cost_rial"])
        if cost <= asset.accumulated_rial:
            raise DomainError("بهای تمام‌شده نمی‌تواند کمتر از استهلاک انباشته باشد.")
        asset.cost_rial = cost
    if payload.get("salvage_rial") is not None:
        asset.salvage_rial = max(0, int(payload["salvage_rial"]))
    if payload.get("useful_life_months") is not None:
        asset.useful_life_months = max(1, min(600, int(payload["useful_life_months"])))
    if payload.get("method") in METHOD_LABELS:
        asset.method = payload["method"]
    if payload.get("annual_rate_percent") is not None:
        rate = float(payload["annual_rate_percent"])
        if not (0 <= rate <= 100):
            raise DomainError("نرخ استهلاک باید بین ۰ و ۱۰۰ درصد باشد.")
        asset.annual_rate_bp = int(round(rate * 100))
    sess.flush()
    svc.audit(sess, user, "asset.update", "asset", str(asset.id), str(payload), ip)
    return asset


def list_assets(sess, status: str | None = "active", text: str | None = None) -> list[dict]:
    q = select(FixedAsset).order_by(FixedAsset.code)
    if status and status != "all":
        q = q.where(FixedAsset.status == status)
    if text:
        like = f"%{text.strip()}%"
        q = q.where(FixedAsset.title.ilike(like) | FixedAsset.code.ilike(like))
    cats = {c.id: c for c in sess.execute(select(AssetCategory)).scalars()}
    out = []
    for a in sess.execute(q).scalars():
        cat = cats.get(a.category_id)
        out.append({
            "id": a.id, "code": a.code, "title": a.title,
            "category": cat.title if cat else "", "category_id": a.category_id,
            "purchase_date": jd.jalali_str(a.purchase_date),
            "in_service_date": jd.jalali_str(a.in_service_date),
            "cost": a.cost_rial, "salvage": a.salvage_rial,
            "accumulated": a.accumulated_rial, "book_value": a.book_value_rial,
            "method": a.method, "method_label": METHOD_LABELS.get(a.method, a.method),
            "annual_rate_percent": a.annual_rate_bp / 100,
            "useful_life_months": a.useful_life_months,
            "status": a.status, "note": a.note,
            "disposal_date": jd.jalali_str(a.disposal_date) if a.disposal_date else "",
            "disposal_amount": a.disposal_amount_rial,
        })
    return out


def asset_detail(sess, asset_id: int) -> dict:
    asset = sess.get(FixedAsset, asset_id)
    if asset is None:
        raise DomainError("این دارایی پیدا نشد.")
    cats = {c.id: c for c in sess.execute(select(AssetCategory)).scalars()}
    cat = cats.get(asset.category_id)
    history = []
    for run in sess.execute(select(DepreciationRun).order_by(DepreciationRun.jy, DepreciationRun.jm)).scalars():
        for row in _run_rows(run):
            if row.get("asset_id") == asset.id:
                history.append({"period": f"{jd.MONTHS[run.jm - 1]} {run.jy}", "amount": row["amount"],
                                "run": run.number, "status": run.status})
    schedule = depreciation_schedule(asset, 12)
    return {"asset": list_assets(sess, status="all", text=None) and next(
        a for a in list_assets(sess, status="all") if a["id"] == asset.id),
        "category": {"id": cat.id, "title": cat.title, "method_label": METHOD_LABELS.get(cat.method, ""),
                     "annual_rate_percent": cat.annual_rate_bp / 100} if cat else None,
        "history": history, "schedule": schedule,
        "entries": [{"id": eid, "kind": kind} for eid, kind in
                    ((asset.purchase_entry_id, "خرید"), (asset.disposal_entry_id, "فروش/کنارگذاری"))
                    if eid]}


def depreciation_schedule(asset: FixedAsset, months: int = 12, start: date | None = None) -> list[dict]:
    """پیش‌بینی استهلاک ماه‌های آینده — بدون تغییر در اطلاعات."""
    start = start or jd.jtoday()
    jy, jm, _ = jd.jalali_parts(start)
    out, accumulated = [], asset.accumulated_rial
    for i in range(months):
        y, m = jy + (jm - 1 + i) // 12, (jm - 1 + i) % 12 + 1
        amount = _month_amount(asset, accumulated)
        accumulated += amount
        out.append({"period": f"{jd.MONTHS[m - 1]} {y}", "jy": y, "jm": m, "amount": amount,
                    "accumulated": accumulated, "book_value": asset.cost_rial - accumulated})
    return out


def _month_amount(asset: FixedAsset, accumulated: int) -> int:
    """استهلاک یک ماه. هیچ‌وقت از بهای تمام‌شده منهای ارزش اسقاط بیشتر نمی‌شود و منفی نمی‌شود."""
    depreciable = asset.cost_rial - asset.salvage_rial
    remaining = depreciable - accumulated
    if remaining <= 0:
        return 0
    if asset.method == "straight":
        amount = depreciable // max(1, asset.useful_life_months)
    else:
        book = asset.cost_rial - accumulated
        amount = int(round(book * (asset.annual_rate_bp / 10000) / 12))
        if amount == 0:
            amount = 1                      # تا عدد ناچیز، استهلاک را کامل تعطیل نکند
    return max(0, min(amount, remaining))


def months_between(start: date, jy: int, jm: int) -> int:
    """چند ماه از تاریخ به‌کارگیری تا آن ماه سپری شده (شامل خود ماه)."""
    sy, sm, _ = jd.jalali_parts(start)
    return (jy - sy) * 12 + (jm - sm) + 1


# ------------------------------------------------------------------ استهلاک ماهانه
def _run_rows(run: DepreciationRun) -> list[dict]:
    try:
        return json.loads(run.detail_json or "[]")
    except Exception:  # noqa: BLE001
        return []


def create_run(sess, user, jy: int, jm: int, ip: str = "") -> DepreciationRun:
    """استهلاک یک ماه را حساب می‌کند (پیش‌نویس). هر بار می‌شود از نو حسابش کرد."""
    if not (1 <= jm <= 12):
        raise DomainError("ماه درست نیست.")
    existing = sess.execute(select(DepreciationRun).where(DepreciationRun.jy == jy,
                                                         DepreciationRun.jm == jm)).scalar_one_or_none()
    if existing and existing.status == "posted":
        raise DomainError(f"استهلاک {jd.MONTHS[jm - 1]} {jy} ثبت شده است؛ اول برگردانید.")

    rows, total = [], 0
    for asset in sess.execute(select(FixedAsset).where(FixedAsset.status == "active")).scalars():
        if months_between(asset.in_service_date, jy, jm) < 1:
            continue                      # هنوز به کار نیفتاده
        amount = _month_amount(asset, asset.accumulated_rial)
        if amount <= 0:
            continue
        rows.append({"asset_id": asset.id, "code": asset.code, "title": asset.title,
                     "amount": amount, "accumulated_after": asset.accumulated_rial + amount,
                     "book_value_after": asset.cost_rial - asset.accumulated_rial - amount,
                     "expense_account_id": asset.expense_account_id,
                     "accumulation_account_id": asset.accumulation_account_id})
        total += amount
    if not rows:
        raise DomainError(f"برای {jd.MONTHS[jm - 1]} {jy} استهلاکی برای حساب کردن نمانده است.")

    run = existing or DepreciationRun(jy=jy, jm=jm, created_by=user.id)
    if existing is None:
        sess.add(run)
        sess.flush()
    if not run.number:
        last = sess.execute(select(func.max(DepreciationRun.number)).where(
            DepreciationRun.number.like(f"{jy}-س-%"))).scalar()
        seq = int(last.split("-")[-1]) + 1 if last else 1
        run.number = f"{jy}-س-{seq:06d}"
    run.status = "draft"
    run.entry_id = None
    run.posted_at = None
    run.total_rial = total
    run.detail_json = json.dumps(rows, ensure_ascii=False)
    sess.flush()
    svc.audit(sess, user, "depreciation.calculate", "depreciation_run", str(run.id),
              f"{jd.MONTHS[jm - 1]} {jy} — {len(rows)} دارایی — {format_amount_fa(total)} ریال", ip)
    return run


def _entry_lines(sess, run: DepreciationRun) -> list[dict]:
    """سند استهلاک: هزینه‌ی استهلاک بدهکار، استهلاک انباشته بستانکار — جمع‌شده به تفکیک حساب."""
    debit: dict[int, int] = {}
    credit: dict[int, int] = {}
    for row in _run_rows(run):
        debit[row["expense_account_id"]] = debit.get(row["expense_account_id"], 0) + row["amount"]
        credit[row["accumulation_account_id"]] = credit.get(row["accumulation_account_id"], 0) + row["amount"]
    names = {a.id: a.name for a in sess.execute(select(Account)).scalars()}
    lines = [{"account_id": acc, "debit": amount, "credit": 0,
              "description": f"هزینه استهلاک {jd.MONTHS[run.jm - 1]} {run.jy} — {names.get(acc, '')}"}
             for acc, amount in debit.items()]
    lines += [{"account_id": acc, "debit": 0, "credit": amount,
               "description": f"استهلاک انباشته {jd.MONTHS[run.jm - 1]} {run.jy} — {names.get(acc, '')}"}
              for acc, amount in credit.items()]
    return lines


def post_run(sess, user, run_id: int, ip: str = "") -> DepreciationRun:
    run = sess.get(DepreciationRun, run_id)
    if run is None:
        raise DomainError("این سند استهلاک پیدا نشد.")
    if run.status == "posted":
        return run
    if run.status != "draft":
        raise DomainError("این استهلاک در وضعیت قابل ثبت نیست.")
    if run.total_rial <= 0:
        raise DomainError("استهلاکی برای ثبت نیست.")
    _, last_day = jd.daterange_month(run.jy, run.jm)
    entry_date = last_day if last_day <= jd.jtoday() else jd.jtoday()
    entry = svc.create_draft(sess, user, entry_date,
                            f"سند استهلاک {jd.MONTHS[run.jm - 1]} {run.jy} — {run.number}",
                            _entry_lines(sess, run), source="depreciation",
                            source_ref=run.number, ip=ip)
    svc.post_entry(sess, user, entry.id, ip)
    for row in _run_rows(run):
        asset = sess.get(FixedAsset, row["asset_id"])
        if asset is not None and asset.status == "active":
            asset.accumulated_rial += row["amount"]
    run.entry_id = entry.id
    run.status = "posted"
    run.posted_at = datetime.now()
    sess.flush()
    svc.audit(sess, user, "depreciation.post", "depreciation_run", str(run.id), str(run.number), ip)
    return run


def cancel_run(sess, user, run_id: int, reason: str = "", ip: str = "") -> DepreciationRun:
    run = sess.get(DepreciationRun, run_id)
    if run is None:
        raise DomainError("این استهلاک پیدا نشد.")
    if run.status != "posted":
        raise DomainError("این استهلاک ثبت نشده که برگردانده شود.")
    svc.reverse_entry(sess, user, run.entry_id, reason or "ابطال سند استهلاک", ip)
    for row in _run_rows(run):
        asset = sess.get(FixedAsset, row["asset_id"])
        if asset is not None:
            asset.accumulated_rial = max(0, asset.accumulated_rial - row["amount"])
    run.status = "cancelled"
    run.entry_id = None
    sess.flush()
    svc.audit(sess, user, "depreciation.cancel", "depreciation_run", str(run.id), reason, ip)
    return run


def list_runs(sess, limit: int = 48) -> list[dict]:
    return [{"id": r.id, "number": r.number, "period": f"{jd.MONTHS[r.jm - 1]} {r.jy}",
             "jy": r.jy, "jm": r.jm, "status": r.status, "assets": len(_run_rows(r)),
             "total": r.total_rial,
             "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""}
            for r in sess.execute(select(DepreciationRun).order_by(DepreciationRun.jy.desc(),
                                                                  DepreciationRun.jm.desc()).limit(limit)).scalars()]


def run_detail(sess, run_id: int) -> dict:
    run = sess.get(DepreciationRun, run_id)
    if run is None:
        raise DomainError("این سند استهلاک پیدا نشد.")
    return {"id": run.id, "number": run.number, "period": f"{jd.MONTHS[run.jm - 1]} {run.jy}",
            "jy": run.jy, "jm": run.jm, "status": run.status, "total": run.total_rial,
            "rows": _run_rows(run)}


# ------------------------------------------------------------------ فروش / کنارگذاری
def dispose_asset(sess, user, asset_id: int, dispose_date: date, amount_rial: int = 0,
                  receive_into_account_id: int | None = None, reason: str = "",
                  ip: str = "") -> FixedAsset:
    """فروش یا کنارگذاری دارایی. سود یا زیان فروش خودش حساب و در سند می‌نشیند."""
    asset = sess.get(FixedAsset, asset_id)
    if asset is None:
        raise DomainError("این دارایی پیدا نشد.")
    if asset.status != "active":
        raise DomainError("این دارایی قبلاً فروخته یا کنار گذاشته شده است.")
    if amount_rial < 0:
        raise DomainError("مبلغ فروش نمی‌تواند منفی باشد.")
    lines = []
    if amount_rial:
        if not receive_into_account_id:
            raise DomainError("حساب دریافت پول فروش را انتخاب کنید.")
        acc = sess.get(Account, receive_into_account_id)
        if acc is None or acc.report_group not in ("cash", "bank"):
            raise DomainError("حساب دریافت باید صندوق یا بانک باشد.")
        lines.append({"account_id": acc.id, "debit": amount_rial, "credit": 0,
                      "description": f"فروش دارایی {asset.code} — {asset.title} از {acc.name}"})
    if asset.accumulated_rial:
        lines.append({"account_id": asset.accumulation_account_id, "debit": asset.accumulated_rial,
                      "credit": 0, "description": "حذف استهلاک انباشته"})
    lines.append({"account_id": asset.asset_account_id, "debit": 0, "credit": asset.cost_rial,
                  "description": f"حذف بهای تمام‌شده {asset.code}"})
    difference = amount_rial + asset.accumulated_rial - asset.cost_rial     # مثبت = سود
    if difference > 0:
        gain = svc.account_by_code(sess, SYSTEM_CODES["gain_asset_disposal"])
        lines.append({"account_id": gain.id, "debit": 0, "credit": difference,
                      "description": f"سود فروش دارایی {asset.code}"})
    elif difference < 0:
        loss = svc.account_by_code(sess, SYSTEM_CODES["loss_asset_disposal"])
        lines.append({"account_id": loss.id, "debit": -difference, "credit": 0,
                      "description": f"زیان فروش دارایی {asset.code}"})

    entry = svc.create_draft(
        sess, user, dispose_date,
        f"{'فروش' if amount_rial else 'کنارگذاری'} دارایی ثابت — {asset.title} ({asset.code})"
        + (f" — {reason}" if reason else ""),
        lines, source="asset", source_ref=asset.code, ip=ip)
    svc.post_entry(sess, user, entry.id, ip)
    asset.status = "sold" if amount_rial else "retired"
    asset.disposal_date = dispose_date
    asset.disposal_amount_rial = amount_rial
    asset.disposal_entry_id = entry.id
    sess.flush()
    svc.audit(sess, user, "asset.dispose", "asset", str(asset.id),
              f"{asset.code} — {format_amount_fa(amount_rial)} ریال — {'سود' if difference > 0 else 'زیان' if difference < 0 else 'بی‌تفاوت'}", ip)
    return asset


def summary(sess) -> dict:
    """جمع دارایی‌ها برای نمایش در سرصفحه و ترازنامه."""
    total_cost = total_acc = total_book = 0
    count = 0
    for a in sess.execute(select(FixedAsset)).scalars():
        if a.status != "active":
            continue
        count += 1
        total_cost += a.cost_rial
        total_acc += a.accumulated_rial
        total_book += a.book_value_rial
    return {"count": count, "cost": total_cost, "accumulated": total_acc, "book_value": total_book}
