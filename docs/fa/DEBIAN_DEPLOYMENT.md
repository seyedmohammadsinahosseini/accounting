# راهنمای استقرار روی Debian

این راهنما برای استقرار یک‌سروری با SQLite، Nginx و systemd است. پیکربندی نمونه عمداً با یک worker اجرا می‌شود؛ نرخ‌محدودسازی و قفل‌های هم‌زمان جلوگیری از تکرار در حافظه‌ی همان worker هستند. برای چند worker یا چند سرور، باید قفل توزیع‌شده/ذخیره‌ساز idempotency مشترک اضافه شود.

## ۱) ساخت کاربر و نصب برنامه

```bash
sudo adduser --system --group --home /var/lib/agency-accounting --shell /usr/sbin/nologin agency-accounting
sudo install -d -o agency-accounting -g agency-accounting -m 0700 /var/lib/agency-accounting
sudo install -d -o root -g root -m 0755 /opt/agency-accounting
```

کد را در `/opt/agency-accounting` قرار دهید، سپس:

```bash
sudo python3 -m venv /opt/agency-accounting/.venv
sudo /opt/agency-accounting/.venv/bin/pip install -r /opt/agency-accounting/requirements.txt
```

برنامه به `cryptography` نیاز دارد؛ نصب ناموفق این وابستگی باید قبل از استفاده رفع شود. برنامه در حالت تولید، نبودن رمزنگاری را نمی‌پذیرد.

## ۲) تنظیم محیط و مدیر اولیه

```bash
sudo install -o root -g agency-accounting -m 0640 \
  /opt/agency-accounting/deploy/debian/agency-accounting.env.example \
  /etc/agency-accounting.env
sudoedit /etc/agency-accounting.env
```

در فایل، نام کاربری و یک رمز قوی و یکتا برای `ACCT_BOOTSTRAP_ADMIN_PASSWORD` بگذارید. نمونه‌ی جای‌نگهدار را عیناً به کار نبرید. روی پایگاه داده‌ی تازه، برنامه فقط در صورتی بالا می‌آید که این دو مقدار تنظیم شده باشند. پس از اولین راه‌اندازی موفق و تغییر رمز مدیر از داخل سامانه، دو خط `ACCT_BOOTSTRAP_ADMIN_*` را از فایل محیطی حذف کنید.

پیکربندی تولیدی باید دست‌کم شامل این موارد باشد:

- `ACCT_DEMO_SEED=0` تا حساب‌های نمونه و داده‌ی نمایشی ساخته نشوند.
- `ACCT_DATA_DIR`, `ACCT_DB`, `ACCT_BACKUP_DIR`, `ACCT_LOG_DIR` در فضای دیسکِ محدود به کاربر سرویس.
- `ACCT_ALLOW_PLAINTEXT_PII=0` و `ACCT_CORS_ORIGINS=`؛ CORS پیش‌فرض بسته است.
- در صورت استفاده از فرانت‌اند جدا، فقط مبدأهای دقیق HTTPS را در `ACCT_CORS_ORIGINS` بنویسید؛ از `*` استفاده نکنید.

## ۳) سرویس وب و HTTPS

نمونه‌های systemd در `deploy/debian/` هستند:

```bash
sudo install -o root -g root -m 0644 deploy/debian/agency-accounting.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/debian/agency-accounting-backup.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/debian/agency-accounting-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agency-accounting.service
```

نمونه‌ی Nginx در `deploy/debian/nginx-accounting.conf.example` است. نام دامنه و گواهی TLS را جایگزین کنید، سپس فقط پورت‌های SSH، HTTP و HTTPS را در فایروال باز بگذارید. برنامه‌ی Uvicorn روی `127.0.0.1:8000` گوش می‌دهد و مستقیماً از اینترنت در دسترس نیست.

## ۴) پشتیبان‌گیری زمان‌بندی‌شده و بررسی صحت

Timer روزانه در ساعت ۰۲:۳۰ (با تأخیر تصادفی حداکثر ۱۵ دقیقه) پشتیبان می‌سازد:

```bash
sudo systemctl enable --now agency-accounting-backup.timer
systemctl list-timers agency-accounting-backup.timer
sudo journalctl -u agency-accounting-backup.service
```

هر پشتیبان محلی:

