# Contributing to perfmon-mcp

Thanks for your interest in contributing.

## Pull requests

- One concern per PR. Small, single-purpose changes are easier to review and
  revert than large ones.
- PR title: one short imperative sentence (e.g.
  `feat: add discover_counter_instances` or `docs: clean up README`). The PR
  body explains *why*.
- Update `CHANGELOG.md` under the appropriate section
  (`Added` / `Changed` / `Deprecated` / `Removed` / `Fixed`) when your change
  is user-visible.
- For any new `@mcp.tool()`, remember the tool count is part of the contract
  — bump the version in `pyproject.toml` and add a CHANGELOG entry.

## Sign-off

All commits must be signed off:

```powershell
git commit -s -m "your message"
```

This appends a `Signed-off-by: Your Name <your@email>` trailer asserting the
[Developer Certificate of Origin](https://developercertificate.org/) over
your contribution.

## Development setup

```powershell
cd C:\git\perfmon-mcp
uv sync
uv run --group dev pytest tests/ -v
```

`uv` manages the venv and Python install — don't `pip install` directly.

Tests use synthetic fixtures and never touch real perfmon counters or `.blg`
files; the `relog.exe` shellout is mocked via
`monkeypatch.setattr(subprocess, "run", ...)`.
