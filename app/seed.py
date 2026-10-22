"""راه‌اندازی اولیه: کدینگ حساب‌های استاندارد، کاربران و چند سند نمونه.

این فایل فقط یک بار اجرا می‌شود (وقتی هنوز هیچ کاربری وجود ندارد) و بعد از آن کاری
به داده‌های واقعی ندارد.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select

from . import config, dates as jd, moadian
from .db import init_db, session_scope
from .models import Account, JournalEntry, MoadianServiceMap, Trip, User
from .rules import SYSTEM_CODES
from .security import ROLE_LABELS, hash_password, verify_password
from . import assets as assets_mod, payroll, sales, services as svc

# (کد، نام، گروهی/تفصیلی، ماهیت، حساب والد، دسته‌ی گزارشی، حسابی از خود برنامه)
CHART = [
    ("1", "دارایی‌ها", "group", "debit", None, "asset", False),
    ("11", "دارایی‌های جاری", "group", "debit", "1", "asset", False),
    ("1101", "صندوق", "ledger", "debit", "11", "cash", True),
    ("1102", "بانک‌ها", "group", "debit", "11", "asset", False),
    ("110201", "بانک ملت — حساب جاری", "ledger", "debit", "1102", "bank", True),
    ("110202", "بانک صادرات — حساب جاری", "ledger", "debit", "1102", "bank", False),
    ("1103", "حساب‌های دریافتنی از مشتریان", "ledger", "debit", "11", "receivable", True),
    ("1104", "چک‌های دریافتی", "ledger", "debit", "11", "cheque_received", True),
    ("1106", "چک‌های نزد بانک — در راه وصول", "ledger", "debit", "11", "cheque_received", True),
    ("1105", "پیش‌پرداخت به تأمین‌کنندگان", "ledger", "debit", "11", "prepaid_supplier", True),
    ("1107", "مالیات بر ارزش افزوده — اعتبار مالیاتی", "ledger", "debit", "11", "vat", True),
    ("1108", "مساعده و وام کارکنان", "ledger", "debit", "11", "employee_loan", True),

    ("1109", "دارایی‌های ثابت", "group", "debit", "11", "asset", False),
    ("110901", "دارایی ثابت — اثاثه، تجهیزات و رایانه", "ledger", "debit", "1109", "asset", True),
    ("110902", "دارایی ثابت — خودرو", "ledger", "debit", "1109", "asset", True),
    ("110903", "دارایی ثابت — ساختمان و تأسیسات", "ledger", "debit", "1109", "asset", True),
    ("1110", "استهلاک انباشته‌ی دارایی‌های ثابت", "group", "credit", "11", "asset", False),
    ("111001", "استهلاک انباشته — اثاثه، تجهیزات و رایانه", "ledger", "credit", "1110", "asset", True),
    ("111002", "استهلاک انباشته — خودرو", "ledger", "credit", "1110", "asset", True),
    ("111003", "استهلاک انباشته — ساختمان و تأسیسات", "ledger", "credit", "1110", "asset", True),

    ("2", "بدهی‌ها", "group", "credit", None, "liability", False),
    ("21", "بدهی‌های جاری", "group", "credit", "2", "liability", False),
    ("2101", "حساب‌های پرداختنی به تأمین‌کنندگان", "ledger", "credit", "21", "payable", True),
    ("2102", "چک‌های پرداختی", "ledger", "credit", "21", "cheque_paid", True),
    ("2103", "پیش‌دریافت از مشتریان", "ledger", "credit", "21", "advance_customer", True),
    ("2104", "مالیات بر ارزش افزوده — قابل پرداخت", "ledger", "credit", "21", "vat", True),
    ("2105", "بیمه پرداختنی", "ledger", "credit", "21", "liability", True),
    ("2106", "مالیات حقوق پرداختنی", "ledger", "credit", "21", "liability", True),
    ("2107", "حقوق پرداختنی", "ledger", "credit", "21", "liability", True),
    ("2108", "بدهی امانی / تسویه‌نشده‌ی ایرلاین", "ledger", "credit", "21", "liability", True),

    ("3", "حقوق مالکانه", "group", "credit", None, "equity", False),
    ("3101", "سرمایه", "ledger", "credit", "3", "equity", True),
    ("3102", "سود و زیان انباشته", "ledger", "credit", "3", "equity", True),
    ("3103", "برداشت مالک", "ledger", "debit", "3", "equity", False),

    ("4", "درآمدها", "group", "credit", None, "income", False),
    ("4101", "درآمد فروش تور", "ledger", "credit", "4", "income", True),
    ("4102", "درآمد فروش بلیت", "ledger", "credit", "4", "income", True),
    ("4103", "درآمد خدمات ویزا و سایر خدمات", "ledger", "credit", "4", "income", True),
    ("4104", "برگشت از فروش و تخفیف‌ها", "ledger", "debit", "4", "income", True),
    ("4105", "درآمد کمیسیون", "ledger", "credit", "4", "income", True),
    ("4106", "سود فروش دارایی", "ledger", "credit", "4", "income", True),
    ("4107", "درآمد جریمه از مشتری — لغو خدمات", "ledger", "credit", "4", "income", True),
    ("4108", "درآمد جریمه از تامین‌کننده — لغو خرید", "ledger", "credit", "4", "income", True),
    ("4109", "تخفیف نقدی خرید", "ledger", "credit", "4", "income", True),

    ("5", "هزینه‌ها", "group", "debit", None, "expense", False),
    ("5101", "بهای تمام‌شده‌ی خدمات", "ledger", "debit", "5", "expense", True),
    ("5102", "حقوق و دستمزد", "ledger", "debit", "5", "expense", True),
    ("5103", "اجاره", "ledger", "debit", "5", "expense", True),
    ("5104", "تبلیغات و بازاریابی", "ledger", "debit", "5", "expense", True),
    ("5105", "هزینه‌های عمومی و اداری", "ledger", "debit", "5", "expense", True),
    ("5106", "کارمزد بانکی", "ledger", "debit", "5", "expense", True),
    ("5107", "استهلاک", "ledger", "debit", "5", "expense", True),
    ("5108", "زیان فروش دارایی", "ledger", "debit", "5", "expense", True),
    ("5109", "جریمه و کنسلی تأمین‌کنندگان", "ledger", "debit", "5", "expense", True),
]

DEMO_USERS = [
    ("admin", "مدیر سیستم", "admin"),
    ("manager", "مدیر آژانس", "manager"),
    ("accountant", "حسابدار آژانس", "accountant"),
    ("employee", "کارمند فروش", "employee"),
    ("auditor", "حسابرس بیرونی", "auditor"),
]


def _write_demo_credentials(creds: dict[str, str]) -> None:
    path = config.DATA_DIR / "DEMO-credentials.txt"
    lines = [
        "حساب‌های نسخه‌ی نمایشی — این رمزهای ساده فقط برای آزمون‌اند؛ در سرور واقعی از آن‌ها استفاده نکنید.",
        "اگر رمز یکی از حساب‌ها را در برنامه تغییر دادید، فایلِ اولیه دیگر رمز جاری را نشان نمی‌دهد.",
        "",
    ]
    lines += [f"{u}  →  {creds[u]}   ({ROLE_LABELS.get(r, r)})"
              for u, _, r in DEMO_USERS if u in creds]
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _read_legacy_demo_credentials(path) -> dict[str, str]:
    """رمزهای نسخه‌ی قدیمی را می‌خواند تا گذار، رمزهایی را که کاربر عوض کرده دست‌کاری نکند."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}
    allowed = {username for username, _full_name, _role in DEMO_USERS}
    found: dict[str, str] = {}
    for line in lines:
        if "→" not in line:
            continue
        username, remainder = line.split("→", 1)
        username = username.strip()
        password = remainder.strip().split(maxsplit=1)
        if username in allowed and password:
            found[username] = password[0]
    return found


