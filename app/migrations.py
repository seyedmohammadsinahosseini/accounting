"""به‌روزرسانی ساختار اطلاعات — نسخه‌دار و فقط رو به جلو.

هر تغییری در ساختار جدول‌ها اینجا ثبت می‌شود، با یک شماره. برنامه موقع بالا آمدن،
نسخه‌ی فعلی را می‌خواند و قدم‌های عقب‌مانده را به ترتیب اجرا می‌کند. هیچ‌وقت ستونی پاک
نمی‌شود و هیچ داده‌ای بازنویسی نمی‌شود؛ فقط ستون تازه اضافه می‌شود.
"""
from __future__ import annotations

from sqlalchemy import text

from .db import app_log, engine

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "افزودن ستون سازنده به فاکتور، صورتحساب و دریافت/پرداخت",
     "ALTER TABLE invoices ADD COLUMN created_by INTEGER"),
    (2, "", "ALTER TABLE supplier_bills ADD COLUMN created_by INTEGER"),
    (3, "", "ALTER TABLE settlements ADD COLUMN created_by INTEGER"),
    (4, "اطلاعات مالیاتی اشخاص برای سامانه‌ی مودیان", "ALTER TABLE parties ADD COLUMN person_type INTEGER DEFAULT 1"),
    (5, "", "ALTER TABLE parties ADD COLUMN national_id VARCHAR(20) DEFAULT ''"),
    (6, "", "ALTER TABLE parties ADD COLUMN economic_code VARCHAR(20) DEFAULT ''"),
    (7, "", "ALTER TABLE parties ADD COLUMN postal_code VARCHAR(15) DEFAULT ''"),
    (8, "", "ALTER TABLE parties ADD COLUMN passport_no VARCHAR(30) DEFAULT ''"),
    (9, "نوع پرواز برای الگوی بلیت هواپیما", "ALTER TABLE invoice_lines ADD COLUMN flight_type INTEGER"),
    (10, "سطح کدینگ حساب‌ها", "ALTER TABLE accounts ADD COLUMN level VARCHAR(16) NOT NULL DEFAULT 'detail'"),
    (11, "نوع گروه حساب‌ها", "ALTER TABLE accounts ADD COLUMN group_type VARCHAR(16) NOT NULL DEFAULT 'permanent'"),
    (12, "نوع گزارش حساب‌ها", "ALTER TABLE accounts ADD COLUMN report_type VARCHAR(20) NOT NULL DEFAULT 'balance_sheet'"),
    (13, "تبدیل حساب‌های موجود به کدینگ چهارسطحی",
     "UPDATE accounts SET level = CASE "
     "WHEN kind = 'group' AND length(code) <= 1 THEN 'group' "
     "WHEN kind = 'group' AND length(code) <= 2 THEN 'general' "
     "WHEN kind = 'group' THEN 'subsidiary' "
     "WHEN instr(code, '-') > 0 OR length(code) > 5 THEN 'detail' "
     "ELSE 'subsidiary' END, "
     "group_type = CASE WHEN report_group IN ('income','expense','equity_draw') THEN 'temporary' "
     "WHEN report_group IN ('asset','liability','equity','cash','bank','receivable','payable','vat','tax') "
     "THEN 'permanent' ELSE 'other' END, "
     "report_type = CASE WHEN report_group IN ('income','expense','equity_draw') THEN 'income_statement' "
     "WHEN report_group IN ('asset','liability','equity','cash','bank','receivable','payable','vat','tax') "
     "THEN 'balance_sheet' ELSE 'other' END"),
    (14, "ثبت ماندگار کلیدهای جلوگیری از تکرار درخواست",
     "CREATE TABLE IF NOT EXISTS idempotency_records ("
     "id INTEGER PRIMARY KEY, scope_key VARCHAR(64) NOT NULL UNIQUE, "
     "request_hash VARCHAR(64) NOT NULL, response_status INTEGER NOT NULL, "
     "response_content_type VARCHAR(100) NOT NULL DEFAULT 'application/json', "
     "response_body TEXT NOT NULL DEFAULT '{}', created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"),
    (15, "افزودن نمایه‌های پاک‌سازی idempotency",
     "CREATE INDEX IF NOT EXISTS ix_idempotency_records_scope_key ON idempotency_records (scope_key)"),
    (16, "افزودن نمایه‌ی تاریخ انقضای idempotency",
     "CREATE INDEX IF NOT EXISTS ix_idempotency_records_created_at ON idempotency_records (created_at)"),
    (17, "مدل درآمدی و بهای خرید ردیف فاکتور",
     "ALTER TABLE invoice_lines ADD COLUMN revenue_model VARCHAR(20) NOT NULL DEFAULT 'gross'"),
    (18, "",
     "ALTER TABLE invoice_lines ADD COLUMN purchase_cost_rial INTEGER NOT NULL DEFAULT 0"),
    (19, "",
     "ALTER TABLE invoice_lines ADD COLUMN supplier_party_id INTEGER REFERENCES parties(id)"),
    (20, "نوع صورتحساب تأمین‌کننده و پیوند به فاکتور مبدأ",
     "ALTER TABLE supplier_bills ADD COLUMN bill_type VARCHAR(20) NOT NULL DEFAULT 'expense'"),
    (21, "",
     "ALTER TABLE supplier_bills ADD COLUMN source_invoice_id INTEGER REFERENCES invoices(id)"),
    (22, "تخصیص مستقیم دریافت کانتر به فاکتور همان فروش",
     "ALTER TABLE settlements ADD COLUMN target_invoice_id INTEGER REFERENCES invoices(id)"),
    (23, "روش پرداخت و چک — دریافتی/پرداختی مثل استاندارد",
     "ALTER TABLE settlements ADD COLUMN method VARCHAR(20) DEFAULT 'cash' NOT NULL"),
    (24, "", "ALTER TABLE settlements ADD COLUMN cheque_id INTEGER REFERENCES cheques(id)"),
    (25, "", "ALTER TABLE settlements ADD COLUMN cheque_number VARCHAR(40) DEFAULT '' NOT NULL"),
    (26, "", "ALTER TABLE settlements ADD COLUMN cheque_bank VARCHAR(80) DEFAULT '' NOT NULL"),
    (27, "", "ALTER TABLE settlements ADD COLUMN cheque_branch VARCHAR(80) DEFAULT '' NOT NULL"),
    (28, "", "ALTER TABLE settlements ADD COLUMN cheque_due_date DATE"),
    (29, "تخصیص دستی پیش‌دریافت — شاخص تخصیص برای گزارش سنی و جلوگیری از دوباره‌سنجی",
     "CREATE INDEX IF NOT EXISTS ix_settlement_alloc_target ON settlement_allocations (target_type, target_id)"),
    (30, "لغو جزئی فاکتور — جدول ردیابی لغو بخشی با اسناد و استرداد",
     "CREATE TABLE IF NOT EXISTS invoice_partial_cancels (id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), doc_date DATE NOT NULL, amount_rial INTEGER NOT NULL CHECK(amount_rial>0), penalty_customer_rial INTEGER NOT NULL DEFAULT 0, supplier_refund_rial INTEGER NOT NULL DEFAULT 0, penalty_supplier_rial INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL, entry_id INTEGER REFERENCES journal_entries(id), penalty_entry_id INTEGER REFERENCES journal_entries(id), refund_id INTEGER REFERENCES settlements(id), supplier_entry_id INTEGER REFERENCES journal_entries(id), created_by INTEGER REFERENCES users(id), created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(invoice_id, doc_date, amount_rial, reason))"),
    (31, "تفکیک اسناد تامین — دو ستون جدا برای برگشت و جریمه تامین",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN supplier_refund_entry_id INTEGER REFERENCES journal_entries(id)"),
    (32, "",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN supplier_penalty_entry_id INTEGER REFERENCES journal_entries(id)"),
    (33, "مالیات برگشتی تامین",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN supplier_refund_vat_rial INTEGER NOT NULL DEFAULT 0"),
    (34, "حساب‌های جریمه مجزا — درآمد جریمه مشتری و تامین",
     ""),
    (35, "",
     ""),
    (36, "انتقال برگشت تامین قدیمی به ستون جدید",
     "UPDATE invoice_partial_cancels SET supplier_refund_entry_id = supplier_entry_id WHERE supplier_refund_rial > 0 AND supplier_refund_entry_id IS NULL"),
    (37, "انتقال جریمه تامین قدیمی به ستون جدید",
     "UPDATE invoice_partial_cancels SET supplier_penalty_entry_id = supplier_entry_id WHERE penalty_supplier_rial > 0 AND supplier_refund_rial = 0 AND supplier_penalty_entry_id IS NULL"),
    (38, "اصلاح حساب‌های جریمه — درج مجدد با ستون note",
     ""),
    # --- اصلاح اصولی لغو جزئی: تفکیک لغو از پرداخت، وضعیت‌ها، کارمزد، اعتبار ---
    (39, "وضعیت گردش‌کار لغو جزئی",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN status VARCHAR(14) NOT NULL DEFAULT 'confirmed'"),
    (40, "حالت تسویه استرداد (فوری/بعداً/اعتبار)",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN refund_mode VARCHAR(10) NOT NULL DEFAULT 'immediate'"),
    (41, "کارمزد خدمات کنسلی آژانس",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN agency_fee_rial INTEGER NOT NULL DEFAULT 0"),
    (42, "سند کارمزد آژانس",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN agency_fee_entry_id INTEGER REFERENCES journal_entries(id)"),
    (43, "مانده قابل پرداخت به مشتری",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN refund_due_rial INTEGER NOT NULL DEFAULT 0"),
    (44, "پرداخت‌شده استرداد",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN refund_paid_rial INTEGER NOT NULL DEFAULT 0"),
    (45, "کل طلب از تأمین",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN supplier_claim_rial INTEGER NOT NULL DEFAULT 0"),
    (46, "دریافت‌شده از تأمین",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN supplier_received_rial INTEGER NOT NULL DEFAULT 0"),
    (47, "تفصیلی تأمین مرتبط با لغو",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN supplier_party_id INTEGER REFERENCES parties(id)"),
    (48, "آخرین سند اعتبار مشتری",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN credit_entry_id INTEGER REFERENCES journal_entries(id)"),
    (49, "حساب اعتبار مشتری برای این لغو",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN credit_account_id INTEGER REFERENCES accounts(id)"),
    (50, "مبلغ اعتبار صادرشده",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN credit_issued_rial INTEGER NOT NULL DEFAULT 0"),
    (51, "هزینه‌ی غیرقابل‌استرداد (برآورد ثبت‌شده)",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN nonrefundable_cost_rial INTEGER NOT NULL DEFAULT 0"),
    (52, "اثر خالص لغو بر سود",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN profit_impact_rial INTEGER NOT NULL DEFAULT 0"),
    (53, "اتصال اختیاری لغو به ردیف خدمت فاکتور",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN line_id INTEGER REFERENCES invoice_lines(id)"),
    (54, "وضعیت ذخیره‌شده برای ابطال رسمی (JSON)",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN reversal_state VARCHAR(4000) NOT NULL DEFAULT ''"),
    (55, "دلیل ابطال لغو",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN cancel_reason VARCHAR(400) NOT NULL DEFAULT ''"),
    (56, "پیوند تسویه به لغو جزئی + حساب اعتبار شخص + بازنشانی رکوردهای قدیمی",
     "ALTER TABLE settlements ADD COLUMN partial_cancel_id INTEGER REFERENCES invoice_partial_cancels(id)"),
    (57, "حساب اعتبار مشتری روی پرونده شخص",
     "ALTER TABLE parties ADD COLUMN credit_account_id INTEGER REFERENCES accounts(id)"),
    (58, "بازنشانی مقادیر محاسباتی لغوهای قدیمی (همه‌ی قبلی فوری+تأییدشده فرض می‌شوند)",
     "UPDATE invoice_partial_cancels SET refund_due_rial = amount_rial - penalty_customer_rial, refund_paid_rial = CASE WHEN refund_id IS NOT NULL THEN amount_rial - penalty_customer_rial ELSE 0 END, supplier_claim_rial = supplier_refund_rial + penalty_supplier_rial, profit_impact_rial = penalty_customer_rial + supplier_refund_rial + penalty_supplier_rial - amount_rial WHERE status = 'confirmed' AND refund_due_rial = 0 AND amount_rial > 0"),
    # بازسازی جدول تخصیص برای پذیرش هدف «partial_cancel» (CHECK قدیمی فقط invoice/bill بود)
    (59, "گام ۱ بازسازی تخصیص‌ها — تغییر نام جدول قدیمی",
     "ALTER TABLE settlement_allocations RENAME TO settlement_allocations_old"),
    (60, "گام ۲ — ساخت جدول جدید با محدودیت سه‌حالته",
     "CREATE TABLE settlement_allocations (id INTEGER PRIMARY KEY, settlement_id INTEGER NOT NULL REFERENCES settlements(id), target_type VARCHAR(16) NOT NULL, target_id INTEGER NOT NULL, amount_rial INTEGER NOT NULL DEFAULT 0, CONSTRAINT ck_alloc_target CHECK (target_type in ('invoice','bill','partial_cancel')))"),
    (61, "گام ۳ — انتقال داده‌ها بدون هیچ تغییری",
     "INSERT INTO settlement_allocations (id, settlement_id, target_type, target_id, amount_rial) SELECT id, settlement_id, target_type, target_id, amount_rial FROM settlement_allocations_old"),
    (62, "گام ۴ — حذف جدول قدیمی",
     "DROP TABLE settlement_allocations_old"),
    (63, "گام ۵ — بازیابی شاخص گزارش‌گیری",
     "CREATE INDEX IF NOT EXISTS ix_settlement_alloc_target ON settlement_allocations (target_type, target_id)"),
    # جریمه بر عهده کانتر + حساب مستقل سوخت کنسلی (۵۱۰۹)
    (64, "مبلغ جریمه بر عهده کانتر روی لغو جزئی",
     "ALTER TABLE invoice_partial_cancels ADD COLUMN counter_penalty_rial INTEGER NOT NULL DEFAULT 0"),
    (65, "کانتر مسئول جریمه", "ALTER TABLE invoice_partial_cancels ADD COLUMN counter_party_id INTEGER"),
    (66, "سند مطالبه جریمه از کانتر", "ALTER TABLE invoice_partial_cancels ADD COLUMN counter_penalty_entry_id INTEGER"),
    (67, "سند انتقال سوخت به ۵۱۰۹", "ALTER TABLE invoice_partial_cancels ADD COLUMN burn_entry_id INTEGER"),
    (68, "حساب مستقل سوخت کنسلی (۵۱۰۹) — به CHART در seed منتقل شد تا bootstrap هنگام "
     "استارت هر نصب با ستون‌های کامل بسازد؛ این نسخه عمداً تهی است", ""),
    # پذیرش نوع شخص «employee» در parties — بازسازی CHECK قدیمی به استاندارد ۱۲-مرحله‌ای
    # SQLite (استخراج دلالت‌ها، بازنشازی، بدون RENAME جدول پدر):
    # ‏«parties_t» جدول مبدأ → parties_new → rename به parties. جدول‌های فرزند دست‌نخورده
    # می‌مانند (FK همچنان به نام «parties» اشاره می‌کند و در هر دو مبدأ/مقصد صادق است).
    # پذیرش نوع شخص «employee» در parties — بازسازی بدون RENAME جدول پدر.
    # مرجع‌های جدول‌های فرزند به نام «parties» باقی می‌ماند؛ چون جدول با همان نام دوباره
    # ساخته می‌شود، برای کپی داده‌ها از جدول موقت parties_t استفاده و در پایان پاک می‌شود.
    # خاموش‌کردن بررسی کلید خارجی فقط در پنجره‌ی DROP/CREATE لازم و با پشتیبانیِ از معیار
    # نسخه‌های قبلی انجام می‌گیرد. SQLIte‌ها باید جدول را هم نام نگه داشته باشیم.
    (69, "گام ۱ بازسازی اشخاص — پشتیبان داده‌ها در جدول موقت",
     "CREATE TABLE parties_t AS SELECT * FROM parties"),
    (70, "گام ۲ بازسازی اشخاص — خاموش‌کردن موقت بررسی کلید خارجی (bاشر از تراکنش)",
     "PRAGMA foreign_keys=OFF"),
    (71, "گام ۳ — حذف جدول مبدأ", "DROP TABLE parties"),
    (72, "گام ۴ — ساخت جدول اشخاص با نوع چهاربعدی (با همان نام تا مراجع دست‌نخورده بمانند)",
     "CREATE TABLE parties (id INTEGER PRIMARY KEY, code VARCHAR(30) NOT NULL UNIQUE, name VARCHAR(160) NOT NULL, "
     "kind VARCHAR(12) NOT NULL DEFAULT 'customer', phone VARCHAR(30) NOT NULL DEFAULT '', note VARCHAR(255) NOT NULL DEFAULT '', "
     "account_id INTEGER REFERENCES accounts(id), credit_account_id INTEGER REFERENCES accounts(id), "
     "is_active BOOLEAN NOT NULL DEFAULT (1), created_by INTEGER REFERENCES users(id), created_at DATETIME NOT NULL, "
     "person_type INTEGER NOT NULL DEFAULT (1), national_id VARCHAR(20) DEFAULT '', economic_code VARCHAR(20) DEFAULT '', "
     "postal_code VARCHAR(15) DEFAULT '', passport_no VARCHAR(30) DEFAULT '', "
     "CONSTRAINT ck_party_kind CHECK (kind in ('customer','supplier','both','employee')))"),
    (73, "گام ۵ — بازگردانی داده‌ها از جدول موقت",
     "INSERT INTO parties (id, code, name, kind, phone, note, account_id, credit_account_id, is_active, created_by, "
     "created_at, person_type, national_id, economic_code, postal_code, passport_no) "
     "SELECT id, code, name, kind, phone, note, account_id, credit_account_id, is_active, created_by, "
     "created_at, person_type, national_id, economic_code, postal_code, passport_no FROM parties_t"),
    (74, "گام ۶ — بازیابی شاخص کد اشخاص", "CREATE UNIQUE INDEX IF NOT EXISTS ix_parties_code ON parties (code)"),
    (75, "گام ۷ — روشن‌کردن دوباره‌ی بررسی کلید خارجی (bاشر از تراکنش)",
     "PRAGMA foreign_keys=ON"),
    (76, "گام ۸ — حذف جدول موقت پشتیبان", "DROP TABLE parties_t"),
    # گروه اشخاص (مانند انتخاب‌گر گروه‌های محک) برای صفحه‌ی دریافت/پرداخت و لیست اشخاص
    (77, "گروه اشخاص", "ALTER TABLE parties ADD COLUMN group_name VARCHAR(80) NOT NULL DEFAULT ''"),
    # مشخصات کامل چک (مانند فرم محک): شبا و شناسه صیاد و شماره حساب
    (78, "شماره حساب چک", "ALTER TABLE cheques ADD COLUMN account_number VARCHAR(40) NOT NULL DEFAULT ''"),
    (79, "شماره شبای چک", "ALTER TABLE cheques ADD COLUMN sheba VARCHAR(40) NOT NULL DEFAULT ''"),
    (80, "شناسه صیاد چک", "ALTER TABLE cheques ADD COLUMN sayad_id VARCHAR(40) NOT NULL DEFAULT ''"),
    # دریافت/پرداخت چندابزاری: پیگیری حواله و چسب سند گروهی
    (81, "شماره پیگیری حواله/کارتخوان در تسویه", "ALTER TABLE settlements ADD COLUMN ref_no VARCHAR(60) NOT NULL DEFAULT ''"),
    (82, "برچسب سند چندابزاری تسویه", "ALTER TABLE settlements ADD COLUMN batch_no VARCHAR(40) NOT NULL DEFAULT ''"),
    # تسویه‌ی ارزی (سبک): ارز و نرخ ثبت صورتحساب و ارز و نرخ روز پرداخت در تسویه
    (83, "ارز صورتحساب تأمین‌کننده", "ALTER TABLE supplier_bills ADD COLUMN currency_code VARCHAR(8) NOT NULL DEFAULT 'IRR'"),
    (84, "", "ALTER TABLE supplier_bills ADD COLUMN fx_amount_minor INTEGER NOT NULL DEFAULT 0"),
    (85, "", "ALTER TABLE supplier_bills ADD COLUMN fx_rate_rial INTEGER NOT NULL DEFAULT 0"),
    (86, "هدف تسویه: اتصال مستقیم پرداخت به یک صورتحساب (برای تسویه‌ی ارزی ضروری است)",
     "ALTER TABLE settlements ADD COLUMN target_bill_id INTEGER REFERENCES supplier_bills(id)"),
    (87, "ارز تسویه", "ALTER TABLE settlements ADD COLUMN currency_code VARCHAR(8) NOT NULL DEFAULT 'IRR'"),
    (88, "", "ALTER TABLE settlements ADD COLUMN fx_amount_minor INTEGER NOT NULL DEFAULT 0"),
    (89, "", "ALTER TABLE settlements ADD COLUMN fx_rate_rial INTEGER NOT NULL DEFAULT 0"),
]

_TABLE = "_schema_versions"


def _ensure_version_table(conn) -> None:
    conn.execute(text(f"CREATE TABLE IF NOT EXISTS {_TABLE} (version INTEGER PRIMARY KEY, "
                      "note TEXT, at TEXT)"))


def applied_versions() -> list[int]:
    with engine.begin() as conn:
        _ensure_version_table(conn)
        rows = conn.execute(text(f"SELECT version FROM {_TABLE} ORDER BY version")).fetchall()
    return [r[0] for r in rows]


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def run_migrations() -> list[int]:
    """قدم‌های عقب‌مانده را به ترتیب اجرا می‌کند و فهرست اجراشده‌ها را برمی‌گرداند.

    پیاده‌سازی جدید: هر قدم در تراکنشِ خودش است؛ قدم‌های PRAGMA (مثل خاموش‌کردن موقت بررسی
    کلید خارجی برای بازسازی جدول مرجع) باید بیرون از تراکنش اجرا شوند — با انسداد تراکنش
    قبلی، اجرای autocommit و ثبت نسخه‌ی جداگانه پوشش می‌دهیم.
    """
    done = []

    def _register(cur, version: int, note: str) -> None:
        cur.commit()   # پایان هر تراکنش ضمنی باز (خواندن‌های بررسی) — بعد ثبت اتمیک نسخه
        with cur.begin():
            cur.execute(text(f"INSERT INTO {_TABLE} (version, note, at) "
                             "VALUES (:v, :n, datetime('now'))"),
                        {"v": version, "n": note})

    with engine.connect() as conn:
        with conn.begin():
            # امنیت در برابر شکست میان‌دوره‌ای: باقی‌مانده‌های موقت بازسازی اشخاص پاک می‌شوند
            # (بدون دست زدن به وضعیت foreign_keys — گرداننده‌ی PRAGMA فقط در بدنه‌ی حلقه اجرا می‌شود)
            conn.exec_driver_sql("DROP TABLE IF EXISTS parties_t")
            conn.exec_driver_sql("DROP TABLE IF EXISTS parties_new")
            _ensure_version_table(conn)
        current = {r[0] for r in conn.execute(text(f"SELECT version FROM {_TABLE}")).fetchall()}
        for version, note, sql in MIGRATIONS:
            if version in current or not sql:
                continue
            sql_up = sql.lstrip().upper()
            if sql_up.startswith("PRAGMA"):
                # PRAGMA داخل تراکنش اثر نمی‌کند؛ قبل از آن تراکنش جاری (اگر هست) را کامیت می‌کنیم
                conn.commit()
                conn.exec_driver_sql(sql)
                _register(conn, version, note or sql)
                done.append(version)
                continue
            # اگر ستون از قبل هست (نسخه‌ی تازه‌ی برنامه آن را ساخته)، فقط ثبت می‌شود
            parts = sql.split()
            if parts[0].upper() == "ALTER" and "COLUMN" in [x.upper() for x in parts]:
                table = parts[2]
                column = parts[[x.upper() for x in parts].index("COLUMN") + 1]
                if _column_exists(conn, table, column):
                    _register(conn, version, (note or sql) + " (از قبل موجود بود)")
                    done.append(version)
                    continue
            try:
                conn.commit()      # بستن تراکنش ضمنیِ بررسی‌های قبلی، سپس قدم در تراکنشِ خودش
                with conn.begin():
                    conn.execute(text(sql))
                    conn.execute(text(f"INSERT INTO {_TABLE} (version, note, at) "
                                      "VALUES (:v, :n, datetime('now'))"),
                                 {"v": version, "n": note or sql})
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "duplicate column" in msg:
                    # ستون از قبل هست؛ فقط نسخه ثبت می‌شود
                    _register(conn, version, (note or sql) + " (از قبل موجود بود)")
                    done.append(version)
                    continue
                if "no such table" in msg and any(tbl in sql.lower()
                        for tbl in ("settlement_allocations", "cheques")):
                    # پایگاه‌های خیلی قدیمی این جدول‌ها را ندارند؛ نسخه ثبت می‌شود و ساختار
                    # تازه با migration‌های بعدی یا ساخت اولیه‌ی ORM به دیتابیس می‌رسد
                    _register(conn, version, note + " (جدول در نسخه قدیمی نبود — نادیده گرفته شد)")
                    done.append(version)
                    continue
                raise
            done.append(version)
    if done:
        app_log.info("به‌روزرسانی ساختار انجام شد",
                     extra={"event": "migration", "extra": str(done)})
    return done
