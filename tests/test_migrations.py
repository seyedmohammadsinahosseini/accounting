"""مهاجرت نسخه‌ای از دیتابیس قدیمی به ساختار جاری."""
from __future__ import annotations

from sqlalchemy import create_engine, text

from app import migrations


def test_legacy_schema_migrates_forward_and_is_repeatable(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", future=True)
    monkeypatch.setattr(migrations, "engine", engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE invoices (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE supplier_bills (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE settlements (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE parties (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, "
                          "name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'customer', "
                          "phone TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', "
                          "account_id INTEGER, is_active INTEGER NOT NULL DEFAULT 1, "
                          "created_by INTEGER, created_at DATETIME NOT NULL)"))
        conn.execute(text("INSERT INTO parties (id,code,name,kind,created_at) "
                          "VALUES (1,'م-00001','نمونه قدیمی','customer', datetime('now'))"))
        conn.execute(text("CREATE TABLE invoice_lines (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE accounts (id INTEGER PRIMARY KEY, code TEXT NOT NULL, "
                          "name TEXT NOT NULL, kind TEXT NOT NULL, nature TEXT NOT NULL, "
                          "parent_code TEXT, report_group TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO accounts (code,name,kind,nature,parent_code,report_group) "
                          "VALUES ('1','دارایی','group','debit',NULL,'asset')"))
        conn.execute(text("INSERT INTO accounts (code,name,kind,nature,parent_code,report_group) "
                          "VALUES ('110201','بانک','ledger','debit','1','bank')"))

    applied = migrations.run_migrations()
    # 34,35,38,68 خالی هستند و اجرا نمی‌شوند (not sql) — بقیه اجرا می‌شوند
    assert applied == list(range(1, 34)) + [36, 37] + list(range(39, 68)) + list(range(69, 90))
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(accounts)"))}
        assert {"level", "group_type", "report_type"} <= columns
        invoice_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(invoice_lines)"))}
        bill_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(supplier_bills)"))}
        settlement_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(settlements)"))}
        assert {"revenue_model", "purchase_cost_rial", "supplier_party_id"} <= invoice_columns
        assert {"bill_type", "source_invoice_id"} <= bill_columns
        assert {"currency_code", "fx_amount_minor", "fx_rate_rial"} <= bill_columns
        assert "target_invoice_id" in settlement_columns
        assert {"partial_cancel_id", "target_bill_id",
                "currency_code", "fx_amount_minor", "fx_rate_rial"} <= settlement_columns
        pc_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(invoice_partial_cancels)"))}
        assert {"status", "refund_mode", "agency_fee_rial", "refund_due_rial", "refund_paid_rial",
                "supplier_claim_rial", "supplier_received_rial", "supplier_party_id",
                "credit_entry_id", "credit_account_id", "credit_issued_rial",
                "nonrefundable_cost_rial", "profit_impact_rial", "line_id",
                "reversal_state", "cancel_reason"} <= pc_columns
        party_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(parties)"))}
        assert "credit_account_id" in party_columns
        rows = conn.execute(text("SELECT code,level,group_type,report_type FROM accounts ORDER BY code")).all()
        assert rows[0] == ("1", "group", "permanent", "balance_sheet")
        assert rows[1] == ("110201", "detail", "permanent", "balance_sheet")
        idem_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(idempotency_records)"))}
        assert {"scope_key", "request_hash", "response_body", "created_at"} <= idem_columns
    assert migrations.run_migrations() == []
    engine.dispose()