def _mark_demo_passwords_migrated() -> None:
    marker = config.DATA_DIR / ".demo-passwords-username123-v1"
    marker.write_text("demo credentials migrated\n", encoding="ascii")
    try:
        marker.chmod(0o600)
    except OSError:
        pass


def _chart_account_fields(code: str, kind: str, report_group: str) -> dict:
    """سطح اولیه را از ساختار کدینگ موجود تعیین می‌کند، بدون تغییر حساب‌های دفتر قدیمی."""
    if kind == "group":
        level = "group" if len(code) <= 1 else ("general" if len(code) <= 2 else "subsidiary")
    else:
        level = "detail" if "-" in code or len(code) > 5 else "subsidiary"
    return {"level": level, "group_type": svc.group_type_for(report_group),
            "report_type": svc.report_type_for(report_group)}


def seed_accounts(sess) -> None:
    """کدینگ را می‌سازد؛ حساب‌های سیستمی جاافتاده را هم (بدون دست‌زدن به بقیه) اضافه می‌کند."""
    exists = {a.code: a for a in sess.execute(select(Account)).scalars()}
    if not exists:
        for code, name, kind, nature, parent, group, is_sys in CHART:
            sess.add(Account(code=code, name=name, kind=kind, nature=nature, parent_code=parent,
                             report_group=group, is_system=is_sys,
                             **_chart_account_fields(code, kind, group)))
        sess.flush()
        return
    for code, name, kind, nature, parent, group, is_sys in CHART:
        if code in exists:
            continue
        if parent and parent not in exists and len(parent) > 1:
            continue
        sess.add(Account(code=code, name=name, kind=kind, nature=nature, parent_code=parent,
                         report_group=group, is_system=is_sys,
                         **_chart_account_fields(code, kind, group)))
        exists[code] = True
    sess.flush()


