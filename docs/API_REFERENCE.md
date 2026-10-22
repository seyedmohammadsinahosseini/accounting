# Accounting Platform — REST API Reference

The Accounting Platform exposes a comprehensive RESTful API built on **FastAPI**. Interactive Swagger UI documentation is available at `/docs`, and ReDoc is hosted at `/redoc` when `ACCT_ENABLE_DOCS=1`.

---

## 1. Authentication & Security

### 1.1 Authentication Protocol
The platform utilizes token-based session authentication:
- Clients submit credentials to `/api/auth/login`.
- Successful authentication returns a session bearer token.
- Protected requests must include the header:
  ```http
  Authorization: Bearer <session_token>
  ```
- Mutating operations (e.g., creating journal entries, posting invoices) accept an idempotency token:
  ```http
  Idempotency-Key: <unique_uuid_or_token>
  ```

### 1.2 Auth Endpoints

#### `POST /api/auth/login`
Authenticates a user and issues an active session token.

**Request Payload:**
```json
{
  "username": "admin",
  "password": "admin123"
}
```

**Response (200 OK):**
```json
{
  "token": "a1b2c3d4e5f6...",
  "user": {
    "id": 1,
    "username": "admin",
    "full_name": "System Administrator",
    "role": "admin"
  },
  "expires_at": "2026-10-19T04:00:00"
}
```

#### `POST /api/auth/logout`
Terminates the active session and invalidates the session token in the database.

#### `GET /api/me`
Retrieves the identity, role, and permission grants of the authenticated user.

#### `POST /api/me/password`
Updates the password of the current user. Requires standard password complexity.

---

## 2. General Ledger & Double-Entry Accounting

### 2.1 Journal Entries

#### `GET /api/entries`
Lists journal entries with filtering by date range, status (`draft`, `posted`, `reversed`), and text search.

**Query Parameters:**
- `from_date` (YYYY-MM-DD or Jalali YYYY/MM/DD)
- `to_date` (YYYY-MM-DD or Jalali YYYY/MM/DD)
- `status` (`draft` | `posted` | `reversed` | `all`)
- `q` (search string across descriptions and entry numbers)

#### `POST /api/entries`
Creates a new draft journal entry.

**Request Payload:**
```json
{
  "date": "1405/07/26",
  "description": "Monthly office rent payment",
  "lines": [
    {
      "account_id": 45,
      "debit": 50000000,
      "credit": 0,
      "description": "Rent Expense"
    },
    {
      "account_id": 12,
      "debit": 0,
      "credit": 50000000,
      "description": "Commercial Bank Account"
    }
  ]
}
```

#### `POST /api/entries/{id}/post`
Transitions a draft entry to `posted`. Enforces zero-sum balance: $\sum \text{Debit} = \sum \text{Credit}$.

#### `POST /api/entries/{id}/reverse`
Generates an immutable reversing journal entry to void a posted entry.

**Request Payload:**
```json
{
  "reason": "Correction of duplicate vendor invoice posting"
}
```

### 2.2 Chart of Accounts

#### `GET /api/accounts`
Retrieves the hierarchical chart of accounts, including code, name, level (group, general, subsidiary, detail), normal nature (debit/credit), and current balance.

#### `POST /api/accounts`
Creates a new user-defined detail account under a parent subsidiary account.

### 2.3 Financial Reporting & Ledgers

#### `GET /api/trial-balance`
Calculates a multi-column trial balance over a specified fiscal period.
- Returns `total_debit`, `total_credit`, and boolean `balanced`.

#### `GET /api/ledger/{account_id}`
Retrieves detailed account activity, including opening balance, chronological debit/credit movements, running balance, and counterparty descriptions.

#### `GET /api/statements/income`
Generates an Income Statement (Profit & Loss) for a specified date range.

#### `GET /api/statements/balance-sheet`
Generates a Balance Sheet statement displaying Assets, Liabilities, and Shareholders' Equity.

---

## 3. Treasury & Cash Flow Operations

#### `GET /api/treasury/accounts`
Lists cash drawers, petty cash, and commercial bank accounts with reconciled balances.

#### `POST /api/treasury/transfers`
Executes an inter-account transfer (e.g., from Cash to Bank) and creates a balanced journal entry without affecting P&L.

#### `GET /api/treasury/calendar`
Returns a 30, 60, or 90-day cash forecasting schedule showing anticipated inflows and committed disbursements.

---

## 4. Sales Invoicing & Travel Dossiers

#### `GET /api/invoices`
Lists customer invoices with issue status, payment state, and connected travel dossiers.

#### `POST /api/invoices`
Creates a commercial service invoice (e.g., tour packages, airline ticketing, hotel accommodation, visas). Automatically applies date-effective VAT rates.

#### `POST /api/invoices/{id}/issue`
Issues an invoice, generating the underlying accounts receivable journal entry.

#### `GET /api/trips`
Lists travel dossiers with trip identifiers, departure dates, destinations, passenger counts, and real-time margin metrics.

#### `GET /api/trips/{id}/profitability`
Returns the profitability analysis of a specific travel dossier:
$$\text{Gross Margin} = \text{Total Billed Revenue} - \text{Total Supplier Direct Costs}$$

---

## 5. Statutory Payroll Engine

#### `GET /api/payroll/employees`
Lists active employees, compensation decreets, and statutory tax parameters.

#### `POST /api/payroll/runs`
Initiates a monthly payroll run for a specified Jalali month (`jy`, `jm`).
- Automatically computes gross compensation, progressive income tax brackets, social security deductions (employee and employer shares), and amortizes loan repayment installments.

#### `POST /api/payroll/runs/{id}/post`
Finalizes the payroll run and commits the balanced salary liability journal entry.

---

## 6. Fixed Asset Depreciation Ledger

#### `GET /api/assets`
Lists capitalized fixed assets with historical acquisition cost, accumulated depreciation, and net book value.

#### `POST /api/assets`
Capitalizes a new asset, establishing its depreciation schedule (straight-line or declining balance).

#### `POST /api/assets/depreciate`
Executes monthly depreciation across all active capitalized assets, posting balanced expense and accumulated depreciation lines.

---

## 7. System Administration & Diagnostics

#### `GET /api/health`
Lightweight healthcheck endpoint utilized by Docker container orchestrators and monitoring agents.

#### `GET /api/admin/storage-health`
Comprehensive diagnostic endpoint verifying:
- SQLite database integrity (`PRAGMA integrity_check`)
- Foreign key constraint consistency
- Disk space utilization
- Cryptographic vault status & PII encryption state
- Hot backup freshness and retention

#### `POST /api/admin/backups`
Triggers an immediate, consistent hot snapshot of the database.
