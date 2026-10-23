# Fiscal-year lifecycle (implementation notes)

## Current model and calendar

- `FiscalYear` and `Period` in `app/models.py` remain the only year/month models. Statuses remain `open|closed`; periods keep `open|closed`.
- `app/dates.py:fiscal_year_of` defines the current supported calendar as 1 Farvardin through the day before the next 1 Farvardin. Fiscal-year dates are stored as Gregorian `date` values and displayed as Jalali dates.
- `app/statements.py:create_fiscal_year` explicitly creates an open year and its 12 open periods. It does not create an opening or closing entry. Legacy `services.ensure_fiscal_year` remains available for existing date-driven posting paths.

## Posting controls

`app/services.py:_assert_entry_date_open` is shared by draft creation, draft updates, and final posting. It validates the supported year range and checks both fiscal-year and month-period status. Drafts in closed periods are rejected; existing drafts cannot be moved into a closed period or posted there. Reversals still use `reverse_entry` and the original date, so a closed period must be handled through the authorized period/year workflow.

Fiscal documents with `source` `opening` or `closing` cannot be posted through the generic journal-post API. They must pass the fiscal preview/fingerprint checks in `app/statements.py` and the fiscal-finalization API.

## Close and opening workflow

- Close preview: `POST /api/fiscal/close-year/precheck`
- Create/update closing draft: `POST /api/fiscal/close-year/draft`
- Finalize: `POST /api/fiscal/close-year`
- Opening preview/draft/finalize: `POST /api/fiscal/opening/{preview,draft,post}`
- Opening reconciliation: `GET /api/fiscal/opening/reconcile`

Closing fingerprints are recomputed before draft creation and finalization. The closing document uses the standard journal services. Periods still open at finalization are disclosed as warnings; successful close posts the closing document, closes the remaining periods, updates the year/closing record, and records per-period audit events in the same session transaction.

Opening remains separate from year creation. A year may have activity before its opening is posted; preview reports this as a warning and keeps the activity. Transfer mode requires a closed source year, a valid close record and all twelve source periods closed. Opening amounts are built from permanent, balance-sheet ledger accounts and use integer Rial. Fingerprints include source/destination entries and account attributes. Transfer reconciliation compares each ledger account and exposes the corresponding party label where the party has its own detail ledger account.

## Controlled reopening

- Preview dependencies: `POST /api/fiscal/reopen-year/precheck`
- Execute: `POST /api/fiscal/reopen-year` with a required reason, preview fingerprint, and explicit acknowledgement when downstream operations exist.

The preview lists transfer openings in the dependent year chain, their status/reversal link and current reconciliation result, plus later posted entries and drafts. Any still-posted downstream transfer opening blocks reopening. The operator must reverse it through the ordinary journal reversal route (with reason), then obtain a fresh preview. Later operations are not deleted or rewritten; they require explicit acknowledgement and subsequent reclose/recreate/reconcile steps. Reopening reverses the closing entry through `reverse_entry` and audits period/year transitions.

## Authorization, audit and schema

Fiscal routes use `fiscal.view`/`fiscal.manage`. Fiscal year creation, previews, failures/denials, and transitions are audited. Existing `AuditLog`, `FiscalClosing`, and idempotency keys are reused. No fiscal-specific schema migration is introduced by this change; the current calendar and year/period/closing models are retained.

## Tests

`tests/test_fiscal_lifecycle.py` covers explicit year creation, duplicate prevention, leap-year end dates, 12 monthly periods, operation before opening and before prior-year close, opening reconciliation/idempotence, generic-post restrictions for fiscal documents, downstream reopening blocks/acknowledgement, and historical profit preservation. `tests/test_accounting.py` covers period-closed draft/post controls. `tests/test_statements.py` covers year close/reopen permissions and statement behavior.