def seed_users(sess, write_credentials: bool = True) -> dict[str, str]:
    existing = {u.username: u for u in sess.execute(select(User)).scalars()}
    if existing:
        # یک‌بار هنگام ارتقای نصب نمایشی قدیمی، رمزهای تصادفی چاپ‌شده در run-windows
        # را به الگوی ساده‌ی خواسته‌شده تبدیل می‌کنیم. marker مانع reset رمزها در اجرای بعدی است.
        credentials_file = config.DATA_DIR / "DEMO-credentials.txt"
        marker = config.DATA_DIR / ".demo-passwords-username123-v1"
        if write_credentials and config.DEMO_SEED and credentials_file.is_file() and not marker.exists():
            old_passwords = _read_legacy_demo_credentials(credentials_file)
            defaults = {username: username + "123" for username, _, _ in DEMO_USERS
                        if username in existing}
            already_migrated = all(verify_password(password, existing[username].password_hash)
                                   for username, password in defaults.items())
            if not old_passwords and not already_migrated:
                return {}
            for username, password in defaults.items():
                user = existing[username]
                if verify_password(password, user.password_hash):
                    continue  # قبلاً همین رمز را داشت؛ فقط فایل/marker را یک‌دست می‌کنیم.
                legacy = old_passwords.get(username)
                if legacy and verify_password(legacy, user.password_hash):
                    user.password_hash = hash_password(password)
                # اگر کاربر قبلاً رمز خودش را گذاشته، آن را حفظ می‌کنیم.
            _write_demo_credentials(defaults)
            _mark_demo_passwords_migrated()
            sess.flush()
        return {}

    creds: dict[str, str] = {}
    for username, full_name, role in DEMO_USERS:
        password = username + "123"
        creds[username] = password
        sess.add(User(username=username, full_name=full_name, role=role,
                      password_hash=hash_password(password), must_change_password=False))
    sess.flush()
    if write_credentials:
        _write_demo_credentials(creds)
        _mark_demo_passwords_migrated()
    return creds


def seed_production_admin(sess) -> None:
    """ساخت تنها مدیر اولیه از اسرار محیط؛ هیچ حساب نمایشی در سرور تولیدی ساخته نمی‌شود."""
    if sess.execute(select(User)).first():
        return
    username = config.BOOTSTRAP_ADMIN_USERNAME
    password = config.BOOTSTRAP_ADMIN_PASSWORD
    if not username or not password:
        raise RuntimeError("برای پایگاه‌داده‌ی تازه، ACCT_BOOTSTRAP_ADMIN_USERNAME و "
                           "ACCT_BOOTSTRAP_ADMIN_PASSWORD را پیش از راه‌اندازی تنظیم کنید.")
    if len(username) > 60 or not all(ch.isalnum() or ch in "._-" for ch in username):
        raise RuntimeError("نام کاربری مدیر اولیه فقط می‌تواند حرف، عدد، نقطه، خط تیره و زیرخط داشته باشد.")
    from .security import password_problem
    problem = password_problem(password)
    if problem:
        raise RuntimeError("رمز مدیر اولیه مناسب نیست: " + problem)
    sess.add(User(username=username, full_name="مدیر سامانه", role="admin",
                  password_hash=hash_password(password), must_change_password=True))
    sess.flush()


