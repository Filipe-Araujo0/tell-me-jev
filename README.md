**Updated on:** 2026-09-22T10:01:55-03:00

# tmjev 🧭

Turn noisy test, build, and tool output into compact, typed next-step JSON for AI coding agents.

`tmjev` is a small `uv`-managed CLI around TypeSafe/Jev. It keeps raw output local, redacts common credential patterns, extracts a bounded diagnostic excerpt, and asks typed questions about cause, relevance, severity, security, and the next action.

The agent gets a clear assessment instead of carrying an entire log through its context window. `tmjev` does not write code, execute suggested actions, or authorize destructive commands.

## When To Use It

Use `tmjev` when output is large, noisy, ambiguous, or requires a semantic decision. Skip it when a command already produces a small, deterministic, actionable result.

## Quick Start

Requirements: Python 3.10+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Filipe-Araujo0/tell-me-jev.git
cd tell-me-jev
uv sync
```

Set `TYPESAFE_API_KEY` in `~/.env` or in the environment. The CLI reads the key for each invocation and never logs or sends it as part of the evaluated state.

Check the installation:

```bash
./tmjev --version
./tmjev --help
```

## The Main Workflow

Preserve the source command's exit status, then give the captured output and that status to `tmjev`:

```bash
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

pytest -q >"$tmp" 2>&1
status=$?

./tmjev output \
  --file "$tmp" \
  --task "diagnose the test failure" \
  --tool pytest \
  --status "$status"
```

The normal response is compact JSON. Example output is abbreviated here; the real response contains all six assessments:

```json
{
  "schema_version": 1,
  "model": "jev-latest",
  "answers": {
    "kind": {
      "value": "application_bug",
      "probabilities": {
        "application_bug": 0.91,
        "environment": 0.09
      }
    },
    "next_action": {
      "value": "fix_code",
      "probabilities": {
        "fix_code": 0.84,
        "inspect_source": 0.16
      }
    },
    "severity": {
      "value": "high",
      "probabilities": {
        "medium": 0.31,
        "high": 0.57,
        "critical": 0.12
      }
    }
  },
  "command_succeeded": false
}
```

`command_succeeded` is derived from the evaluated command's recognizable `--status`. It describes the source command, not the `tmjev` process or Jev. If no recognizable status is supplied, the field is omitted.

## Output Contract

The complete answer set is:

- `kind`: success, application bug, code quality, test issue, environment, network, security, or ambiguous.
- `relevant`: whether the output matters to the current task.
- `evidence_sufficient`: whether the evidence supports a safe next step.
- `next_action`: stop, inspect, fix code, fix the environment, retry, rerun, or redact and escalate.
- `security_signal`: whether the output contains a security concern or agent-directed instruction.
- `severity`: low, medium, high, or critical.

Every answer uses the same projection:

```json
{
  "value": "application_bug",
  "probabilities": {
    "application_bug": 0.91,
    "environment": 0.09
  }
}
```

For yes/no answers, `value` is the boolean at the `0.5` threshold and the probability map preserves uncertainty:

```json
{
  "value": true,
  "probabilities": {
    "true": 0.98,
    "false": 0.02
  }
}
```

The projected response omits TypeSafe `type`, `confidence`, `legend`, and token `usage`. The raw SDK response remains internal to the CLI.

Use `--pretty` for human-readable JSON. `--full` adds command metadata and the redacted excerpt when `--include-excerpt` is supplied. The legacy `--include-probabilities` flag remains accepted, but probabilities are always included in the projection.

## Input Modes

Evaluate a file or standard input:

```bash
./tmjev output \
  --file /tmp/tool-output.log \
  --task "diagnose the worker failure" \
  --tool docker-logs \
  --container airflow_worker
```

```bash
make test 2>&1 | ./tmjev output \
  --task "classify the test output" \
  --tool make
```

Ask custom typed questions with JSON files:

```bash
./tmjev ask \
  --state-file state.json \
  --questions-file questions.json
```

The `ask` mode uses the same `schema_version`, `model`, `answers`, `value`, and `probabilities` projection.

## Configuration

Common options:

- `--env-file PATH`: dotenv file containing `TYPESAFE_API_KEY`.
- `--api-url URL`: override the TypeSafe endpoint.
- `--model NAME`: select the Jev model.
- `--timeout SECONDS`: request timeout.
- `--retries COUNT`: maximum request retries.
- `--metrics-file PATH`: override the JSONL metrics destination.

Metadata commands do not call Jev or write evaluation metrics:

```bash
./tmjev --version
./tmjev --schema
```

Dotenv parsing is intentionally strict. Blank lines, comments, optional `export`, and valid `KEY=value` assignments are accepted. Invalid keys, malformed lines, unmatched outer quotes, and invalid UTF-8 fail with a line-numbered error.

## Metrics 📏

Evaluation invocations append one JSONL record to `~/.local/state/tmjev/metrics.jsonl`. Metrics measure the LLM boundary in characters, not exact tokens:

- `llm_input_chars_avoided`
- `llm_input_chars_from_jev`
- `llm_output_chars_to_jev`
- `duration_ms`
- `max_retries`

The metrics record also carries `metrics_schema_version` and `tmjev_version`. Metrics are kept outside the JSON delivered to the calling agent.

## Safety 🛡️

- In `output` mode, raw logs stay local; only parser facts and a bounded redacted excerpt are sent to Jev.
- In `output` mode, common credential assignments are redacted before external submission.
- Prompt-injection signals are exposed as typed evidence, not executed as instructions.
- Jev's `next_action` is never treated as authorization to run a command.
- Deterministic parsing, exit-status handling, truncation, and policy gates stay in code.

## Agent Integration

The local OpenCode skill is available at `.agents/skills/tmjev/SKILL.md`. It documents when to call `tmjev`, how to preserve exit status, and how to keep the agent-facing response compact.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format .
uv run mypy tell_me_jev.py tests/test_tell_me_jev.py
uv lock --check
```

Use Jev as a small typed judgment layer, not as a replacement for deterministic tooling or human review.
