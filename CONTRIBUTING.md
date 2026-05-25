# Contributing to Auctus Agent

Thanks for your interest. A few ground rules to keep contributions easy to
review and integrate.

## Before you start

- **Open an issue first** for anything beyond a typo or a one-line fix. We'd
  rather agree on the shape of a change before you write it.
- **Look for an existing discussion** — search Issues and Discussions. Many
  ideas have prior context we'd hate to lose.
- **Pre-1.0**: the on-disk schema (SQLite tables, JSON formats) and the tool
  API surface are not yet frozen. If your contribution would lock something
  in, flag it explicitly in your PR.

## How to set up

```bash
git clone https://github.com/ddjiang327/auctus-agent.git
cd auctus-agent
bash scripts/install_mac.sh        # or scripts/install_windows.ps1
cp .env.example .env               # add a model API key
bash scripts/start_mac.sh
```

You'll need Python 3.10+. The first run installs the venv at `.venv/`.

## Running tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python tests/run_tests.py
```

Both should pass before opening a PR.

## What we'd love help with

- **New tools.** A tool needs:
  1. A pure function in `app/tools.py` (or a new `app/tools_*.py`) that takes
     plain Python types and returns a `dict`.
  2. An entry in `TOOL_SCHEMAS` describing it in the OpenAI function-calling
     format. Be specific about when the LLM should call it.
  3. An entry in `_DISPATCH` mapping the schema name to the function.
  4. A unit test in `tests/test_tools.py`.
  5. If it loads heavy dependencies, register the module in
     `auctus-agent.spec` `hiddenimports` for PyInstaller bundling.
- **Provider support.** New LLM provider integrations through LiteLLM, or
  alternative web-search / TTS / image-gen backends.
- **UI polish.** `app/ui.html` is a single-file UI. Focused improvements are
  welcome; large rewrites should be discussed first.
- **Docs.** README clarifications, troubleshooting recipes, deployment notes.

## What's out of scope

- Network endpoints that require a centralised auth/billing system. The
  project is local-first by design.
- Tools that exfiltrate user data to third parties without an explicit
  permission prompt.
- Changes that remove the AGPL or weaken the "local-first" guarantee.

## Pull request guidelines

- **Small and focused.** One change per PR. Refactor PRs separate from
  feature PRs.
- **Tests pass locally** before opening.
- **No secrets in commits.** Don't commit `.env`, real tokens, internal
  hostnames, personal paths. The `.gitignore` covers most of these, but
  please double-check `git diff` before pushing.
- **Commit messages.** Imperative mood, short subject (≤72 chars), free-form
  body if useful.
- **Sign off (optional but appreciated).** `git commit -s` for DCO.

## Reporting security issues

Don't open a public issue for security bugs. Email the maintainers (see the
repo's GitHub profile) or open a private security advisory through GitHub.

## License

By contributing, you agree that your contribution will be licensed under
the same [AGPL-3.0](LICENSE) as the rest of the project.