def _acc(sess, key: str) -> Account:
    return svc.account_by_code(sess, SYSTEM_CODES[key])


def seed_sample_entries(sess) -> None:
    if sess.execute(select(JournalEntry)).first():
        return
    today = jd.jtoday()
    jy, jm, _ = jd.jalali_parts(today)
    m_start, m_end = jd.daterange_month(jy, jm)
    base = min(today, m_end) - timedelta(days=6)
    if base < m_start:
        base = m_start
    admin = sess.execute(select(User).where(User.role == "accountant")).scalar_one()

    def d(n: int) -> date:
        return base + timedelta(days=n)

    # ۱) پیش‌دریافت از مشتری بابت یک تور
    svc.create_draft(sess, admin, d(0), "پیش‌دریافت از آقای رضایی بابت تور آنتالیا",
                     [{"account_id": _acc(sess, "bank").id, "debit": 250_000_000},
                      {"account_id": _acc(sess, "advance_customer").id, "credit": 250_000_000}],
                     source="receipt")
    # ۲) فروش تور با مالیات بر ارزش افزوده ۹٪
    svc.create_draft(sess, admin, d(1), "فاکتور فروش تور آنتالیا — مسافر: آقای رضایی",
                     [{"account_id": _acc(sess, "receivable").id, "debit": 436_000_000},
                      {"account_id": _acc(sess, "revenue_tour").id, "credit": 400_000_000},
                      {"account_id": _acc(sess, "vat_payable").id, "credit": 36_000_000}],
                     source="system", source_ref="sample")
    # ۳) تسویه‌ی همان فاکتور
    svc.create_draft(sess, admin, d(2), "تسویه فاکتور تور آنتالیا",
                     [{"account_id": _acc(sess, "bank").id, "debit": 436_000_000},
                      {"account_id": _acc(sess, "receivable").id, "credit": 436_000_000}],
                     source="receipt")
    # ۴) خرید خدمات از تأمین‌کننده (هتل) — بدهکار شدیم
    svc.create_draft(sess, admin, d(2), "خرید اقامت تور آنتالیا از هتل آکوا",
                     [{"account_id": _acc(sess, "cogs").id, "debit": 250_000_000},
                      {"account_id": _acc(sess, "payable").id, "credit": 250_000_000}],
                     source="system")
    # ۵) پرداخت به تأمین‌کننده: بخشی نقد، بخشی با چک
    svc.create_draft(sess, admin, d(3), "پرداخت به هتل آکوا — نقد و چک",
                     [{"account_id": _acc(sess, "payable").id, "debit": 250_000_000},
                      {"account_id": _acc(sess, "bank").id, "credit": 150_000_000},
                      {"account_id": _acc(sess, "cheque_paid").id, "credit": 100_000_000}],
                     source="payment")
    # ۶) اجاره‌ی دفتر
    svc.create_draft(sess, admin, d(4), "اجاره‌ی دفتر آژانس — ماه جاری",
                     [{"account_id": _acc(sess, "rent").id, "debit": 30_000_000},
                      {"account_id": _acc(sess, "bank").id, "credit": 30_000_000}],
                     source="payment")
    # ۷) حقوق ماهانه
    svc.create_draft(sess, admin, d(5), "حقوق و دستمزد کارکنان — ماه جاری",
                     [{"account_id": _acc(sess, "salary_expense").id, "debit": 80_000_000},
                      {"account_id": _acc(sess, "salary_payable").id, "credit": 60_000_000},
                      {"account_id": _acc(sess, "salary_tax_payable").id, "credit": 8_000_000},
                      {"account_id": _acc(sess, "insurance_payable").id, "credit": 12_000_000}],
                     source="payroll")
    # ۸) یک پیش‌نویس ناتراز برای نشان دادن قاعده (عمداً ثبت نهایی نمی‌شود)
    svc.create_draft(sess, admin, d(5), "نمونه: این پیش‌نویس عمداً ناتراز است تا قاعده را ببینید",
                     [{"account_id": _acc(sess, "admin").id, "debit": 10_000_000},
                      {"account_id": _acc(sess, "bank").id, "credit": 9_000_000}],
                     source="manual")

    for e in sess.execute(select(JournalEntry)).scalars():
        if e.status == "draft" and "ناتراز" not in e.description:
            try:
                svc.post_entry(sess, admin, e.id)
            except Exception:  # noqa: BLE001
                pass


