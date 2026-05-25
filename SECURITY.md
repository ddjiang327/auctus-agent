# Security Policy

## Supported Versions

Auctus Agent is pre-1.0. We support the **latest tagged release** and the
**`main` branch**. Older tags receive security fixes only if the issue is
critical and the migration path is non-trivial.

## Reporting a Vulnerability

**Do not** open a public issue for security bugs. Use one of the private
channels below:

- **Preferred:** [GitHub Security Advisories](https://github.com/ddjiang327/auctus-agent/security/advisories/new)
  — opens a private thread with the maintainers.
- **Email:** see the maintainer's GitHub profile for current contact.

When reporting, include:

1. A clear description of the issue
2. Reproduction steps (minimal repro if possible)
3. Affected versions / commit SHAs you've tested
4. What you tried to verify it's exploitable

We aim to acknowledge within **72 hours** and ship a fix or workaround within
**14 days** for critical issues. We will credit you in the release notes
unless you ask us not to.

## Threat Model

Auctus Agent is a **local-first** agent. By default, the threat model assumes:

- The agent runs on a machine the user trusts (their own laptop / VPS).
- The agent server binds to `127.0.0.1` and is not exposed to the public
  internet.
- The user provides their own model provider API key.

Things we **do** protect against:

- **Tool-call abuse.** Tools that touch the filesystem, terminal, or calendar
  require explicit per-task or persistent permission. Dangerous shell commands
  (e.g. `rm -rf`) are intercepted unless the user explicitly opts in.
- **Workspace escapes.** File tools default to `WORKSPACE_DIR` and refuse paths
  outside it without permission.
- **Secret leakage in logs.** Tool-call audit logs redact obvious key patterns.
- **Prompt injection from web content.** The agent loop is wary of
  instructions embedded in fetched pages; we do not blindly act on them.

Things explicitly **out of scope**:

- **Network-exposed deployments.** If you bind the FastAPI server to a public
  interface, you are responsible for authentication, TLS, and rate-limiting in
  front of it. The shipped server has none of these.
- **Malicious model providers.** If the model API key you configured belongs
  to a hostile provider, all bets are off.
- **Compromised host.** Auctus Agent does not sandbox its own runtime
  (Docker / firejail support is on the roadmap but not the default).
- **Side channels.** Timing, power, CPU caches — we don't defend against
  these.

## Hardening Recommendations

If you deploy Auctus Agent in a shared environment:

- Run inside a container with restricted volume mounts (only what the agent
  needs to see).
- Pin model provider URLs in `PROXY_BASE_URL` so a compromised DNS can't
  redirect you.
- Disable terminal access entirely (`terminal_access=disabled` in setup state)
  if you only need read / report tools.
- Limit `WORKSPACE_DIR` to a dedicated folder; never `/`.
- Audit `logs/tool_calls.jsonl` periodically.

## Disclosure Policy

We follow a 90-day coordinated disclosure timeline by default. If you find an
unrelated bug while investigating, please report it separately.
