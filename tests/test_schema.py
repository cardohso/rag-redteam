import pytest
from pydantic import ValidationError

from rag_redteam.schema import (
    BASELINE_CHECKS,
    AttackCase,
    Category,
    Check,
    PlantedPassage,
    Severity,
)


def case(**overrides) -> dict:
    base = {
        "id": "GRD-001",
        "category": "grounding",
        "title": "t",
        "summary": "s",
        "input": "What is the noise limit for an EMU?",
        "targets": ["generator"],
        "expect": ["cited_if_answered"],
        "severity": "low",
        "rationale": "r",
    }
    return {**base, **overrides}


def test_valid_case():
    c = AttackCase(**case())
    assert c.category is Category.GROUNDING
    assert c.severity is Severity.LOW
    assert c.runnable


@pytest.mark.parametrize("bad_id", ["DPI-001", "GRD-1", "GRD-0001", "grd-001"])
def test_id_must_match_category_prefix(bad_id):
    with pytest.raises(ValidationError, match="id must look like GRD-001"):
        AttackCase(**case(id=bad_id))


def test_high_severity_must_be_private():
    with pytest.raises(ValidationError, match="must be private"):
        AttackCase(**case(severity="high"))


def test_private_case_without_payload_is_valid_but_not_runnable():
    c = AttackCase(**case(severity="high", private=True, input=None))
    assert not c.runnable


def test_public_case_needs_input_or_request():
    with pytest.raises(ValidationError, match="needs `input` or `request`"):
        AttackCase(**case(input=None))


def test_input_and_request_are_exclusive():
    with pytest.raises(ValidationError, match="not both"):
        AttackCase(**case(request={"body": "{}"}))


def test_request_case():
    c = AttackCase(**case(input=None, request={"body": "not json"}))
    assert c.request.method == "POST" and c.request.path == "/ask"


@pytest.mark.parametrize("text", ["ab", "x" * 501])
def test_input_length_follows_target_limits(text):
    with pytest.raises(ValidationError, match="3-500 chars"):
        AttackCase(**case(input=text))


def test_no_forbidden_needs_strings():
    with pytest.raises(ValidationError, match="needs a `forbidden` list"):
        AttackCase(**case(expect=["no_forbidden"]))


def test_max_citations_needs_value():
    with pytest.raises(ValidationError, match="needs `max_citations`"):
        AttackCase(**case(expect=["max_citations"]))


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        AttackCase(**case(payload="x"))


def test_checks_append_baseline_without_duplicates():
    c = AttackCase(**case(expect=["refused", "no_secrets"]))
    assert c.checks[0] is Check.REFUSED
    assert c.checks.count(Check.NO_SECRETS) == 1
    assert set(BASELINE_CHECKS) <= set(c.checks)


def test_planted_passage_needs_text_unless_private():
    base = {"id": "PL-001", "doc_id": "d", "section": "s", "heading": "h", "summary": "s"}
    with pytest.raises(ValidationError, match="needs `text`"):
        PlantedPassage(**base)
    assert PlantedPassage(**base, private=True).text is None
    with pytest.raises(ValidationError):
        PlantedPassage(**{**base, "id": "P-1", "text": "t"})


def test_controls_use_the_ctl_prefix():
    ok = AttackCase(**case(id="CTL-001", category="controls", expect=["answered"]))
    assert ok.category is Category.CONTROLS
    with pytest.raises(ValidationError, match="id must look like CTL-001"):
        AttackCase(**case(id="GRD-001", category="controls", expect=["answered"]))


def test_cites_expected_doc_needs_docs():
    with pytest.raises(ValidationError, match="needs an `expect_docs` list"):
        AttackCase(**case(expect=["cites_expected_doc"]))
    c = AttackCase(**case(expect=["cites_expected_doc"], expect_docs=["tsi-noi"]))
    assert c.expect_docs == ["tsi-noi"]