def seed_sales_demo(sess) -> None:
    """داده‌ی نمونه‌ی فروش: یک آژانس با دو مشتری، یک تأمین‌کننده، یک سفر، فاکتور و دریافت."""
    from .models import Party

    if sess.execute(select(Party)).first():
        return
    admin = sess.execute(select(User).where(User.role == "accountant")).scalar_one()
    sales.seed_tax_rates(sess)

    customer = sales.create_party(
        sess, admin, "آقای رضایی", "customer", "09120000000", tax={
            "person_type": 1, "national_id": "0012345678",
            "postal_code": "1518764512"})
    customer2 = sales.create_party(sess, admin, "خانم موسوی", "customer", "09350000000")
    supplier = sales.create_party(sess, admin, "هتل آکوا آنتالیا", "supplier", "")
    office = sales.create_party(sess, admin, "مالک دفتر — آقای کریمی", "supplier", "")

    today = jd.jtoday()
    trip = sales.create_trip(sess, admin, "تور آنتالیا — مهر", today - timedelta(days=10),
                             today + timedelta(days=4), "آنتالیا", "۱۴ مسافر")
    trip2 = sales.create_trip(sess, admin, "تور استانبول — آبان", today + timedelta(days=12),
                              today + timedelta(days=17), "استانبول", "ظرفیت ۲۰ نفر")

    sales.create_invoice(
        sess, admin, customer.id, today - timedelta(days=8),
        [{"service_type": "tour", "description": "تور آنتالیا، ۷ شب، ۱۴ نفر", "qty": 1,
          "unit_price": 300_000_000, "discount": 0, "taxable": True},
         {"service_type": "visa", "description": "هزینه ویزا", "qty": 1,
          "unit_price": 100_000_000, "discount": 0, "taxable": True}],
        trip_id=trip.id, description="فاکتور تور آنتالیا — آقای رضایی",
        due_date=today + timedelta(days=5), issue=True)

    sales.create_invoice(
        sess, admin, customer2.id, today - timedelta(days=3),
        [{"service_type": "ticket", "description": "بلیت تهران — استانبول (رفت و برگشت)",
          "qty": 2, "unit_price": 70_000_000, "discount": 10_000_000, "taxable": False,
          "flight_type": 2}],
        trip_id=trip2.id, description="فاکتور بلیت — خانم موسوی",
        due_date=today + timedelta(days=9), issue=True)

    sales.create_bill(sess, admin, supplier.id, today - timedelta(days=7),
                              net=180_000_000, vat=0, expense_account_id=None, trip_id=trip.id,
                              description="اقامت ۱۴ مسافر — هتل آکوا",
                              due_date=today + timedelta(days=3), issue=True)
    sales.create_bill(sess, admin, office.id, today - timedelta(days=6), net=30_000_000,
                      vat=0, expense_account_id=svc.account_by_code(sess, "5103").id,
                      description="اجاره‌ی دفتر — ماه جاری", due_date=today + timedelta(days=1),
                      issue=True)
    sales.create_bill(sess, admin, supplier.id, today - timedelta(days=2), net=60_000_000,
                      vat=0, trip_id=trip2.id, description="هتل استانبول — پیش‌پرداخت",
                      due_date=today + timedelta(days=12), issue=True)
    transfer_co = sales.create_party(sess, admin, "شرکت ترانسفر آسمان", "supplier")
    sales.create_bill(sess, admin, transfer_co.id, today - timedelta(days=1), net=20_000_000,
                      expense_account_id=svc.account_by_code(sess, "5101").id, trip_id=trip.id,
                      description="ترانسفر فرودگاهی — ۱۴ مسافر",
                      due_date=today + timedelta(days=7), issue=True)

    # نمونه‌ی سررسیدهای گذشته برای اینکه بخش «معوق» تقویم پول هم در نصب نمایشی خالی نباشد.
    sales.create_invoice(sess, admin, customer2.id, today - timedelta(days=12),
                         [{"service_type": "other", "description": "خدمت اداری نمونه برای تقویم",
                           "qty": 1, "unit_price": 12_000_000, "discount": 0, "taxable": False}],
                         description="نمونه‌ی طلب معوق تقویم پول", due_date=today - timedelta(days=2),
                         issue=True)
    sales.create_bill(sess, admin, office.id, today - timedelta(days=10), net=8_000_000,
                      vat=0, expense_account_id=svc.account_by_code(sess, "5103").id,
                      description="هزینه‌ی نمونه‌ی معوق برای تقویم پول",
                      due_date=today - timedelta(days=1), issue=True)

    sales.create_settlement(sess, admin, "in", customer.id,
                            svc.account_by_code(sess, "110201").id, 250_000_000,
                            today - timedelta(days=6), trip_id=trip.id,
                            description="پیش‌دریافت تور آنتالیا", issue=True)
    sales.create_settlement(sess, admin, "in", customer.id,
                            svc.account_by_code(sess, "110201").id, 100_000_000,
                            today - timedelta(days=2), trip_id=trip.id,
                            description="بخشی از مانده فاکتور", issue=True)
    sales.create_settlement(sess, admin, "out", supplier.id,
                            svc.account_by_code(sess, "110201").id, 120_000_000,
                            today - timedelta(days=1), trip_id=trip.id,
                            description="پرداخت به هتل آکوا", issue=True)

    sales.create_recurring(
        sess, admin, "اجاره‌ی دفتر", 5,
        [{"account_id": svc.account_by_code(sess, "5103").id, "debit": 30_000_000, "credit": 0,
          "description": "اجاره ماهانه"},
         {"account_id": svc.account_by_code(sess, "110201").id, "debit": 0, "credit": 30_000_000,
          "description": "پرداخت اجاره"}],
        description="اجاره‌ی دفتر آژانس — هر ماه")
    sales.create_recurring(
        sess, admin, "حقوق کارکنان", 28,
        [{"account_id": svc.account_by_code(sess, "5102").id, "debit": 80_000_000, "credit": 0,
          "description": "حقوق ماهانه"},
         {"account_id": svc.account_by_code(sess, "2107").id, "debit": 0, "credit": 80_000_000,
          "description": "حقوق پرداختنی"}],
        description="حقوق ماهانه‌ی کارکنان")