1. با SQLite Backup API و snapshot سازگار ساخته می‌شود؛ کپی‌کردن خام فایل فعال یا فایل WAL روش مورد اتکا نیست.
2. قبل از انتشار، با `PRAGMA integrity_check`، بررسی جدول‌های اصلی و `PRAGMA foreign_key_check` آزموده می‌شود.
3. به‌صورت اتمی ذخیره می‌شود، مجوز فایل محدود می‌گیرد و یک checksum جداگانه‌ی SHA-256 دارد.
4. اگر اطلاعات هویتی رمز‌شده وجود داشته باشد، کلید رمزنگاری نیز به‌صورت sidecar همراه آن قرار می‌گیرد. کلید و پشتیبان باید هر دو محافظت شوند.
5. طبق `ACCT_BACKUP_KEEP` تعداد نسخه‌های قدیمی محلی پاک می‌شود.

بررسی دستی آخرین نسخه:

```bash
sudo -u agency-accounting bash -lc 'set -a; . /etc/agency-accounting.env; set +a; cd /opt/agency-accounting; .venv/bin/python -m app.backup_cli --verify'
```

در صفحه‌ی «مدیریت سامانه ← پشتیبان و سلامت داده» نیز وضعیت پایگاه، مجوزها، کلید، پشتیبان آخر و فضای دیسک نمایش داده می‌شود. پایش محلی جای نسخه‌ی خارج از سرور را نمی‌گیرد. برای پشتیبان خارج از سرور، پوشه‌ی `backups/` (شامل فایل‌های `.db`, `.sha256`, `.key`) را با ابزاری مانند Restic در مخزن رمزگذاری‌شده‌ی جداگانه بفرستید؛ مخزن Restic باید گذرواژه و مقصد خارج از همان ماشین داشته باشد. پوشه‌ی داده‌ی زنده را بدون رمزنگاری روی سرویس عمومی همگام‌سازی نکنید.

## ۵) تمرین بازیابی

بازیابی را ابتدا در یک سرور آزمایشی با نسخه‌ی متناظر برنامه تمرین کنید. روش برنامه‌ای:

```bash
sudo systemctl stop agency-accounting.service
sudo -u agency-accounting bash -lc 'set -a; . /etc/agency-accounting.env; set +a; cd /opt/agency-accounting; .venv/bin/python -c "from app.backup import restore; restore(\"accounting-scheduled-نام-فایل.db\")"'
sudo systemctl start agency-accounting.service
```

تابع `restore()` پیش از جایگزینی فایل زنده، خودِ پشتیبان را بررسی می‌کند و اگر پایگاه فعلی موجود باشد از آن نیز نسخه‌ی `before-restore` می‌گیرد. پس از بازیابی، ورود کاربر، تراز آزمایشی، فاکتورهای اخیر، گزارش سلامت و بازشدن اطلاعات هویتی مجاز را کنترل کنید. فایل کلید را جداگانه حذف یا جایگزین نکنید؛ بدون آن داده‌های رمز‌شده قابل خواندن نیستند.

## ۶) ثبت‌های عملیاتی و آزمون

- سرویس را با یک worker اجرا کنید؛ چند worker فقط پس از افزودن قفل idempotency توزیع‌شده مجاز است.
- فایل‌های برنامه فقط‌خواندنی‌اند؛ داده، لاگ و پشتیبان در `/var/lib/agency-accounting` با مجوز محدود نگه داشته می‌شود.
- خطاها و لاگ‌های امنیتی را با `journalctl -u agency-accounting.service` و فایل‌های چرخشی داخل پوشه‌ی log ببینید. لاگ‌ها نباید حاوی رمز، توکن یا شماره‌ی کامل ملی/گذرنامه باشند.
- پیش از انتشار هر نسخه، از نسخه‌ی فعلی پشتیبان بگیرید، مهاجرت را روی کپی آزمایش کنید و آزمون‌ها را اجرا کنید:

```bash
cd /opt/agency-accounting
.venv/bin/python -m pytest tests -q
```

## ۷) دامنه‌ی فعلی محصول

کد فعلی سامانه‌ی حسابداری آژانس مسافرتی است: فاکتور خدمات سفر، پرونده‌ی سفر، بلیت/تور/اقامت در حسابداری، هزینه، دریافت و پرداخت و گزارش‌ها را دارد. این کد به‌تنهایی سامانه‌ی کامل مدیریت هتل نیست؛ رزرو اتاق و ظرفیت، پذیرش و تسویه‌ی folio مهمان، مدیریت رستوران/وعده و POS هتل هنوز نیازمند تعریف فرایند و توسعه‌ی مجزا هستند.
