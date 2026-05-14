# Auctus Agent Release Rules

## Repository

GitHub repository name:

```text
auctus-agent
```

This repository contains the local desktop/web Agent only:

```text
app/
agent.py
prompts/
scripts/
tests/
docs/
Setup.command
Setup.bat
Start.command
Start.bat
README.md
INSTALL.md
requirements.txt
```

Do not commit user data, local databases, generated outputs, logs, virtual environments, or API keys.

## Version Source

The local Agent version is defined in:

```text
app/version.py
```

The local server exposes it at:

```text
GET /api/version
```

Example:

```json
{
  "name": "Auctus Agent",
  "version": "0.1.0",
  "channel": "dev",
  "api_compat": "v1",
  "update_check_url": null
}
```

## Versioning

Use semantic versions:

```text
MAJOR.MINOR.PATCH
```

- PATCH: bug fixes and small UI copy changes.
- MINOR: new user-visible features or compatible API changes.
- MAJOR: breaking config, data, API, or installation changes.

Pre-1.0 rule: until payment, hosted API deployment, and installer UX are production-ready, keep versions under `1.0.0`.

## Git Tags

Use Agent-specific tags:

```text
agent-v0.1.0
agent-v0.1.1
agent-v0.2.0
```

## Release Package

Short-term release format:

```text
Auctus-Agent-mac-v0.1.0.zip
Auctus-Agent-windows-v0.1.0.zip
```

Each package should include:

```text
Setup.command / Setup.bat
Start.command / Start.bat
app/
agent.py
prompts/
scripts/
requirements.txt
README.md
INSTALL.md
docs/
```

Do not include:

```text
.env
data/
inputs/
outputs/
logs/
.venv/
```

## Update Flow

Short-term:

1. App shows the current version from `/api/version`.
2. App checks the configured `UPDATE_CHECK_URL` or the Auctus API `/version` endpoint.
3. If a newer version exists, the app shows a download link.
4. User downloads and runs the new installer package.

Do not implement silent auto-update yet. The Python app may need dependency installs, data migrations, and user confirmation.

Long-term:

- Package as Electron/Tauri/native installer.
- Add signed installers.
- Add in-app download, install, and restart.

## Release Checklist

1. Update `app/version.py`.
2. Run tests:

```bash
python -m pytest -q
```

3. Run compile check:

```bash
PYTHONPYCACHEPREFIX=.pycache_check python -m compileall -q app agent.py
rm -rf .pycache_check
```

4. Confirm `.gitignore` excludes local data and secrets.
5. Create tag:

```bash
git tag agent-v0.1.0
git push origin agent-v0.1.0
```

6. Upload macOS and Windows zip packages to GitHub Releases.