def seed_payroll_demo(sess) -> None:
    """داده‌ی نمونه‌ی حقوق: سه کارمند با حکم حقوقی و یک مساعده."""
    from .models import Employee

    if sess.execute(select(Employee)).first():
        return
    admin = sess.execute(select(User).where(User.role == "accountant")).scalar_one()
    items = {i.code: i for i in sess.execute(select(__import__("app.models", fromlist=["PayItem"]).PayItem)).scalars()}
    today = jd.jtoday()

    e1 = payroll.create_employee(
        sess, admin, "مریم احمدی", "کارشناس فروش بلیت", today - timedelta(days=400),
        "permanent", 30, note="نمونه: تمام‌وقت با حق اولاد")
    payroll.set_employee_items(sess, admin, e1.id, [
        {"pay_item_id": items["BASE"].id, "amount_rial": 220_000_000},
        {"pay_item_id": items["HOUSING"].id, "amount_rial": 30_000_000},
        {"pay_item_id": items["FOOD"].id, "amount_rial": 22_000_000},
        {"pay_item_id": items["CHILD"].id, "amount_rial": 17_179_735},
    ])
    e2 = payroll.create_employee(
        sess, admin, "رضا کریمی", "راهنمای تور", today - timedelta(days=220),
        "permanent", 30, note="نمونه: تمام‌وقت")
    payroll.set_employee_items(sess, admin, e2.id, [
        {"pay_item_id": items["BASE"].id, "amount_rial": 420_000_000},
        {"pay_item_id": items["HOUSING"].id, "amount_rial": 30_000_000},
        {"pay_item_id": items["FOOD"].id, "amount_rial": 22_000_000},
    ])
    e3 = payroll.create_employee(
        sess, admin, "سارا نوری", "حسابدار پاره‌وقت", today - timedelta(days=90),
        "part_time", 15, note="نمونه: پاره‌وقت")
    payroll.set_employee_items(sess, admin, e3.id, [
        {"pay_item_id": items["BASE"].id, "amount_rial": 150_000_000},
        {"pay_item_id": items["HOUSING"].id, "amount_rial": 15_000_000},
        {"pay_item_id": items["FOOD"].id, "amount_rial": 11_000_000},
    ])
    payroll.create_loan(sess, admin, e2.id, 36_000_000, "loan", 3, today - timedelta(days=20),
                        "وام نمونه — سه قسط", pay_now_from=svc.account_by_code(sess, "110201").id)


