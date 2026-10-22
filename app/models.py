"""ساختار اطلاعات برنامه.

اصل‌های رعایت‌شده (کار داخلی، نه چک‌لیست کارفرما):
- محدودیت‌ها صریح‌اند: مبلغ منفی ممنوع، یک سطر نمی‌تواند هم بدهکار باشد هم بستانکار،
  کد حساب تکراری نمی‌شود، و هر سند فقط یک‌بار با یک شناسه‌ی مشخص ثبت می‌شود.
- هیچ منطق کسب‌وکاری داخل دیتابیس پنهان نشده؛ همه‌ی قواعد در خود برنامه است.
- برای هر جدول، سیاست دسترسی در برنامه صریح است.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(60), unique=True, nullable=False, index=True)
    full_name = Column(String(120), nullable=False, default="")
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, index=True)   # admin|manager|accountant|employee|auditor
    is_active = Column(Boolean, nullable=False, default=True)
    must_change_password = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    last_login_at = Column(DateTime, nullable=True)


class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    token_hash = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)
    closed_at = Column(DateTime, nullable=True)


class FiscalYear(Base):
    __tablename__ = "fiscal_years"
    id = Column(Integer, primary_key=True)
    title = Column(String(20), unique=True, nullable=False)   # «1404»
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    status = Column(String(10), nullable=False, default="open")  # open|closed


class Period(Base):
    __tablename__ = "periods"
    id = Column(Integer, primary_key=True)
    fiscal_year_id = Column(Integer, ForeignKey("fiscal_years.id"), nullable=False, index=True)
    jy = Column(Integer, nullable=False)
    jm = Column(Integer, nullable=False)
    status = Column(String(10), nullable=False, default="open")  # open|closed
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    __table_args__ = (UniqueConstraint("jy", "jm", name="uq_period_year_month"),)


class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    kind = Column(String(10), nullable=False)        # group | ledger
    nature = Column(String(10), nullable=False)      # debit | credit  (ماهیت حساب)
    parent_code = Column(String(20), nullable=True)
    # چهار سطح کدینگ: group → general → subsidiary → detail. «kind» برای سازگاری با
    # دفتر قدیمی، جداگانه می‌گوید این حساب در سند قابل انتخاب است یا فقط سرگروه است.
    level = Column(String(16), nullable=False, default="detail")
    group_type = Column(String(16), nullable=False, default="permanent")  # permanent|temporary|other
    report_type = Column(String(20), nullable=False, default="balance_sheet")
    # balance_sheet|income_statement|cash_flow|other
    report_group = Column(String(20), nullable=False, default="other")
    # other|asset|liability|equity|income|expense|cash|bank|receivable|payable|vat|tax|equity_draw
    is_active = Column(Boolean, nullable=False, default=True)
    is_system = Column(Boolean, nullable=False, default=False)
    note = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    __table_args__ = (
        CheckConstraint("kind in ('group','ledger')", name="ck_account_kind"),
        CheckConstraint("nature in ('debit','credit')", name="ck_account_nature"),
    )


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id = Column(Integer, primary_key=True)
    entry_no = Column(String(30), nullable=True, index=True, unique=True)  # «1404-000123»
    entry_date = Column(Date, nullable=False, index=True)
    jy = Column(Integer, nullable=False, index=True)
    jm = Column(Integer, nullable=False, index=True)
    description = Column(String(400), nullable=False, default="")
    status = Column(String(12), nullable=False, default="draft", index=True)  # draft|posted|reversed
    source = Column(String(30), nullable=False, default="manual")
    # manual|transfer|receipt|payment|payroll|depreciation|opening|closing|system
    source_ref = Column(String(60), nullable=True)
    idempotency_key = Column(String(80), nullable=True, unique=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    posted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    posted_at = Column(DateTime, nullable=True)
    reversal_of_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    reversed_by_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    lines = relationship("JournalLine", back_populates="entry", cascade="all, delete-orphan",
                         order_by="JournalLine.line_no")


class JournalLine(Base):
    __tablename__ = "journal_lines"
    id = Column(Integer, primary_key=True)
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    description = Column(String(300), nullable=False, default="")
    debit_rial = Column(Integer, nullable=False, default=0)
    credit_rial = Column(Integer, nullable=False, default=0)
    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account")
    __table_args__ = (
        CheckConstraint("debit_rial >= 0 and credit_rial >= 0", name="ck_line_nonnegative"),
        CheckConstraint("not (debit_rial > 0 and credit_rial > 0)", name="ck_line_one_side"),
        CheckConstraint("debit_rial > 0 or credit_rial > 0", name="ck_line_not_empty"),
        Index("ix_line_account_entry", "account_id", "entry_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(60), nullable=False, default="")
    action = Column(String(60), nullable=False, index=True)
    entity = Column(String(40), nullable=False, default="")
    entity_id = Column(String(40), nullable=False, default="")
    detail = Column(Text, nullable=False, default="")
    ip = Column(String(60), nullable=False, default="")


class ImportRecord(Base):
    """هر فایل اکسل که وارد برنامه شده، با اثر انگشت خودش. برای اینکه یک فایل دوبار وارد نشود."""
    __tablename__ = "import_records"
    id = Column(Integer, primary_key=True)
    fingerprint = Column(String(64), unique=True, nullable=False, index=True)
    kind = Column(String(20), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    rows = Column(Integer, nullable=False, default=0)
    created = Column(Integer, nullable=False, default=0)
    skipped = Column(Integer, nullable=False, default=0)
    note = Column(String(200), nullable=False, default="")
    at = Column(DateTime, nullable=False, default=datetime.now)


class IdempotencyRecord(Base):
    """پاسخ درخواست تغییردهنده، برای تکرار امن درخواست‌های شبکه.

    شناسه‌ی نشست خام و کلید خام ذخیره نمی‌شوند؛ فقط اثر انگشت آن‌ها نگه داشته می‌شود.
    رکوردها کوتاه‌عمرند و بدنه‌ی پاسخ فقط برای درخواست‌های موفق JSON ذخیره می‌شود.
    """
    __tablename__ = "idempotency_records"
    id = Column(Integer, primary_key=True)
    scope_key = Column(String(64), unique=True, nullable=False, index=True)
    request_hash = Column(String(64), nullable=False)
    response_status = Column(Integer, nullable=False)
    response_content_type = Column(String(100), nullable=False, default="application/json")
    response_body = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)


# =====================================================================
# قدم بعدی: فروش خدمات آژانس، پرونده‌ی سفر، دریافت و پرداخت
# =====================================================================

class TaxRate(Base):
    """نرخ مالیات — هیچ نرخی در کد برنامه سخت‌شده نیست؛ همه‌چیز از این جدول با تاریخ اجرا می‌آید.

    (آزمون نمونه‌ی ماژول ۵ کارفرما: نرخ مالیات نباید یک مقدار ثابت و دائمی باشد.)
    """
    __tablename__ = "tax_rates"
    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False, default="vat")        # vat | salary_tax
    service_type = Column(String(20), nullable=True)                 # خالی = همه‌ی خدمات
    title = Column(String(120), nullable=False, default="")
    rate_bp = Column(Integer, nullable=False)                        # صدم درصد: 900 = ۹٪
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date, nullable=True)
    note = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    __table_args__ = (
        CheckConstraint("rate_bp >= 0 and rate_bp <= 10000", name="ck_tax_rate_range"),
    )


class Party(Base):
    """اشخاص: مشتری، تأمین‌کننده، یا هر دو. هر کس حساب تفصیلی خودش را دارد."""
    __tablename__ = "parties"
    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    kind = Column(String(12), nullable=False, default="customer")   # customer|supplier|both
    group_name = Column(String(80), nullable=False, default="")     # گروه اشخاص (مثل گروه محک): نمایش در انتخاب‌گرها
    phone = Column(String(30), nullable=False, default="")
    note = Column(String(255), nullable=False, default="")
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    # حساب اعتبار مشتری (زیرمجموعه‌ی 2103) — هنگام نخستین تبدیل استرداد به اعتبار ساخته می‌شود
    credit_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    # اطلاعاتی که سامانه‌ی مودیان برای صورتحساب نوع اول لازم دارد (فاز ۵)
    person_type = Column(Integer, nullable=False, default=1)          # ۱ حقیقی، ۲ حقوقی
    national_id = Column(String(20), nullable=False, default="")       # کد ملی یا شناسه ملی
    economic_code = Column(String(20), nullable=False, default="")    # شماره اقتصادی
    postal_code = Column(String(15), nullable=False, default="")
    passport_no = Column(String(30), nullable=False, default="")      # برای مسافر غیرایرانی
    __table_args__ = (CheckConstraint("kind in ('customer','supplier','both','employee')", name="ck_party_kind"),)


class Trip(Base):
    """پرونده‌ی سفر — همان پیشنهاد اصلی: سود هر سفر، لحظه‌ای."""
    __tablename__ = "trips"
    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, nullable=False, index=True)
    title = Column(String(160), nullable=False)
    destination = Column(String(120), nullable=False, default="")
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    status = Column(String(12), nullable=False, default="open")      # open|closed|cancelled
    note = Column(String(400), nullable=False, default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class Invoice(Base):
    """فاکتور فروش خدمات: تور، بلیت، ویزا و سایر."""
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    number = Column(String(30), unique=True, nullable=True, index=True)   # 1405-ف-000001
    doc_date = Column(Date, nullable=False, index=True)
    due_date = Column(Date, nullable=True, index=True)
    party_id = Column(Integer, ForeignKey("parties.id"), nullable=False, index=True)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=True, index=True)
    description = Column(String(300), nullable=False, default="")
    status = Column(String(12), nullable=False, default="draft", index=True)  # draft|issued|cancelled
    subtotal_rial = Column(Integer, nullable=False, default=0)
    discount_rial = Column(Integer, nullable=False, default=0)
    vat_rial = Column(Integer, nullable=False, default=0)
    total_rial = Column(Integer, nullable=False, default=0)
    paid_rial = Column(Integer, nullable=False, default=0)
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    lines = relationship("InvoiceLine", back_populates="invoice", cascade="all, delete-orphan",
                         order_by="InvoiceLine.line_no")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    service_type = Column(String(20), nullable=False, default="other")
    description = Column(String(300), nullable=False, default="")
    qty = Column(Integer, nullable=False, default=1)
    unit_price_rial = Column(Integer, nullable=False, default=0)
    discount_rial = Column(Integer, nullable=False, default=0)
    taxable = Column(Boolean, nullable=False, default=True)
    flight_type = Column(Integer, nullable=True)                       # الگوی بلیت هواپیما: ۱ داخلی، ۲ خارجی
    revenue_model = Column(String(20), nullable=False, default="gross")  # gross|commission
    purchase_cost_rial = Column(Integer, nullable=False, default=0)
    supplier_party_id = Column(Integer, ForeignKey("parties.id"), nullable=True)
    net_rial = Column(Integer, nullable=False, default=0)
    vat_rial = Column(Integer, nullable=False, default=0)
    rate_bp = Column(Integer, nullable=False, default=0)
    invoice = relationship("Invoice", back_populates="lines")
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_inv_qty"),
        CheckConstraint("unit_price_rial >= 0", name="ck_inv_price"),
    )


class SupplierBill(Base):
    """صورتحساب تأمین‌کننده / هزینه‌ی سفر (هتل، ایرلاین، ویزا، ترانسفر…)."""
    __tablename__ = "supplier_bills"
    id = Column(Integer, primary_key=True)
    number = Column(String(30), unique=True, nullable=True, index=True)   # 1405-خ-000001
    doc_date = Column(Date, nullable=False, index=True)
    due_date = Column(Date, nullable=True, index=True)
    party_id = Column(Integer, ForeignKey("parties.id"), nullable=False, index=True)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=True, index=True)
    expense_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    bill_type = Column(String(20), nullable=False, default="expense")  # expense|consignment
    source_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    description = Column(String(300), nullable=False, default="")
    net_rial = Column(Integer, nullable=False, default=0)
    vat_rial = Column(Integer, nullable=False, default=0)
    total_rial = Column(Integer, nullable=False, default=0)
    paid_rial = Column(Integer, nullable=False, default=0)
    status = Column(String(12), nullable=False, default="draft", index=True)  # draft|issued|cancelled
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class Settlement(Base):
    """دریافت از مشتری یا پرداخت به تأمین‌کننده."""
    __tablename__ = "settlements"
    id = Column(Integer, primary_key=True)
    number = Column(String(30), unique=True, nullable=True, index=True)   # 1405-د-000001 / 1405-پ-000001
    direction = Column(String(4), nullable=False)                    # in (دریافت) | out (پرداخت)
    doc_date = Column(Date, nullable=False, index=True)
    party_id = Column(Integer, ForeignKey("parties.id"), nullable=False, index=True)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)   # صندوق یا بانک یا چک
    amount_rial = Column(Integer, nullable=False)
    description = Column(String(300), nullable=False, default="")
    status = Column(String(12), nullable=False, default="draft", index=True)
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    method = Column(String(20), nullable=False, default="cash")  # cash|bank|card|transfer|cheque
    cheque_id = Column(Integer, ForeignKey("cheques.id"), nullable=True)
    cheque_number = Column(String(40), nullable=False, default="")
    cheque_bank = Column(String(80), nullable=False, default="")
    cheque_branch = Column(String(80), nullable=False, default="")
    cheque_due_date = Column(Date, nullable=True)
    ref_no = Column(String(60), nullable=False, default="")       # شماره پیگیری حواله/کارتخوان
    # برچسب گروهی — ردیف‌های یک دریافت/پرداخت چندابزاری را به هم می‌چسباند
    batch_no = Column(String(40), nullable=False, default="", index=True)
    target_invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True)
    # پیوند به لغو جزئی — برای پرداخت استرداد به مشتری یا وصول طلب از تأمین‌کننده
    partial_cancel_id = Column(Integer, ForeignKey("invoice_partial_cancels.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    allocations = relationship("SettlementAllocation", back_populates="settlement",
                               cascade="all, delete-orphan")
    __table_args__ = (
        CheckConstraint("direction in ('in','out')", name="ck_settle_dir"),
        CheckConstraint("amount_rial > 0", name="ck_settle_amount"),
    )


class SettlementAllocation(Base):
    """این پول بابت کدام فاکتور (یا صورتحساب) بود."""
    __tablename__ = "settlement_allocations"
    id = Column(Integer, primary_key=True)
    settlement_id = Column(Integer, ForeignKey("settlements.id"), nullable=False, index=True)
    target_type = Column(String(16), nullable=False)                 # invoice | bill | partial_cancel
    target_id = Column(Integer, nullable=False)
    amount_rial = Column(Integer, nullable=False, default=0)
    settlement = relationship("Settlement", back_populates="allocations")
    __table_args__ = (CheckConstraint("target_type in ('invoice','bill','partial_cancel')",
                                      name="ck_alloc_target"),)


class RecurringEntry(Base):
    """سند تکراری: اجاره، حقوق، قبوض — یک بار تعریف، هر ماه با یک کلیک."""
    __tablename__ = "recurring_entries"
    id = Column(Integer, primary_key=True)
    title = Column(String(120), nullable=False)
    day_of_month = Column(Integer, nullable=False, default=1)
    description = Column(String(300), nullable=False, default="")
    lines_json = Column(Text, nullable=False, default="[]")
    is_active = Column(Boolean, nullable=False, default=True)
    last_period = Column(String(10), nullable=False, default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    __table_args__ = (CheckConstraint("day_of_month between 1 and 31", name="ck_recur_day"),)


# =====================================================================
# فاز ۳: حقوق و دستمزد
# =====================================================================

class Employee(Base):
    """پرونده‌ی کارمند — بدون اطلاعات هویتی حساس؛ فقط چیزی که برای محاسبه لازم است."""
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    position = Column(String(120), nullable=False, default="")
    contract_type = Column(String(20), nullable=False, default="permanent")  # permanent|temporary|part_time
    hire_date = Column(Date, nullable=False)
    work_days = Column(Integer, nullable=False, default=30)      # روز کارکرد پیش‌فرض در ماه
    is_active = Column(Boolean, nullable=False, default=True)
    note = Column(String(255), nullable=False, default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class PayItem(Base):
    """اقلام حقوق.

    هر قلم خودش می‌گوید مشمول بیمه است یا نه، مشمول مالیات است یا نه، و تا چه سقفی از
    مالیات معاف است. هیچ قاعده‌ای در کد نوشته نشده؛ همه‌چیز از همین جدول می‌آید، پس
    حسابدار می‌تواند قواعد را با تنظیمات تطبیق دهد.
    """
    __tablename__ = "pay_items"
    id = Column(Integer, primary_key=True)
    code = Column(String(40), unique=True, nullable=False, index=True)
    title = Column(String(120), nullable=False)
    kind = Column(String(10), nullable=False, default="earning")      # earning|deduction
    insurable = Column(Boolean, nullable=False, default=True)
    taxable = Column(Boolean, nullable=False, default=True)
    tax_exempt_monthly_rial = Column(Integer, nullable=False, default=0)
    is_system = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=100)
    note = Column(String(255), nullable=False, default="")
    __table_args__ = (CheckConstraint("kind in ('earning','deduction')", name="ck_payitem_kind"),)


class EmployeePayItem(Base):
    """اقلام ثابت حکم حقوقی هر کارمند (حق مسکن، خواربار، حق اولاد…)."""
    __tablename__ = "employee_pay_items"
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    pay_item_id = Column(Integer, ForeignKey("pay_items.id"), nullable=False)
    amount_rial = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    __table_args__ = (UniqueConstraint("employee_id", "pay_item_id", name="uq_emp_payitem"),)


class PayrollSettings(Base):
    """اعداد قانونی هر سال: بیمه و پله‌های مالیات.

    این جدول قلب ماژول است. هیچ‌کدام از این اعداد در کد برنامه نیست؛ برای هر سال یک سطر
    با تاریخ و منبع ثبت می‌شود و تا وقتی حسابدار تأییدش نکند، برنامه آن را «تأییدنشده»
    نشان می‌دهد.
    """
    __tablename__ = "payroll_settings"
    id = Column(Integer, primary_key=True)
    year = Column(Integer, unique=True, nullable=False, index=True)     # سال شمسی
    insurance_employee_bp = Column(Integer, nullable=False, default=700)   # ۷٪
    insurance_employer_bp = Column(Integer, nullable=False, default=2000)  # ۲۰٪
    unemployment_bp = Column(Integer, nullable=False, default=300)         # ۳٪ بیمه بیکاری (سهم کارفرما)
    tax_mode = Column(String(10), nullable=False, default="monthly")        # monthly|annual
    tax_brackets_json = Column(Text, nullable=False, default="[]")
    standard_monthly_hours = Column(Integer, nullable=False, default=220)
    overtime_coefficient_bp = Column(Integer, nullable=False, default=14000)  # ۱.۴ برابر
    source_note = Column(String(400), nullable=False, default="")
    is_confirmed = Column(Boolean, nullable=False, default=False)
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)


class EmployeeLoan(Base):
    """مساعده یا وام کارمند. مساعده = یک قسط، وام = چند قسط."""
    __tablename__ = "employee_loans"
    id = Column(Integer, primary_key=True)
    number = Column(String(30), unique=True, nullable=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    kind = Column(String(10), nullable=False, default="advance")     # advance|loan
    doc_date = Column(Date, nullable=False)
    amount_rial = Column(Integer, nullable=False)
    installments = Column(Integer, nullable=False, default=1)
    paid_rial = Column(Integer, nullable=False, default=0)
    paid_out_rial = Column(Integer, nullable=False, default=0)        # پولی که واقعاً به کارمند داده شده
    status = Column(String(12), nullable=False, default="open")       # open|closed|cancelled
    description = Column(String(255), nullable=False, default="")
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    __table_args__ = (
        CheckConstraint("kind in ('advance','loan')", name="ck_loan_kind"),
        CheckConstraint("amount_rial > 0", name="ck_loan_amount"),
        CheckConstraint("installments >= 1", name="ck_loan_installments"),
    )

    @property
    def remaining_rial(self) -> int:
        return max(0, self.amount_rial - self.paid_rial)

    @property
    def monthly_rial(self) -> int:
        return self.amount_rial // self.installments if self.installments else self.amount_rial


class PayrollRun(Base):
    """لیست حقوق یک ماه."""
    __tablename__ = "payroll_runs"
    id = Column(Integer, primary_key=True)
    number = Column(String(30), unique=True, nullable=True, index=True)   # 1405-ح-000001
    jy = Column(Integer, nullable=False, index=True)
    jm = Column(Integer, nullable=False, index=True)
    status = Column(String(12), nullable=False, default="draft", index=True)  # draft|posted|paid|cancelled
    settings_id = Column(Integer, ForeignKey("payroll_settings.id"), nullable=True)
    gross_rial = Column(Integer, nullable=False, default=0)
    insurance_employee_rial = Column(Integer, nullable=False, default=0)
    insurance_employer_rial = Column(Integer, nullable=False, default=0)
    unemployment_rial = Column(Integer, nullable=False, default=0)
    tax_rial = Column(Integer, nullable=False, default=0)
    loan_rial = Column(Integer, nullable=False, default=0)
    net_rial = Column(Integer, nullable=False, default=0)
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    payment_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    posted_at = Column(DateTime, nullable=True)
    items = relationship("PayrollItem", back_populates="run", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("jy", "jm", name="uq_payroll_period"),)


class PayrollItem(Base):
    """محاسبه‌ی یک کارمند در یک لیست حقوق."""
    __tablename__ = "payroll_items"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("payroll_runs.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    work_days = Column(Integer, nullable=False, default=30)
    earnings_json = Column(Text, nullable=False, default="[]")
    overtime_hours = Column(Integer, nullable=False, default=0)
    gross_rial = Column(Integer, nullable=False, default=0)
    insurable_base_rial = Column(Integer, nullable=False, default=0)
    taxable_base_rial = Column(Integer, nullable=False, default=0)
    insurance_employee_rial = Column(Integer, nullable=False, default=0)
    insurance_employer_rial = Column(Integer, nullable=False, default=0)
    unemployment_rial = Column(Integer, nullable=False, default=0)
    tax_rial = Column(Integer, nullable=False, default=0)
    loan_rial = Column(Integer, nullable=False, default=0)
    other_deduction_rial = Column(Integer, nullable=False, default=0)
    net_rial = Column(Integer, nullable=False, default=0)
    run = relationship("PayrollRun", back_populates="items")
    employee = relationship("Employee")


# ------------------------------------------------------------------ فاز ۴: دارایی ثابت و بستن سال
class AssetCategory(Base):
    """گروه دارایی ثابت. روش و نرخ استهلاک اینجا نگه داشته می‌شود تا حسابدار خودش عوضش کند."""
    __tablename__ = "asset_categories"
    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False)
    title = Column(String(120), nullable=False)
    method = Column(String(12), nullable=False, default="declining")   # declining | straight
    annual_rate_bp = Column(Integer, nullable=False, default=2500)     # ۲۵٪ = ۲۵۰۰
    useful_life_months = Column(Integer, nullable=False, default=48)   # برای روش مستقیم
    asset_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    accumulation_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    expense_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    source_note = Column(String(400), nullable=False, default="")
    is_system = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=50)


class FixedAsset(Base):
    """یک دارایی ثابت با کارت کامل: بهای تمام‌شده، استهلاک انباشته و ارزش دفتری."""
    __tablename__ = "fixed_assets"
    id = Column(Integer, primary_key=True)
    code = Column(String(20), unique=True, nullable=False)             # «ث-0001»
    title = Column(String(160), nullable=False)
    category_id = Column(Integer, ForeignKey("asset_categories.id"), nullable=False)
    purchase_date = Column(Date, nullable=False)
    in_service_date = Column(Date, nullable=False)                     # از چه تاریخی به کار افتاد
    cost_rial = Column(Integer, nullable=False, default=0)
    salvage_rial = Column(Integer, nullable=False, default=0)           # ارزش اسقاط
    useful_life_months = Column(Integer, nullable=False, default=48)
    method = Column(String(12), nullable=False, default="declining")
    annual_rate_bp = Column(Integer, nullable=False, default=2500)
    asset_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    accumulation_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    expense_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    accumulated_rial = Column(Integer, nullable=False, default=0)
    status = Column(String(12), nullable=False, default="active")       # active | sold | retired
    disposal_date = Column(Date, nullable=True)
    disposal_amount_rial = Column(Integer, nullable=False, default=0)
    purchase_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    disposal_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    note = Column(String(255), nullable=False, default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    @property
    def book_value_rial(self) -> int:
        return self.cost_rial - self.accumulated_rial


class DepreciationRun(Base):
    """استهلاک یک ماه. مثل لیست حقوق: اول حساب می‌شود، بعد سندش ثبت می‌شود."""
    __tablename__ = "depreciation_runs"
    id = Column(Integer, primary_key=True)
    number = Column(String(30), unique=True, nullable=True)
    jy = Column(Integer, nullable=False)
    jm = Column(Integer, nullable=False)
    status = Column(String(12), nullable=False, default="draft")        # draft | posted | cancelled
    total_rial = Column(Integer, nullable=False, default=0)
    detail_json = Column(Text, nullable=False, default="[]")
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    posted_at = Column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("jy", "jm", name="uq_depreciation_year_month"),)


class FiscalClosing(Base):
    """بستن سال مالی: سند اختتامیه و انتقال سود (زیان) به سود و زیان انباشته."""
    __tablename__ = "fiscal_closings"
    id = Column(Integer, primary_key=True)
    jy = Column(Integer, unique=True, nullable=False)
    status = Column(String(12), nullable=False, default="closed")       # closed | reopened
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    revenue_rial = Column(Integer, nullable=False, default=0)
    expense_rial = Column(Integer, nullable=False, default=0)
    profit_rial = Column(Integer, nullable=False, default=0)
    retained_before_rial = Column(Integer, nullable=False, default=0)
    retained_after_rial = Column(Integer, nullable=False, default=0)
    note = Column(String(255), nullable=False, default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class Passenger(Base):
    """مسافر — اطلاعات هویتی برای بلیت و لیست تور.

    شماره‌ی ملی/گذرنامه در فایل داده رمزنگاری‌شده ذخیره می‌شود (نه ساده) و در فهرست‌ها
    فقط ماسک‌شده نشان داده می‌شود؛ دیدن کامل دسترسی جدا دارد و در «ردّ کار» ثبت می‌شود.
    """
    __tablename__ = "passengers"
    id = Column(Integer, primary_key=True)
    full_name = Column(String(160), nullable=False)
    kind = Column(String(10), nullable=False, default="adult")        # adult|child|infant
    person_type = Column(Integer, nullable=False, default=1)          # ۱ ایرانی، ۲ غیرایرانی
    national_id = Column(String(300), nullable=False, default="")     # رمزنگاری‌شده
    passport_no = Column(String(300), nullable=False, default="")     # رمزنگاری‌شده
    passport_expiry = Column(Date, nullable=True)
    birth_date = Column(Date, nullable=True)
    phone = Column(String(30), nullable=False, default="")
    note = Column(String(255), nullable=False, default="")
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    __table_args__ = (
        CheckConstraint("kind in ('adult','child','infant')", name="ck_passenger_kind"),
        CheckConstraint("trip_id is not null or invoice_id is not null", name="ck_passenger_owner"),
    )


# =====================================================================
# فاز ۵ — سامانه‌ی مودیان
# =====================================================================
class MoadianSettings(Base):
    """اطلاعات مالیاتی خود آژانس که در هر صورتحساب الکترونیکی تکرار می‌شود."""
    __tablename__ = "moadian_settings"
    id = Column(Integer, primary_key=True)
    taxid = Column(String(30), nullable=False, default="")            # شناسه یکتای حافظه مالیاتی (۲۲ نویسه)
    economic_code = Column(String(20), nullable=False, default="")    # شماره اقتصادی فروشنده (tins)
    seller_name = Column(String(160), nullable=False, default="")
    person_type = Column(Integer, nullable=False, default=2)          # ۱ حقیقی، ۲ حقوقی
    branch_code = Column(String(10), nullable=False, default="")      # کد شعبه (sbc)
    postal_code = Column(String(15), nullable=False, default="")
    phone = Column(String(30), nullable=False, default="")
    address = Column(String(300), nullable=False, default="")
    send_method = Column(String(12), nullable=False, default="motamed")   # motamed | direct
    motamed_name = Column(String(160), nullable=False, default="")
    motamed_id = Column(String(60), nullable=False, default="")
    environment = Column(String(12), nullable=False, default="sandbox")   # sandbox | production
    is_active = Column(Boolean, nullable=False, default=False)
    note = Column(String(400), nullable=False, default="")
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class MoadianServiceMap(Base):
    """نگاشت نوع خدمت آژانس به «شناسه کالا/خدمت» و وضعیت مالیاتی‌اش.

    شناسه‌ی ۱۳ رقمی را سازمان مالیاتی می‌دهد؛ برنامه هیچ شناسه‌ی حدسی نمی‌سازد و
    تا وقتی حسابدار شناسه‌ی درست را وارد و تأیید نکند، صورتحساب ساخته نمی‌شود.
    """
    __tablename__ = "moadian_service_map"
    id = Column(Integer, primary_key=True)
    service_type = Column(String(20), nullable=False, unique=True)    # tour | ticket | visa | hotel | insurance | other
    title = Column(String(120), nullable=False, default="")
    stuff_id = Column(String(13), nullable=False, default="")         # شناسه کالا/خدمت (۱۳ رقم)
    unit_code = Column(Integer, nullable=False, default=1627)         # واحد اندازه‌گیری (خدمت)
    invoice_pattern = Column(Integer, nullable=False, default=1)      # ۱ فروش، ۶ بلیت هواپیما
    is_confirmed = Column(Boolean, nullable=False, default=False)
    note = Column(String(400), nullable=False, default="")
    sort_order = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class MoadianInvoice(Base):
    """صورتحساب الکترونیکی آماده‌ی ارسال به سامانه‌ی مودیان (کارپوشه‌ی محلی)."""
    __tablename__ = "moadian_invoices"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=True, index=True)
    serial = Column(String(30), nullable=False, default="")           # سریال صورتحساب (inno)
    doc_date = Column(Date, nullable=False)
    invoice_type = Column(Integer, nullable=False, default=1)         # inty: ۱ یا ۲
    pattern = Column(Integer, nullable=False, default=1)              # inp
    subject = Column(Integer, nullable=False, default=1)              # ins: ۱ اصلی ۲ اصلاحی ۳ ابطالی ۴ برگشت
    status = Column(String(12), nullable=False, default="draft")
    # draft = ساخته شده ولی ناقص | ready = آماده‌ی ارسال | sent = ارسال شده |
    # accepted = پذیرفته‌شده | rejected = مردود
    taxid = Column(String(30), nullable=False, default="")            # شماره مالیاتی دریافتی از سامانه
    reference_number = Column(String(60), nullable=False, default="")  # شماره ارجاع شرکت معتمد
    reference_taxid = Column(String(30), nullable=False, default="")   # شماره مالیاتی صورتحساب مرجع
    payload_json = Column(Text, nullable=False, default="{}")
    problems_json = Column(Text, nullable=False, default="[]")
    total_rial = Column(Integer, nullable=False, default=0)
    vat_rial = Column(Integer, nullable=False, default=0)
    sent_at = Column(DateTime, nullable=True)
    error_text = Column(String(500), nullable=False, default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    __table_args__ = (UniqueConstraint("invoice_id", "subject", name="uq_moadian_invoice_subject"),)


class Cheque(Base):
    """چک دریافتی / پرداختی — طبق استاندارد: سریال، بانک، سررسید و وضعیت."""
    __tablename__ = "cheques"
    id = Column(Integer, primary_key=True)
    number = Column(String(40), nullable=False)  # شماره چک
    series = Column(String(40), nullable=False, default="")  # سریال
    bank_name = Column(String(80), nullable=False, default="")
    branch = Column(String(80), nullable=False, default="")
    account_number = Column(String(40), nullable=False, default="")   # شماره حساب صادرکننده
    sheba = Column(String(40), nullable=False, default="")            # شماره شبا
    sayad_id = Column(String(40), nullable=False, default="")         # شناسه صیاد
    amount_rial = Column(Integer, nullable=False)
    issue_date = Column(Date, nullable=False)  # تاریخ صدور
    due_date = Column(Date, nullable=False)  # سررسید
    direction = Column(String(8), nullable=False)  # in = دریافتی، out = پرداختی
    status = Column(String(20), nullable=False, default="in_hand")  # in_hand|deposited|cleared|bounced|transferred|issued|cancelled
    party_id = Column(Integer, ForeignKey("parties.id"), nullable=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # حساب بانکی مربوطه
    description = Column(String(300), nullable=False, default="")
    settlement_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    __table_args__ = (
        CheckConstraint("direction in ('in','out')", name="ck_cheque_direction"),
    )


class InvoicePartialCancel(Base):
    """لغو جزئی فاکتور — هر ردیف یک عملیات حساب‌شده و قابل‌حسابرسی."""
    __tablename__ = "invoice_partial_cancels"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    doc_date = Column(Date, nullable=False, index=True)
    amount_rial = Column(Integer, nullable=False)  # مبلغ لغو از فروش
    penalty_customer_rial = Column(Integer, nullable=False, default=0)  # جریمه مشتری
    supplier_refund_rial = Column(Integer, nullable=False, default=0)  # برگشت از تامین
    penalty_supplier_rial = Column(Integer, nullable=False, default=0)  # جریمه تامین
    reason = Column(String(400), nullable=False, default="")
    entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)  # سند برگشت فروش
    penalty_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)  # سند جریمه مشتری
    refund_id = Column(Integer, ForeignKey("settlements.id"), nullable=True)  # پرداخت استرداد
    supplier_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)  # قدیمی: سند برگشت خرید/جریمه تامین (برای سازگاری)
    supplier_refund_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)  # سند برگشت تامین (هزینه)
    supplier_penalty_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)  # سند جریمه تامین (درآمد)
    supplier_refund_vat_rial = Column(Integer, nullable=False, default=0)  # مالیات برگشتی تأمین (اگر مشخص باشد)
    # --- مرحله اصلاح لغو: تفکیک لغو از پرداخت، کارمزد و گردش‌کار ---
    status = Column(String(14), nullable=False, default="confirmed")  # draft|pending|confirmed|rejected|cancelled
    refund_mode = Column(String(10), nullable=False, default="immediate")  # immediate|deferred|credit
    agency_fee_rial = Column(Integer, nullable=False, default=0)  # کارمزد خدمات کنسلی آژانس
    agency_fee_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)
    refund_due_rial = Column(Integer, nullable=False, default=0)    # قابل پرداخت به مشتری (لغو−جریمه−کارمزد)
    refund_paid_rial = Column(Integer, nullable=False, default=0)   # پرداخت‌شده (از روی تخصیص‌ها همگام می‌شود)
    supplier_claim_rial = Column(Integer, nullable=False, default=0)    # کل طلب از تأمین
    supplier_received_rial = Column(Integer, nullable=False, default=0)  # دریافت‌شده از تأمین
    supplier_party_id = Column(Integer, ForeignKey("parties.id"), nullable=True)
    credit_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)   # آخرین سند اعتبار
    credit_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)        # حساب اعتبار مشتری
    credit_issued_rial = Column(Integer, nullable=False, default=0)
    nonrefundable_cost_rial = Column(Integer, nullable=False, default=0)  # هزینه‌ی غیرقابل‌استرداد (برآورد ثبت‌شده)
    profit_impact_rial = Column(Integer, nullable=False, default=0)       # اثر خالص لغو بر سود
    # جریمه بر عهده کانتر (خطای داخلی): به‌جای سوخت آن را طلب از کانتر می‌کنیم (۵۱۰۹ ← بدهی کانتر زیر ۱۱۰۸)
    counter_penalty_rial = Column(Integer, nullable=False, default=0)      # مبلغ جریمه بر عهده کانتر
    counter_party_id = Column(Integer, ForeignKey("parties.id"), nullable=True)  # کانتر/کارمند مسئول
    counter_penalty_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)  # سند مطالبه از کانتر
    burn_entry_id = Column(Integer, ForeignKey("journal_entries.id"), nullable=True)             # سند انتقال سوخت ۵۱۰۱→۵۱۰۹
    line_id = Column(Integer, ForeignKey("invoice_lines.id"), nullable=True)  # اتصال به ردیف خدمت (مرحله ساختار خدمات)
    reversal_state = Column(String(4000), nullable=False, default="")     # JSON وضعیت برای ابطال رسمی
    cancel_reason = Column(String(400), nullable=False, default="")       # دلیل ابطال لغو
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    __table_args__ = (
        CheckConstraint("amount_rial > 0", name="ck_partial_amount"),
        CheckConstraint("penalty_customer_rial >= 0", name="ck_partial_penalty_cust"),
        CheckConstraint("supplier_refund_rial >= 0", name="ck_partial_supplier_refund"),
        CheckConstraint("penalty_supplier_rial >= 0", name="ck_partial_penalty_sup"),
        CheckConstraint("counter_penalty_rial >= 0", name="ck_partial_penalty_counter"),
        UniqueConstraint("invoice_id", "doc_date", "amount_rial", "reason", name="uq_partial_once"),
    )
