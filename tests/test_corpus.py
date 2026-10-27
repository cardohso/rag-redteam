from pathlib import Path

import pytest
import yaml

from rag_redteam.corpus import ATTACKS_DIR, CorpusError, load_corpus
from rag_redteam.schema import Category, Severity


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def case(id_: str, **overrides) -> dict:
    base = {
        "id": id_,
        "title": "t",
        "summary": "s",
        "input": "What is the noise limit for an EMU?",
        "targets": ["generator"],
        "expect": ["cited_if_answered"],
        "severity": "low",
        "rationale": "r",
    }
    return {**base, **overrides}


def grounding_file(root: Path, *cases: dict) -> None:
    write(
        root / "grounding.yaml",
        {"category": "grounding", "threat_model": "tm", "cases": list(cases)},
    )


PRIVATE = {"severity": "high", "private": True, "input": None}


def private_case(id_: str) -> dict:
    c = case(id_, **PRIVATE)
    del c["input"]
    return c


def test_loads_category_file(tmp_path):
    grounding_file(tmp_path, case("GRD-001"), case("GRD-002"))
    corpus = load_corpus(tmp_path)
    assert [c.id for c in corpus.by_category(Category.GROUNDING)] == ["GRD-001", "GRD-002"]
    assert corpus.threat_models[Category.GROUNDING] == "tm"
    assert not corpus.private_loaded and corpus.missing_private == []


def test_header_category_must_match_filename(tmp_path):
    write(tmp_path / "grounding.yaml", {"category": "jailbreak", "cases": []})
    with pytest.raises(CorpusError, match="header category"):
        load_corpus(tmp_path)


def test_case_must_not_set_its_own_category(tmp_path):
    grounding_file(tmp_path, case("GRD-001", category="grounding"))
    with pytest.raises(CorpusError, match="comes from the file header"):
        load_corpus(tmp_path)


def test_duplicate_ids_rejected(tmp_path):
    grounding_file(tmp_path, case("GRD-001"), case("GRD-001"))
    with pytest.raises(CorpusError, match="duplicate case ids"):
        load_corpus(tmp_path)


@pytest.mark.parametrize("field", ["input", "request"])
def test_guard_rejects_private_payload_in_public_file(tmp_path, field):
    leaked = private_case("GRD-001")
    leaked[field] = "What is the noise limit?" if field == "input" else {"body": "{}"}
    grounding_file(tmp_path, leaked)
    with pytest.raises(CorpusError, match="private payload must not be in grounding.yaml"):
        load_corpus(tmp_path)


def test_private_case_without_payload_file_is_reported_missing(tmp_path):
    grounding_file(tmp_path, private_case("GRD-001"))
    corpus = load_corpus(tmp_path)
    assert corpus.missing_private == ["GRD-001"]
    assert not corpus.cases[0].runnable


def test_private_payload_is_merged(tmp_path):
    grounding_file(tmp_path, private_case("GRD-001"))
    write(tmp_path / "private" / "payloads.yaml", {"cases": {"GRD-001": "Merged question text"}})
    corpus = load_corpus(tmp_path)
    assert corpus.private_loaded and corpus.missing_private == []
    assert corpus.cases[0].input == "Merged question text"
    assert corpus.cases[0].severity is Severity.HIGH


def test_planted_passages(tmp_path):
    passage = {"doc_id": "tsi-prm", "section": "s-PL001", "heading": "h", "summary": "s"}
    write(
        tmp_path / "planted.yaml",
        {
            "passages": [
                {**passage, "id": "PL-001", "text": "t"},
                {**passage, "id": "PL-002", "private": True},
            ]
        },
    )
    write(tmp_path / "private" / "payloads.yaml", {"planted": {"PL-002": "private text"}})
    grounding_file(tmp_path, case("GRD-001", planted=["PL-001", "PL-002"]))
    corpus = load_corpus(tmp_path)
    assert corpus.planted["PL-002"].text == "private text"


def test_private_planted_text_in_public_file_rejected(tmp_path):
    passage = {"id": "PL-001", "doc_id": "d", "section": "s", "heading": "h", "summary": "s"}
    write(tmp_path / "planted.yaml", {"passages": [{**passage, "private": True, "text": "t"}]})
    with pytest.raises(CorpusError, match="private planted text"):
        load_corpus(tmp_path)


def test_unknown_planted_reference_rejected(tmp_path):
    grounding_file(tmp_path, case("GRD-001", planted=["PL-009"]))
    with pytest.raises(CorpusError, match="unknown planted"):
        load_corpus(tmp_path)


def test_repo_corpus_is_valid_without_private_payloads():
    """The committed corpus must load in CI, where attacks/private/ does not exist."""
    corpus = load_corpus(ATTACKS_DIR, private_file=ATTACKS_DIR / "does-not-exist.yaml")
    for c in corpus.cases:
        assert c.severity is not Severity.HIGH or c.private, c.id


def test_unrecognised_yaml_is_an_error_not_silently_skipped(tmp_path):
    grounding_file(tmp_path, case("GRD-001"))
    write(tmp_path / "grounding_extra.yaml", {"category": "grounding", "cases": []})
    with pytest.raises(CorpusError, match="unrecognised yaml"):
        load_corpus(tmp_path)


def test_controls_file_loads(tmp_path):
    ctl = case("CTL-001", expect=["answered"])
    write(
        tmp_path / "controls.yaml", {"category": "controls", "threat_model": "tm", "cases": [ctl]}
    )
    corpus = load_corpus(tmp_path)
    assert [c.id for c in corpus.by_category(Category.CONTROLS)] == ["CTL-001"]
