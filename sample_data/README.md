# Sample Data & Verification Workbooks

This directory contains standardized Excel spreadsheets utilized for integration testing, bidirectional data roundtrips, and demonstration of the Accounting Platform's reporting and compliance engines.

## Directory Structure

- `excel_templates/`: Standardized English-named Excel templates.
- `persian_templates/`: Original localized spreadsheets matching Persian accounting practices.

## Catalog of Workbooks

| Filename | Description | Roundtrip Capable | Target Subsystem |
| :--- | :--- | :---: | :--- |
| `01-journal-entry-template.xlsx` | Sample journal entry authoring template | :white_check_mark: Yes | General Ledger |
| `02-journal-entries-export.xlsx` | Exported journal ledger with metadata help sheet | :white_check_mark: Yes | General Ledger |
| `03-trial-balance-export.xlsx` | Multi-column trial balance (debit, credit, balance) | :x: Read-Only | Financial Statements |
| `04-trip-dossiers-export.xlsx` | Travel dossiers with real-time margin breakdown | :x: Read-Only | Sales & Operations |
| `05-invoices-export.xlsx` | Commercial sales and supplier invoices | :x: Read-Only | Billing & Receivables |
| `06-cash-calendar-export.xlsx` | 30/60/90-day cash flow forecast | :x: Read-Only | Treasury |
| `07-tax-invoices-export.xlsx` | Multi-sheet tax reconciliation report | :x: Read-Only | E-Invoicing / Moudian |
| `08-vat-report-export.xlsx` | Value-Added Tax quarterly summary | :x: Read-Only | Statutory Tax |
| `09-passengers-export.xlsx` | Passenger roster with masked sensitive PII | :x: Read-Only | Passenger Registry |
| `statutory-payroll-constants.xlsx` | Statutory income tax brackets & insurance ceilings | :x: Verification | Payroll Engine |
| `payroll-register.xlsx` | Monthly employee payroll register | :x: Verification | Payroll Engine |
| `social-security-worksheet.xlsx` | Social security (Tamin Ejtemaei) calculation sheet | :x: Verification | Payroll Engine |
| `payroll-tax-worksheet.xlsx` | Progressive payroll tax brackets breakdown | :x: Verification | Payroll Engine |
| `employee-contracts-decrees.xlsx` | Base compensation decrees and employment terms | :x: Verification | Payroll Engine |

## Bidirectional Roundtrip Protocol

Workbooks tagged as **Roundtrip Capable** (`01` and `02`) include a dedicated metadata sheet (`راهنما` / Help) storing schema versioning and cryptographic file fingerprints (`fingerprint`). Upon uploading through `/api/import/journal` or `/api/import/accounts`:
1. The engine checks file fingerprint uniqueness to prevent duplicate imports (`import_records` table).
2. The sheet rows are validated in memory against account nature, debit/credit parity, and date formats.
3. If valid, entries are staged into the ledger as drafts pending accountant review.