def seed_assets_demo(sess) -> None:
    """سه دارایی نمونه با سند خرید، و یک سند استهلاک دوره‌های سپری‌شده."""
    from datetime import timedelta

    from .models import AssetCategory, FixedAsset

    if sess.execute(select(FixedAsset)).first():
        return
    admin = sess.execute(select(User).where(User.role == "accountant")).scalar_one()
    cats = {c.code: c for c in sess.execute(select(AssetCategory)).scalars()}
    today = jd.jtoday()
    capital = svc.account_by_code(sess, "3101")
    bank = svc.account_by_code(sess, SYSTEM_CODES["bank"])
    samples = [
        ("رایانه و تجهیزات رایانه‌ای دفتر", "IT", 400, 120_000_000, capital),
        ("میز، صندلی و کمد اداری", "EQUIP", 380, 80_000_000, capital),
        ("دوربین و تجهیزات سفر", "EQUIP", 200, 50_000_000, bank),
    ]
    for title, cat_code, days_ago, cost, counter in samples:
        cat = cats.get(cat_code)
        if cat is None or counter is None:
            continue
        assets_mod.create_asset(
            sess, admin, title, cat.id, today - timedelta(days=days_ago),
            today - timedelta(days=days_ago), cost, 0, note="نمونه",
            counter_account_id=counter.id, ip="seed")

    # استهلاک از ماه به‌کارگیری تا پایان ماه قبل، ماه‌به‌ماه و از راه خود برنامه
    jy, jm, _ = jd.jalali_parts(today)
    y, m = (jy, jm - 1) if jm > 1 else (jy - 1, 12)
    start_y, start_m = y, m
    for _ in range(12):                      # حداکثر یک سال عقب می‌رویم
        if start_m == 1:
            break
        start_m -= 1
    while (start_y, start_m) <= (y, m):
        try:
            run = assets_mod.create_run(sess, admin, start_y, start_m)
            assets_mod.post_run(sess, admin, run.id)
        except Exception:                    # noqa: BLE001 — ماهی که دارایی فعالی نداشته
            pass
        if start_m == 12:
            start_y, start_m = start_y + 1, 1
        else:
            start_m += 1


def seed_passengers_demo(sess) -> None:
    """چند مسافر نمونه روی پرونده‌های سفر — برای نشان دادن محافظت از اطلاعات هویتی."""
    from .models import Passenger
    from . import passengers as pax

    if sess.execute(select(Passenger)).first():
        return
    admin = sess.execute(select(User).where(User.role == "accountant")).scalar_one()
    trips = list(sess.execute(select(Trip).order_by(Trip.id)).scalars())
    if not trips:
        return
    today = jd.jtoday()

    def add(trip, name, **over):
        payload = {"full_name": name, "kind": "adult", "person_type": 1,
                   "trip_id": trip.id, **over}
        try:
            pax.create_passenger(sess, admin, payload)
        except Exception:                    # noqa: BLE001 — داده‌ی نمونه، خطا کل شروع را نبندد
            pass

    first = trips[0]
    add(first, "آقای رضایی", national_id="0012345678", phone="09120000000")
    add(first, "خانم رضایی", national_id="0012345679", kind="child")
    if len(trips) > 1:
        second = trips[1]
        add(second, "خانم موسوی", national_id="0023456789", phone="09350000000")
        add(second, "Mr. Ali Hosseini (مسافر غیرایرانی)", person_type=2,
            passport_no="P5544120",
            passport_expiry=jd.jalali_str(today + timedelta(days=800)),
            note="نمونه‌ی مسافر غیرایرانی — برای صورتحساب بلیت، شماره‌ی گذرنامه لازم است")
    sess.flush()


