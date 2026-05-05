# Security Policy

## Reporting a security issue

Please do not open a public GitHub issue for security problems.

Instead, use GitHub's private reporting flow:

**Security → Advisories → Report a vulnerability**

This keeps the report private until it can be reviewed and fixed.

When reporting an issue, please include:

- What happened.
- Why you think it is a security issue.
- Steps to reproduce it.
- A small sample file or query, if helpful.
- Your OS, app version, and WebView2 Runtime version.

You should receive an initial response within a week. If you do not, feel free to follow up.

## Scope

File Quacker is a local desktop application. It does not run as a service, listen for incoming network connections, or send data to a cloud service.

Security issues we care about include:

- SQL injection through file names, column names, or other user-supplied values.
- Path traversal or arbitrary file writes during import or export.
- Command injection.
- Data from a loaded file rendering as HTML or script inside the app.
- Mishandling of database credentials entered by the user.

Generally out of scope:

- A malformed file causing DuckDB or another dependency to crash.
- A deliberately huge file using too much memory or disk.
- Bugs in third-party dependencies such as DuckDB, pywebview, WebView2, pyodbc, openpyxl, or chardet. Please report those upstream.

## Disclosure

The goal is to fix confirmed security issues within 30 days.

Reporters will be credited in release notes unless they ask not to be.