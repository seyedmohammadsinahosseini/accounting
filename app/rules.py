"""موتور قواعد حسابداری — قلب برنامه.

هر قاعده‌ای که به «درست بودن حساب» مربوط است اینجاست، نه در صفحه‌ها و نه در دیتابیس.
برای هر قاعده یک پیام فارسی روشن هم هست که به کاربر نشان داده می‌شود و می‌گوید
دقیقاً چه چیزی مانع ثبت شده.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .money import format_amount_fa, parse_amount

# حساب‌های سیستمی: برنامه برای ثبت‌های خودکار (انتقال وجه، حقوق، مالیات) از این کدها
# استفاده می‌کند. کدها در یک جا تعریف شده‌اند تا تغییر کدینگ کار ساده‌ای باشد.
SYSTEM_CODES = {
    "cash": "1101",
    "bank": "110201",
    "receivable": "1103",
    "cheque_received": "1104",
    "prepaid_supplier": "1105",
    "employee_loan": "1108",
    "vat_credit": "1107",
    "payable": "2101",
    "cheque_paid": "2102",
    "advance_customer": "2103",
    "vat_payable": "2104",
    "insurance_payable": "2105",
    "salary_tax_payable": "2106",
    "salary_payable": "2107",
    "airline_clearing": "2108",
    "capital": "3101",
    "retained": "3102",
    "revenue_tour": "4101",
    "revenue_ticket": "4102",
    "revenue_other": "4103",
    "sales_return": "4104",
    "revenue_commission": "4105",
    "cogs": "5101",
    "cancel_burn": "5109",
    "asset_equipment": "110901",
    "asset_vehicle": "110902",
    "asset_building": "110903",
    "accumulated_depreciation": "111001",
    "gain_asset_disposal": "4106",
    "loss_asset_disposal": "5108",
    "salary_expense": "5102",
    "rent": "5103",
    "marketing": "5104",
    "admin": "5105",
    "bank_fee": "5106",
    "depreciation": "5107",
}


class DomainError(Exception):
    """خطای قواعد کسب‌وکار؛ متن پیام برای نمایش به کاربر است."""

    def __init__(self, message: str, code: str = "rule_violation"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class Line:
    account_id: int
    debit: int = 0
    credit: int = 0
    description: str = ""


@dataclass
class Validation:
    lines: list[Line] = field(default_factory=list)
    debit_total: int = 0
    credit_total: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def diff(self) -> int:
        return self.debit_total - self.credit_total

    @property
    def is_balanced(self) -> bool:
        return self.diff == 0 and self.debit_total > 0

    def message(self) -> str:
        if self.problems:
            return " ".join(self.problems)
        if self.is_balanced:
            return "سند تراز است."
        return (
            f"سند تراز نیست: جمع بدهکار {format_amount_fa(self.debit_total)} ریال و "
            f"جمع بستانکار {format_amount_fa(self.credit_total)} ریال است؛ "
            f"اختلاف {format_amount_fa(abs(self.diff))} ریال. "
            "سندی که بدهکار و بستانکارش برابر نباشد ثبت نهایی نمی‌شود."
        )


def build_validation(raw_lines, max_lines: int = 200, require_balanced: bool = True) -> Validation:
    """سطرهای خام ورودی را بررسی و به سطرهای سالم تبدیل می‌کند.

    require_balanced=False برای پیش‌نویس است: حسابدار می‌تواند سند نیمه‌کاره را ذخیره کند و
    بعد کاملش کند، ولی ثبت نهایی همیشه تراز می‌خواهد.
    """
    v = Validation()
    if not raw_lines:
        v.problems.append("سند باید حداقل دو سطر داشته باشد (یک بدهکار و یک بستانکار).")
        return v
    if len(raw_lines) > max_lines:
        v.problems.append(f"تعداد سطرهای یک سند نباید از {max_lines} بیشتر باشد.")
        return v

    for idx, raw in enumerate(raw_lines, start=1):
        account_id = raw.get("account_id")
        if not account_id:
            v.problems.append(f"سطر {idx}: حساب انتخاب نشده است.")
            continue
        try:
            debit = parse_amount(raw.get("debit", 0) or 0) if (raw.get("debit") not in (0, "0", None, "")) else 0
            credit = parse_amount(raw.get("credit", 0) or 0) if (raw.get("credit") not in (0, "0", None, "")) else 0
        except Exception as exc:  # noqa: BLE001
            v.problems.append(f"سطر {idx}: {exc}")
            continue
        if debit > 0 and credit > 0:
            v.problems.append(
                f"سطر {idx}: یک سطر نمی‌تواند هم بدهکار باشد هم بستانکار؛ یکی را خالی بگذارید."
            )
            continue
        if debit == 0 and credit == 0:
            v.problems.append(f"سطر {idx}: مبلغ بدهکار یا بستانکار را بنویسید.")
            continue
        v.lines.append(Line(int(account_id), debit, credit, str(raw.get("description") or "")[:300]))

    v.debit_total = sum(l.debit for l in v.lines)
    v.credit_total = sum(l.credit for l in v.lines)
    if not v.problems:
        if len(v.lines) < 2:
            v.problems.append("سند باید حداقل دو سطر داشته باشد.")
        elif require_balanced and not v.is_balanced:
            v.problems.append(v.message())
    return v


def describe_lines(lines: list[Line], account_labels: dict[int, str]) -> list[dict]:
    return [
        {
            "line_no": i,
            "account_id": l.account_id,
            "account_label": account_labels.get(l.account_id, ""),
            "debit": l.debit,
            "credit": l.credit,
            "description": l.description,
        }
        for i, l in enumerate(lines, start=1)
    ]
