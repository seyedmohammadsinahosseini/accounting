# Contributing to Accounting Platform

Thank you for your interest in contributing to the Accounting Platform project! We welcome contributions, bug reports, and architectural feedback.

## Code of Conduct

All contributors and participants are expected to adhere to our [Code of Conduct](CODE_OF_CONDUCT.md).

## Development Setup

1. **Fork and Clone the Repository**:
   ```bash
   git clone https://github.com/seyedmohammadsinahosseini/Accounting-Platform.git
   cd Accounting-Platform
   ```

2. **Set Up Python Virtual Environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   pip install pytest httpx ruff
   ```

3. **Initialize Demo Data**:
   ```bash
   python3 reset_demo.py
   ```

4. **Run the Test Suite**:
   ```bash
   pytest
   ```

5. **Run Linter**:
   ```bash
   ruff check .
   ```

## Commit Message Conventions

We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification:
- `feat`: A new user-facing feature or domain engine
- `fix`: A bug fix in accounting logic or API
- `docs`: Documentation updates or additions
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `test`: Adding missing tests or correcting existing tests
- `ci`: Changes to CI/CD workflows or scripts

## Pull Request Guidelines

1. Create a descriptive feature branch (`feature/double-entry-validation` or `fix/vat-rounding`).
2. Ensure all existing 136 tests pass without regressions.
3. Write new tests covering new business logic or bug fixes.
4. Verify code conforms to Ruff formatting standards.
5. Submit a Pull Request detailing the changes and rationale.
