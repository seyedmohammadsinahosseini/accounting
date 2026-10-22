"""تنظیمات برنامه — همه‌چیز از محیط قابل تغییر است، هیچ رمزی داخل کد نیست."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # پوشه‌ی accounting
DATA_DIR = Path(os.environ.get("ACCT_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("ACCT_DB", DATA_DIR / "accounting.db"))
BACKUP_DIR = Path(os.environ.get("ACCT_BACKUP_DIR", DATA_DIR / "backups"))
LOG_DIR = Path(os.environ.get("ACCT_LOG_DIR", DATA_DIR / "logs"))

APP_NAME = "سامانه‌ی حسابداری آژانس مسافرتی"
CURRENCY_LABEL = "ریال"

# نشست کاربر: چند ساعت بی‌کاری یعنی خروج خودکار
SESSION_TTL_HOURS = int(os.environ.get("ACCT_SESSION_HOURS", "12"))

# پشتیبان‌گیری خودکار
BACKUP_ENABLED = os.environ.get("ACCT_BACKUP_ENABLED", "1") == "1"
BACKUP_EVERY_HOURS = int(os.environ.get("ACCT_BACKUP_EVERY_HOURS", "20"))
BACKUP_KEEP = int(os.environ.get("ACCT_BACKUP_KEEP", "30"))

# محدودیت نرخ (لایه ۰۹) — سقف درخواست برای جلوگیری از سوءاستفاده و خرابی هزینه
RATE_LOGIN_PER_15MIN = int(os.environ.get("ACCT_RATE_LOGIN", "10"))
RATE_API_PER_MIN = int(os.environ.get("ACCT_RATE_API", "240"))
IDEMPOTENCY_TTL_HOURS = int(os.environ.get("ACCT_IDEMPOTENCY_TTL_HOURS", "24"))

# مبدأهای مجاز برای CORS. برنامه‌ی اصلی هم‌مبدأ است؛ برای API جداگانه، فهرست مبدأها را
# در تنظیمات سرور صریح وارد کنید، نه با wildcard همراه اعتبارنامه.
CORS_ORIGINS = [x.strip() for x in os.environ.get("ACCT_CORS_ORIGINS", "").split(",") if x.strip()]
ALLOW_PLAINTEXT_PII = os.environ.get("ACCT_ALLOW_PLAINTEXT_PII", "0") == "1"

# نسخه‌ی نمایشی: داده‌ی نمونه و کاربران آزمایشی می‌سازد
ENABLE_DOCS = os.environ.get("ACCT_ENABLE_DOCS", "1") == "1"
DEMO_SEED = os.environ.get("ACCT_DEMO_SEED", "1") == "1"
BOOTSTRAP_ADMIN_USERNAME = os.environ.get("ACCT_BOOTSTRAP_ADMIN_USERNAME", "").strip().lower()
BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("ACCT_BOOTSTRAP_ADMIN_PASSWORD", "")


def ensure_dirs() -> None:
    for d in (DATA_DIR, BACKUP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            try:
                d.chmod(0o700)
            except OSError:
                pass
