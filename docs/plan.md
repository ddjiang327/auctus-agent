# Auctus Agent Plan

This is the active plan for the desktop/local agent. Older two-stage product planning has been folded into the current roadmap.

## Product Role

`auctus-agent` is the local execution environment. It runs on the user's Mac or Windows machine, owns local permissions, uses local files, and produces outputs.

## Current Focus

- Keep setup reliable for non-technical users.
- Keep local data and permissions understandable.
- Keep the desktop release package easy to install and update.
- Preserve local-first behavior while allowing optional hosted API usage.

## Main Capabilities

- Web UI and desktop shell.
- Chat with local tools.
- File generation and file analysis.
- Browser/search helpers.
- Email and messaging integrations where configured.
- Memory and skill learning with human confirmation.
- Scheduled jobs.
- Mobile/relay pairing support.

## Boundaries

Owned here:

- Local tool execution.
- Local settings and model configuration.
- Desktop packaging.
- Local memory, skills, outputs, and logs.

Owned elsewhere:

- Hosted subscriptions and billing: `../../auctus-api`
- App Store mobile client: `../../auctus-agent-mobile`
- Workspace-level roadmap: `../../docs/roadmap.md`

## Active Docs

- `README.md`: install and usage.
- `INSTALL.md`: user-facing setup.
- `docs/roadmap.md`: current desktop roadmap.
- `docs/release.md`: release process.
- `docs/standalone-build.md`: packaging details.
- `docs/mobile-cloud-relay.md`: longer-term mobile/desktop relay design.
- `docs/closed-learning-loop.md`: memory and skill learning design.
- `docs/agent-capability-gaps.md`: known capability gaps.
