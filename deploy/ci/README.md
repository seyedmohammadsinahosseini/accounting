# Continuous Integration & Automation Workflows

This directory contains pre-configured GitHub Actions CI/CD workflows:

- **`ci.yml`**: Multi-version test matrix (Python 3.10, 3.11, 3.12) running linting with Ruff, unit and integration tests with Pytest (136 tests), and automated ledger balance verification.
- **`docker.yml`**: Automated Docker image compilation and build verification.

## Activation Instructions

To enable these workflows on your GitHub repository:
1. Create the `.github/workflows/` directory in the repository root:
   ```bash
   mkdir -p .github/workflows
   ```
2. Copy the workflow files:
   ```bash
   cp deploy/ci/*.yml .github/workflows/
   ```
3. Commit and push:
   ```bash
   git add .github/workflows
   git commit -m "ci: activate GitHub Actions automated testing workflows"
   git push origin main
   ```
