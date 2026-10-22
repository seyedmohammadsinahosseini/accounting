"""تقویم پول باید سررسیدهای آینده و معوق را با داده‌ی واقعی و مرتب برگرداند."""
from __future__ import annotations

from datetime import timedelta

from app import dates as jd
from conftest import account_id


def _party(client, headers, name, kind):
    response = client.post("/api/parties", headers=headers, json={"name": name, "kind": kind})
    assert response.status_code == 200, response.text
    return response.json()


def test_cash_calendar_lists_and_sorts_real_open_future_and_overdue_items(client, accountant):
    customer = _party(client, accountant, "مشتری آزمون تقویم پول", "customer")
    supplier = _party(client, accountant, "تأمین‌کننده آزمون تقویم پول", "supplier")
    today = jd.jtoday()
    today_s = jd.jalali_str(today)
    future_invoice_due = jd.jalali_str(today + timedelta(days=7))
    overdue_invoice_due = jd.jalali_str(today - timedelta(days=3))
    future_bill_due = jd.jalali_str(today + timedelta(days=10))
    overdue_bill_due = jd.jalali_str(today - timedelta(days=2))

    future_invoice = client.post("/api/invoices", headers=accountant, json={
        "party_id": customer["id"], "date": today_s, "due_date": future_invoice_due,
        "description": "فاکتور آینده برای آزمون تقویم پول",
        "lines": [{"service_type": "tour", "unit_price": 41_000_000, "qty": 1, "taxable": False}],
        "issue": True,
    })
    assert future_invoice.status_code == 200, future_invoice.text
    overdue_invoice = client.post("/api/invoices", headers=accountant, json={
        "party_id": customer["id"], "date": today_s, "due_date": overdue_invoice_due,
        "description": "فاکتور معوق برای آزمون تقویم پول",
        "lines": [{"service_type": "visa", "unit_price": 13_000_000, "qty": 1, "taxable": False}],
        "issue": True,
    })
    assert overdue_invoice.status_code == 200, overdue_invoice.text

    expense = account_id(client, accountant, "5101")
    future_bill = client.post("/api/bills", headers=accountant, json={
        "party_id": supplier["id"], "date": today_s, "due_date": future_bill_due,
        "net": 17_000_000, "vat": 0, "expense_account_id": expense,
        "description": "صورتحساب آینده برای آزمون تقویم پول", "issue": True,
    })
    assert future_bill.status_code == 200, future_bill.text
    overdue_bill = client.post("/api/bills", headers=accountant, json={
        "party_id": supplier["id"], "date": today_s, "due_date": overdue_bill_due,
        "net": 9_000_000, "vat": 0, "expense_account_id": expense,
        "description": "صورتحساب معوق برای آزمون تقویم پول", "issue": True,
    })
    assert overdue_bill.status_code == 200, overdue_bill.text

    response = client.get("/api/calendar?days=30", headers=accountant)
    assert response.status_code == 200, response.text
    report = response.json()
    future_by_ref = {row["ref"]: row for row in report["items"]}
    overdue_by_ref = {row["ref"]: row for row in report["overdue"]}

    assert future_invoice.json()["number"] in future_by_ref
    assert future_bill.json()["number"] in future_by_ref
    assert future_by_ref[future_invoice.json()["number"]]["direction"] == "in"
    assert future_by_ref[future_invoice.json()["number"]]["amount"] == 41_000_000
    assert future_by_ref[future_bill.json()["number"]]["direction"] == "out"
    assert future_by_ref[future_bill.json()["number"]]["amount"] == 17_000_000

    assert overdue_invoice.json()["number"] in overdue_by_ref
    assert overdue_bill.json()["number"] in overdue_by_ref
    assert overdue_by_ref[overdue_invoice.json()["number"]]["days_left"] == -3
    assert overdue_by_ref[overdue_bill.json()["number"]]["days_left"] == -2
    assert report["overdue_in"] >= 13_000_000
    assert report["overdue_out"] >= 9_000_000
    assert [row["date_iso"] for row in report["items"]] == sorted(row["date_iso"] for row in report["items"])
    assert [row["date_iso"] for row in report["overdue"]] == sorted(row["date_iso"] for row in report["overdue"])
    assert "amount_fa" in future_by_ref[future_invoice.json()["number"]]
    assert any("نمونه‌ی طلب معوق تقویم پول" in row["title"] for row in report["overdue"])
