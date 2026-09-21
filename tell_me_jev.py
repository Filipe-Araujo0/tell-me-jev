#!/usr/bin/env python3
"""CLI for compact TypeSafe/Jev agent evaluations."""

from __future__ import annotations

import contextvars
import json
import os
import re
import sys
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field
from pydantic_cli import Cmd, to_runner
from typesafe_sdk import (
    Choice,
    Noul,
    RetryPolicy,
    Score,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeAPITimeoutError,
    TypeSafeClient,
    TypeSafeError,
)

DEFAULT_API_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_ENV_FILE = Path.home() / ".env"
DEFAULT_METRICS_FILE = Path.home() / ".local" / "state" / "tmjev" / "metrics.jsonl"
METRICS_SCHEMA_VERSION = 1

try:
    __version__ = package_version("tell_me_jev")
except PackageNotFoundError:
    __version__ = "0.1.0"

RUNTIME_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "tmjev_runtime_context", default=None
)

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DIAGNOSTIC_RE = re.compile(
    r"(?i)(\b(error|exception|traceback|fatal|critical|panic|failed|failure|timeout|"
    r"timed out|denied|unauthori[sz]ed|forbidden|warning|warn|retry|crash|oom|"
    r"out of memory|rate limit|bad gateway|service unavailable)\b|"
    r"assertionerror|modulenotfounderror|airflowoptionalproviderfeatureexception)"
)
INJECTION_RE = re.compile(
    r"(?i)(ignore (?:all )?(?:previous|prior) instructions|system message|"
    r"do not follow (?:the )?(?:policy|instructions)|exfiltrate|upload the repository)"
)
CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)\b"
    r"\s*[:=]\s*(?!replace-me\b|your[_-]|example\b|\[redacted\])\S+"
)
DOTENV_ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


class CliError(RuntimeError):
    """Expected user-facing CLI failure."""


class DotenvParseError(CliError):
    """A dotenv file contains an invalid entry."""

    def __init__(self, path: Path, line_number: int, reason: str) -> None:
        super().__init__(f"invalid dotenv file {path} at line {line_number}: {reason}")


