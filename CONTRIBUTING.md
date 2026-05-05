# Contributing to File Quacker

Thanks for your interest in contributing.

File Quacker is a small, focused desktop app for inspecting, querying, profiling, and exporting local files. Contributions are welcome when they support that goal and keep the app simple to use and maintain.

## Before opening a PR

For small bug fixes, documentation updates, and obvious cleanup, feel free to open a PR directly.

For larger changes, new features, UI redesigns, architecture changes, or new dependencies, please open an issue first. It is much easier to agree on the approach before code is written.

Please also read [`docs/coding_conventions.md`](docs/coding_conventions.md) before contributing.

## Dev setup

```bash
# Python
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

# Frontend
npm --prefix frontend install
```

Run the app with hot reload:

```bash
python dev.py
```

Run against the production frontend build:

```bash
npm --prefix frontend run build
python -m file_quacker
```

## Build the Windows executable

```bash
npm --prefix frontend run build
pyinstaller file_quacker.spec
```

The output is written to:

```text
dist/file_quacker.exe
```

## Tests

The pytest suite covers ingest, profiling, type derivation, DDL generation, and file export paths against DuckDB.

```bash
pytest
```

SQL Server export tests are marked as integration tests and are skipped by default. To run them, you need the required `FQ_SS_*` environment variables and Microsoft ODBC Driver 18 for SQL Server.

```bash
pytest -m integration
```

## Frontend type check

```bash
cd frontend
npx vue-tsc --noEmit
```

This should be clean before a PR is merged.

## Pull request guidelines

- Keep changes focused.
- Prefer simple, readable code.
- Avoid unrelated refactoring.
- Avoid new dependencies unless they were discussed first.
- Include tests or a short manual verification note when practical.
- Follow the existing code style and project conventions.

## Reporting bugs

Use the GitHub issue tracker. Please include:

- Your OS and Windows build number.
- Your WebView2 Runtime version.
- What file type you were opening.
- Steps to reproduce the issue.
- What you expected to happen.
- What actually happened.

If the window opens but the body is blank white, check the WebView2 troubleshooting section in [`README.md`](README.md) before filing a bug.

## Security issues

Please do not open a public issue for security problems. See [`SECURITY.md`](SECURITY.md).
