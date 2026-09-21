**Written on:** 2026-09-21T15:00:00-03:00

# tell_me_jev

A small global CLI managed by `uv` for compact TypeSafe/Jev evaluations during agentic coding.

## Goal

**Updated on:** 2026-09-21T15:05:58-03:00

Reduce the context sent to the main LLM. The `output` command reads raw output, redacts common secret patterns locally, extracts only diagnostic context, and sends typed questions to Jev. By default, the response for the LLM contains only `passed` (when an exit status is available), `kind`, and `next_action`; use `--full` for human inspection.

## Local Installation

**Updated on:** 2026-09-21T15:13:05-03:00

The project uses the official `typesafe-sdk` for Jev calls and `pydantic-cli` for typed subcommands. The installed executable is `/home/filipe/.local/bin/tmjev` and points to this source checkout through `uv run`. The key is read from `/home/filipe/.env` on every call, without relying on the OpenCode service environment.

**Updated on:** 2026-09-21T17:34:32-03:00

The local agent skill is at `.agents/skills/tmjev/SKILL.md`.

Prepare the environment:

```bash
cd /home/filipe/tell_me_jev
uv sync
./tmjev --help
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
  --task "Tell whether the test stack passed; the exit status is authoritative." \
  --tool test-stack \
  --status "$status"
```

`--tool` is only a contextual label sent to Jev; it does not execute the tool. Raw output stays in the temporary file and is not printed by the normal flow. Do not add or remove quiet flags solely because of Jev; choose the command whose output is appropriate for the decision. The reduced response delivered to the LLM has this format:

```json
{"passed":true,"kind":"success","next_action":"stop"}
```

Use `--full` only to investigate a human execution; it restores metadata, usage, severity, and the other answers.

Use `--pretty` only when a person needs to read the response. The default format is compact JSON to preserve tokens. Use `--include-probabilities` only when diagnosing model behavior.

## Savings Measurement

**Updated on:** 2026-09-21T14:49:39-03:00

The CLI always records one JSONL line with metrics at the LLM boundary without adding the counters to the normal response. The default path is `~/.local/state/tmjev/metrics.jsonl`; use `--metrics-file` only to override the destination:

```bash
command 2>&1 | tmjev output \
  --task "diagnose the failure" \
  --tool pytest
```

The file records `llm_input_chars_avoided`, `llm_input_chars_from_jev`, and `llm_output_chars_to_jev` separately. The first is the size of the raw output received by the CLI; the second is the exact size of the JSON emitted by the CLI, including the newline; the third is calculated automatically from the arguments received by the CLI without counting `--metrics-file` itself. The record is appended even when the call fails. In `ask` mode, the CLI does not know the avoided raw input, so this field is `null`.

## `output` Mode Contract

The state sent to Jev includes the task, file metadata, parser facts, and a redacted excerpt. The questions evaluate:

- `kind`: output type.
- `relevant`: relevance to the task.
- `evidence_sufficient`: whether there is enough evidence to choose the next step.
- `next_action`: next operational action.
- `security_signal`: semantic security signal or agent-directed instruction.
- `severity`: operational severity.

The default result delivered to the agent is reduced to `passed`, `kind`, and `next_action`. The `--full` mode adds metadata, model, usage, and the remaining answers for diagnosis.

Exit codes:

- `0`: evaluation completed.
- `2`: invalid input, configuration, or call.

The CLI does not execute the suggested action and does not treat Jev's response as authorization for destructive commands.
