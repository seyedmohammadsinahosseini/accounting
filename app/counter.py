"""سرویس کانتر فروش: یک درخواست، یک نشست، و یک تراکنش برای همه‌ی آثار فروش."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select

from . import moadian, sales, services as svc
from .models import Account, MoadianInvoice, Party, Trip
from .money import MoneyError, parse_amount
from .rules import DomainError, SYSTEM_CODES
from .security import role_has


def counter_meta(sess, user) -> dict:
    """فقط گزینه‌های عملیاتی لازم برای فرم را برمی‌گرداند؛ مانده و سود افشا نمی‌شود."""
    parties = list(sess.execute(select(Party).where(Party.is_active.is_(True))
                                .order_by(Party.name)).scalars())
    trips = list(sess.execute(select(Trip).where(Trip.status == "open")
                              .order_by(Trip.start_date.desc(), Trip.id.desc())).scalars())
    accounts = list(sess.execute(select(Account).where(
        Account.kind == "ledger", Account.is_active.is_(True),
        Account.report_group.in_(("cash", "bank", "cheque_received"))
    ).order_by(Account.code)).scalars())
    balances = svc.account_balances(sess)
    def _credit(p: Party) -> int:
        if not p.credit_account_id:
            return 0
        cs = balances.get(p.credit_account_id, {"debit": 0, "credit": 0})
        return cs["credit"] - cs["debit"]
    return {
        "service_types": [{"key": key, "label": value["label"]}
                          for key, value in sales.SERVICE_TYPES.items()],
        "vat": sales.list_tax_rates(sess),
        "customers": [{"id": p.id, "code": p.code, "name": p.name, "kind": p.kind,
                       "phone": p.phone, "credit_account_id": p.credit_account_id,
                       "credit_balance": _credit(p)}
                      for p in parties if p.kind in ("customer", "both")],
        "suppliers": [{"id": p.id, "code": p.code, "name": p.name, "kind": p.kind,
                       "phone": p.phone} for p in parties if p.kind in ("supplier", "both")],
        "trips": [{"id": t.id, "code": t.code, "title": t.title,
                   "destination": t.destination} for t in trips],
        "accounts": [{"id": a.id, "code": a.code, "name": a.name,
                      "report_group": a.report_group} for a in accounts],
        "cheque_account_id": next((a.id for a in accounts
                                   if a.code == SYSTEM_CODES["cheque_received"]), None),
        "can_post": role_has(user.role, "journal.post"),
        "show_profit": role_has(user.role, "reports.view"),
    }


def _amount(value, label: str) -> int:
    try:
        amount = parse_amount(value)
    except MoneyError as exc:
        raise DomainError(f"{label}: {exc}") from exc
    if amount <= 0:
        raise DomainError(f"{label} باید بزرگ‌تر از صفر باشد.")
    return amount


def create_sale(sess, user, *, party_id: int, doc_date: date, due_date: date | None,
                trip_id: int | None, description: str, raw_lines: list[dict],
                raw_payments: list[dict], ip: str = "") -> dict:
    """فاکتور، صورتحساب‌های خرید/امانی، دریافت‌ها و صف مؤدیان را اتمی می‌سازد."""
    # اعتبارسنجی محاسباتی پیش از نوشتن؛ در صورت هر خطا، این نشست هنوز چیزی ثبت نکرده است.
    doc = sales.compute_invoice(sess, doc_date, raw_lines)
    payment_rows: list[tuple[dict, int]] = []
    for index, raw in enumerate(raw_payments, start=1):
        payment_rows.append((raw, _amount(raw.get("amount"), f"دریافت {index}")))
    received_total = sum(amount for _, amount in payment_rows)
    if received_total > doc["total"]:
        raise DomainError("جمع دریافت‌ها از مبلغ فاکتور بیشتر است؛ مازاد خودکار به فاکتور دیگری "
                          "یا پیش‌دریافت تبدیل نمی‌شود.")
    for index, (raw, _amount_value) in enumerate(payment_rows, start=1):
        method = str(raw.get("method") or "").strip().lower()
        if method not in ("cash", "card", "cheque", "credit"):
            raise DomainError(f"روش دریافت {index} نامعتبر است؛ نقد، کارت، چک یا اعتبار مشتری را انتخاب کنید.")
    if any(str(p.get("method") or "").strip().lower() == "credit" for p, _ in payment_rows):
        payer = sess.get(Party, party_id)
        if payer is None or not payer.credit_account_id:
            raise DomainError("این مشتری اعتبار خرید بعدی ندارد؛ ابتدا از لغو بلیت برای او اعتبار صادر کنید.")

    post_now = role_has(user.role, "journal.post")
    invoice = sales.create_invoice(
        sess, user, party_id, doc_date, raw_lines, trip_id, description, due_date,
        issue=post_now, ip=ip)

    # خرید مستقیم هزینه‌ی تأمین‌کننده است و به شکل پیش‌نویس می‌ماند؛ سهم ایرلاین در مدل
    # امانی، ابتدا بدهیِ تسویه‌نشده است و فقط کارمزد در درآمد فروش می‌نشیند.
    grouped: dict[tuple[int, str], int] = defaultdict(int)
    for line in doc["lines"]:
        supplier_id = line["supplier_party_id"]
        purchase = line["purchase_cost"]
        if supplier_id and purchase:
            bill_type = "consignment" if line["revenue_model"] == "commission" else "expense"
            grouped[(supplier_id, bill_type)] += purchase
    bills = []
    for (supplier_id, bill_type), net in grouped.items():
        account_code = SYSTEM_CODES["airline_clearing"] if bill_type == "consignment" else SYSTEM_CODES["cogs"]
        account = svc.account_by_code(sess, account_code)
        supplier = sess.get(Party, supplier_id)
        bill = sales.create_bill(
            sess, user, supplier_id, doc_date, net, vat=0,
            expense_account_id=account.id, trip_id=trip_id,
            description=(f"بدهی امانیِ فاکتور {invoice.number or invoice.id} — {supplier.name}"
                         if bill_type == "consignment" else
                         f"خرید خدمات فاکتور {invoice.number or invoice.id} — {supplier.name}"),
            due_date=due_date or doc_date,
            issue=(post_now and bill_type == "consignment"), ip=ip,
            bill_type=bill_type, source_invoice_id=invoice.id)
        bills.append(bill)

    settlements = []
    cheque_account = (svc.account_by_code(sess, SYSTEM_CODES["cheque_received"])
                      if any(str(p.get("method") or "").lower() == "cheque"
                             for p, _ in payment_rows) else None)
    for index, (raw, amount) in enumerate(payment_rows, start=1):
        method = str(raw.get("method") or "").strip().lower()
        account_id = raw.get("account_id")
        if method == "cheque":
            if cheque_account is None:
                raise DomainError("حساب چک‌های دریافتی در کدینگ پیدا نشد.")
            account_id = cheque_account.id
        elif method == "credit":
            # اعتبار فقط از حساب اعتبار خودِ همین مشتری و فقط به اندازه‌ی مانده مصرف می‌شود
            account_id = payer.credit_account_id
        else:
            try:
                account_id = int(account_id)
            except (TypeError, ValueError) as exc:
                raise DomainError(f"برای دریافت {index}، حساب صندوق/بانک را انتخاب کنید.") from exc
            account = sess.get(Account, account_id)
            required_group = "cash" if method == "cash" else "bank"
            if account is None or account.kind != "ledger" or account.report_group != required_group:
                label = "صندوق" if method == "cash" else "بانک یا پایانه‌ی کارت"
                raise DomainError(f"حساب دریافت {index} باید از نوع «{label}» باشد.")
        cheque_due = raw.get("cheque_due_date")
        if isinstance(cheque_due, str) and cheque_due.strip():
            from . import dates as jd
            cheque_due = jd.parse_date_any(cheque_due)
        else:
            cheque_due = None
        settlement = sales.create_settlement(
            sess, user, "in", party_id, account_id, amount, doc_date,
            trip_id=trip_id, description=(raw.get("description") or
                                         f"دریافت کانتر بابت فاکتور {invoice.number or invoice.id}"),
            issue=post_now, ip=ip, method=method,
            cheque_number=str(raw.get("cheque_number") or "").strip(),
            cheque_bank=str(raw.get("cheque_bank") or "").strip(),
            cheque_branch=str(raw.get("cheque_branch") or "").strip(),
            cheque_due_date=cheque_due, target_invoice_id=invoice.id)
        settlements.append(settlement)

    # ساختن ردیف صف مؤدیان، نه ارسال به سازمان یا شرکت معتمد.
    queue_result = moadian.create_for_invoices(sess, user, invoice_ids=[invoice.id],
                                                include_cancelled=False, ip=ip)
    queue_row = queue_result["rows"][0] if queue_result.get("rows") else None
    if queue_row is None:
        queue = sess.execute(select(MoadianInvoice).where(
            MoadianInvoice.invoice_id == invoice.id, MoadianInvoice.subject == 1
        )).scalar_one_or_none()
        queue_row = ({"id": queue.id, "serial": queue.serial, "status": queue.status,
                      "problems": []} if queue else None)

    purchase_total = sum(line["purchase_cost"] for line in doc["lines"])
    result = {
        "invoice": {"id": invoice.id, "number": invoice.number or "", "status": invoice.status,
                    "total": invoice.total_rial, "vat": invoice.vat_rial},
        "sales_total": invoice.total_rial,
        "received_total": received_total,
        "balance": max(0, invoice.total_rial - received_total),
        "post_now": post_now,
        "supplier_bills": [{"id": b.id, "number": b.number or "", "type": b.bill_type,
                            "status": b.status,
                            "supplier": sess.get(Party, b.party_id).name}
                           for b in bills],
        "settlements": [{"id": s.id, "number": s.number or "", "method": s.method,
                         "status": s.status, "amount": s.amount_rial,
                         "allocation_invoice_id": s.target_invoice_id}
                        for s in settlements],
        "moadian": queue_row,
    }
    if role_has(user.role, "reports.view"):
        net_sales = sum(line["net"] for line in doc["lines"])
        result["purchase_total"] = purchase_total
        result["profit"] = net_sales - purchase_total
        for row, bill in zip(result["supplier_bills"], bills):
            row["amount"] = bill.total_rial
    return result


def list_pending(sess) -> list[dict]:
    """صف تأیید یکپارچه — هر فروش کانترِ پیش‌نویس با همه‌ی اقلامش."""
    from .models import Invoice, Settlement, SupplierBill
    from . import dates as jd
    # همه‌ی فاکتورهای پیش‌نویس که دریافتِ تخصیص‌دار دارند = فروش‌های کانتر در انتظار
    q = select(Invoice).where(Invoice.status == "draft").order_by(Invoice.doc_date.desc(), Invoice.id.desc())
    parties = {p.id: p.name for p in sess.execute(select(Party)).scalars()}
    trips = {t.id: t.title for t in sess.execute(select(Trip)).scalars()}
    out = []
    for inv in sess.execute(q).scalars():
        # فقط آنهایی که حداقل یک settlement با target_invoice_id دارند (امضای کانتر)
        s_q = select(Settlement).where(Settlement.target_invoice_id == inv.id, Settlement.status == "draft")
        settlements = list(sess.execute(s_q).scalars())
        if not settlements:
            continue
        b_q = select(SupplierBill).where(SupplierBill.source_invoice_id == inv.id, SupplierBill.status == "draft")
        bills = list(sess.execute(b_q).scalars())
        # مانده‌ها
        s_total = sum(s.amount_rial for s in settlements)
        b_total = sum(b.total_rial for b in bills)
        out.append({
            "invoice": {"id": inv.id, "number": inv.number or f"پیش‌نویس#{inv.id}", "date": jd.jalali_str(inv.doc_date),
                        "total": inv.total_rial, "status": inv.status, "description": inv.description},
            "party": parties.get(inv.party_id, "?"),
            "trip": trips.get(inv.trip_id, "—") if inv.trip_id else "—",
            "settlements": [{"id": s.id, "number": s.number or "—", "method": s.method, "amount": s.amount_rial} for s in settlements],
            "settlements_total": s_total,
            "bills": [{"id": b.id, "number": b.number or "—", "type": b.bill_type, "supplier": parties.get(b.party_id, "?"), "amount": b.total_rial} for b in bills],
            "bills_total": b_total,
            "balance": inv.total_rial - s_total,
        })
    return out


def approve_pending(sess, user, invoice_id: int, ip: str = "") -> dict:
    """تأیید یکپارچه — فاکتور + همه‌ی بیل‌ها + همه‌ی دریافت‌های همان فروش را با هم صادر می‌کند."""
    from .models import Invoice, Settlement, SupplierBill
    inv = sess.get(Invoice, invoice_id)
    if inv is None or inv.status != "draft":
        raise DomainError("این فروش در صف تأیید نیست (یا قبلاً تأیید شده).")
    # 1) فاکتور
    sales.issue_invoice(sess, user, inv.id, ip)
    # 2) بیل‌های همان فاکتور
    for b in sess.execute(select(SupplierBill).where(SupplierBill.source_invoice_id == inv.id, SupplierBill.status == "draft")).scalars():
        sales.issue_bill(sess, user, b.id, ip)
    # 3) دریافت‌های تخصیص‌دار همان فاکتور
    for s in sess.execute(select(Settlement).where(Settlement.target_invoice_id == inv.id, Settlement.status == "draft")).scalars():
        sales.issue_settlement(sess, user, s.id, ip)
    # مودیان را ریفرش کن
    try:
        from . import moadian as moad
        moad.create_for_invoices(sess, user, invoice_ids=[inv.id], include_cancelled=False, ip=ip)
    except Exception:
        pass
    return {"id": inv.id, "number": inv.number, "message": f"فروش {inv.number} با همه‌ی اقلامش صادر شد."}
