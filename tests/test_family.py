import pytest

from rag_redteam.judges.family import SameFamilyError, check_families, family


@pytest.mark.parametrize(
    ("model", "fam"),
    [
        ("gemini:gemini-3.5-flash-lite", "google"),
        ("groq:gemma2-9b-it", "google"),
        ("groq:openai/gpt-oss-120b", "openai"),
        ("openai:gpt-4o-mini", "openai"),
        ("groq:llama-3.3-70b-versatile", "meta"),
        ("groq:meta-llama/llama-4-scout-17b-16e-instruct", "meta"),
        ("groq:qwen/qwen3-32b", "alibaba"),
        ("groq:moonshotai/kimi-k2-instruct", "moonshot"),
        ("anthropic:claude-haiku-5-5", "anthropic"),
        ("groq:mixtral-8x7b-32768", "mistral"),
        ("local:my-finetune", None),
    ],
)
def test_family(model, fam):
    assert family(model) == fam


def test_different_families_pass():
    summary = check_families("gemini:gemini-3.5-flash-lite", "groq:openai/gpt-oss-120b")
    assert "google" in summary and "openai" in summary


def test_same_family_on_another_provider_is_refused():
    # The case a provider-level check misses: Gemma on Groq grading Gemini.
    with pytest.raises(SameFamilyError, match="both google"):
        check_families("gemini:gemini-3.5-flash-lite", "groq:gemma2-9b-it")


@pytest.mark.parametrize(
    ("gen", "judge"), [(None, "groq:openai/gpt-oss-120b"), ("gemini:x", "local:y")]
)
def test_unknown_family_fails_closed(gen, judge):
    with pytest.raises(SameFamilyError, match="cannot verify"):
        check_families(gen, judge)


def test_override_is_recorded():
    summary = check_families("gemini:a", "groq:gemma-b", allow_same_family=True)
    assert "overridden" in summary
