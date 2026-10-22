# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |

## Security Architecture Highlights

Accounting Platform is engineered with strict financial and data security principles:
1. **Cryptographic Vault for PII**: All Personally Identifiable Information (such as National IDs and Passport numbers) is encrypted at rest using AES-GCM (Fernet primitives) with dedicated secret key management. Plaintext storage is rejected by default (`ALLOW_PLAINTEXT_PII=0`).
2. **Access Control & RBAC**: Granular role-based permissions (Administrator, Manager, Accountant, Sales, Auditor) ensure least-privilege access.
3. **Audit Trail**: High-consequence financial transactions and PII unmasking events generate immutable records in the `audit_log` table.
4. **Idempotency Safeguards**: Mutating requests accept an `Idempotency-Key` header with database-level uniqueness constraints and atomic execution locks to prevent duplicate billing or ledger postings.
5. **Systemd Sandboxing**: Production service configurations enforce `ProtectSystem=strict`, `NoNewPrivileges=true`, `PrivateTmp=true`, and `UMask=0077`.

## Reporting a Vulnerability

If you discover a security vulnerability within Accounting Platform, please report it privately:
- **Email**: `seyedmohammadsinahosseini@gmail.com`
- Please provide detailed steps to reproduce the issue and any relevant payloads.
- Reports are acknowledged within 48 hours, and patches are prioritized.
