"""حقوق و دستمزد.

سه تصمیم مهم این بخش (کار من، نه کارفرما):

۱) **هیچ عدد قانونی در کد نیست.** درصد بیمه، پله‌های مالیات، سقف معافیت و ضریب اضافه‌کاری
   همه در جدول «اعداد قانونی سال» با تاریخ و منبع نگه داشته می‌شوند. حسابدار می‌تواند
   هر سال اعداد تازه را وارد کند و برنامه از آن روز به بعد با اعداد تازه حساب می‌کند.
   تا وقتی حسابدار «تأیید» نکند، برنامه آن اعداد را تأییدنشده نشان می‌دهد.

۲) **هر قلم حقوق، قانون خودش را می‌گوید:** مشمول بیمه بودن، مشمول مالیات بودن و سقف معافیت
   مالیاتی، روی خود قلم تعریف می‌شود. پس «تغییر یک قلم مزایا فقط اقلامی را عوض می‌کند که
   طبق تنظیمات از آن اثر می‌پذیرند» — همان آزمون نمونه‌ی کارفرما.

۳) **سند حقوق متوازن و قابل ردیابی است** و پرداخت حقوق سند جدا دارد.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import func, select

from . import dates as jd
from . import services as svc
from .models import (Employee, EmployeeLoan, EmployeePayItem, PayItem,
                     PayrollItem, PayrollRun, PayrollSettings)
from .money import format_amount_fa
from .rules import SYSTEM_CODES, DomainError

CONTRACT_LABELS = {"permanent": "تمام‌وقت", "temporary": "موقت", "part_time": "پاره‌وقت"}

# اقلام پیش‌فرض حقوق. پرچم‌های «مشمول بیمه» و «مشمول مالیات» و سقف معافیت، قابل ویرایش‌اند.
DEFAULT_ITEMS = [
    # کد، عنوان، نوع، مشمول بیمه، مشمول مالیات، سقف معافیت ماهانه (ریال)، اجباری، ترتیب
    ("BASE", "حقوق پایه", "earning", True, True, 0, True, 10),
    ("HOUSING", "حق مسکن", "earning", True, True, 0, True, 20),
    ("FOOD", "خواربار (بن)", "earning", True, True, 0, True, 30),
    ("CHILD", "حق اولاد", "earning", False, False, 0, False, 40),
    ("OVERTIME", "اضافه‌کاری", "earning", True, True, 0, False, 50),
    ("BONUS", "پاداش و مزایای دیگر", "earning", True, True, 0, False, 60),
    ("OTHER_DED", "سایر کسورات", "deduction", False, False, 0, False, 90),
]

# اعداد پیش‌فرض سال ۱۴۰۴ (از منابع عمومی؛ باید توسط حسابدار با قانون جاری تطبیق شود).
DEFAULT_SETTINGS_1404 = {
    "year": 1404,
    "insurance_employee_bp": 700,
    "insurance_employer_bp": 2000,
    "unemployment_bp": 300,
    "tax_mode": "monthly",
    "tax_brackets": [
        {"up_to": 240_000_000, "rate_bp": 0},
        {"up_to": 300_000_000, "rate_bp": 1000},
        {"up_to": 380_000_000, "rate_bp": 1500},
        {"up_to": 500_000_000, "rate_bp": 2000},
        {"up_to": 666_666_667, "rate_bp": 2500},
        {"up_to": None, "rate_bp": 3000},
    ],
    "standard_monthly_hours": 220,
    "overtime_coefficient_bp": 14000,
    "source_note": "پیش‌فرض راه‌اندازی — باید با قانون و بخشنامه‌ی سال جاری تطبیق داده شود.",
}


# ------------------------------------------------------------------ راه‌اندازی
# اعداد پیش‌فرض سال ۱۴۰۵ از منابع عمومی (قانون بودجه ۱۴۰۵ و بخشنامه‌های بیمه).
# مثل ۱۴۰۴، این‌ها فقط «نقطه‌ی شروع»‌اند و تا حسابدار تأیید نکند، تأییدنشده می‌مانند.
DEFAULT_SETTINGS_1405 = {
    "year": 1405,
    "insurance_employee_bp": 700,
    "insurance_employer_bp": 2000,
    "unemployment_bp": 300,
    "tax_mode": "monthly",
    "tax_brackets": [
        {"up_to": 400_000_000, "rate_bp": 0},
        {"up_to": 800_000_000, "rate_bp": 1000},
        {"up_to": 1_000_000_000, "rate_bp": 1500},
        {"up_to": 1_200_000_000, "rate_bp": 2000},
        {"up_to": 1_400_000_000, "rate_bp": 2500},
        {"up_to": None, "rate_bp": 3000},
    ],
    "standard_monthly_hours": 220,
    "overtime_coefficient_bp": 14000,
    "source_note": ("سال ۱۴۰۵ — از منابع عمومی (قانون بودجه ۱۴۰۵ و بخشنامه‌های بیمه): سقف معافیت "
                    "ماهانه ۴۰۰٬۰۰۰٬۰۰۰ ریال، نرخ‌های ۱۰/۱۵/۲۰/۲۵/۳۰٪ روی پله‌های ۸۰۰/۱۰۰۰/۱۲۰۰/۱۴۰۰ "
                    "میلیون ریال؛ بیمه ۷٪ کارمند و ۲۳٪ کارفرما (۲۰٪ + ۳٪ بیمه بیکاری). "
                    "حداقل دستمزد ۱۴۰۵: پایه ۱۷۱٬۷۹۷٬۳۵۰ + بن ۲۲٬۰۰۰٬۰۰۰ + حق مسکن ۳۰٬۰۰۰٬۰۰۰ ریال. "
                    "نیاز به تأیید حسابدار با بخشنامه‌ی جاری."),
}


def seed_payroll(sess) -> None:
    if not sess.execute(select(PayItem)).first():
        for code, title, kind, insurable, taxable, ceiling, system, order in DEFAULT_ITEMS:
            sess.add(PayItem(code=code, title=title, kind=kind, insurable=insurable,
                             taxable=taxable, tax_exempt_monthly_rial=ceiling,
                             is_system=system, sort_order=order))
        sess.flush()
    for d in (DEFAULT_SETTINGS_1404, DEFAULT_SETTINGS_1405):
        if sess.execute(select(PayrollSettings).where(PayrollSettings.year == d["year"])).scalar_one_or_none():
            continue
        sess.add(PayrollSettings(
            year=d["year"], insurance_employee_bp=d["insurance_employee_bp"],
            insurance_employer_bp=d["insurance_employer_bp"],
            unemployment_bp=d["unemployment_bp"], tax_mode=d["tax_mode"],
            tax_brackets_json=json.dumps(d["tax_brackets"]),
            standard_monthly_hours=d["standard_monthly_hours"],
            overtime_coefficient_bp=d["overtime_coefficient_bp"],
            source_note=d["source_note"], is_confirmed=False))
    sess.flush()


def settings_for_year(sess, jy: int) -> tuple[PayrollSettings, bool]:
    """اعداد قانونی آن سال. اگر برای آن سال ثبت نشده باشد، از آخرین سال قبل استفاده می‌شود."""
    exact = sess.execute(select(PayrollSettings).where(PayrollSettings.year == jy)).scalar_one_or_none()
    if exact is not None:
        return exact, False
    prev = sess.execute(select(PayrollSettings).where(PayrollSettings.year < jy)
                        .order_by(PayrollSettings.year.desc())).scalars().first()
    if prev is None:
        raise DomainError("اعداد قانونی حقوق ثبت نشده است؛ اول در صفحه‌ی حقوق و دستمزد اعداد سال را وارد کنید.")
    return prev, True


def list_settings(sess) -> list[dict]:
    out = []
    for s in sess.execute(select(PayrollSettings).order_by(PayrollSettings.year.desc())).scalars():
        try:
            brackets = json.loads(s.tax_brackets_json or "[]")
        except Exception:  # noqa: BLE001
            brackets = []
        out.append({
            "id": s.id, "year": s.year,
            "insurance_employee_percent": s.insurance_employee_bp / 100,
            "insurance_employer_percent": (s.insurance_employer_bp + s.unemployment_bp) / 100,
            "employer_direct_percent": s.insurance_employer_bp / 100,
            "unemployment_percent": s.unemployment_bp / 100,
            "overtime_coefficient": s.overtime_coefficient_bp / 10000,
            "standard_monthly_hours": s.standard_monthly_hours,
            "brackets": brackets, "tax_mode": s.tax_mode,
            "source_note": s.source_note, "is_confirmed": s.is_confirmed,
            "confirmed_at": s.confirmed_at.strftime("%Y-%m-%d %H:%M") if s.confirmed_at else "",
        })
    return out


def save_settings(sess, user, year: int, employee_percent: float, employer_percent: float,
                  unemployment_percent: float, brackets: list[dict], source_note: str = "",
                  standard_monthly_hours: int = 220, overtime_coefficient: float = 1.4,
                  ip: str = "") -> PayrollSettings:
    if not (1390 <= year <= 1450):
        raise DomainError("سال شمسی درست نیست.")
    if not brackets:
        raise DomainError("پله‌های مالیات را وارد کنید.")
    clean = []
    for b in brackets:
        up_to = b.get("up_to")
        rate = float(b.get("rate_percent", 0))
        if rate < 0 or rate > 100:
            raise DomainError("درصد پله‌ی مالیات باید بین ۰ و ۱۰۰ باشد.")
        clean.append({"up_to": None if up_to in (None, "", "null") else int(up_to),
                      "rate_bp": int(round(rate * 100))})
    row = sess.execute(select(PayrollSettings).where(PayrollSettings.year == year)).scalar_one_or_none()
    if row is None:
        row = PayrollSettings(year=year)
        sess.add(row)
    row.insurance_employee_bp = int(round(employee_percent * 100))
    row.insurance_employer_bp = int(round((employer_percent - unemployment_percent) * 100))
    row.unemployment_bp = int(round(unemployment_percent * 100))
    row.tax_brackets_json = json.dumps(clean)
    row.standard_monthly_hours = int(standard_monthly_hours)
    row.overtime_coefficient_bp = int(round(overtime_coefficient * 10000))
    row.source_note = str(source_note or "")[:400]
    row.is_confirmed = False        # هر تغییری یعنی باید دوباره تأیید شود
    row.confirmed_by = None
    row.confirmed_at = None
    sess.flush()
    svc.audit(sess, user, "payroll.settings_save", "payroll_settings", str(row.id),
              f"year={year} emp={employee_percent}% er={employer_percent}%", ip)
    return row


def confirm_settings(sess, user, year: int, note: str = "", ip: str = "") -> PayrollSettings:
    row = sess.execute(select(PayrollSettings).where(PayrollSettings.year == year)).scalar_one_or_none()
    if row is None:
        raise DomainError("اعداد این سال ثبت نشده است.")
    row.is_confirmed = True
    row.confirmed_by = user.id
    row.confirmed_at = __import__("datetime").datetime.now()
    if note:
        row.source_note = str(note)[:400]
    svc.audit(sess, user, "payroll.settings_confirm", "payroll_settings", str(row.id),
              f"year={year}", ip)
    return row


# ------------------------------------------------------------------ اقلام
def list_items(sess, include_inactive: bool = False) -> list[dict]:
    q = select(PayItem).order_by(PayItem.sort_order, PayItem.id)
    if not include_inactive:
        q = q.where(PayItem.is_active.is_(True))
    return [{"id": i.id, "code": i.code, "title": i.title, "kind": i.kind,
             "insurable": i.insurable, "taxable": i.taxable,
             "exempt_ceiling": i.tax_exempt_monthly_rial,
             "is_system": i.is_system, "is_active": i.is_active, "note": i.note} for i in sess.execute(q).scalars()]


def create_item(sess, user, title: str, kind: str = "earning", insurable: bool = True,
                taxable: bool = True, exempt_ceiling: int = 0, note: str = "", ip: str = "") -> PayItem:
    """قلم حقوق تازه. آژانس هر مزایا یا کسور تازه‌ای بخواهد، خودش از داخل برنامه اضافه می‌کند."""
    title = (title or "").strip()
    if len(title) < 3:
        raise DomainError("عنوان قلم را کامل بنویسید.")
    if kind not in ("earning", "deduction"):
        raise DomainError("نوع قلم باید مزایا یا کسور باشد.")
    if int(exempt_ceiling) < 0:
        raise DomainError("سقف معافیت نمی‌تواند منفی باشد.")
    if sess.execute(select(PayItem).where(PayItem.title == title)).scalar_one_or_none():
        raise DomainError("قلمی با همین عنوان وجود دارد.")
    n = 100
    while sess.execute(select(PayItem).where(PayItem.code == f"CUSTOM{n}")).scalar_one_or_none():
        n += 1
    item = PayItem(code=f"CUSTOM{n}", title=title, kind=kind, insurable=bool(insurable),
                   taxable=bool(taxable) and kind == "earning",
                   tax_exempt_monthly_rial=int(exempt_ceiling), is_system=False,
                   sort_order=n, note=str(note or "")[:255])
    sess.add(item)
    sess.flush()
    svc.audit(sess, user, "payroll.item_create", "pay_item", str(item.id), f"{item.code} {title}", ip)
    return item


def update_item(sess, user, item_id: int, payload: dict, ip: str = "") -> PayItem:
    item = sess.get(PayItem, item_id)
    if item is None:
        raise DomainError("این قلم پیدا نشد.")
    for field, key in (("insurable", "insurable"), ("taxable", "taxable"),
                       ("is_active", "is_active")):
        if key in payload and payload[key] is not None:
            setattr(item, field, bool(payload[key]))
    if payload.get("tax_exempt_monthly_rial") is not None:
        value = int(payload["tax_exempt_monthly_rial"])
        if value < 0:
            raise DomainError("سقف معافیت نمی‌تواند منفی باشد.")
        item.tax_exempt_monthly_rial = value
    if payload.get("title"):
        item.title = str(payload["title"])[:120]
    svc.audit(sess, user, "payroll.item_update", "pay_item", str(item.id), str(payload), ip)
    return item


# ------------------------------------------------------------------ کارکنان
def _next_employee_code(sess) -> str:
    n = sess.execute(select(func.count(Employee.id))).scalar() or 0
    while True:
        n += 1
        code = f"ک-{n:04d}"
        if not sess.execute(select(Employee).where(Employee.code == code)).scalar_one_or_none():
            return code


def create_employee(sess, user, name: str, position: str, hire_date: date,
                    contract_type: str = "permanent", work_days: int = 30,
                    items: list[dict] | None = None, note: str = "", ip: str = "") -> Employee:
    name = (name or "").strip()
    if len(name) < 3:
        raise DomainError("نام کارمند را کامل بنویسید.")
    if contract_type not in CONTRACT_LABELS:
        raise DomainError("نوع قرارداد نامعتبر است.")
    if not (1 <= int(work_days) <= 31):
        raise DomainError("روز کارکرد باید بین ۱ و ۳۱ باشد.")
    emp = Employee(code=_next_employee_code(sess), name=name, position=position[:120],
                   contract_type=contract_type, hire_date=hire_date, work_days=int(work_days),
                   note=note[:255], created_by=user.id)
    sess.add(emp)
    sess.flush()
    if items:
        set_employee_items(sess, user, emp.id, items, ip)
    svc.audit(sess, user, "employee.create", "employee", str(emp.id), f"{emp.code} {emp.name}", ip)
    return emp


def set_employee_items(sess, user, employee_id: int, items: list[dict], ip: str = "") -> list[EmployeePayItem]:
    emp = sess.get(Employee, employee_id)
    if emp is None:
        raise DomainError("این کارمند پیدا نشد.")
    for row in sess.execute(select(EmployeePayItem).where(EmployeePayItem.employee_id == employee_id)).scalars():
        sess.delete(row)
    sess.flush()
    out = []
    for it in items:
        item_id = it.get("pay_item_id")
        amount = int(it.get("amount_rial") or 0)
        if not item_id or amount < 0:
            continue
        if sess.get(PayItem, item_id) is None:
            raise DomainError("یکی از اقلام انتخابی وجود ندارد.")
        row = EmployeePayItem(employee_id=employee_id, pay_item_id=item_id, amount_rial=amount)
        sess.add(row)
        out.append(row)
    sess.flush()
    svc.audit(sess, user, "employee.items", "employee", str(employee_id), f"{len(out)} قلم", ip)
    return out


def employee_detail(sess, employee_id: int) -> dict:
    emp = sess.get(Employee, employee_id)
    if emp is None:
        raise DomainError("این کارمند پیدا نشد.")
    items = {i.id: i for i in sess.execute(select(PayItem)).scalars()}
    rows = sess.execute(select(EmployeePayItem).where(EmployeePayItem.employee_id == employee_id)).scalars()
    loans = sess.execute(select(EmployeeLoan).where(EmployeeLoan.employee_id == employee_id,
                                                    EmployeeLoan.status == "open")).scalars()
    return {
        "id": emp.id, "code": emp.code, "name": emp.name, "position": emp.position,
        "contract": emp.contract_type, "contract_label": CONTRACT_LABELS.get(emp.contract_type, ""),
        "hire_date": jd.jalali_str(emp.hire_date), "work_days": emp.work_days,
        "is_active": emp.is_active, "note": emp.note,
        "items": [{"pay_item_id": r.pay_item_id, "title": items[r.pay_item_id].title if r.pay_item_id in items else "",
                   "code": items[r.pay_item_id].code if r.pay_item_id in items else "",
                   "amount": r.amount_rial} for r in rows],
        "loans": [{"id": l.id, "kind": l.kind, "number": l.number, "amount": l.amount_rial,
                   "paid": l.paid_rial, "remaining": l.remaining_rial,
                   "monthly": l.monthly_rial, "installments": l.installments,
                   "date": jd.jalali_str(l.doc_date),
                   "kind_label": "مساعده" if l.kind == "advance" else "وام"} for l in loans],
    }


def list_employees(sess, include_inactive: bool = True, text: str | None = None) -> list[dict]:
    q = select(Employee).order_by(Employee.name)
    if not include_inactive:
        q = q.where(Employee.is_active.is_(True))
    if text:
        q = q.where(Employee.name.ilike(f"%{text.strip()}%") | Employee.code.ilike(f"%{text.strip()}%"))
    items = {i.id: i for i in sess.execute(select(PayItem)).scalars()}
    out = []
    for emp in sess.execute(q).scalars():
        rows = sess.execute(select(EmployeePayItem).where(EmployeePayItem.employee_id == emp.id)).scalars()
        fixed = 0
        for r in rows:
            item = items.get(r.pay_item_id)
            if item and item.kind == "earning" and r.is_active:
                fixed += r.amount_rial
        loans = sess.execute(select(EmployeeLoan).where(EmployeeLoan.employee_id == emp.id,
                                                        EmployeeLoan.status == "open")).scalars()
        out.append({"id": emp.id, "code": emp.code, "name": emp.name, "position": emp.position,
                    "contract_label": CONTRACT_LABELS.get(emp.contract_type, ""),
                    "hire_date": jd.jalali_str(emp.hire_date), "work_days": emp.work_days,
                    "is_active": emp.is_active, "fixed_total": fixed,
                    "loan_remaining": sum(l.remaining_rial for l in loans)})
    return out


def update_employee(sess, user, employee_id: int, payload: dict, ip: str = "") -> Employee:
    emp = sess.get(Employee, employee_id)
    if emp is None:
        raise DomainError("این کارمند پیدا نشد.")
    if payload.get("name"):
        emp.name = str(payload["name"]).strip()[:160]
    if payload.get("position") is not None:
        emp.position = str(payload["position"])[:120]
    if payload.get("contract_type") in CONTRACT_LABELS:
        emp.contract_type = payload["contract_type"]
    if payload.get("work_days") is not None:
        val = int(payload["work_days"])
        if not (1 <= val <= 31):
            raise DomainError("روز کارکرد باید بین ۱ و ۳۱ باشد.")
        emp.work_days = val
    if payload.get("is_active") is not None:
        emp.is_active = bool(payload["is_active"])
    if payload.get("note") is not None:
        emp.note = str(payload["note"])[:255]
    svc.audit(sess, user, "employee.update", "employee", str(emp.id), str(payload), ip)
    return emp


# ------------------------------------------------------------------ مساعده و وام
def create_loan(sess, user, employee_id: int, amount: int, kind: str = "advance",
                installments: int = 1, doc_date: date | None = None, description: str = "",
                pay_now_from: int | None = None, ip: str = "") -> EmployeeLoan:
    emp = sess.get(Employee, employee_id)
    if emp is None:
        raise DomainError("این کارمند پیدا نشد.")
    if amount <= 0:
        raise DomainError("مبلغ باید بزرگ‌تر از صفر باشد.")
    if kind not in ("advance", "loan"):
        raise DomainError("نوع باید مساعده یا وام باشد.")
    installments = max(1, int(installments))
    doc_date = doc_date or jd.jtoday()
    jy = jd.jalali_parts(doc_date)[0]
    last = sess.execute(select(func.max(EmployeeLoan.number)).where(
        EmployeeLoan.number.like(f"{jy}-و-%"))).scalar()
    seq = int(last.split("-")[-1]) + 1 if last else 1
    loan = EmployeeLoan(number=f"{jy}-و-{seq:06d}", employee_id=employee_id, kind=kind,
                        doc_date=doc_date, amount_rial=amount, installments=installments,
                        description=description[:255], created_by=user.id)
    sess.add(loan)
    sess.flush()
    if pay_now_from:
        acc = sess.get(__import__("app.models", fromlist=["Account"]).Account, pay_now_from)
        if acc is None or acc.report_group not in ("cash", "bank"):
            raise DomainError("حساب پرداخت باید صندوق یا بانک باشد.")
        emp_acc = svc.account_by_code(sess, SYSTEM_CODES["employee_loan"])
        if emp_acc is None:
            raise DomainError("حساب «مساعده و وام کارکنان» در کدینگ پیدا نشد.")
        entry = svc.create_draft(sess, user, doc_date,
                                 f"پرداخت {'مساعده' if kind == 'advance' else 'وام'} به {emp.name}",
                                 [{"account_id": emp_acc.id, "debit": amount, "credit": 0,
                                   "description": f"{emp.name} — {loan.number}"},
                                  {"account_id": acc.id, "debit": 0, "credit": amount,
                                   "description": f"پرداخت از {acc.name}"}],
                                 source="system", source_ref=loan.number, ip=ip)
        svc.post_entry(sess, user, entry.id, ip)
        loan.entry_id = entry.id
        loan.paid_out_rial = amount
    sess.flush()
    svc.audit(sess, user, "loan.create", "loan", str(loan.id),
              f"{emp.name} — {amount:,} ریال", ip)
    return loan


def list_loans(sess, employee_id: int | None = None, status: str | None = "open") -> list[dict]:
    q = select(EmployeeLoan).order_by(EmployeeLoan.doc_date.desc(), EmployeeLoan.id.desc())
    if employee_id:
        q = q.where(EmployeeLoan.employee_id == employee_id)
    if status and status != "all":
        q = q.where(EmployeeLoan.status == status)
    emps = {e.id: e.name for e in sess.execute(select(Employee)).scalars()}
    return [{"id": l.id, "number": l.number, "employee": emps.get(l.employee_id, "?"),
             "employee_id": l.employee_id, "kind": l.kind,
             "kind_label": "مساعده" if l.kind == "advance" else "وام",
             "date": jd.jalali_str(l.doc_date), "amount": l.amount_rial,
             "paid": l.paid_rial, "remaining": l.remaining_rial, "monthly": l.monthly_rial,
             "installments": l.installments, "paid_out": l.paid_out_rial,
             "status": l.status, "description": l.description} for l in sess.execute(q).scalars()]


def cancel_loan(sess, user, loan_id: int, reason: str = "", ip: str = "") -> EmployeeLoan:
    loan = sess.get(EmployeeLoan, loan_id)
    if loan is None:
        raise DomainError("این مساعده/وام پیدا نشد.")
    if loan.paid_rial > 0:
        raise DomainError("از این مساعده/وام در لیست حقوق کسر شده است؛ اول آن لیست را برگردانید.")
    if loan.entry_id:
        svc.reverse_entry(sess, user, loan.entry_id, reason or "ابطال مساعده/وام", ip)
    loan.status = "cancelled"
    svc.audit(sess, user, "loan.cancel", "loan", str(loan.id), reason, ip)
    return loan


# ------------------------------------------------------------------ محاسبه
def _bracket_tax(taxable_monthly: int, brackets: list[dict], mode: str) -> int:
    """مالیات پله‌ای. مبالغ همیشه ریال و ماهانه‌اند؛ پله‌ها هم ماهانه تعریف می‌شوند."""
    if taxable_monthly <= 0:
        return 0
    tax = 0
    lower = 0
    for b in brackets:
        up_to = b.get("up_to")
        rate = int(b.get("rate_bp") or 0)
        if up_to is None:
            slice_amount = max(0, taxable_monthly - lower)
        else:
            slice_amount = max(0, min(taxable_monthly, int(up_to)) - lower)
        tax += slice_amount * rate // 10000
        if up_to is None or taxable_monthly <= int(up_to):
            break
        lower = int(up_to)
    return tax


def compute_employee(sess, emp: Employee, settings: PayrollSettings, pay_items: dict,
                     overtime_hours: int = 0, extra_earnings: list[dict] | None = None,
                     other_deduction: int = 0, loan_deduction: int = 0) -> dict:
    """محاسبه‌ی یک کارمند. همه‌ی قاعده‌ها از روی «قلم» و «اعداد سال» خوانده می‌شود."""
    rows = sess.execute(select(EmployeePayItem).where(EmployeePayItem.employee_id == emp.id,
                                                      EmployeePayItem.is_active.is_(True))).scalars()
    earnings, insurable_base, taxable_base = [], 0, 0
    for r in rows:
        item = pay_items.get(r.pay_item_id)
        if item is None or item.kind != "earning" or not item.is_active:
            continue
        amount = r.amount_rial
        earnings.append({"code": item.code, "title": item.title, "amount": amount,
                         "insurable": item.insurable, "taxable": item.taxable})
        if item.insurable:
            insurable_base += amount
        if item.taxable:
            taxable_base += max(0, amount - item.tax_exempt_monthly_rial)

    # اضافه‌کاری: از حقوق پایه و ضریب همان سال حساب می‌شود
    base_amount = next((e["amount"] for e in earnings if e["code"] == "BASE"), 0)
    overtime_amount = 0
    if overtime_hours > 0:
        if base_amount <= 0:
            raise DomainError(f"{emp.name}: برای اضافه‌کاری، اول حقوق پایه را در حکم حقوقی ثبت کنید.")
        hourly = base_amount // max(1, settings.standard_monthly_hours)
        overtime_amount = hourly * int(overtime_hours) * settings.overtime_coefficient_bp // 10000
        earnings.append({"code": "OVERTIME", "title": f"اضافه‌کاری ({overtime_hours} ساعت)",
                         "amount": overtime_amount, "insurable": True, "taxable": True})
        insurable_base += overtime_amount
        taxable_base += overtime_amount

    for extra in (extra_earnings or []):
        item = pay_items.get(extra.get("pay_item_id"))
        if item is None or item.kind != "earning":
            continue
        amount = int(extra.get("amount") or 0)
        if amount <= 0:
            continue
        earnings.append({"code": item.code, "title": item.title, "amount": amount,
                         "insurable": item.insurable, "taxable": item.taxable})
        if item.insurable:
            insurable_base += amount
        if item.taxable:
            taxable_base += max(0, amount - item.tax_exempt_monthly_rial)

    gross = sum(e["amount"] for e in earnings)
    brackets = json.loads(settings.tax_brackets_json or "[]")
    tax = _bracket_tax(taxable_base, brackets, settings.tax_mode)
    insurance_employee = insurable_base * settings.insurance_employee_bp // 10000
    insurance_employer = insurable_base * settings.insurance_employer_bp // 10000
    unemployment = insurable_base * settings.unemployment_bp // 10000

    loan_deduction = max(0, min(int(loan_deduction), max(0, gross - insurance_employee - tax)))
    other_deduction = max(0, int(other_deduction))
    net = gross - insurance_employee - tax - loan_deduction - other_deduction
    if net < 0:
        raise DomainError(f"{emp.name}: کسورات از حقوق بیشتر شده است؛ مبالغ را بازبینی کنید.")
    return {
        "employee_id": emp.id, "employee": emp.name, "code": emp.code,
        "work_days": emp.work_days, "earnings": earnings, "overtime_hours": overtime_hours,
        "gross": gross, "insurable_base": insurable_base, "taxable_base": taxable_base,
        "insurance_employee": insurance_employee, "insurance_employer": insurance_employer,
        "unemployment": unemployment, "tax": tax, "loan": loan_deduction,
        "other_deduction": other_deduction, "net": net,
    }


def _open_loan_deduction(sess, employee_id: int) -> tuple[int, list[EmployeeLoan]]:
    rows = sess.execute(select(EmployeeLoan).where(EmployeeLoan.employee_id == employee_id,
                                                   EmployeeLoan.status == "open")).scalars()
    total, used = 0, []
    for l in rows:
        amount = min(l.monthly_rial, l.remaining_rial)
        if amount > 0:
            total += amount
            used.append(l)
    return total, used


def create_run(sess, user, jy: int, jm: int, employees: list[int] | None = None,
               overtime: dict | None = None, ip: str = "") -> PayrollRun:
    """لیست حقوق ماه را می‌سازد (پیش‌نویس). هر بار می‌شود دوباره حسابش کرد."""
    if not (1 <= jm <= 12):
        raise DomainError("ماه درست نیست.")
    existing = sess.execute(select(PayrollRun).where(PayrollRun.jy == jy, PayrollRun.jm == jm)
                            ).scalar_one_or_none()
    if existing and existing.status == "posted":
        raise DomainError(f"لیست حقوق {jd.MONTHS[jm - 1]} {jy} ثبت شده است؛ اول آن را برگردانید.")
    if existing and existing.status == "paid":
        raise DomainError(f"حقوق {jd.MONTHS[jm - 1]} {jy} پرداخت شده است؛ اول لیست را برگردانید.")
    settings, fallback = settings_for_year(sess, jy)
    pay_items = {i.id: i for i in sess.execute(select(PayItem)).scalars()}
    q = select(Employee)
    if employees:
        q = q.where(Employee.id.in_(employees))
    rows = [e for e in sess.execute(q.order_by(Employee.name)).scalars() if e.is_active]
    if not rows:
        raise DomainError("کارمند فعالی برای محاسبه‌ی حقوق نیست.")

    overtime = overtime or {}
    if existing:
        run = existing
        sess.query(PayrollItem).filter(PayrollItem.run_id == run.id).delete()
        # اگر لیست قبلاً ابطال شده بود، حالا از نو محاسبه می‌شود و به وضعیت پیش‌نویس برمی‌گردد
        run.status = "draft"
        run.entry_id = None
        run.payment_entry_id = None
        run.posted_at = None
        sess.flush()
    else:
        run = PayrollRun(jy=jy, jm=jm, status="draft", created_by=user.id)
        sess.add(run)
        sess.flush()
    if not run.number:
        last = sess.execute(select(func.max(PayrollRun.number)).where(
            PayrollRun.number.like(f"{jy}-ح-%"))).scalar()
        seq = int(last.split("-")[-1]) + 1 if last else 1
        run.number = f"{jy}-ح-{seq:06d}"
    run.settings_id = settings.id

    totals = {"gross": 0, "ins_emp": 0, "ins_er": 0, "unemp": 0, "tax": 0, "loan": 0, "net": 0}
    for emp in rows:
        loan_amount, loan_rows = _open_loan_deduction(sess, emp.id)
        calc = compute_employee(sess, emp, settings, pay_items,
                                overtime_hours=int(overtime.get(str(emp.id), 0) or 0),
                                loan_deduction=loan_amount)
        item = PayrollItem(
            run_id=run.id, employee_id=emp.id, work_days=emp.work_days,
            earnings_json=json.dumps(calc["earnings"], ensure_ascii=False),
            overtime_hours=calc["overtime_hours"], gross_rial=calc["gross"],
            insurable_base_rial=calc["insurable_base"], taxable_base_rial=calc["taxable_base"],
            insurance_employee_rial=calc["insurance_employee"],
            insurance_employer_rial=calc["insurance_employer"],
            unemployment_rial=calc["unemployment"], tax_rial=calc["tax"],
            loan_rial=calc["loan"], other_deduction_rial=calc["other_deduction"],
            net_rial=calc["net"])
        sess.add(item)
        for key, value in (("gross", calc["gross"]), ("ins_emp", calc["insurance_employee"]),
                           ("ins_er", calc["insurance_employer"]), ("unemp", calc["unemployment"]),
                           ("tax", calc["tax"]), ("loan", calc["loan"]), ("net", calc["net"])):
            totals[key] += value
    run.gross_rial = totals["gross"]
    run.insurance_employee_rial = totals["ins_emp"]
    run.insurance_employer_rial = totals["ins_er"]
    run.unemployment_rial = totals["unemp"]
    run.tax_rial = totals["tax"]
    run.loan_rial = totals["loan"]
    run.net_rial = totals["net"]
    sess.flush()
    svc.audit(sess, user, "payroll.calculate", "payroll_run", str(run.id),
              f"{jd.MONTHS[jm - 1]} {jy} — {len(rows)} کارمند — خالص {format_amount_fa(run.net_rial)}", ip)
    if fallback:
        run.__dict__["used_fallback_settings"] = True
    return run


def _run_entry_lines(sess, run: PayrollRun) -> list[dict]:
    salary_acc = svc.account_by_code(sess, SYSTEM_CODES["salary_expense"])
    ins_acc = svc.account_by_code(sess, SYSTEM_CODES["insurance_payable"])
    tax_acc = svc.account_by_code(sess, SYSTEM_CODES["salary_tax_payable"])
    pay_acc = svc.account_by_code(sess, SYSTEM_CODES["salary_payable"])
    loan_acc = svc.account_by_code(sess, SYSTEM_CODES["employee_loan"])
    employer_total = run.insurance_employer_rial + run.unemployment_rial
    lines = [{"account_id": salary_acc.id, "debit": run.gross_rial + employer_total, "credit": 0,
              "description": f"حقوق {jd.MONTHS[run.jm - 1]} {run.jy} — {run.number}"}]
    insurance_total = run.insurance_employee_rial + employer_total
    if insurance_total:
        lines.append({"account_id": ins_acc.id, "debit": 0, "credit": insurance_total,
                      "description": "بیمه سهم کارمند و کارفرما"})
    if run.tax_rial:
        lines.append({"account_id": tax_acc.id, "debit": 0, "credit": run.tax_rial,
                      "description": "مالیات حقوق"})
    if run.loan_rial:
        lines.append({"account_id": loan_acc.id, "debit": 0, "credit": run.loan_rial,
                      "description": "کسر مساعده و وام"})
    lines.append({"account_id": pay_acc.id, "debit": 0, "credit": run.net_rial,
                  "description": "خالص حقوق قابل پرداخت"})
    return lines


def post_run(sess, user, run_id: int, ip: str = "") -> PayrollRun:
    run = sess.get(PayrollRun, run_id)
    if run is None:
        raise DomainError("این لیست حقوق پیدا نشد.")
    if run.status == "posted":
        return run
    if run.status != "draft":
        raise DomainError("این لیست در وضعیت قابل ثبت نیست.")
    if run.gross_rial <= 0:
        raise DomainError("لیست حقوق خالی است.")
    entry = svc.create_draft(sess, user, _doc_date_for(run.jy, run.jm),
                            f"سند حقوق {jd.MONTHS[run.jm - 1]} {run.jy} — {run.number}",
                            _run_entry_lines(sess, run), source="payroll",
                            source_ref=run.number, ip=ip)
    svc.post_entry(sess, user, entry.id, ip)
    run.entry_id = entry.id
    run.status = "posted"
    run.posted_at = __import__("datetime").datetime.now()
    # کسر مساعده/وام فقط وقتی ثبت می‌شود که سند حقوق نهایی شده باشد
    for item in run.items:
        if item.loan_rial:
            remaining = item.loan_rial
            for loan in sess.execute(select(EmployeeLoan).where(
                    EmployeeLoan.employee_id == item.employee_id,
                    EmployeeLoan.status == "open")).scalars():
                if remaining <= 0:
                    break
                amount = min(loan.monthly_rial, loan.remaining_rial, remaining)
                loan.paid_rial += amount
                remaining -= amount
                if loan.remaining_rial == 0:
                    loan.status = "closed"
    sess.flush()
    svc.audit(sess, user, "payroll.post", "payroll_run", str(run.id), run.number, ip)
    return run


def pay_run(sess, user, run_id: int, account_id: int, doc_date: date | None = None,
            ip: str = "") -> PayrollRun:
    run = sess.get(PayrollRun, run_id)
    if run is None:
        raise DomainError("این لیست حقوق پیدا نشد.")
    if run.status != "posted":
        raise DomainError("اول سند حقوق ثبت شود، بعد پرداخت.")
    if run.payment_entry_id:
        raise DomainError("حقوق این ماه قبلاً پرداخت شده است.")
    acc = sess.get(__import__("app.models", fromlist=["Account"]).Account, account_id)
    if acc is None or acc.report_group not in ("cash", "bank"):
        raise DomainError("حساب پرداخت باید صندوق یا بانک باشد.")
    pay_acc = svc.account_by_code(sess, SYSTEM_CODES["salary_payable"])
    when = doc_date or _doc_date_for(run.jy, run.jm)
    entry = svc.create_draft(sess, user, when,
                             f"پرداخت حقوق {jd.MONTHS[run.jm - 1]} {run.jy} — {run.number} از {acc.name}",
                             [{"account_id": pay_acc.id, "debit": run.net_rial, "credit": 0,
                               "description": f"تسویه حقوق {run.number}"},
                              {"account_id": acc.id, "debit": 0, "credit": run.net_rial,
                               "description": f"پرداخت از {acc.name}"}],
                             source="payroll", source_ref=run.number, ip=ip)
    svc.post_entry(sess, user, entry.id, ip)
    run.payment_entry_id = entry.id
    run.status = "paid"
    sess.flush()
    svc.audit(sess, user, "payroll.pay", "payroll_run", str(run.id),
              f"{run.number} — {format_amount_fa(run.net_rial)} ریال", ip)
    return run


def cancel_run(sess, user, run_id: int, reason: str = "", ip: str = "") -> PayrollRun:
    run = sess.get(PayrollRun, run_id)
    if run is None:
        raise DomainError("این لیست حقوق پیدا نشد.")
    if run.status == "cancelled":
        raise DomainError("این لیست حقوق قبلاً برگردانده شده است.")
    if run.status == "draft":
        raise DomainError("این لیست هنوز ثبت نشده؛ برگرداندنی ندارد.")
    if run.payment_entry_id:
        svc.reverse_entry(sess, user, run.payment_entry_id, reason or "ابطال پرداخت حقوق", ip)
    if run.entry_id:
        svc.reverse_entry(sess, user, run.entry_id, reason or "ابطال سند حقوق", ip)
    for item in run.items:
        if item.loan_rial:
            remaining = item.loan_rial
            for loan in sess.execute(select(EmployeeLoan).where(
                    EmployeeLoan.employee_id == item.employee_id)).scalars():
                if remaining <= 0:
                    break
                amount = min(loan.paid_rial, remaining)
                loan.paid_rial -= amount
                remaining -= amount
                if loan.paid_rial < loan.amount_rial and loan.status == "closed":
                    loan.status = "open"
    run.status = "cancelled"
    run.payment_entry_id = None
    sess.flush()
    svc.audit(sess, user, "payroll.cancel", "payroll_run", str(run.id), reason, ip)
    return run


def _month_end(jy: int, jm: int) -> date:
    _, last = jd.daterange_month(jy, jm)
    return last


def _doc_date_for(jy: int, jm: int) -> date:
    """تاریخ سند حقوق: پایان ماه؛ ولی اگر ماه هنوز تمام نشده، امروز (سند نباید در آینده باشد)."""
    end = _month_end(jy, jm)
    today = jd.jtoday()
    return end if end <= today else today


# ------------------------------------------------------------------ گزارش و فیش
def list_runs(sess, limit: int = 60) -> list[dict]:
    return [{"id": r.id, "number": r.number, "period": f"{jd.MONTHS[r.jm - 1]} {r.jy}",
             "jy": r.jy, "jm": r.jm, "status": r.status, "employees": len(r.items),
             "gross": r.gross_rial, "insurance_employee": r.insurance_employee_rial,
             "insurance_employer": r.insurance_employer_rial + r.unemployment_rial,
             "tax": r.tax_rial, "loan": r.loan_rial, "net": r.net_rial,
             "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""}
            for r in sess.execute(select(PayrollRun).order_by(PayrollRun.jy.desc(),
                                                              PayrollRun.jm.desc()).limit(limit)).scalars()]


def run_detail(sess, run_id: int) -> dict:
    run = sess.get(PayrollRun, run_id)
    if run is None:
        raise DomainError("این لیست حقوق پیدا نشد.")
    settings, fallback = settings_for_year(sess, run.jy)
    rows = []
    for item in run.items:
        try:
            earnings = json.loads(item.earnings_json or "[]")
        except Exception:  # noqa: BLE001
            earnings = []
        rows.append({
            "employee_id": item.employee_id, "employee": item.employee.name,
            "code": item.employee.code, "work_days": item.work_days,
            "earnings": earnings, "overtime_hours": item.overtime_hours,
            "gross": item.gross_rial, "insurable_base": item.insurable_base_rial,
            "taxable_base": item.taxable_base_rial,
            "insurance_employee": item.insurance_employee_rial,
            "insurance_employer": item.insurance_employer_rial,
            "unemployment": item.unemployment_rial, "tax": item.tax_rial,
            "loan": item.loan_rial, "other_deduction": item.other_deduction_rial,
            "net": item.net_rial,
        })
    rules = {"insurance_employee_percent": settings.insurance_employee_bp / 100,
             "insurance_employer_percent": (settings.insurance_employer_bp + settings.unemployment_bp) / 100,
             "brackets": json.loads(settings.tax_brackets_json or "[]"),
             "settings_year": settings.year, "confirmed": settings.is_confirmed,
             "fell_back": fallback}
    return {"id": run.id, "number": run.number, "period": f"{jd.MONTHS[run.jm - 1]} {run.jy}",
            "jy": run.jy, "jm": run.jm, "status": run.status, "rows": rows,
            "totals": {"gross": run.gross_rial, "insurance_employee": run.insurance_employee_rial,
                       "insurance_employer": run.insurance_employer_rial + run.unemployment_rial,
                       "tax": run.tax_rial, "loan": run.loan_rial, "net": run.net_rial,
                       "employer_cost": run.gross_rial + run.insurance_employer_rial + run.unemployment_rial},
            "rules": rules}


def payslip(sess, run_id: int, employee_id: int) -> dict:
    d = run_detail(sess, run_id)
    row = next((r for r in d["rows"] if r["employee_id"] == employee_id), None)
    if row is None:
        raise DomainError("این کارمند در این لیست نیست.")
    return {"run": {"number": d["number"], "period": d["period"], "status": d["status"]},
            "employee": {"id": row["employee_id"], "name": row["employee"], "code": row["code"]},
            "work_days": row["work_days"], "earnings": row["earnings"],
            "gross": row["gross"], "insurance_employee": row["insurance_employee"],
            "insurance_employer": row["insurance_employer"], "unemployment": row["unemployment"],
            "tax": row["tax"], "loan": row["loan"], "other_deduction": row["other_deduction"],
            "net": row["net"]}
