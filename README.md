# Accounting Platform

<div align="center">

### Enterprise-Grade Double-Entry Financial Engine, ERP Backend & Compliance System

[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0+-red.svg?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Test Suite](https://img.shields.io/badge/pytest-142%20passed-brightgreen.svg?logo=pytest&logoColor=white)](tests/)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-black.svg?logo=ruff&logoColor=white)](https://github.com/astral-sh/ruff)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](Dockerfile)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Security: AES-GCM](https://img.shields.io/badge/PII%20Vault-AES--GCM-blueviolet.svg)](app/vault.py)

**Designed and Engineered by Seyed Mohammad Sina Hosseini**

[Architecture Spec](docs/ARCHITECTURE.md) • [REST API Reference](docs/API_REFERENCE.md) • [Deployment Guide](docs/DEPLOYMENT.md) • [Sample Datasets](docs/SAMPLE_DATA.md) • [Persian Manuals (فارسی)](docs/fa/)

</div>

---

## Executive Summary

**Accounting Platform** is a full-featured, high-assurance enterprise accounting and ERP platform written in **Python (FastAPI + SQLAlchemy 2.0)**. Built from the ground up around standard double-entry bookkeeping principles, it delivers strict mathematical ledger invariants, distributed request idempotency, cryptographic privacy protection for Personally Identifiable Information (PII), automated statutory payroll and progressive tax calculations, fixed asset depreciation, and electronic tax compliance.

While tailored with domain logic for travel agency operations—including real-time gross margin tracking per trip dossier and date-effective tax calculation—the core engine constitutes a general-purpose double-entry financial ledger suitable for commercial enterprises and SMEs.

---

## System Architecture

The platform is designed using a **clean, domain-centric layered architecture** with strict separation of concerns:

```
┌────────────────────────────────────────────────────────────────────────┐
│                      PRESENTATION & CLIENT LAYER                       │
│  * Responsive Single-Page Application (Vanilla JS / RTL / CSS)         │
│  * Interactive OpenAPI / Swagger UI (/docs) & ReDoc (/redoc)           │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / JSON / Form-Data
┌───────────────────────────────────▼────────────────────────────────────┐
│                       API & SECURITY MIDDLEWARE                        │
│  * Granular Role-Based Access Control (RBAC: Admin, Accountant, etc.)  │
│  * Token-Bucket Rate Limiter & Structured Exception Translators        │
│  * Request Idempotency Key Processor (DB-Backed Hash Lock)             │
│  * Cryptographic PII Vault (AES-GCM / Fernet Field-Level Encryption)   │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Domain DTOs
┌───────────────────────────────────▼────────────────────────────────────┐
│                        CORE BUSINESS DOMAIN LAYER                      │
│  ┌───────────────────────┐ ┌──────────────────────┐ ┌────────────────┐ │
│  │ Double-Entry Ledger   │ │ 4-Tier Chart of Accs │ │ Treasury/Banks │ │
│  │ (Debit = Credit Rule) │ │ (Group/General/Sub)  │ │ (Zero-PL Xfer) │ │
│  └───────────────────────┘ └──────────────────────┘ └────────────────┘ │
│  ┌───────────────────────┐ ┌──────────────────────┐ ┌────────────────┐ │
│  │ Sales & Dossier Margin│ │ Fixed Assets Depr.   │ │ Payroll & Tax  │ │
│  │ (Gross Profit Engine) │ │ (Straight/Declining) │ │ (Tiered Slabs) │ │
│  └───────────────────────┘ └──────────────────────┘ └────────────────┘ │
│  ┌───────────────────────┐ ┌──────────────────────┐ ┌────────────────┐ │
│  │ E-Invoicing (Moudian) │ │ Bidirectional Excel  │ │ Immutable Log  │ │
│  │ (Type 1/2 Tax Payloads│ │ (Roundtrip Engine)   │ │ (Audit Trail)  │ │
│  └───────────────────────┘ └──────────────────────┘ └────────────────┘ │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ SQLAlchemy 2.0 ORM
┌───────────────────────────────────▼────────────────────────────────────┐
│                      PERSISTENCE & STORAGE ENGINE                      │
│  * SQLite 3 Engine with Write-Ahead Logging (WAL) Mode                 │
│  * Referential Foreign Keys & Domain Check Constraints                 │
│  * Zero-Downtime Hot Backup Engine with Cryptographic Key Bundling     │
│  * Deterministic Forward-Only Migration Runner                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Core Engineering Invariants & Guarantees

1. **Strict Double-Entry Zero-Sum Parity ($\sum \text{Debit} = \sum \text{Credit}$)**:
   - Line items are validated client-side, verified in the domain service, and guarded by database check constraints. An unbalanced entry cannot be posted to the ledger under any circumstance.
2. **Ledger Immutability & Reversing Entries**:
   - Posted journal entries are strictly immutable. Correction requires generating an explicit reversing entry (`reversal_of_id` / `reversed_by_id`) with a mandatory audit rationale.
3. **Scaled Integer Rial Arithmetic**:
   - Eliminates IEEE 754 floating-point drift by storing all financial balances as **64-bit integer Rials**. All tax rates and ratios use scaled basis-point calculations.
4. **Distributed Request Idempotency**:
   - Mutating API endpoints accept an `Idempotency-Key` header backed by a database table with SHA-256 payload digest verification. Prevents accidental double-billing or duplicate ledger entries during network retries or concurrent clicks.
5. **Cryptographic PII Vault (Fail-Closed)**:
   - Sensitive passenger credentials (National ID, Passport number) are encrypted at rest using AES-GCM (Fernet). In production, the system fails to boot if cryptography dependencies or keys are missing (`ALLOW_PLAINTEXT_PII=0`). Full unmasking generates an immutable audit record.
6. **Online Hot Backups & Integrity Verification**:
   - Consistent zero-downtime snapshots are captured using SQLite's native backup API under active WAL write transactions. Snapshots automatically undergo `PRAGMA integrity_check` and `PRAGMA foreign_key_check`.

---

## Key Feature Matrix

| Subsystem | Capabilities & Technical Implementation |
| :--- | :--- |
| **General Ledger** | Draft, Posted, and Reversed states; 4-tier Chart of Accounts (Group, General, Subsidiary, Detail); trial balance; chronological account ledgers; financial period locking. |
| **Treasury & Cash Flow** | Cash registers, petty cash, and commercial bank accounts; zero-P&L inter-account liquidity transfers; 30/60/90-day predictive cash calendar. |
| **Sales & Billing** | Service invoices (flights, hotels, visas, packages); supplier purchase bills; date-effective VAT rates; automatic allocation of payments against oldest receivables. |
| **Trip Dossier Profitability**| Real-time margin analytics tracking revenue, vendor direct expenses, and gross profit per trip folder. Automatic loss-making alerts. |
| **Fixed Assets** | Capital expenditure tracking; Straight-Line and Declining-Balance depreciation models; monthly automated depreciation journal runs; asset disposal and salvage accounting. |
| **Statutory Payroll** | Base salary, allowances, overtime, and loan amortization; progressive income tax calculations; statutory social security deductions (7% employee, 23% employer). |
| **E-Invoicing (Moudian)** | 13-digit service code validation; structured Type 1 and Type 2 JSON payload generator; dispatch queue; multi-sheet audit reconciliation workbooks. |
| **Bidirectional Excel I/O**| Complete roundtrip engine for journal entries and chart of accounts; schema validation; cryptographic file fingerprinting preventing duplicate imports. |
| **Security & RBAC** | 5 discrete roles (`admin`, `manager`, `accountant`, `employee`, `auditor`); password hashing; session tokens; append-only audit trail. |

---

## Quick Start Guide

### Option 1: Run with Docker Compose (Recommended)

Zero-friction deployment with containerized application and volume persistence:

```bash
# Clone the repository
git clone https://github.com/seyedmohammadsinahosseini/Accounting-Platform.git
cd Accounting-Platform

# Start containerized platform
docker compose up -d --build
```

Access the application:
- **Web Interface**: `http://localhost:8000`
- **Interactive Swagger API Docs**: `http://localhost:8000/docs`
- **Health Check**: `http://localhost:8000/api/health`

To view container logs or stop:
```bash
docker compose logs -f
docker compose down
```

---

### Option 2: Local Python Environment

#### 1. Prerequisites
- Python 3.10, 3.11, 3.12, or 3.13
- Virtual environment support (`venv`)

#### 2. Setup & Installation
```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

#### 3. Seed Demo Data & Start Server
```bash
# Seed demo accounts and baseline data
python3 reset_demo.py

# Launch development server with automatic port selection & browser launch
python3 tools_start.py
```

---

## Pre-Configured Demo Credentials

When running in evaluation mode (`ACCT_DEMO_SEED=1`), the database initializes with role-based demo accounts:

| Username | Password | Role | Permissions & Scope |
| :--- | :--- | :--- | :--- |
| **`admin`** | `admin123` | System Administrator | Full administrative authority, user provisioning, backup management |
| **`accountant`** | `accountant123` | Chief Accountant | Journal entry posting, reversals, fiscal period closing, payroll approval |
| **`manager`** | `manager123` | Agency Manager | Read-only access across all financial ledgers, reports, and margin metrics |
| **`employee`** | `employee123` | Front-Desk / Sales | Draft sales invoice creation, trip dossiers (isolated to own entries) |
| **`auditor`** | `auditor123` | External Auditor | Read-only access across all books, ledgers, and audit logs |

---

## Comprehensive Test Suite & Verification

The platform includes an automated test suite with **142 tests** covering core accounting rules, edge cases, tax calculations, cryptographic vault security, Excel roundtripping, counter sales, targeted settlement allocation, idempotency, rollback behavior, and cash-calendar due dates.

```bash
# Run complete test suite
pytest -v

# Run with short traceback
pytest -q

# Run code style and linting verification
ruff check .
```

### Test Suite Summary
```text
142 tests collected
142 passed
```

The current environment reports one upstream Starlette/httpx deprecation warning; it does not fail the suite.

---

## Command-Line Maintenance Utility (`tools.py`)

A production-grade CLI utility is included for operational maintenance and disaster recovery:

```bash
# Verify double-entry ledger balance across all accounts
python3 tools.py check-balance

# Trigger an immediate SQLite WAL consistent hot backup
python3 tools.py backup

# List all available snapshots with timestamps and sizes
python3 tools.py backups

# Restore the accounting database from a specific snapshot
python3 tools.py restore accounting-manual-20261018.db

# Reset a user password
python3 tools.py password admin
```

---

## Repository Structure

```text
├── deploy/
│   ├── ci/                        # GitHub Actions automated test & Docker CI pipelines
│   └── debian/                    # Hardened Linux systemd units & Nginx reverse proxy configs
├── app/
│   ├── api.py                     # FastAPI route definitions (148 REST endpoints)
│   ├── assets.py                  # Fixed asset depreciation ledger engine
│   ├── backup.py                  # Consistent SQLite hot-backup engine
│   ├── config.py                  # Centralized environment configuration
│   ├── dates.py                   # High-precision Jalali-Gregorian date converter
│   ├── db.py                      # Database engine, session scope, WAL configuration
│   ├── excel_io.py                # Bidirectional Excel roundtrip serialization engine
│   ├── main.py                    # Application lifespan, background backup loop, static server
│   ├── migrations.py              # Forward-only schema migration runner
│   ├── moadian.py                 # Electronic tax invoicing (Moudian) compliance engine
│   ├── models.py                  # SQLAlchemy ORM models with schema check constraints
│   ├── money.py                   # Integer Rial currency parsing and formatting
│   ├── passengers.py              # Passenger dossier registry with PII encryption
│   ├── payroll.py                 # Statutory payroll engine with progressive tax slabs
│   ├── rules.py                   # Accounting business domain rules & validation
│   ├── sales.py                   # Invoicing, vendor bills, trip margin engine
│   ├── security.py                # RBAC permissions, session tokens, password hashing
│   ├── seed.py                    # Chart of accounts and baseline demo seeder
│   ├── services.py                # General ledger, trial balance, and journal services
│   ├── statements.py              # Financial statements (P&L, Balance Sheet, Cash Flow)
│   ├── vault.py                   # AES-GCM cryptographic vault for sensitive PII
│   └── static/                    # Responsive Single-Page Application interface

├── docs/
│   ├── ARCHITECTURE.md            # Detailed architectural and domain design specification
│   ├── API_REFERENCE.md           # Comprehensive REST API reference
│   ├── DEPLOYMENT.md              # Production deployment & hardening guide
│   ├── SAMPLE_DATA.md             # Sample dataset structures & verification schemas
│   └── fa/                        # Original localized Persian documentation
├── sample_data/
│   ├── excel_templates/           # 14 standardized verification spreadsheets (English)
│   └── persian_templates/         # Localized verification spreadsheets (Persian)
├── tests/                         # 142 automated unit and integration tests
├── .dockerignore                  # Docker build exclusions
├── .env.example                   # Environment configuration template
├── .gitignore                     # Git ignore rules
├── CODE_OF_CONDUCT.md             # Contributor Covenant Code of Conduct
├── CONTRIBUTING.md                # Contribution guidelines and coding conventions
├── docker-compose.yml             # Production Docker Compose orchestration
├── Dockerfile                     # Hardened multi-stage Docker container specification
├── LICENSE                        # MIT License
├── pyproject.toml                 # Modern Python build metadata & tool configurations
├── README.md                      # Primary project documentation
├── requirements.txt               # Declared Python dependencies
├── reset_demo.py                  # Database reset and demo seed script
├── run.sh                         # Production ASGI runner script
├── SECURITY.md                    # Security policy and vulnerability disclosure procedures
├── THIRD-PARTY-NOTICES.md         # Third-party dependency licenses and notices
├── tools.py                       # CLI utility for maintenance, backups, and ledger audits
└── tools_start.py                 # Interactive development server launcher
```

---

## Production Hardening & Deployment

The platform is designed to run in hardened enterprise environments:
- **Systemd Sandboxing**: Pre-configured systemd unit enforces `ProtectSystem=strict`, `NoNewPrivileges=true`, `PrivateTmp=true`, and `UMask=0077`.
- **Nginx Reverse Proxy**: Complete SSL/TLS termination, HTTP/2, security headers (`CSP`, `HSTS`, `X-Frame-Options`), and static caching.
- **Automated Backup Timers**: Automated systemd timer for daily off-peak consistent backups with retention rotation.

For complete step-by-step instructions, see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## 🌐 Localization & Persian Documentation

While this repository is fully documented and structured in English for international technical evaluation, comprehensive Persian user manuals and operational guides are preserved under [`docs/fa/`](docs/fa/):
- [راهنمای جامع کاربری (User Guide)](docs/fa/USER_GUIDE.md)
- [راهنمای اجرای محلی (Local Setup Guide)](docs/fa/LOCAL_SETUP.md)
- [راهنمای استقرار روی دبیان (Debian Deployment Guide)](docs/fa/DEBIAN_DEPLOYMENT.md)

---

## Author & Attribution

**Seyed Mohammad Sina Hosseini**
- GitHub: [@seyedmohammadsinahosseini](https://github.com/seyedmohammadsinahosseini)
- Email: [seyedmohammadsinahosseini@gmail.com](mailto:seyedmohammadsinahosseini@gmail.com)

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details. Third-party component licenses are cataloged in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
