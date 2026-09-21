**Written on:** 2026-09-21T15:05:00-03:00

# Continuity Prompt: tmjev For Agentic Coding

Use this context in fresh sessions before proposing Jev/TypeSafe-related changes.

## Goal

Use Jev exclusively as an inexpensive semantic judgment layer to reduce the main LLM's token usage during agentic coding. Jev does not write code, freely summarize logs, or authorize commands.

## Installation And Invocation

**Updated on:** 2026-09-21T17:34:32-03:00

- The TypeSafe skill was installed for OpenCode at `~/.agents/skills/typesafe-ai`.
- The API key is in `/home/filipe/.env` under `TYPESAFE_API_KEY`.
- Never display, record, copy, or put the key in prompts, responses, Git, or memory.
- The installed global CLI is `tmjev`, with compact JSON output by default.
- In `output` mode, the normal output for the LLM contains only `passed` (when a status is supplied), `kind`, and `next_action`; use `--full` only for human diagnosis.
- The project is managed with `uv`, uses the official `typesafe-sdk` for Jev, and uses `pydantic-cli` for typed subcommands.
- The environment must be synchronized with `uv sync`; the global wrapper runs through the uv project, not the system Python.
- The local CLI usage skill is at `.agents/skills/tmjev/SKILL.md`.
- `tmjev output` must receive a file or `stdin`, redact common secret patterns locally, extract a diagnostic excerpt, and send only typed questions to Jev.
- `tmjev ask` accepts `state` and `questions` from JSON files for custom evaluations.
- Use `--pretty` only for human reading; agents should use compact JSON.

## Recommended Output Flow

**Updated on:** 2026-09-21T15:22:55-03:00

1. Capture raw output in a temporary file when logs are large.
2. Parse exit codes, truncation, paths, counts, and secret scanning deterministically.
3. Redact the content before any external submission.
4. Send Jev only metadata, parser facts, and a redacted diagnostic excerpt.
5. Ask small, typed questions:
   - `kind`: success, application bug, quality, environment, network, security, or ambiguous.
   - `relevant`: relevance to the current task.
   - `evidence_sufficient`: whether there is enough evidence to choose the next step.
   - `next_action`: stop, inspect, fix, fix the environment, retry, collect more evidence, or escalate.
   - `security_signal`: semantic security signal or agent-directed instruction.
   - `severity`: low, medium, high, or critical.
6. The code combines the answers and keeps raw output locally when necessary.
7. Never use Jev as the sole detector for secrets, prompt injection, or authorization for a destructive command.

To evaluate any tool, first check whether its output is already small, deterministic, and actionable. A command such as `pytest -q` may be an unnecessary use of Jev. Capture the complete output and call `tmjev output` with `--status` only when there is noise, ambiguity, volume, or a need for semantic diagnosis. `--tool` is only a contextual label. The normal output for the LLM must be the JSON summary with `passed`, `kind`, and `next_action`; use `--full` only for human diagnosis.

## Main LLM Savings Measurement

**Updated on:** 2026-09-21T15:05:58-03:00

- Measure characters at the main LLM boundary; do not use Jev's `usage.input_tokens` as the LLM savings measure.
- Compare `llm_input_chars_avoided` (raw input that would have gone directly to the LLM) with `llm_input_chars_from_jev` (JSON actually received from the CLI output).
- Measure `llm_output_chars_to_jev` separately. It is calculated automatically from the arguments received by the CLI; in `output` mode, questions and choices created internally by the CLI are not included in this count.
- The CLI automatically records these metrics outside the JSON delivered to the LLM at `~/.local/state/tmjev/metrics.jsonl`; `--metrics-file` only overrides the destination, preventing instrumentation from increasing the measured input.
- In `ask` mode, `state` and `questions` written by the LLM are part of `llm_output_chars_to_jev`; do not claim avoided raw input when the CLI did not receive that content directly.
- Apply input and output prices separately afterward; the primary values are characters, not exact tokens.

## Observed Results

- Tool-output triage achieved `93.3%` accuracy in the first batch.
- The output gate reached `100%` when truncation, secrets, injection, and relevance were provided to Jev as structured facts.
- Generic tool routing was less reliable; generic ambiguity questions produced false positives.
- Jev works best when `state` includes the real context. Do not use artificial values such as `"available"` in place of the required content.
- `Choice` and explicit rules are preferable for operational decisions. `Score` must not authorize actions by itself.
- Security should use composition: deterministic parser/scanner, Jev judgment, and policy in code.

## Previously Analyzed Docker Incident

On 2026-09-21, that day's logs were evaluated sequentially:

- `impostos-guia-airflow_flower-1` and `impostos-guia-airflow_worker-1` were in a restart loop, both with exit code `1`.
- The logs repeated `ModuleNotFoundError: No module named 'airflow_runtime_settings'` and an optional-feature incompatibility requiring Airflow `>= 2.8.0`.
- `ai-memory` was running, but the embeddings provider returned `401` and warned that `AI_MEMORY_AUTH_TOKEN` was missing; exposure depends on publishing the port.
- Redis warned that `vm.overcommit_memory` is not enabled.
- PostgreSQL went through a fast shutdown and automatic recovery, but was healthy afterward.
- The temporary log files were removed after analysis.

## Operational Instruction

Before using Jev for a new task, ask: which small, typed decision will reduce context or fragile parsing? Keep exact facts, execution, and policy in code; deliver only the compact result and necessary evidence to the main LLM.
