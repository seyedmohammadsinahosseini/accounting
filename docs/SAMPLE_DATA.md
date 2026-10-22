# Accounting Platform — Sample Datasets & Verification Catalog

This document details the structure of the pre-configured sample datasets, chart of accounts codes, and verification spreadsheets bundled with **Accounting Platform**.

---

## 1. Baseline Chart of Accounts Hierarchy

The platform seeds a standard accounting structure for commercial and service operations:

| Code Range | Account Level | Nature | Description |
| :--- | :--- | :---: | :--- |
| `1` | Group (گروه) | Debit | Current & Non-Current Assets |
| `11` | General (کل) | Debit | Cash, Cash Equivalents & Bank Accounts |
| `1101` | Subsidiary (معین) | Debit | Cash Drawers & Petty Cash (صندوق و تنخواه‌گردان) |
| `1102` | Subsidiary (معین) | Debit | Commercial Bank Checking Accounts (بانک‌های ریالی) |
| `110201` | Detail (تفصیلی) | Debit | Bank Mellat Main Checking |
| `12` | General (کل) | Debit | Accounts Receivable & Trade Debtors (حساب‌های دریافتنی) |
| `1201` | Subsidiary (معین) | Debit | Commercial Customers Receivables (مشتریان تجاری) |
| `13` | General (کل) | Debit | Prepayments & Advances (پیش‌پرداخت‌ها) |
| `14` | General (کل) | Debit | Value Added Tax (VAT) Receivables / Input Credit |
| `15` | General (کل) | Debit | Property, Plant & Equipment / Capital Assets |
| `1599` | General (کل) | Credit | Accumulated Depreciation (Contra-Asset) |
| `2` | Group (گروه) | Credit | Current & Long-Term Liabilities |
| `21` | General (کل) | Credit | Accounts Payable & Trade Creditors (حساب‌های پرداختنی) |
| `2101` | Subsidiary (معین) | Credit | Suppliers & Vendors Payable (بستانکاران تجاری) |
| `2105` | Subsidiary (معین) | Credit | Value Added Tax (VAT) Payable (مالیات ارزش افزوده پرداختی) |
| `2107` | Subsidiary (معین) | Credit | Accrued Salaries & Wages Payable (حقوق و دستمزد پرداختنی) |
| `2108` | Subsidiary (معین) | Credit | Statutory Social Security Payable (بیمه تامین اجتماعی پرداختنی) |
| `2109` | Subsidiary (معین) | Credit | Withholding Payroll Tax Payable (مالیات تکلیفی حقوق) |
| `3` | Group (گروه) | Credit | Shareholders' Equity & Capital |
| `3101` | Subsidiary (معین) | Credit | Registered Capital (سرمایه اولیه) |
| `3102` | Subsidiary (معین) | Credit | Retained Earnings / Accumulated Profit & Loss (سود و زیان انباشته) |
| `4` | Group (گروه) | Credit | Operating & Service Revenues |
| `4101` | Subsidiary (معین) | Credit | Tour Package Revenue (درآمد تور) |
| `4102` | Subsidiary (معین) | Credit | Airline Ticketing Revenue (درآمد بلیت) |
| `4103` | Subsidiary (معین) | Credit | Hotel & Accommodation Revenue (درآمد اقامت) |
| `5` | Group (گروه) | Debit | Cost of Sales & Direct Service Costs |
| `5101` | Subsidiary (معین) | Debit | Direct Airline & Ticket Supplier Costs |
| `5102` | Subsidiary (معین) | Debit | Direct Hotel & Accommodation Supplier Costs |
| `6` | Group (گروه) | Debit | Administrative, Selling & General Expenses |
| `6101` | Subsidiary (معین) | Debit | Salaries & Wages Expense (هزینه‌ی حقوق و دستمزد) |
| `6102` | Subsidiary (معین) | Debit | Employer Social Security Expense (هزینه‌ی بیمه‌ی سهم کارفرما) |
| `6103` | Subsidiary (معین) | Debit | Office Rent Expense (هزینه‌ی اجاره) |
| `6104` | Subsidiary (معین) | Debit | Fixed Asset Depreciation Expense (هزینه‌ی استهلاک) |

---

## 2. Sample Verification Spreadsheets

The `sample_data/excel_templates/` directory contains 14 real-world workbooks:

1. **`01-journal-entry-template.xlsx`**: Blank standardized workbook for authoring draft journal entries in Excel.
2. **`02-journal-entries-export.xlsx`**: Exported snapshot of journal transactions containing cryptographic hash stamps in the metadata sheet.
3. **`03-trial-balance-export.xlsx`**: Full 4-tier trial balance with account codes, debit balances, credit balances, and zero-sum verification.
4. **`04-trip-dossiers-export.xlsx`**: Real-time gross margin and direct costs calculated across active trip dossiers.
5. **`05-invoices-export.xlsx`**: Commercial customer sales invoices with itemized tax breakdowns and settlement state.
6. **`06-cash-calendar-export.xlsx`**: Liquidity forecasting sheet modeling receivables maturities vs. vendor due dates.
7. **`07-tax-invoices-export.xlsx`**: Multi-sheet workbook formatted for audit reconciliation against electronic tax platforms.
8. **`08-vat-report-export.xlsx`**: Quarterly report contrasting taxable supplies, exempt supplies, and input/output VAT balances.
9. **`09-passengers-export.xlsx`**: Passenger roster showing masked identifiers conforming to privacy standards.
10. **`statutory-payroll-constants.xlsx`**: Progressive tax bracket boundaries and statutory insurance rate definitions.
11. **`payroll-register.xlsx`**: Complete itemized monthly payroll sheet for salaried and commissioned personnel.
12. **`social-security-worksheet.xlsx`**: Breakdown of the 7% employee and 23% employer statutory social security allocations.
13. **`payroll-tax-worksheet.xlsx`**: Progressive tax slab calculation matrix illustrating bracket transitions.
14. **`employee-contracts-decrees.xlsx`**: Base employment terms, child allowances, housing subsidies, and job classification titles.
