import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from typesafe_sdk import Choice, Noul

from tell_me_jev import (
    DotenvParseError,
    __version__,
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


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("BROKEN\n", "expected KEY=value"),
        ("1INVALID=value\n", "expected KEY=value"),
        ("KEY='unclosed\n", "unmatched outer quote"),
    ],
)
def test_parse_dotenv_rejects_malformed_entries(
    tmp_path: Path, content: str, reason: str
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")

    with pytest.raises(DotenvParseError, match=rf"line 1: {reason}"):
        parse_dotenv(env_file)


def test_version_command_is_metadata_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0

    captured = capsys.readouterr()
    assert captured.out == f"{__version__}\n"
    assert captured.err == ""


def test_schema_command_exposes_the_versioned_cli_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--schema"]) == 0

    schema = json.loads(capsys.readouterr().out)
    assert schema["schema_version"] == 1
    assert schema["name"] == "tmjev"
    assert schema["version"] == __version__
    assert set(schema["commands"]) == {"ask", "output"}
    assert "task" in schema["commands"]["output"]["required"]
    assert "TYPESAFE_API_KEY" not in json.dumps(schema)


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


def test_compact_response_projects_all_answer_types_without_usage_metadata() -> None:
    response = {
        "model": "jev-1.13.0",
        "answers": {
            "kind": {
                "type": "choice",
                "choice": "application_bug",
                "confidence": 0.91,
                "probabilities": {"application_bug": 0.91, "environment": 0.09},
            },
            "relevant": {"type": "noul", "noul": 0.8},
            "severity": {
                "type": "score",
                "score": 1.25,
                "confidence": 0.75,
                "legend": {"0": "low", "1": "medium", "2": "high"},
                "probabilities": {"0": 0.25, "1": 0.5, "2": 0.25},
            },
        },
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }

    compact = compact_response(response)

    assert compact == {
        "schema_version": 1,
        "model": "jev-1.13.0",
        "answers": {
            "kind": {
                "value": "application_bug",
                "probabilities": {"application_bug": 0.91, "environment": 0.09},
            },
            "relevant": {
                "value": True,
                "probabilities": {"true": 0.8, "false": 0.2},
            },
            "severity": {
                "value": 1.25,
                "probabilities": {"0": 0.25, "1": 0.5, "2": 0.25},
            },
        },
    }
    assert compact_response(response, include_probabilities=False) == compact


@pytest.mark.parametrize(
    ("noul", "value"),
    [(0.0, False), (0.5, True), (1.0, True)],
)
def test_compact_response_preserves_noul_uncertainty(noul: float, value: bool) -> None:
    compact = compact_response(
        {"answers": {"relevant": {"type": "noul", "noul": noul}}}
    )

    assert compact["answers"]["relevant"] == {
        "value": value,
        "probabilities": {"true": noul, "false": 1 - noul},
    }


def test_agent_summary_keeps_command_status_and_semantic_decisions() -> None:
    result = {
        "schema_version": 1,
        "model": "jev-test",
        "input": {"status": "0"},
        "answers": {
            "kind": {"value": "success"},
            "next_action": {"value": "stop"},
            "severity": {"value": "low"},
            "relevant": {"value": True},
        },
    }

    assert agent_summary(result) == {
        "schema_version": 1,
        "model": "jev-test",
        "answers": {
            "kind": {"value": "success"},
            "next_action": {"value": "stop"},
            "severity": {"value": "low"},
            "relevant": {"value": True},
        },
        "command_succeeded": True,
    }


def test_agent_summary_marks_a_failed_source_command() -> None:
    result = {
        "schema_version": 1,
        "model": "jev-test",
        "input": {"status": "1"},
        "answers": {
            "kind": {"value": "application_bug"},
            "next_action": {"value": "fix_code"},
        },
    }

    assert agent_summary(result) == {
        "schema_version": 1,
        "model": "jev-test",
        "answers": {
            "kind": {"value": "application_bug"},
            "next_action": {"value": "fix_code"},
        },
        "command_succeeded": False,
    }


def test_agent_summary_omits_unknown_command_status() -> None:
    result = {
        "schema_version": 1,
        "model": "jev-test",
        "input": {"status": "unknown"},
        "answers": {
            "kind": {"value": "ambiguous"},
            "next_action": {"value": "rerun"},
        },
    }

    assert agent_summary(result) == {
        "schema_version": 1,
        "model": "jev-test",
        "answers": {
            "kind": {"value": "ambiguous"},
            "next_action": {"value": "rerun"},
        },
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
    assert metrics["metrics_schema_version"] == 1
    assert metrics["tmjev_version"] == __version__
    assert metrics["duration_ms"] is None
    assert metrics["max_retries"] is None


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
                "--retries",
                "4",
                "--status",
                "0",
                "--env-file",
                str(env_file),
                "--metrics-file",
                str(metrics_file),
            ]
        )
        == 0
    )

    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "model": "jev-test",
        "answers": {},
        "command_succeeded": True,
    }
    assert metrics["llm_output_chars_to_jev"] == invocation_chars(
        [
            "output",
            "--file",
            str(raw_file),
            "--task",
            "diagnose",
            "--retries",
            "4",
            "--status",
            "0",
            "--env-file",
            str(env_file),
        ]
    )
    assert metrics["llm_input_chars_avoided"] == len("ERROR failed\n")
    assert metrics["llm_input_chars_from_jev"] == len(stdout)
    assert metrics["metrics_schema_version"] == 1
    assert metrics["tmjev_version"] == __version__
    assert metrics["duration_ms"] >= 0
    assert metrics["max_retries"] == 4