def seed_moadian_demo(sess) -> None:
    """داده‌ی نمونه‌ی سامانه‌ی مودیان: اطلاعات مالیاتی، شناسه‌های نمونه و صف صورتحساب‌ها.

    همه‌ی شناسه‌ها و شماره‌ها عمداً «نمونه» هستند (با کلمه‌ی DEMO) تا با اطلاعات واقعی
    اشتباه گرفته نشوند. ارسال واقعی هیچ‌وقت در این نمونه انجام نمی‌شود.
    """
    from .models import MoadianInvoice

    if sess.execute(select(MoadianInvoice)).first():
        return
    admin = sess.execute(select(User).where(User.role == "accountant")).scalar_one()

    settings = moadian.get_settings(sess)
    settings.taxid = "DEMO" + "0" * 17 + "1"          # ۲۲ نویسه
    settings.economic_code = "14009998888"
    settings.seller_name = "آژانس مسافرتی نمونه — آسمان‌گشت"
    settings.person_type = 2
    settings.branch_code = "1"
    settings.postal_code = "1518764512"
    settings.phone = "02188880000"
    settings.address = "تهران، خیابان ولیعصر، پلاک ۱۲، طبقه‌ی سوم"
    settings.send_method = "motamed"
    settings.motamed_name = "شرکت معتمد مالیاتی نمونه"
    settings.motamed_id = "DEMO-MOTAMED-1"
    settings.environment = "sandbox"
    settings.is_active = False
    settings.note = ("اطلاعات نمونه است؛ با اطلاعات کارپوشه‌ی مالیاتی خودتان جایگزین کنید. "
                     "تا وقتی تیکِ «فعال» زده نشود، هیچ صورتحسابی جایی فرستاده نمی‌شود.")
    moadian.seed_service_map(sess)

    # دو خدمت با شناسه‌ی نمونه‌ی تأییدشده، بقیه خالی تا حالت «ناقص» هم دیده شود
    demo_ids = {"tour": "2717030010001", "ticket": "2717030010002", "visa": "2717030010003"}
    for row in sess.execute(select(MoadianServiceMap)).scalars():
        if row.service_type in demo_ids:
            row.stuff_id = demo_ids[row.service_type]
            row.is_confirmed = True
            row.note = "شناسه‌ی نمونه برای تمرین؛ شناسه‌ی واقعی را از سازمان مالیاتی بگیرید."
    sess.flush()

    # بلیت نمونه را به مسافران همان سفر وصل می‌کنیم تا حالت «آماده‌ی ارسال» هم دیده شود
    from . import passengers as pax_svc
    from .models import Invoice, Passenger
    ticket_inv = sess.execute(
        select(Invoice).join(Invoice.lines).where(Invoice.lines.any(service_type="ticket"))
        .order_by(Invoice.id)).scalars().first()
    if ticket_inv is not None:
        pax_ids = [p.id for p in sess.execute(
            select(Passenger).where(Passenger.trip_id == ticket_inv.trip_id,
                                    Passenger.invoice_id.is_(None))
            .order_by(Passenger.id.desc())).scalars()][:1]
        if pax_ids:
            try:
                pax_svc.set_invoice_passengers(sess, admin, ticket_inv.id, pax_ids, "seed")
            except Exception:                # noqa: BLE001 — نمونه؛ خطا کل شروع را نبندد
                pass

    moadian.create_for_invoices(sess, admin)
    # یکی از صورتحساب‌های آماده را «پذیرفته‌شده» می‌کنیم تا حالت‌های بعدی هم دیده شود
    first = sess.execute(select(MoadianInvoice).where(MoadianInvoice.status == "ready")
                         .order_by(MoadianInvoice.id)).scalars().first()
    if first is not None:
        first.status = "accepted"
        first.reference_number = "نمونه — ارسال واقعی انجام نشده"
        first.taxid = f"DEMO-TAXID-{first.id:011d}"        # ۲۲ نویسه، عمداً نمونه
        first.sent_at = datetime.now()
    sess.flush()


def bootstrap(demo: bool = True) -> dict[str, str]:
    init_db()
    creds = {}
    with session_scope() as sess:
        seed_accounts(sess)
        sales.seed_tax_rates(sess)
        payroll.seed_payroll(sess)
        assets_mod.seed_assets(sess)
        if demo:
            creds = seed_users(sess, write_credentials=True)
            seed_sample_entries(sess)
            seed_sales_demo(sess)
            seed_payroll_demo(sess)
            seed_assets_demo(sess)
            seed_passengers_demo(sess)
            seed_moadian_demo(sess)
        else:
            seed_production_admin(sess)
    return creds
