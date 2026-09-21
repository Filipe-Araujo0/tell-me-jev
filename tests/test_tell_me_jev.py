import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from typesafe_sdk import Choice, Noul

from tell_me_jev import (
    agent_summary,
    build_excerpt,
    build_llm_metrics,
    compact_response,
    invocation_chars,
    main,
    normalize_base_url,
    output_questions,
    parse_dotenv,
    prepare_output_state,
    redact_text,
    request_evaluation,
)


def test_parse_dotenv_does_not_execute_shell_syntax(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nexport TYPESAFE_API_KEY='key-value'\nOTHER=value\n",
        encoding="utf-8",
    )

    assert parse_dotenv(env_file) == {"TYPESAFE_API_KEY": "key-value", "OTHER": "value"}


def test_redact_text_hides_common_secret_shapes() -> None:
    text = "API_KEY=sk-example-value email=person@example.com"

    redacted, count = redact_text(text)

    assert count >= 2
    assert "sk-example-value" not in redacted
    assert "person@example.com" not in redacted
    assert "[REDACTED]" in redacted or "[REDACTED_TOKEN]" in redacted


def test_build_excerpt_keeps_diagnostic_context() -> None:
    text = "info start\ninfo setup\nERROR failed request\ntraceback line\ninfo end\n"

    excerpt, hits, truncated = build_excerpt(text, max_chars=1_000, context_lines=1)

    assert hits == 2
    assert "ERROR failed request" in excerpt
    assert "traceback line" in excerpt
    assert truncated is True


def test_prepare_output_state_does_not_send_raw_text_field() -> None:
    state, metadata = prepare_output_state(
        "API_KEY=sk-example-value\nERROR failed\n",
        task="classify the command output",
        tool="pytest",
        container=None,
        status=None,
        max_chars=1_000,
        context_lines=1,
    )

    assert "raw_text" not in state
    assert "sk-example-value" not in state["log_excerpt"]
    assert metadata["redaction_count"] >= 1
    assert state["parser_facts"]["contains_sensitive_pattern"] is True
    assert metadata["raw_chars"] == len("API_KEY=sk-example-value\nERROR failed\n")


def test_compact_response_omits_probability_distribution_by_default() -> None:
    response = {
        "model": "jev-1.13.0",
        "answers": {
            "kind": {
                "type": "choice",
                "choice": "application_bug",
                "confidence": 0.91,
                "probabilities": {"application_bug": 0.91, "environment": 0.09},
            }
        },
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }

    compact = compact_response(response)
    full = compact_response(response, include_probabilities=True)

    assert "probabilities" not in compact["answers"]["kind"]
    assert full["answers"]["kind"]["probabilities"]["application_bug"] == 0.91


def test_agent_summary_keeps_only_verdict_and_next_action() -> None:
    result = {
        "input": {"status": "0"},
        "answers": {
            "kind": {"choice": "success"},
            "next_action": {"choice": "stop"},
            "severity": {"choice": "low"},
            "relevant": {"noul": 1.0},
        },
    }

    assert agent_summary(result) == {
        "passed": True,
        "kind": "success",
        "next_action": "stop",
    }


def test_output_questions_use_official_typesafe_primitives() -> None:
    questions = output_questions()

    assert isinstance(questions["kind"], Choice)
    assert isinstance(questions["relevant"], Noul)
    assert isinstance(questions["evidence_sufficient"], Noul)
    assert isinstance(questions["next_action"], Choice)
    assert isinstance(questions["severity"], Choice)


def test_normalize_base_url_accepts_the_legacy_endpoint_value() -> None:
    assert normalize_base_url("https://api.typesafe.ai/v1/systemone") == (
        "https://api.typesafe.ai"
    )
    assert normalize_base_url("https://api.typesafe.ai/") == ("https://api.typesafe.ai")


def test_request_evaluation_uses_the_official_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse(BaseModel):
        model: str
        answers: dict[str, dict[str, object]]
        usage: dict[str, int]

    class FakeClient:
        received: dict[str, object] = {}

        def __init__(self, **kwargs: object) -> None:
            self.received = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def system_one(self, **kwargs: object) -> FakeResponse:
            self.received["call"] = kwargs
            return FakeResponse(model="jev-test", answers={}, usage={})

    fake_client = FakeClient()

    def fake_factory(**kwargs: object) -> FakeClient:
        fake_client.received.update(kwargs)
        return fake_client

    monkeypatch.setattr("tell_me_jev.TypeSafeClient", fake_factory)

    result = request_evaluation(
        {"message": "failure"},
        output_questions(),
        "test-key",
        "https://api.typesafe.ai/v1/systemone",
        "jev-latest",
        10.0,
        1,
    )

    assert result == {"model": "jev-test", "answers": {}, "usage": {}}
    assert fake_client.received["base_url"] == "https://api.typesafe.ai"
    assert fake_client.received["call"] == {
        "state": {"message": "failure"},
        "questions": output_questions(),
    }