def test_full_output_uses_the_projected_answer_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_file = tmp_path / "output.log"
    raw_file.write_text("ERROR failed\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("TYPESAFE_API_KEY=test-key\n", encoding="utf-8")
    metrics_file = tmp_path / "metrics.jsonl"

    monkeypatch.setattr(
        "tell_me_jev.request_evaluation",
        lambda *_args, **_kwargs: {
            "model": "jev-test",
            "answers": {
                "kind": {
                    "type": "choice",
                    "choice": "application_bug",
                    "confidence": 0.9,
                    "probabilities": {"application_bug": 0.9, "ambiguous": 0.1},
                },
                "relevant": {"type": "noul", "noul": 0.8},
            },
            "usage": {"input_tokens": 10, "output_tokens": 2},
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
                "--full",
                "--env-file",
                str(env_file),
                "--metrics-file",
                str(metrics_file),
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["answers"] == {
        "kind": {
            "value": "application_bug",
            "probabilities": {"application_bug": 0.9, "ambiguous": 0.1},
        },
        "relevant": {
            "value": True,
            "probabilities": {"true": 0.8, "false": 0.2},
        },
    }
    assert "usage" not in payload


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
                "--retries",
                "0",
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
    assert metrics["metrics_schema_version"] == 1
    assert metrics["tmjev_version"] == __version__
    assert metrics["duration_ms"] >= 0
    assert metrics["max_retries"] == 0


def test_malformed_dotenv_returns_exit_2_without_calling_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_file = tmp_path / "output.log"
    raw_file.write_text("ERROR failed\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("BROKEN\n", encoding="utf-8")
    metrics_file = tmp_path / "metrics.jsonl"

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the API must not be called for malformed dotenv input")

    monkeypatch.setattr("tell_me_jev.request_evaluation", fail_if_called)

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
        == 2
    )

    stdout = capsys.readouterr().out
    error = json.loads(stdout)
    metrics = json.loads(metrics_file.read_text(encoding="utf-8").splitlines()[0])
    assert "line 1: expected KEY=value" in error["error"]
    assert metrics["success"] is False
    assert metrics["duration_ms"] >= 0
    assert metrics["max_retries"] == 2
