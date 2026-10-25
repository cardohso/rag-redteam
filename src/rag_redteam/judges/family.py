"""Model-family guard: the LLM judge must not share a family with the generator it grades.

Compares model families, not providers. Groq serves Google's Gemma as well as OpenAI's
gpt-oss, so a provider check alone would let a Gemma judge grade a Gemini generator.
"""

from __future__ import annotations

import re

# Ordered: the first pattern that matches the model name wins. Matched case-insensitively
# against the model part of "provider:model" or "org/model".
FAMILIES: list[tuple[str, str]] = [
    (r"gemini|gemma|palm|bison", "google"),
    (r"gpt-oss|gpt-?\d|\bo\d\b|o\d-|davinci|chatgpt", "openai"),
    (r"llama|meta-", "meta"),
    (r"claude", "anthropic"),
    (r"mistral|mixtral|codestral|ministral|magistral", "mistral"),
    (r"qwen|qwq", "alibaba"),
    (r"deepseek", "deepseek"),
    (r"kimi|moonshot", "moonshot"),
    (r"command|aya|cohere", "cohere"),
    (r"grok", "xai"),
    (r"phi-?\d", "microsoft"),
    (r"granite", "ibm"),
]


class SameFamilyError(ValueError):
    pass


def family(model: str) -> str | None:
    """Family for a model name such as 'gemini:gemini-3.5-flash-lite' or
    'groq:openai/gpt-oss-120b'. None when unknown."""
    name = model.split(":", 1)[-1].lower()
    for pattern, fam in FAMILIES:
        if re.search(pattern, name):
            return fam
    return None


def check_families(generator: str | None, judge: str, *, allow_same_family: bool = False) -> str:
    """Raise unless the judge is from a different, known family than the generator.

    Fails closed: an unknown family on either side is refused, because "different" cannot be
    shown. Returns a one-line description for the run record.
    """
    gen_family = family(generator) if generator else None
    judge_family = family(judge)
    summary = f"generator {generator} ({gen_family}), judge {judge} ({judge_family})"
    if allow_same_family:
        return summary + " [same-family guard overridden]"
    if gen_family is None or judge_family is None:
        raise SameFamilyError(f"cannot verify the families differ: {summary}")
    if gen_family == judge_family:
        raise SameFamilyError(
            f"judge and generator are both {gen_family}: {summary}. Self-preference bias would "
            "make the judge lenient; pick a judge from another family."
        )
    return summary
