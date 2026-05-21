# Auctus Agent Roadmap

This roadmap is only for the desktop/local agent.

## Now

- Stabilize the current desktop UI and runtime.
- Keep Mac/Windows packaging scripts working.
- Improve first-run setup and model configuration.
- Add an explicit Task Mode for multi-step work: users can keep normal chat fast, or opt into a slower planned workflow for research, comparisons, purchasing decisions, and other complex tasks.
- Tighten permission prompts for files, terminal, browser, and external integrations.
- Keep mobile pairing compatible with `auctus-agent-mobile`.

### Task Mode MVP

Goal: make Auctus Agent behave more like a practical assistant for complex tasks without slowing down simple questions.

- Default chat stays fast and direct.
- Users can explicitly enter Task Mode from the UI.
- Task Mode starts by turning the user's goal into a visible checklist.
- The agent works through steps one by one instead of jumping from a single search result to a final answer.
- The UI shows a subdued side/bottom progress panel with the current step, completed steps, pending steps, and short activity updates.
- The progress panel shows observable work notes, not hidden chain-of-thought.
- The first version should support one active task at a time and does not need long-term task history.

Initial implementation steps:

1. Add a task state object with `goal`, `steps`, `current_step`, `activity`, `status`, and optional `artifacts`.
2. Add prompt instructions for Task Mode: plan first, ask for missing requirements, update progress, then execute.
3. Add API support for creating, updating, reading, and ending the active task.
4. Add UI controls for `Quick answer` versus `Task Mode`, plus a light task progress panel.
5. Test with real examples such as building insurance comparison, laptop selection, travel planning, and simple questions that should remain fast.

## Done

- CLI and FastAPI/Web UI foundation.
- Local tool execution and output generation.
- Long-term memory and candidate confirmation flow.
- Skill learning and evaluation loop scaffolding.
- Telegram/remote-control integration.
- Cron/scheduled task support.
- macOS standalone build pipeline.
- Hosted API/BYO key integration hooks.

## Next

- Reduce UI complexity in settings and onboarding.
- Add clearer runtime diagnostics for failed tool calls.
- Improve release smoke tests before packaging.
- Document what is local-only versus hosted/API-backed.
- Keep Windows packaging aligned with Mac packaging.

## Later

- More native desktop shell polish.
- Richer mobile relay integration through `auctus-api`.
- Safer browser automation presets.
- More structured exports for generated user projects.

## Related Docs

- `docs/release.md`
- `docs/standalone-build.md`
- `docs/mobile-cloud-relay.md`
- `docs/closed-learning-loop.md`
- `docs/agent-capability-gaps.md`
