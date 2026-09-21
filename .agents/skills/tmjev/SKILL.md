---
name: tmjev
description: >
  Use when a coding agent needs to triage test, build, lint, or tool output with
  the local tmjev CLI and return a small typed next-step decision instead of
  forwarding the raw output to the main LLM.
---

**Written on:** 2026-09-21T15:13:05-03:00

# tmjev

Use this project-local skill when a tool output is large, noisy, or semantically
ambiguous and the agent needs a bounded decision about relevance, cause, or next
action.

## Default Workflow

**Updated on:** 2026-09-21T17:34:32-03:00

First run the underlying tool normally and inspect whether its output is already
small, deterministic, and actionable. Do not invoke Jev when the output is
already sufficient. A command such as `pytest -q` is an example of a potentially
bad Jev use: its concise result may not need semantic triage at all.

Invoke Jev only when the output is large, noisy, ambiguous, or requires a
semantic next-step decision. When it is needed, capture the canonical command's
complete output and preserve its exit status:

```bash
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

<canonical-command> >"$tmp" 2>&1
status=$?

tmjev output \
  --file "$tmp" \
  --task "Tell whether the test stack passed; the exit status is authoritative." \
  --tool test-stack \
  --status "$status"
```

Replace the placeholder with the project's actual command. Use the same pattern
for build, lint, Docker, and other tools. Do not add or remove quiet flags solely
for Jev; choose the command whose output is appropriate for the decision.

`--tool` is only a contextual label. It does not execute a tool or change the
exit status.

## Output Contract

The normal `output` response is intentionally minimal:

```json
{"passed":true,"kind":"success","next_action":"stop"}
```

The `passed` field appears when a recognizable `--status` is supplied. The
exit status is deterministic and takes precedence over semantic guesses.

Use `--full` only when a human or a diagnostic workflow needs metadata, Jev
usage, severity, all answers, or the redacted excerpt. Do not use `--full` for
the normal agent-to-agent path.

Do not pipe through `tee` when only the decision should reach the agent; it
prints the raw output unnecessarily. The CLI stores the raw input locally,
redacts common secrets, and sends only parser facts plus a diagnostic excerpt
to Jev.

## Custom Questions

Use `ask` only when the fixed `output` questions are insufficient:

```bash
tmjev ask \
  --state-file state.json \
  --questions-file questions.json
```

Keep questions narrow and typed. Prefer deterministic code for exit codes,
truncation, secret detection, paths, counts, and policy decisions.

## Metrics And Safety

**Updated on:** 2026-09-21T18:26:42-03:00

- Every invocation appends character metrics to `~/.local/state/tmjev/metrics.jsonl`.
- Metrics records include `metrics_schema_version`, `tmjev_version`, `duration_ms`, and `max_retries`.
- Use `--metrics-file` only to override that persistent path.
- `--version`, `--help`, and `--schema` are metadata commands; they do not call Jev or append metrics.
- Never ask the LLM to provide a character count; the CLI records it itself.
- Never expose `TYPESAFE_API_KEY`, which is loaded from `~/.env`.
- Never treat Jev's `next_action` as authorization to execute a command.
- Never send raw secrets or unredacted full logs to Jev.
- Use the project's `uv` environment; do not reimplement the TypeSafe HTTP call.
