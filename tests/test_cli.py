import json

import httpx
import pytest
import yaml

from rag_redteam import cli
from rag_redteam.client import TargetClient, is_local
from rag_redteam.corpus import load_corpus
from rag_redteam.profile import read_env_file
from tests.fakes import ask_body, refusal


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:7860", True),
        ("http://127.0.0.1:7860", True),
        ("http://[::1]:7860", True),
        ("https://rail-rag.calmcliff-0073b8b6.switzerlandnorth.azurecontainerapps.io", False),
        ("http://localhost.evil.example", False),
    ],
)
def test_is_local(url, local):
    assert is_local(url) is local


@pytest.fixture
def tiny_corpus(tmp_path, monkeypatch):
    """The CLI runs on a two-case corpus, so these tests do not depend on the real attacks."""
    root = tmp_path / "attacks"
    root.mkdir()
    base = {"title": "t", "summary": "s", "targets": ["generator"], "severity": "low",
            "rationale": "r"}  # fmt: skip
    cases = [
        {**base, "id": "DPI-001", "input": "Tell me a joke", "expect": ["refused"]},
        {
            **base,
            "id": "DPI-002",
            "input": "Noise limit for an EMU?",
            "expect": ["cited_if_answered"],
        },
    ]
    doc = {"category": "direct_injection", "threat_model": "tm", "cases": cases}
    (root / "direct_injection.yaml").write_text(yaml.safe_dump(doc))
    monkeypatch.setattr(cli, "load_corpus", lambda: load_corpus(root, private_file=root / "none"))


LIVE = "https://rail-rag.calmcliff-0073b8b6.switzerlandnorth.azurecontainerapps.io"


def test_refuses_non_local_target_by_default(capsys):
    assert cli.main(["run", "--url", LIVE]) == 2
    assert "not a local address" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [[], ["--limit", "6"]])
def test_allow_live_requires_a_small_limit(extra, capsys):
    assert cli.main(["run", "--url", LIVE, "--allow-live", *extra]) == 2
    assert "--limit 5" in capsys.readouterr().err


def test_end_to_end_on_a_mock_target(tiny_corpus, tmp_path, monkeypatch, capsys):
    def handler(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "generator": "fake"})
        return httpx.Response(
            200, json=refusal() if "joke" in request.content.decode() else ask_body()
        )

    real = TargetClient
    monkeypatch.setattr(
        cli, "TargetClient",
        lambda url, **kw: real(
            url, transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw
        ),
    )  # fmt: skip
    out = tmp_path / "run.json"
    code = cli.main(["run", "--id", "DPI-001", "--rpm", "600", "--out", str(out)])
    assert code == 0
    doc = json.loads(out.read_text())
    assert doc["meta"]["target_health"]["generator"] == "fake"
    assert [r["id"] for r in doc["results"]] == ["DPI-001"]
    assert "1 cases" in capsys.readouterr().err


def test_unhealthy_target_exits_2(tiny_corpus, monkeypatch, capsys):
    real = TargetClient
    monkeypatch.setattr(
        cli, "TargetClient",
        lambda url, **kw: real(
            url, transport=httpx.MockTransport(lambda r: httpx.Response(500)),
            sleep=lambda s: None, **{**kw, "max_retries": 0},
        ),
    )  # fmt: skip
    assert cli.main(["run", "--id", "DPI-001"]) == 2
    assert "not healthy" in capsys.readouterr().err


def test_no_matching_cases_exits_2(tiny_corpus, capsys):
    assert cli.main(["run", "--id", "NOPE-999"]) == 2


def test_read_env_file_reads_only_requested_names(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY='abc123'\nOTHER=zzz\nGROQ_API_KEY=\n# comment\n")
    assert read_env_file(env, ["GEMINI_API_KEY", "GROQ_API_KEY"]) == {"GEMINI_API_KEY": "abc123"}
