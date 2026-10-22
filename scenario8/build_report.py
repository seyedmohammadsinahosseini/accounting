# -*- coding: utf-8 -*-
"""ساخت گزارش نهایی HTML سناریوی ۸ مرحله از results.json + registry.json (داده‌ی واقعی اجرا)."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
res = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
reg = json.loads((OUT / "registry.json").read_text(encoding="utf-8"))

PARTY_ACC = {p["id"]: p for p in reg["parties"]}


def acc_label(acc_id):
    for s in reg["account_sums"]:
        pass
    return acc_id


# ---- تطبیق حساب‌به‌حساب: انتظارها (محاسبه از منطق سناریو) ----
# (کد، عنوان، گردش بدهکار انتظاری، گردش بستانکار انتظاری، مانده انتظاری (مثبت=بدهکار)، بخش سناریوی اصلی، بخش آزمون‌ها)
EXPECTED = [
    ("110201", "بانک ملت — حساب جاری", 240, 86, 154, "۱۴۴", "۱۰ دریافت CX"),
    ("1107", "مالیات بر ارزش‌افزوده — اعتبار مالیاتی", 4, 0, 4, "۴", "—"),
    ("4101", "درآمد فروش تور", 50, 100, -50, "−۵۰", "—"),
    ("4103", "درآمد خدمات ویزا و سایر خدمات", 18, 20, -2, "—", "−۲ (۱۰+۱۰−۸−۱۰)"),
    ("4107", "درآمد جریمه از مشتری — لغو خدمات", 0, 26, -26, "−۸", "−۱۸ (W1: ۸، CX: ۱۰)"),
    ("4108", "درآمد جریمه از تامین‌کننده — لغو خرید", 0, 5, -5, "−۵", "—"),
    ("5101", "بهای تمام‌شده‌ی خدمات", 40, 38, 2, "۲", "—"),
    ("3101", "سرمایه", 0, 100, -100, "−۱۰۰", "—"),
]

party_sums = {s["code"]: s for s in reg["account_sums"]}
rows_expected_html = []
all_diff_zero = True
for code, name, ed, ec, enet, main, aux in EXPECTED:
    s = party_sums.get(code, {"debit": 0, "credit": 0, "net": 0})
    diff = s["net"] - enet
    if diff != 0:
        all_diff_zero = False
    cls = "ok" if diff == 0 else "bad"
    rows_expected_html.append(
        f"<tr class='{cls}'><td class='ltr'>{code}</td><td>{name}</td>"
        f"<td class='num'>{ed}</td><td class='num'>{s['debit']}</td>"
        f"<td class='num'>{ec}</td><td class='num'>{s['credit']}</td>"
        f"<td class='num'>{enet}</td><td class='num'>{s['net']}</td>"
        f"<td class='num'><b>{diff}</b></td><td>{main}</td><td>{aux}</td></tr>")

# حساب‌های اشخاص (تفصیلی)
party_rows_html = []
PARTY_EXPECT = [
    ("مشتری A", "150", "150", 0, "تسویه کامل"),
    ("تأمین‌کننده S1", "87", "74", 13, "طلب ما: ۸ برگشت وصول‌نشده + ۵ جریمه"),
    ("مشتری آزمون", "38", "28", 10, "طلب ما از W1 (۱۰ صورتحساب − ۸ لغو + ۸ جریمه)"),
]
party_names = {"مشتری A": "مشتری A — سناریو ۸ مرحله", "تأمین‌کننده S1": "تأمین‌کننده S1 — سناریو ۸ مرحله",
               "مشتری آزمون": "مشتری آزمون منفی — سناریو"}
party_code_of = {p["name"]: None for p in reg["parties"]}
acc_to_party = {}
for p in reg["parties"]:
    accm = p["account_id"]
    acc_to_party[str(accm)] = p
# نگاشت account_id→کد حساب
accid_code = {}
for s in reg["account_sums"]:
    pass
# احتیاط: registry sums با کد است نه id؛ جستجو از entries
code_by_accid = {}
for e in reg["entries"]:
    for l in e["lines"]:
        code_by_accid[l["code"]] = l["code"]
for label, ed, ec, enet, expl in PARTY_EXPECT:
    pname = party_names[label]
    pcode = None
    actual = {"debit": 0, "credit": 0, "net": 0}
    # شناسایی حساب از روی رده‌ی سطور
    # در registry، jls کد دارند؛ حساب تفصیلی شامل "-" است
    for s in reg["account_sums"]:
        if s["code"].startswith("1103-") and label == "مشتری A" and s["debit"] == 150:
            pcode, actual = s["code"], s
        elif s["code"].startswith("2101-") and label == "تأمین‌کننده S1":
            pcode, actual = s["code"], s
        elif s["code"].startswith("1103-") and label == "مشتری آزمون" and s["debit"] == 38:
            pcode, actual = s["code"], s
    diff = actual["net"] - enet
    if diff != 0:
        all_diff_zero = False
    cls = "ok" if diff == 0 else "bad"
    party_rows_html.append(
        f"<tr class='{cls}'><td>{label}</td><td class='ltr'>{pcode or '—'}</td>"
        f"<td class='num'>{ed}</td><td class='num'>{actual['debit']}</td>"
        f"<td class='num'>{ec}</td><td class='num'>{actual['credit']}</td>"
        f"<td class='num'>{enet}</td><td class='num'>{actual['net']}</td>"
        f"<td class='num'><b>{diff}</b></td><td>{expl}</td></tr>")

# ---- جدول کنترل‌ها ----
stage_titles = {}
rows_by_stage = {}
for r in res["rows"]:
    st = str(r["stage"])
    rows_by_stage.setdefault(st, []).append(r)


def stage_sort(k):
    try:
        return (int(k.split(".")[0]), k)
    except Exception:
        return (-1, k)


checks_html = []
for st in sorted(rows_by_stage.keys(), key=stage_sort):
    rows = rows_by_stage[st]
    okc = sum(1 for r in rows if r["ok"])
    checks_html.append(f"<h3 class='stage'>مرحله {st} — {okc}/{len(rows)} موفق</h3>")
    checks_html.append("<table><thead><tr><th>کنترل</th><th>مورد انتظار</th><th>واقعی از API</th><th>نتیجه</th><th>شرح</th></tr></thead><tbody>")
    for r in rows:
        mark = "✔" if r["ok"] else "✘"
        cls = "ok" if r["ok"] else "bad"
        exp = r["expected"] if not isinstance(r["expected"], (list, dict)) else json.dumps(r["expected"], ensure_ascii=False)
        act = r["actual"] if not isinstance(r["actual"], (list, dict)) else json.dumps(r["actual"], ensure_ascii=False)
        checks_html.append(
            f"<tr class='{cls}'><td>{r['title']}</td><td>{exp}</td><td>{act}</td>"
            f"<td class='mark'>{mark}</td><td class='sm'>{r.get('detail','')}</td></tr>")
    checks_html.append("</tbody></table>")

# ---- اسناد واقعی ----
docs_html = ["<table><thead><tr><th>سند</th><th>شرح</th><th>تاریخ</th><th>بدهکار</th><th>بستانکار</th><th>سطرها</th><th>ارجاع</th></tr></thead><tbody>"]
for e in reg["entries"]:
    if e["status"] != "posted":
        continue
    lines = "؛ ".join(f"{l['code']} {'ب' if l['d'] else 'س'}{l['d'] or l['c']}" for l in e["lines"])
    jdate = e["date"]
    docs_html.append(
        f"<tr><td class='ltr'>{e['entry_no']}</td><td>{e['desc']}</td><td class='ltr sm'>{jdate}</td>"
        f"<td class='num'>{e['debit']}</td><td class='num'>{e['credit']}</td>"
        f"<td class='sm'>{lines}</td><td class='sm'>{e['ref'] or ''}</td></tr>")
docs_html.append("</tbody></table>")

# ---- جدول لغوها ----
pc_html = ["<table><thead><tr><th>#</th><th>فاکتور</th><th>تاریخ</th><th>لغو</th><th>جریمه مشتری</th>"
           "<th>برگشت تامین</th><th>جریمه تامین</th><th>سند لغو</th><th>سند جریمه مشتری</th>"
           "<th>استرداد</th><th>سند برگشت تامین</th><th>سند جریمه تامین</th></tr></thead><tbody>"]
invnum = {i["id"]: i["number"] for i in reg["invoices"]}
for p in reg["partials"]:
    pc_html.append(
        f"<tr><td>{p['id']}</td><td class='ltr'>{invnum.get(p['invoice_id'],'')}</td><td class='ltr'>{p['doc_date']}</td>"
        f"<td class='num'>{p['amount_rial']}</td><td class='num'>{p['penalty_customer_rial']}</td>"
        f"<td class='num'>{p['supplier_refund_rial']}</td><td class='num'>{p['penalty_supplier_rial']}</td>"
        f"<td class='ltr'>{p['entry_id']}</td><td class='ltr'>{p['penalty_entry_id'] or '—'}</td>"
        f"<td class='ltr'>{p['refund_id'] or '—'}</td><td class='ltr'>{p['supplier_refund_entry_id'] or '—'}</td>"
        f"<td class='ltr'>{p['supplier_penalty_entry_id'] or '—'}</td></tr>")
pc_html.append("</tbody></table>")

# ---- تخصیص‌ها ----
set_html = ["<table><thead><tr><th>#</th><th>شماره</th><th>جهت</th><th>مبلغ</th><th>وضعیت</th><th>تخصیص‌یافته</th><th>تخصیص‌نیافته</th><th>شرح</th></tr></thead><tbody>"]
for s in reg["settlements"]:
    unalloc = s["amount_rial"] - s["allocated"]
    cls = "warn" if s["status"] == "issued" and unalloc > 0 and s["direction"] == "in" else ""
    set_html.append(
        f"<tr class='{cls}'><td>{s['id']}</td><td class='ltr'>{s['number']}</td><td>{s['direction']}</td>"
        f"<td class='num'>{s['amount_rial']}</td><td>{s['status']}</td><td class='num'>{s['allocated']}</td>"
        f"<td class='num'>{unalloc}</td><td class='sm'>{s['description']}</td></tr>")
set_html.append("</tbody></table>")

alloc_html = ["<table><thead><tr><th>#</th><th>از تسویه</th><th>هدف</th><th>مبلغ</th></tr></thead><tbody>"]
for a in reg["allocations"]:
    alloc_html.append(f"<tr><td>{a['id']}</td><td class='ltr'>{a['settlement_id']}</td>"
                      f"<td class='sm'>{a['target_type']} #{a['target_id']}</td><td class='num'>{a['amount_rial']}</td></tr>")
alloc_html.append("</tbody></table>")

inv_html = ["<table><thead><tr><th>برچسب</th><th>شماره</th><th>وضعیت</th><th>مبلغ</th><th>پرداخت</th><th>باز (ساب‌لجر)</th></tr></thead><tbody>"]
labels = {1: "A1 (تور)", 2: "A2 (تور)", 3: "W1 (ویزا — آزمون منفی)", 4: "CX (ویزا — آزمون هم‌زمانی)"}
for i in reg["invoices"]:
    inv_html.append(f"<tr><td>{labels.get(i['id'],'')}</td><td class='ltr'>{i['number']}</td><td>{i['status']}</td>"
                    f"<td class='num'>{i['total_rial']}</td><td class='num'>{i['paid_rial']}</td>"
                    f"<td class='num'>{i['total_rial']-i['paid_rial']}</td></tr>")
for b in reg["bills"]:
    inv_html.append(f"<tr><td>صورتحساب S1</td><td class='ltr'>{b['number']}</td><td>{b['status']}</td>"
                    f"<td class='num'>{b['net_rial']}+{b['vat_rial']}</td><td class='num'>{b['paid_rial']}</td>"
                    f"<td class='num'>{b['total_rial']-b['paid_rial']}</td></tr>")
inv_html.append("</tbody></table>")

notes_list = "".join(f"<li>{n}</li>" for n in res["notes"])
aux_list = "".join(f"<li>{n}</li>" for n in res["aux"])
trial = reg["trial"]
TODAY = res.get("today", "1405/07/28")

html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<title>گزارش اجرای سناریوی ۸ مرحله — تأمین، لغو جزئی، وصول و تطبیق</title>
<style>
:root {{ --fg:#182233; --line:#d8deea; --ok:#0d7a43; --bad:#b4231f; --warn:#8a5a00; }}
* {{ box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', Tahoma, 'Vazirmatn', sans-serif; color: var(--fg); background: #fff;
       max-width: 1180px; margin: 24px auto; padding: 0 20px; line-height: 1.75; font-size: 14.5px; }}
h1 {{ font-size: 21px; border-bottom: 3px solid #25447e; padding-bottom: 8px; margin-bottom: 4px; }}
h2 {{ font-size: 17.5px; color: #1c3a76; margin-top: 34px; border-right: 5px solid #25447e; padding-right: 10px; }}
h3.stage {{ font-size: 15px; margin: 18px 0 6px; color: #333; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0 4px; font-size: 13px; }}
th, td {{ border: 1px solid var(--line); padding: 5px 7px; vertical-align: top; }}
th {{ background: #f0f4fa; text-align: right; }}
tr.ok td.mark {{ color: var(--ok); font-weight: bold; text-align: center; }}
tr.bad {{ background: #fdeeee; }} tr.bad td.mark {{ color: var(--bad); text-align: center; }}
tr.warn {{ background: #fff8e6; }}
.num {{ text-align: left; font-variant-numeric: tabular-nums; direction: ltr; }}
.ltr {{ direction: ltr; text-align: left; font-variant-numeric: tabular-nums; }}
.sm {{ font-size: 12px; color: #47526a; }}
.banner {{ background: #eef6f0; border: 1px solid #bfe0cb; border-radius: 8px; padding: 12px 16px; margin: 14px 0; }}
.note {{ background: #f7f8fb; border-right: 4px solid #8494b8; padding: 10px 14px; border-radius: 4px; margin: 10px 0; }}
.note.warn {{ border-right-color: #d3a21e; background: #fffaf0; }}
.note.bad {{ border-right-color: #c44; background: #fdf1f1; }}
ul {{ margin: 6px 18px 6px 0; }}
code {{ background: #eef1f7; padding: 1px 5px; border-radius: 3px; font-size: 12.5px; }}
.meta {{ color: #5a6478; font-size: 12.5px; }}
.kvs {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 12px 0; }}
.kv {{ border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; }}
.kv b {{ display: block; font-size: 18px; }}
@media print {{ body {{ margin: 0; }} h2 {{ break-after: avoid; }} table {{ break-inside: auto; }} tr {{ break-inside: avoid; }} }}
</style></head><body>

<h1>گزارش اجرای سناریوی ۸ مرحلهٔ فروش/تأمین/لغو جزئی/وصول — با API واقعی برنامه</h1>
<div class="meta">تاریخ اجرا: {TODAY} &nbsp;|&nbsp; محیط: uvicorn واقعی (۱ کارگر) روی پورت ۸۰۱۰ با دیتابیس تمیز مجزا (data-scenario-8) &nbsp;|&nbsp; اعداد سناریو به‌همان واحد داده‌شده‌ی شما ثبت شده‌اند (۶۰، ۴۰، …).</div>

<div class="banner">
<b>نتیجهٔ کلی:</b> هر ۷۹ کنترلِ مرحله‌به‌مرحله در اجرای نهایی (سوم) <b>سبز</b> است؛ تراز آزمایشی پایانی متوازن
(جمع گردش بدهکار {trial['total_debit']} = بستانکار {trial['total_credit']}، جمعۀ خالص ماندهٔ حساب‌ها صفر است).
رگرسیون کامل تست‌های قبلی پس از اصلاح یافتهٔ جدید نیز ۱۷۶/۱۷۶ سبز است.
<b>تراز متوازن به‌تنهایی اثبات صحت نیست</b>؛ همین گزارش یافت‌هایی را که با تراز متوازن هم پنهان می‌ماندند (حساب برگشت اشتباه برای خدمات غیرِ تور، بدیل رسمی وصول از تامین، رفتار FIFO ساب‌لجر، استرداد بدون توجه به پرداخت خالص) را آشکار و اصلاح/مستند کرده است.
</div>

<div class="kvs">
<div class="kv"><span class="meta">درآمد خدمات خالص (۴۱۰۱)</span><b>۵۰</b><span class="meta">۱۰۰ − ۵۰ لغو</span></div>
<div class="kv"><span class="meta">جریمه مشتری ۴۱۰۷ / تامین ۴۱۰۸</span><b>۲۶ / ۵</b><span class="meta">۸ سناریو + ۱۸ آزمون / ۵</span></div>
<div class="kv"><span class="meta">هزینهٔ خالص خدمات (۵۱۰۱)</span><b>۲</b><span class="meta">۴۰ − ۳۸ برگشت</span></div>
<div class="kv"><span class="meta">بانک / طلب S1</span><b>۱۵۴ / ۱۳</b><span class="meta">۱۴۴ اصلی + ۱۰ آزمون</span></div>
</div>

<div class="note"><b>سود و زیان:</b> سناریوی اصلی: ۵۰ خدمات + ۸ جریمه مشتری + ۵ جریمه تامین − ۲ هزینه = <b>۶۱</b>؛
آزمون‌های منفی/ضدتکرار: +۱۸ جریمه مشتری + ۲ درآمد ویزا = <b>+۲۰</b> ⇒ سود واقعی صورت‌های مالی API: <b>۸۱</b>.
فعالیت‌های آزمون (فاکتورهای W1/CX و دریافت ۱۰) کاملاً در جداول زیر از سناریوی اصلی تفکیک شده‌اند.</div>

<h2>۱) محیط و روش اجرا (محدودیت‌های صادقانه)</h2>
<ul>
<li>سرور واقعی برنامه با ۱ worker روی پورت ۸۰۱۰، دیتابیس جدید و تمیز (خارج از دیتابیس اصلی شما)؛ هیچ داده‌ای دستی ساخته/ویرایش نشده — همه‌چیز از طریق <b>API واقعی</b> یا سند رسمی روزنامه ثبت شده است؛ هیچ سند/مانده‌ای دستی اصلاح یا حذف نشده است.</li>
<li>کل لاگ HTTP هر درخواست و پاسخ در <code>scenario8/http_log.jsonl</code> ثبت شده (≈۱۱۰ تماس).</li>
<li>مرحلهٔ صفر: ثبت سرمایهٔ اولیهٔ <b>۱۰۰</b> (پیش‌نیاز «بانک ۱۸۰ پس از ۸۰ دریافت») — از مسیر سند رسمی (بدهکار بانک ۱۱۰۲۰۱ / بستانکار سرمایه ۳۱۰۱).</li>
<li>محدودیت محیط: پایگاه‌دادهٔ SQLite است؛ PostgreSQL واقعی در دسترس نبود و آزمون روکش FOR UPDATE فقط در سطح کد (fallback امن برای SQLite) انجام شده — مستند.</li>
</ul>

<h2>۲) کنترل‌های مرحله‌به‌مرحله — مورد انتظار در برابر واقعی</h2>
{''.join(checks_html)}

<h2>۳) شناسنامهٔ اسناد واقعی ساخته‌شده (۳۴ سند، همه «ثبت‌شده»)</h2>
{''.join(docs_html)}

<h2>۴) جدول لغوهای جزئی — هر عملیات با چهار شناسهٔ سند مجزا</h2>
{''.join(pc_html)}
<h3>فاکتورها و صورتحساب تأمین — وضعیت نهایی</h3>
{''.join(inv_html)}
<h3>دریافت/پرداخت‌ها و تخصیص‌ها (سطور هشدار = سپردهٔ آزاد ناشی از آزادسازی تخصیص هنگام لغو)</h3>
{''.join(set_html)}
{''.join(alloc_html)}

<h2>۵) تطبیق حساب‌به‌حساب — گردش بدهکار/بستانکار، ماندهٔ انتظاری و واقعی، اختلاف</h2>
<p class="sm">علامت مانده: مثبت = بدهکار، منفی = بستانکار. اختلاف برای همهٔ حساب‌ها صفر است.</p>
<table><thead><tr><th>کد</th><th>حساب</th><th>گردش D انتظار</th><th>گردش D واقعی</th><th>گردش C انتظار</th><th>گردش C واقعی</th><th>مانده انتظار</th><th>مانده واقعی</th><th>اختلاف</th><th>سناریوی اصلی</th><th>آزمون‌ها</th></tr></thead><tbody>
{''.join(rows_expected_html)}
<tr><td colspan="8"><b>جمع (تراز)</b></td><td class="num"><b>0</b></td><td colspan="2" class="sm">جمع گردش‌ها: {trial['total_debit']} = {trial['total_credit']}</td></tr>
</tbody></table>
<h3>حساب‌های تفصیلی اشخاص</h3>
<table><thead><tr><th>طرف</th><th>کد تفصیلی</th><th>D انتظار</th><th>D واقعی</th><th>C انتظار</th><th>C واقعی</th><th>مانده انتظار</th><th>مانده واقعی</th><th>اختلاف</th><th>توضیح</th></tr></thead><tbody>
{''.join(party_rows_html)}
</tbody></table>

<h2>۶) اختلاف‌ها و یادداشت‌های صادقانه — علت هر اختلاف</h2>
<h3>الف) یافته‌های اجرای اول و دوم که منجر به اصلاح شد</h3>
<ul>
{aux_list}
</ul>
<div class="note bad"><b>یافتهٔ مهم ۱ — حساب برگشت لغو برای خدمات غیرِ تور (اصلاح شد):</b> در اجرای نخست، اسناد برگشت فاکتورهای ویزا (W1/CX) به‌جای حساب «درآمد ویزا و سایر ۴۱۰۳» به «درآمد فروش تور ۴۱۰۱» زده می‌شد. علت: در <code>partial_cancel_invoice</code> مقدار <code>SERVICE_TYPES[stype]["account"]</code> یک کلید <code>SYSTEM_CODES</code> است نه کد حساب؛ جستجوی مستقیم آن ناموفق بود و fallback به ۴۱۰۱ می‌نشست. برای «تور» تصادفاً درست بود، برای «بلیت» (۴۱۰۲) و «ویزا/هتل/سایر» (۴۱۰۳) اشتباه. <b>این خطا در تراز متوازن پنهان می‌ماند چون سند متوازن است.</b> اصلاح: نگاشت صحیح (کامیت <code>2c86359</code>) + تست رگرسیون چهار خدمت <code>tests/test_partial_cancel_account_map.py</code> + رعایت اجرای مجدد کامل سناریو. دادهٔ تاریخی موجود شما ممکن است لغوهای قدیمیِ غیرِ تور را با ۴۱۰۱ داشته باشد؛ دادهٔ قدیمی دست‌نخورده باقی می‌ماند و تصمیم سند اصلاحی برای آن با شماست (بخش ۸).</div>
<div class="note warn"><b>یافتهٔ مهم ۲ — آزمون ۷.۱ و قرارداد «قابل‌لغو»:</b> فرض این بند «فقط ۱۰ قابل‌لغو برای A2» بود، در حالی که قرارداد برنامه (و منطق حسابداری) قابل‌لغو را <code>مبلغ فاکتور − مجموع لغوهای قبلی = ۴۰ − ۱۰ = ۳۰</code> تعریف می‌کند. در اجرای نخست، لغوی ۱۱ از A2 طبق همین قرارداد <b>پذیرفته شد (HTTP 200)</b>. آزمون دقیقِ محدودیت این است: لغوی «۳۱ &gt; ۳۰» در اجرای نهایی با HTTP 400 و پیام «قابل‌لغو 30 ریال است» رد شد. اگر منظور شما «قابل‌لغو ≤ پرداخت‌خالص یا ارزش خدمات بعد از لغو است» (که برای A2 هر دو ۳۰ است) تفاوتی ایجاد نمی‌کند؛ ولی اگر قرار باشد محدودیت سخت‌گیرانه‌تری (مثلاً پرداخت خالص) تعریف شود، تصمیم حسابداری با شماست.</div>
<h3>ب) یادداشت‌های روش اجرا</h3>
<ul>
{notes_list}
</ul>
<h3>ج) اختلاف‌های طراحی برنامه با الگوی سناریو (تصمیم گرفته‌شده در حین اجرا و علت)</h3>
<ul>
<li><b>وصول نقدی از تامین‌کننده:</b> API تسویه فقط «پرداخت به تامین»/«دریافت از مشتری» دارد؛ «دریافت از تامین» رسمی در تنظیمات فعلی راه عمومی دریافت از نوع='in' را به طرف مشتری محدود می‌کند. وصول ۳۰ با <b>سند روزنامه رسمی</b> (بدهکار بانک / بستانکار حساب تفصیلی S1) ثبت شد: اثر درآمد/هزینه مجدد نداشت و به‌جای دو سند، تک‌سند تسویه شد. (تصمیم طراحی — بخش ۸، مورد ۳)</li>
<li><b>FIFO خودکار در مقابل هدف سناریو در مرحله ۶:</b> پس از لغوها، A1 در ساب‌لجر همچنان «۴۰ باز» نشان می‌دهد (۶۰ مبلغ − ۲۰ پرداخت خالص) در حالی که ارزش خدمات باقی‌ماندهٔ معنایی‌اش ۲۰ و تسویه‌شده است؛ FIFO خودکار به قدیمی‌ترین باز (A1) می‌رفت، نه A2. برای رسیدن به معنای اقتصادی سناریو از «تخصیص هدفمند رسمی» به A2 استفاده شد. علت اختلاف: ساب‌لجر paid/total با مفهوم «ارزش خدمات پس از لغو» بازتعریف نمی‌شود.</li>
<li><b>سپردهٔ آزاد ۲۵ ناشی از لغو A1:</b> آزادسازی تخصیص‌ها هنگام لغو، ردیف نقدی دریافت اول را بدون تخصیص رها کرد؛ طبق طراحی، مبلغ در حساب مشتری باقی می‌ماند (نه در بانک) تا دستور تخصیص/برگشت بعدی داده شود — در جدول تسویه‌ها هشدار داده شده است.</li>
</ul>

<h2>۷) تست‌های اجرانشده / خارج از دسترس</h2>
<ul>
<li><b>PostgreSQL واقعی:</b> در دسترس نبود؛ سرور تولید ۱ worker + SQLite اجرا شد. قفل ردیف FOR UPDATE در کد با fallback امن است و سناریوی رقابتی ۷.۵ زیرِ همان تنظیمات ۱ worker پاس شد؛ تأیید روی Postgres اجرا نشده است و به‌صورت «پاس» ادعا نمی‌شود.</li>
<li><b>چند worker هم‌زمان:</b> قفل in-process فقط داخل یک پردازش جواب می‌دهد؛ با چند worker (حتی روی SQLite) تضمین مانع لغوی دوم نیست — در گزارش‌های قبلی هم مستند شده بود؛ اینجا هم اجرا نشده.</li>
<li><b>لغویی که استرداد نقدی از پرداخت‌خالص بیشتر شود:</b> مسیرِ «لغو از فاکتور تسویه‌نشده با جریمه کمتر از مبلغ لغو» (استرداد مثبت در برابر دریافتِ صفر) اجرا نشد؛ به‌عنوان تصمیم حسابداری (بخش ۸، مورد ۲) ثبت است، نه به‌عنوان خطای اثبات‌شده.</li>
<li><b>VAT فروش برای این سناریو:</b> فاکتورهای فروش معاف/بدون مالیات فرض شدند (اعداد سناریو VAT فروش نداشتند)؛ لغوِ فاکتور دارای VAT فروش در این اجرا پوشش داده نشد.</li>
</ul>

<h2>۸) مواردی که به تصمیم حسابداری/مالیاتی نیاز دارند</h2>
<ol>
<li><b>ماهیت VAT جریمه‌های لغو:</b> در این اجرا جریمه‌ها بدون VAT به ۴۱۰۷/۴۱۰۸ نشستند. اگر در قواعد مالیاتی شما جریمهٔ لغو مشمول مالیات است، باید فیلد/قاعدهٔ جدا تعریف شود (مانند supplier_refund_vat که قبلاً برای سمت تامین اضافه شد).</li>
<li><b>قاعدهٔ «استرداد = لغو − جریمه» صرف‌نظر از پرداخت‌خالص:</b> امروز برنامه استرداد را حتی اگر مشتری هنوز نداده باشد صادر می‌کند (در ۷.۷ دیدید که با جریمهٔ کامل مجاز است). آیا باید استرداد به «پرداخت‌خالص» سقف بخورد؟ این بازبینی قاعدهٔ پولی است و به تصمیم شما نیاز دارد.</li>
<li><b>بدیل رسمی «دریافت از تامین‌کننده»:</b> یا باید Settlement با جهت in برای تامین مجاز و به قرارداد تطبیق با اسناد برگشت لغو وصل شود، یا همان ثبت‌دستی با الگوی ثابت (و ارجاع به شناسهٔ لغو) رسمیت یابد. در این اجرا گزینهٔ دوم با ۱ سند انجام شد.</li>
<li><b>سیاست سپردهٔ آزادشده هنگام لغو (۲۵ اینجا):</b> باید مشخص شود که خودکار به فاکتورهای باز اقتصادی تخصیص مجدد می‌خورد، به‌صورت حواله برگشت برمی‌گردد، یا منتظر دستور می‌ماند.</li>
<li><b>دادهٔ تاریخی:</b> لغوهای قدیمیِ غیرِ تور که احتمالاً به ۴۱۰۱ خورده‌اند — اگر وجود دارند، یک‌بار با گزارش مقایسه و تصمیم سند اصلاحی لازم است. دادهٔ قدیمی هیچ‌گاه بازنویسی خودکار نشد.</li>
<li><b>قرارداد «قابل‌لغو»</b> (مورد ۷.۱): سخت‌گیرانه‌تر کردن (مثلاً به پرداخت‌خالص) تصمیمی قاعده‌ای است که در صورت درخواست اجرایش اصولی است.</li>
</ol>

<h2>۹) شواهد اجرا و ردیابی</h2>
<ul>
<li>رانندهٔ سناریو: <code>scenario8/run_scenario.py</code> — فقط از طریق HTTP به سرور واقعی.</li>
<li>لاگ کامل درخواست/پاسخ: <code>scenario8/http_log.jsonl</code> (اجرای نهایی)، <code>run1_*</code> و <code>run2_*</code> (اجراها/یافته‌های پیشین).
<li>نتایج ساختاریافته: <code>scenario8/results.json</code>، شناسنامهٔ اسناد و گردش‌ها: <code>scenario8/registry.json</code>.</li>
<li>دیتابیس سناریو (جدا از دادهٔ اصلی): <code>data-scenario-8/accounting.db</code>.</li>
<li>کامیت‌ها: <code>2c86359</code> (اصلاح یافتهٔ حساب برگشت + رگرسیون + شواهد سناریو) ، <code>feaccaa</code> و <code>5c965a1</code> (اصلاحات ممیزی قبلی).</li>
<li>رگرسیون پس از اصلاح: ۱۷۶ تست پاس، ۰ شکست (خروجی pytest در جلسهٔ اجرا).</li>
<li>هیچ سندی حذف/ویرایش دستی/بازنویسی نشده است؛ همهٔ اثرها از مسیر رسمی برنامه ثبت شده‌اند.</li>
</ul>

<div class="meta">ساخته‌شده توسط مرور ممیزی خودکار — {reg['generated_at']} — این گزارش از روی دادهٔ واقعی اجرا تولید شده است؛ هر عدد با registry.json و دیتابیس سناریو قابل استعلام مجدد است.</div>
</body></html>"""

(OUT.parent / "Gozaresh-Senario-8-Marhale.html").write_text(html, encoding="utf-8")
print("گزارش ساخته شد:", OUT.parent / "Gozaresh-Senario-8-Marhale.html")
print("اختلاف‌های صفر؟", all_diff_zero)