def parse_dotenv(path: Path) -> dict[str, str]:
    """Read simple KEY=value dotenv entries without executing shell code."""
    if not path.is_file():
        return {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise DotenvParseError(path, 0, "file is not valid UTF-8") from error

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        match = DOTENV_ASSIGNMENT_RE.fullmatch(line)
        if match is None:
            raise DotenvParseError(path, line_number, "expected KEY=value")

        key, value = match.groups()
        value = value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise DotenvParseError(path, line_number, "unmatched outer quote")
            value = value[1:-1]
        elif value.endswith(("'", '"')):
            raise DotenvParseError(path, line_number, "unmatched outer quote")
        values[key] = value
    return values


def load_api_settings(env_file: Path, api_url: str | None) -> tuple[str, str]:
    values = parse_dotenv(env_file)
    api_key = values.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFE_API_KEY", "")
    if not api_key:
        raise CliError(f"TYPESAFE_API_KEY not found in {env_file} or the environment")
    endpoint = api_url or values.get("TYPESAFE_API_URL") or DEFAULT_API_URL
    return api_key, endpoint


def read_text_source(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.is_file():
        raise CliError(f"input file not found: {path}")
    return path.read_text(encoding="utf-8", errors="replace")


def load_json_source(
    file_source: str | None, inline_source: str | None, label: str
) -> Any:
    if bool(file_source) == bool(inline_source):
        raise CliError(f"provide exactly one of --{label}-file or --{label}-json")
    raw = read_text_source(file_source) if file_source else inline_source or ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise CliError(f"invalid JSON for {label}: {error.msg}") from error


def redact_text(text: str) -> tuple[str, int]:
    patterns: list[tuple[re.Pattern[str], str]] = [
        (
            re.compile(
                r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|"
                r"secret|authorization|bearer)\b\s*(?:[:=]\s*|\s+))([^\s,;]+)"
            ),
            r"\1[REDACTED]",
        ),
        (
            re.compile(
                r"(?i)([?&](?:api[_-]?key|token|access_token|signature|sig)=)([^&\s]+)"
            ),
            r"\1[REDACTED]",
        ),
        (
            re.compile(r"\b(?:sk|pk|ghp|github_pat)-[A-Za-z0-9_-]+\b"),
            "[REDACTED_TOKEN]",
        ),
        (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
        (
            re.compile(
                r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
            ),
            "[REDACTED_JWT]",
        ),
        (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    ]
    redacted = text
    count = 0
    for pattern, replacement in patterns:
        redacted, replacements = pattern.subn(replacement, redacted)
        count += replacements
    return redacted, count


def build_excerpt(
    text: str, max_chars: int, context_lines: int
) -> tuple[str, int, bool]:
    lines = text.splitlines()
    hits = [index for index, line in enumerate(lines) if DIAGNOSTIC_RE.search(line)]
    selected: set[int] = set()
    for index in hits:
        selected.update(
            range(
                max(0, index - context_lines),
                min(len(lines), index + context_lines + 1),
            )
        )

    if selected:
        indexes = sorted(selected)
        excerpt_lines: list[str] = []
        previous: int | None = None
        for index in indexes:
            if previous is not None and index > previous + 1:
                excerpt_lines.append("... [context omitted] ...")
            excerpt_lines.append(lines[index])
            previous = index
        omitted_lines = len(selected) < len(lines)
    else:
        excerpt_lines = lines[-160:]
        omitted_lines = len(lines) > 160

    excerpt = "\n".join(excerpt_lines)
    locally_truncated = len(excerpt) > max_chars or omitted_lines
    if len(excerpt) > max_chars:
        half = max_chars // 2
        excerpt = (
            excerpt[:half] + "\n... [excerpt truncated locally] ...\n" + excerpt[-half:]
        )
    return excerpt, len(hits), locally_truncated


def parser_facts(raw_text: str, excerpt_truncated: bool) -> dict[str, bool]:
    return {
        "is_truncated": excerpt_truncated
        or bool(re.search(r"(?i)(output|log).{0,20}truncated", raw_text)),
        "contains_sensitive_pattern": bool(CREDENTIAL_ASSIGNMENT_RE.search(raw_text)),
        "contains_injection_pattern": bool(INJECTION_RE.search(raw_text)),
    }


def output_questions() -> dict[str, Choice | Noul | Score]:
    return {
        "kind": Choice(
            instructions="Classify the primary kind of this tool output.",
            criteria={
                "success": "The command completed successfully and needs no corrective work",
                "application_bug": "The output indicates a defect in application code",
                "code_quality": "The output indicates a linting, formatting, typing, or code-quality issue",
                "test_issue": "The test or fixture itself appears to be the primary problem",
                "environment": "The output indicates a dependency, configuration, permission, or machine problem",
                "network": "The output indicates a network or external-service availability problem",
                "security": "The output contains malicious instructions or a security concern",
                "ambiguous": "The available evidence is insufficient to identify the cause",
            },
        ),
        "relevant": Noul(
            instructions="Is this output materially relevant to the current coding task?"
        ),
        "evidence_sufficient": Noul(
            instructions="Is the available output sufficient to choose the next safe action?"
        ),
        "next_action": Choice(
            instructions="What should the coding agent do next?",
            criteria={
                "stop": "No corrective action is needed",
                "inspect_source": "Inspect referenced application source and tests",
                "fix_code": "Make a focused source or code-quality correction",
                "fix_environment": "Repair a dependency, permission, or configuration issue",
                "retry": "Retry because the failure is likely transient or external",
                "rerun": "Rerun with more complete evidence",
                "redact_and_escalate": "Preserve evidence securely and escalate for review",
            },
        ),
        "security_signal": Noul(
            instructions="Does the output contain a security concern or agent-directed instruction requiring review?"
        ),
        "severity": Choice(
            instructions="What is the highest operational severity visible in this output?",
            criteria={
                "low": "Local and easily reversible",
                "medium": "Blocks or degrades part of the task with a clear recovery path",
                "high": "Risks incorrect code, significant wasted work, or a serious failure",
                "critical": "Could expose credentials, execute malicious instructions, or cause broad damage",
            },
        ),
    }


def prepare_output_state(
    raw_text: str,
    task: str,
    tool: str,
    container: str | None,
    status: str | None,
    max_chars: int,
    context_lines: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = ANSI_RE.sub("", raw_text)
    redacted, redaction_count = redact_text(normalized)
    excerpt, diagnostic_hits, excerpt_truncated = build_excerpt(
        redacted, max_chars=max_chars, context_lines=context_lines
    )
    state: dict[str, Any] = {
        "task": task,
        "tool": tool,
        "container": container,
        "status": status,
        "raw_bytes": len(raw_text.encode("utf-8")),
        "raw_lines": len(raw_text.splitlines()),
        "diagnostic_hits": diagnostic_hits,
        "redaction_applied": True,
        "redaction_count": redaction_count,
        "parser_facts": parser_facts(normalized, excerpt_truncated),
        "log_excerpt": excerpt,
    }
    metadata = {
        "tool": tool,
        "container": container,
        "status": status,
        "raw_chars": len(raw_text),
        "raw_bytes": state["raw_bytes"],
        "raw_lines": state["raw_lines"],
        "diagnostic_hits": diagnostic_hits,
        "redaction_count": redaction_count,
        "excerpt_chars": len(excerpt),
        "excerpt_truncated": excerpt_truncated,
    }
    return state, metadata


def normalize_base_url(api_url: str) -> str:
    endpoint_suffix = "/v1/systemone"
    normalized = api_url.rstrip("/")
    if normalized.endswith(endpoint_suffix):
        return normalized[: -len(endpoint_suffix)]
    return normalized


def request_evaluation(
    state: Any,
    questions: dict[str, Any],
    api_key: str,
    api_url: str,
    model: str,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    try:
        with TypeSafeClient(
            api_key=api_key,
            base_url=normalize_base_url(api_url),
            model=model,
            retry=RetryPolicy(max_retries=retries, timeout=None),
            timeout=timeout,
        ) as client:
            response = client.system_one(state=state, questions=questions)
    except TypeSafeAPITimeoutError as error:
        raise CliError("TypeSafe request timed out") from error
    except TypeSafeAPIConnectionError as error:
        raise CliError("TypeSafe connection failed") from error
    except TypeSafeAPIError as error:
        raise CliError(f"TypeSafe HTTP {error.status}") from error
    except TypeSafeError as error:
        raise CliError("TypeSafe request failed") from error

    if not isinstance(response, BaseModel):
        raise CliError("TypeSafe returned an unsupported response")
    return response.model_dump(mode="json")


def compact_response(
    response: dict[str, Any], include_probabilities: bool = False
) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for key, answer in response.get("answers", {}).items():
        compact_answer = {"type": answer.get("type")}
        for field in ("choice", "noul", "score", "confidence"):
            if field in answer:
                compact_answer[field] = answer[field]
        if include_probabilities and "probabilities" in answer:
            compact_answer["probabilities"] = answer["probabilities"]
        answers[key] = compact_answer
    return {
        "model": response.get("model"),
        "answers": answers,
        "usage": response.get("usage", {}),
    }


def status_passed(status: Any) -> bool | None:
    if status is None:
        return None
    normalized = str(status).strip().lower()
    if normalized in {"0", "ok", "pass", "passed", "success"}:
        return True
    if normalized in {"fail", "failed", "failure", "error"}:
        return False
    if normalized.isdigit():
        return int(normalized) == 0
    return None


def agent_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Return only the decisions the calling agent needs for the next step."""
    answers = result.get("answers", {})
    summary: dict[str, Any] = {}
    passed = status_passed(result.get("input", {}).get("status"))
    if passed is not None:
        summary["passed"] = passed
    for answer_key in ("kind", "next_action"):
        answer = answers.get(answer_key, {})
        if "choice" in answer:
            summary[answer_key] = answer["choice"]
    return summary


def serialize_json(value: Any, pretty: bool) -> str:
    if pretty:
        return json.dumps(value, ensure_ascii=False, indent=2)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def print_json(value: Any, pretty: bool) -> None:
    print(serialize_json(value, pretty))


def invocation_chars(argv: list[str]) -> int:
    """Count the CLI argument payload without counting metrics plumbing."""
    measured_argv: list[str] = []
    skip_next = False
    for argument in argv:
        if skip_next:
            skip_next = False
            continue
        if argument == "--metrics-file":
            skip_next = True
            continue
        if argument.startswith("--metrics-file="):
            continue
        measured_argv.append(argument)
    return len(serialize_json(measured_argv, pretty=False))


def build_llm_metrics(
    mode: str,
    result: dict[str, Any],
    rendered_output: str,
    invocation_chars_to_jev: int,
    raw_chars: int | None = None,
    error: str | None = None,
    *,
    duration_ms: float | None = None,
    max_retries: int | None = None,
) -> dict[str, Any]:
    input_metadata = result.get("input", {})
    avoided_chars = raw_chars
    if avoided_chars is None and mode == "output":
        avoided_chars = input_metadata.get("raw_chars")
    metrics: dict[str, Any] = {
        "metrics_schema_version": METRICS_SCHEMA_VERSION,
        "tmjev_version": __version__,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "success": error is None,
        "duration_ms": duration_ms,
        "max_retries": max_retries,
        "llm_output_chars_to_jev": invocation_chars_to_jev,
        "llm_input_chars_avoided": avoided_chars,
        "llm_input_chars_from_jev": len(rendered_output),
    }
    if isinstance(avoided_chars, int):
        metrics["llm_input_chars_saved"] = (
            avoided_chars - metrics["llm_input_chars_from_jev"]
        )
    if error is not None:
        metrics["error"] = error
    return metrics


def write_metrics(path: Path, metrics: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(metrics, ensure_ascii=False) + "\n")
    except OSError as error:
        print(f"tmjev: could not write metrics file: {path}: {error}", file=sys.stderr)


def cli_field(default: Any = ..., *flags: str, **constraints: Any) -> Any:
    return Field(
        default,
        json_schema_extra=cast(Any, {"cli": flags}),
        **constraints,
    )


class CommandBase(Cmd):
    env_file: str = cli_field(str(DEFAULT_ENV_FILE), "--env-file")
    api_url: str | None = cli_field(None, "--api-url")
    model: str = cli_field(DEFAULT_MODEL, "--model")
    timeout: float = cli_field(30.0, "--timeout", gt=0)
    retries: int = cli_field(2, "--retries", ge=0)
    pretty: bool = cli_field(False, "--pretty")
    include_probabilities: bool = cli_field(False, "--include-probabilities")
    metrics_file: str = cli_field(str(DEFAULT_METRICS_FILE), "--metrics-file")

    def prepare_runtime(self) -> dict[str, Any]:
        context = RUNTIME_CONTEXT.get()
        assert context is not None
        context["metrics_file"] = Path(self.metrics_file)
        context["pretty"] = self.pretty
        context["max_retries"] = self.retries
        return context


class AskCommand(CommandBase):
    state_file: str | None = cli_field(None, "--state-file")
    state_json: str | None = cli_field(None, "--state-json")
    questions_file: str | None = cli_field(None, "--questions-file")
    questions_json: str | None = cli_field(None, "--questions-json")

    def run(self) -> None:
        self.prepare_runtime()["result"] = run_ask(self)


class OutputCommand(CommandBase):
    file: str = cli_field("-", "--file")
    task: str = cli_field(..., "--task", min_length=1)
    tool: str = cli_field("tool", "--tool")
    container: str | None = cli_field(None, "--container")
    status: str | None = cli_field(None, "--status")
    max_chars: int = cli_field(24_000, "--max-chars", gt=0)
    context_lines: int = cli_field(2, "--context-lines", ge=0)
    include_excerpt: bool = cli_field(False, "--include-excerpt")
    full: bool = cli_field(False, "--full")

    def run(self) -> None:
        context = self.prepare_runtime()
        context["result"] = run_output(self, context)


BOOLEAN_FLAGS = frozenset(
    {"--pretty", "--include-probabilities", "--include-excerpt", "--full"}
)


def normalize_cli_args(argv: list[str]) -> list[str]:
    """Keep bare boolean flags compatible with the old argparse interface."""
    normalized: list[str] = []
    for index, argument in enumerate(argv):
        normalized.append(argument)
        if argument in BOOLEAN_FLAGS and (
            index + 1 == len(argv) or argv[index + 1].startswith("-")
        ):
            normalized.append("true")
    return normalized


def cli_exception_handler(error: BaseException) -> int:
    context = RUNTIME_CONTEXT.get()
    if context is not None:
        context["error"] = str(error)
    return 2


def cli_epilogue_handler(_exit_code: int, run_time_sec: float) -> None:
    context = RUNTIME_CONTEXT.get()
    if context is not None:
        context["duration_ms"] = round(run_time_sec * 1000, 3)


CLI_RUNNER = to_runner(
    {"ask": AskCommand, "output": OutputCommand},
    description="Compact TypeSafe/Jev evaluations for coding agents",
    version=__version__,
    exception_handler=cli_exception_handler,
    epilogue_handler=cli_epilogue_handler,
)


def build_cli_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": "tmjev",
        "version": __version__,
        "commands": {
            "ask": AskCommand.model_json_schema(),
            "output": OutputCommand.model_json_schema(),
        },
    }


def run_ask(args: AskCommand) -> dict[str, Any]:
    state = load_json_source(args.state_file, args.state_json, "state")
    questions = load_json_source(args.questions_file, args.questions_json, "questions")
    if not isinstance(questions, dict):
        raise CliError("questions JSON must be an object")
    api_key, api_url = load_api_settings(Path(args.env_file), args.api_url)
    response = request_evaluation(
        state, questions, api_key, api_url, args.model, args.timeout, args.retries
    )
    return compact_response(response, include_probabilities=args.include_probabilities)


def run_output(
    args: OutputCommand, metric_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    raw_text = read_text_source(args.file)
    if metric_context is not None:
        metric_context["raw_chars"] = len(raw_text)
    state, metadata = prepare_output_state(
        raw_text,
        task=args.task,
        tool=args.tool,
        container=args.container,
        status=args.status,
        max_chars=args.max_chars,
        context_lines=args.context_lines,
    )
    api_key, api_url = load_api_settings(Path(args.env_file), args.api_url)
    response = request_evaluation(
        state,
        output_questions(),
        api_key,
        api_url,
        args.model,
        args.timeout,
        args.retries,
    )
    result: dict[str, Any] = {
        "mode": "output",
        "input": metadata,
        **compact_response(response, include_probabilities=args.include_probabilities),
    }
    if args.include_excerpt:
        result["redacted_excerpt"] = state["log_excerpt"]
    return result if args.full else agent_summary(result)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv == ["--schema"]:
        print_json(build_cli_schema(), pretty=False)
        return 0

    context: dict[str, Any] = {
        "raw_argv": raw_argv,
        "raw_chars": None,
        "result": None,
        "error": None,
        "metrics_file": DEFAULT_METRICS_FILE,
        "duration_ms": None,
        "max_retries": None,
    }
    token = RUNTIME_CONTEXT.set(context)
    try:
        status = CLI_RUNNER(normalize_cli_args(raw_argv))
        result = context["result"]
        error = context["error"]
        if result is None and error is None:
            return status
        mode = raw_argv[0] if raw_argv else "unknown"
        pretty = bool(context.get("pretty", False))
        if result is not None:
            rendered_output = serialize_json(result, pretty) + "\n"
        else:
            rendered_output = serialize_json({"error": error}, pretty=False) + "\n"
        write_metrics(
            context["metrics_file"],
            build_llm_metrics(
                mode,
                result or {},
                rendered_output,
                invocation_chars(raw_argv),
                raw_chars=context["raw_chars"],
                error=error,
                duration_ms=context["duration_ms"],
                max_retries=context["max_retries"],
            ),
        )
        sys.stdout.write(rendered_output)
        return status
    finally:
        RUNTIME_CONTEXT.reset(token)


if __name__ == "__main__":
    raise SystemExit(main())
