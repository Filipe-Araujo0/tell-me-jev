**Written on:** 2026-09-21T15:00:00-03:00

# tell_me_jev

A small global CLI managed by `uv` for compact TypeSafe/Jev evaluations during agentic coding.

## Goal

**Updated on:** 2026-09-21T15:05:58-03:00

**Updated on:** 2026-09-22T09:09:19-03:00

**Updated on:** 2026-09-22T09:40:55-03:00

Reduce the context sent to the main LLM. The `output` command reads raw output, redacts common secret patterns locally, extracts only diagnostic context, and sends typed questions to Jev. By default, the response for the LLM uses the projected assessment schema with all answers and probabilities; `command_succeeded` is added when `--status` is recognizable.

## Local Installation

**Updated on:** 2026-09-21T15:13:05-03:00

The project uses the official `typesafe-sdk` for Jev calls and `pydantic-cli` for typed subcommands. The key is read from `~/.env` on every call, without relying on the OpenCode service environment.

**Updated on:** 2026-09-21T17:34:32-03:00

**Updated on:** 2026-09-21T18:26:42-03:00

The local agent skill is at `.agents/skills/tmjev/SKILL.md`. The tracked `./tmjev`
wrapper resolves its own checkout path, including when invoked through a symlink.
The packaged console script is also available as `tmjev` after installation. The
key is read from `~/.env` on every call, without relying on the OpenCode service
environment.

Prepare the environment:

```bash
cd /path/to/tell_me_jev
uv sync
./tmjev --help
```

Metadata commands do not call Jev or append LLM-boundary metrics:

```bash
tmjev --version
tmjev --schema
```

## Usage

Evaluate tool output:

```bash
command 2>&1 | tmjev output \
  --task "fix the checkout calculation" \
  --tool pytest
```

Evaluate an already captured file:

```bash
tmjev output \
  --file /tmp/tool-output.log \
  --task "diagnose the worker failure" \
  --tool docker-logs \
  --container airflow_worker
```

Evaluate custom questions:

```bash
tmjev ask \
  --state-file state.json \
  --questions-file questions.json
```

## Test Stack For The LLM

**Updated on:** 2026-09-21T15:22:55-03:00

First run the tool normally and check whether its output is already small,
deterministic, and actionable. Do not call Jev when the result is already
sufficient. A command such as `pytest -q` is an example of a potentially poor
Jev use: its concise result may not need semantic triage.

Call Jev only when the output is large, noisy, ambiguous, or requires a
semantic decision about the next action. When needed, capture the complete
output of the canonical command and preserve its exit code:

```bash
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

<canonical-command> >"$tmp" 2>&1
status=$?

tmjev output \
  --file "$tmp" \
  --task "Tell whether the test stack succeeded; the exit status is authoritative." \
  --tool test-stack \
  --status "$status"
```

`--tool` is only a contextual label sent to Jev; it does not execute the tool. Raw output stays in the temporary file and is not printed by the normal flow. Do not add or remove quiet flags solely because of Jev; choose the command whose output is appropriate for the decision. The projected response delivered to the LLM has this shape:

```json
{"schema_version":1,"model":"jev-latest","answers":{}}
```

The normal `output` response and `--full` both expose the projected assessment. Each answer has only `value` and `probabilities`; Jev `type`, `confidence`, `legend`, and token `usage` are not exposed. `--full` additionally exposes command metadata and the redacted excerpt when requested. `--include-probabilities` remains accepted for compatibility but no longer changes the response because probabilities are always included.

Use `--pretty` only when a person needs to read the response. The default format is compact JSON to preserve tokens. `--include-probabilities` remains accepted for compatibility; probabilities are always included in the projected full response.

## Savings Measurement

**Updated on:** 2026-09-21T14:49:39-03:00

**Updated on:** 2026-09-21T18:26:42-03:00

The CLI always records one JSONL line with metrics at the LLM boundary without adding the counters to the normal response. Each record has `metrics_schema_version`, `tmjev_version`, `duration_ms`, and `max_retries` in addition to the character counters. The default path is `~/.local/state/tmjev/metrics.jsonl`; use `--metrics-file` only to override the destination:

```bash
command 2>&1 | tmjev output \
  --task "diagnose the failure" \
  --tool pytest
```

The file records `llm_input_chars_avoided`, `llm_input_chars_from_jev`, and `llm_output_chars_to_jev` separately. The first is the size of the raw output received by the CLI; the second is the exact size of the JSON emitted by the CLI, including the newline; the third is calculated automatically from the arguments received by the CLI without counting `--metrics-file` itself. The record is appended even when the call fails. In `ask` mode, the CLI does not know the avoided raw input, so this field is `null`.

## `output` Mode Contract

**Updated on:** 2026-09-21T18:26:42-03:00

**Updated on:** 2026-09-22T09:09:19-03:00

**Updated on:** 2026-09-22T09:33:42-03:00

The state sent to Jev includes the task, file metadata, parser facts, and a redacted excerpt. The questions evaluate:

- `kind`: output type.
- `relevant`: relevance to the task.
- `evidence_sufficient`: whether there is enough evidence to choose the next step.
- `next_action`: next operational action.
- `security_signal`: semantic security signal or agent-directed instruction.
- `severity`: operational severity.

The default result delivered to the agent uses the projected assessment with `schema_version`, `model`, and all six answers. `command_succeeded` is derived from the evaluated command's recognizable `--status`; it does not describe whether `tmjev` or Jev succeeded. The `--full` mode additionally exposes command metadata; use `--include-excerpt` to include the redacted excerpt.

The `answers` portion of the response uses one stable shape for every question:

```json
{
  "kind": {
    "value": "application_bug",
    "probabilities": {
      "application_bug": 0.91,
      "environment": 0.09
    }
  },
  "relevant": {
    "value": true,
    "probabilities": {
      "true": 0.98,
      "false": 0.02
    }
  },
  "next_action": {
    "value": "fix_code",
    "probabilities": {
      "fix_code": 0.84,
      "inspect_source": 0.16
    }
  }
}
```

The complete set of answer names is `kind`, `relevant`, `evidence_sufficient`, `next_action`, `security_signal`, and `severity`. For `noul` answers, `value` is the boolean at the `0.5` threshold and the probability distribution preserves the original uncertainty.

Exit codes:

- `0`: evaluation completed.
- `2`: invalid input, configuration, or call.

Dotenv files accept blank lines, comments, optional `export`, and valid `KEY=value` assignments. Malformed lines, invalid keys, unmatched outer quotes, and invalid UTF-8 fail with a line-numbered error and exit code `2`.

The CLI does not execute the suggested action and does not treat Jev's response as authorization for destructive commands.