def test_build_llm_metrics_separates_input_and_output_character_counts() -> None:
    result = {"mode": "output", "input": {"raw_chars": 4_200}}
    rendered_output = '{"mode":"output","answers":{}}\n'

    metrics = build_llm_metrics(
        "output",
        result,
        rendered_output,
        invocation_chars_to_jev=120,
    )

    assert metrics["mode"] == "output"
    assert metrics["success"] is True
    assert metrics["llm_output_chars_to_jev"] == 120
    assert metrics["llm_input_chars_avoided"] == 4_200
    assert metrics["llm_input_chars_from_jev"] == len(rendered_output)
    assert metrics["llm_input_chars_saved"] == 4_200 - len(rendered_output)


def test_build_llm_metrics_does_not_claim_avoided_raw_for_ask_mode() -> None:
    rendered_output = '{"model":"jev-1.13.0"}\n'

    metrics = build_llm_metrics(
        "ask",
        {"model": "jev-1.13.0"},
        rendered_output,
        invocation_chars_to_jev=18,
    )

    assert metrics["success"] is True
    assert metrics["llm_input_chars_avoided"] is None
    assert metrics["llm_input_chars_from_jev"] == len(rendered_output)


def test_invocation_chars_ignores_metrics_plumbing() -> None:
    argv = [
        "output",
        "--task",
        "diagnose",
        "--metrics-file",
        "/tmp/metrics.json",
        "--tool",
        "pytest",
    ]

    measured = invocation_chars(argv)

    assert measured == invocation_chars(
        ["output", "--task", "diagnose", "--tool", "pytest"]
    )


def test_output_command_writes_boundary_metrics_without_changing_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_file = tmp_path / "output.log"
    raw_file.write_text("ERROR failed\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("TYPESAFE_API_KEY=test-key\n", encoding="utf-8")
    metrics_file = tmp_path / "metrics.json"

    monkeypatch.setattr(
        "tell_me_jev.request_evaluation",
        lambda *_args, **_kwargs: {
            "model": "jev-test",
            "answers": {},
            "usage": {},
        },
    )

    assert (
        main(
            [
                "output",
                "--file",
                str(raw_file),
                "--task",
                "diagnose",
                "--env-file",
                str(env_file),
                "--metrics-file",
                str(metrics_file),
            ]
        )
        == 0
    )

    stdout = capsys.readouterr().out
    metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert metrics["llm_output_chars_to_jev"] == invocation_chars(
        [
            "output",
            "--file",
            str(raw_file),
            "--task",
            "diagnose",
            "--env-file",
            str(env_file),
        ]
    )
    assert metrics["llm_input_chars_avoided"] == len("ERROR failed\n")
    assert metrics["llm_input_chars_from_jev"] == len(stdout)


def test_ask_command_metrics_have_no_avoided_raw_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text('{"task":"diagnose"}\n', encoding="utf-8")
    questions_file = tmp_path / "questions.json"
    questions_file.write_text('{"kind":{"type":"choice"}}\n', encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("TYPESAFE_API_KEY=test-key\n", encoding="utf-8")
    metrics_file = tmp_path / "metrics.json"

    monkeypatch.setattr(
        "tell_me_jev.request_evaluation",
        lambda *_args, **_kwargs: {
            "model": "jev-test",
            "answers": {},
            "usage": {},
        },
    )

    assert (
        main(
            [
                "ask",
                "--state-file",
                str(state_file),
                "--questions-file",
                str(questions_file),
                "--env-file",
                str(env_file),
                "--metrics-file",
                str(metrics_file),
            ]
        )
        == 0
    )

    stdout = capsys.readouterr().out
    metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert metrics["mode"] == "ask"
    assert metrics["llm_input_chars_avoided"] is None
    assert metrics["llm_input_chars_from_jev"] == len(stdout)


def test_failed_command_also_appends_metrics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    metrics_file = tmp_path / "metrics.jsonl"

    assert (
        main(
            [
                "output",
                "--file",
                str(tmp_path / "missing.log"),
                "--task",
                "diagnose",
                "--metrics-file",
                str(metrics_file),
            ]
        )
        == 2
    )

    stdout = capsys.readouterr().out
    metrics = json.loads(metrics_file.read_text(encoding="utf-8").splitlines()[0])
    assert metrics["success"] is False
    assert metrics["error"] == "input file not found: " + str(tmp_path / "missing.log")
    assert metrics["llm_input_chars_from_jev"] == len(stdout)
