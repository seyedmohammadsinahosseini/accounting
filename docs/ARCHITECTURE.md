# Accounting Platform — Architectural Specification

This document details the software architecture, domain design, data invariants, and security patterns implemented in **Accounting Platform**.

---

## 1. Architectural Philosophy & Layering

The application follows a **Domain-Centric Layered Architecture** optimized for high-reliability financial operations:

```
+--------------------------------------------------------------------------+
|                       PRESENTATION & CLIENT LAYER                        |
|   Responsive Single-Page Web UI (Vanilla JS / CSS) | Swagger / OpenAPI    |
+--------------------------------------------------------------------------+
                                     |  HTTP / JSON / FormData
+--------------------------------------------------------------------------+
|                        API & ORCHESTRATION LAYER                         |
|   FastAPI ASGI Engine | RBAC Middleware | Idempotency Key Pipeline       |
|   Rate Limiting (Token Bucket) | Exception Translators & Tracking ID     |
+--------------------------------------------------------------------------+
                                     |  DTOs / Value Objects
+--------------------------------------------------------------------------+
|                          BUSINESS DOMAIN LAYER                           |
|  * Double-Entry General Ledger      * 4-Tier Chart of Accounts           |
|  * Treasury & Cash Flow Engine       * Sales & Supplier Invoicing         |
|  * Travel Dossier Margin Analyzer    * Fixed Asset Depreciation Engine    |
|  * Statutory Payroll & Tax Slabs    * E-Invoicing (Moudian) Validator    |
|  * Cryptographic PII Vault Engine   * Bidirectional Excel Serialization  |
+--------------------------------------------------------------------------+
                                     |  SQLAlchemy 2.0 ORM
+--------------------------------------------------------------------------+
|                       STORAGE & PERSISTENCE LAYER                        |
|  SQLite 3 Engine (WAL Mode) | Strict Foreign Keys | Integrity Checkers   |
|  Consistent Snapshot Hot-Backup Engine | Forward-Only Migration Engine   |
+--------------------------------------------------------------------------+
```

---

## 2. Core Financial Invariants & Integrity Guarantees

### 2.1 Double-Entry Parity Invariant
In double-entry bookkeeping, every financial transaction must satisfy the fundamental accounting identity:

$$\sum \text{Debit} = \sum \text{Credit}$$

In Accounting Platform, this identity is enforced at three distinct layers:
1. **Frontend Reactive Verification**: Client interface calculates running sums of line items in real time, disabling the "Post" button if $\Delta \ne 0$.
2. **Domain Service Validation**: `services.py` verifies zero-sum parity prior to state transition from `draft` to `posted`.
3. **Database Check Constraints**:
   - `CheckConstraint("debit_rial >= 0 and credit_rial >= 0")`
   - `CheckConstraint("not (debit_rial > 0 and credit_rial > 0)")` (a line cannot simultaneously debit and credit)
   - `CheckConstraint("debit_rial > 0 or credit_rial > 0")` (no zero-value phantom lines)

### 2.2 Ledger Immutability & Audit Trail
Once a journal entry enters the `posted` state, it is **strictly immutable**:
- Updating or deleting posted entries via the API is forbidden.
- To correct errors, an explicit reversal transaction is generated (`services.reverse_entry`).
- The reversing entry contains inverted debit/credit lines, maintains a foreign key relationship (`reversal_of_id` / `reversed_by_id`), and mandates an audit rationale string.
- All mutating administrative and financial actions are logged to the append-only `audit_log` table with user identification, timestamp, action slug, and client IP address.

### 2.3 Integer-Based Currency Arithmetic
Floating-point arithmetic (IEEE 754) introduces non-deterministic precision drift when calculating financial totals. Accounting Platform stores all currency amounts as **64-bit integer Rials** in the persistence layer. All fractional ratios (such as VAT basis points or depreciation rates) are computed using scaled integer arithmetic or explicit rounding boundaries.

---

## 3. Distributed Idempotency Architecture

In enterprise financial applications, network retries, client timeouts, or double-clicks must never result in duplicate financial postings.

```
Client Request (POST /api/entries, Header: Idempotency-Key)
                       │
                       ▼
         Does key exist in DB?
            ├─── YES (Status = 'completed') ──► Replay stored HTTP response
            ├─── YES (Status = 'processing') ─► Reject with 409 Conflict
            └─── NO
                   │
                   ▼
         Insert IdempotencyRecord (status='processing')
                   │
                   ▼
         Execute Financial Transaction
                   │
                   ▼
         Update IdempotencyRecord (status='completed', response_json)
                   │
                   ▼
         Return Fresh HTTP 200/201 Response
```

- **Database-Level Lock**: The `idempotency_records` table enforces a `UNIQUE` constraint on `idempotency_key`.
- **Payload Fingerprinting**: A SHA-256 digest of the request body is compared against the initial key registration to prevent key re-use with diverging payloads.
- **Client Session Storage**: The web client maintains transient idempotency keys in browser `sessionStorage` across network drops.

---

## 4. Cryptographic PII Vault

Travel agency workflows require handling sensitive passenger records, including National Identification Numbers and Passport identifiers.

### 4.1 Cryptographic Primitives
- Encryption is performed using **Fernet symmetric encryption** (AES-128 in CBC mode with PKCS7 padding, HMAC-SHA256 authentication).
- Ciphertext strings are prefixed with `enc:v1:` to enable future key rotation and algorithm versioning.

### 4.2 Fail-Closed Security Posture
- In production (`ACCT_ALLOW_PLAINTEXT_PII=0`), the application startup lifecycle (`lifespan`) refuses to boot if the `cryptography` dependency or secret key cannot be initialized.
- Secret key files (`data/secret.key`) are automatically created with POSIX `0600` permissions (`stat.S_IRUSR | stat.S_IWUSR`).

### 4.3 Data Masking & Controlled Unmasking
- Standard API listings return masked representations of sensitive identifiers (e.g., `AB••••••12` or `00••••••89`).
- Full unmasking is restricted to authorized roles (`users.manage` / `passengers.view_sensitive`) and triggers a mandatory entry in the immutable `audit_log`.

---

## 5. Chart of Accounts (COA) Hierarchy

The platform implements a standardized 4-tier Chart of Accounts:

```
Level 1: Group (گروه)          - 1 digit (e.g., 1 = Current Assets)
  Level 2: General / Control (کل) - 2-4 digits (e.g., 11 = Cash and Banks)
    Level 3: Subsidiary (معین)      - 4-6 digits (e.g., 1102 = Bank Accounts)
      Level 4: Detail (تفصیلی)        - 6+ digits (e.g., 110201 = Bank Mellat Checking)
```

- **Nature (ماهیت حساب)**: Each account is tagged as `debit` or `credit` to validate normal balance expectations in trial balances.
- **Report Classification**: Accounts are mapped to `balance_sheet`, `income_statement`, or `cash_flow`.
- **System Accounts**: Core accounts (Retained Earnings, VAT Payable, Salary Payable, Accumulated Depreciation) are protected by `is_system=True` to prevent accidental deletion.

---

## 6. Business Subsystems

### 6.1 Travel Dossier Profitability Engine
- Travel agencies bundle airfare, hotel bookings, visa processing, and transfers into distinct **Trip Dossiers** (`Trip`).
- Sales invoices and vendor bills link directly to a `trip_id`.
- The engine calculates real-time revenue, direct costs, gross profit, and operating margin per trip, flagging loss-making dossiers automatically.

### 6.2 Treasury & Cash Flow Calendar
- Manages cash drawers and commercial bank accounts.
- **Inter-Account Transfers**: Automatically generates balanced journal entries shifting liquidity between accounts without impacting profit-and-loss balances.
- **Cash Calendar**: Projects liquidity inflows and outflows across 30, 60, and 90-day forward horizons, aggregating scheduled receivables, supplier maturities, and recurring commitments.

### 6.3 Fixed Asset Depreciation Ledger
- Tracks capital expenditures with purchase value, acquisition date, estimated useful life, and salvage value.
- Implements two standard amortization models:
  - **Straight-Line Depreciation**
  - **Declining-Balance Depreciation**
- Executes monthly automated depreciation runs, calculating monthly book depreciation and posting balanced journal entries:
  $$\text{Debit: Depreciation Expense (هزینه‌ی استهلاک)} \quad \longleftrightarrow \quad \text{Credit: Accumulated Depreciation (استهلاک انباشته)}$$

### 6.4 Statutory Payroll & Tax Slabs
- Computes gross salary, base pay, housing allowance, grocery subsidies, and overtime.
- Implements progressive multi-tier income tax slabs with dynamic thresholds.
- Calculates statutory social security contributions (7% employee share, 23% employer share).
- Manages employee loans and advance repayments with automatic balance amortization across monthly payroll runs.

### 6.5 Electronic Tax Invoicing (Moudian)
- Validates 13-digit national service and commodity identifiers (`moadian_service_map`).
- Constructs compliant Type 1 (commercial with tax credit) and Type 2 (consumer) JSON payloads.
- Implements an asynchronous dispatch queue with validation checks and error diagnosis.
- Exports multi-sheet audit workbooks for fiscal reconciliation.

---

## 7. Storage Engine & Backup Architecture

### 7.1 SQLite WAL (Write-Ahead Logging) Configuration
- The engine operates in `PRAGMA journal_mode=WAL;`, allowing concurrent readers without blocking active writers.
- `PRAGMA foreign_keys=ON;` enforces referential integrity across all relational boundaries.
- `PRAGMA synchronous=NORMAL;` ensures crash-resilience with optimal I/O throughput.

### 7.2 Zero-Downtime Hot Backups
- Backups utilize SQLite's native `sqlite3_backup_step` API to capture consistent point-in-time snapshots during active system operation.
- Automated background tasks (`_backup_loop`) trigger periodic snapshots every $N$ hours.
- Backups undergo automated `PRAGMA integrity_check` and `PRAGMA foreign_key_check` upon generation.
- Cryptographic keys are securely bundled with snapshots to guarantee full disaster recovery capability.
