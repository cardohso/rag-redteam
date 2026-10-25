"""Generic recommended fix per failed check, used by the report.

These are starting points chosen by the kind of failure, not a diagnosis of one case: the fix
that actually ships is proposed and measured separately (step 7), after review.
"""

from __future__ import annotations

from rag_redteam.schema import Check

FIXES: dict[str, str] = {
    Check.REFUSED: (
        "Add a prompt rule to refuse requests that tell the assistant to change or ignore its "
        "instructions, and tighten the pre-LLM gates (retrieval score floor, grader)."
    ),
    Check.CITED_IF_ANSWERED: (
        "Enforce citations per sentence in the citation gate: refuse or strip answers with "
        "uncited sentences, instead of accepting one valid marker for the whole answer."
    ),
    Check.NO_FORBIDDEN: (
        "Add an instruction hierarchy to the prompt (question and passages are data, not "
        "instructions) and check each claim against the passage it cites."
    ),
    Check.NO_SYSTEM_PROMPT: (
        "Refuse requests about the assistant's instructions and filter outputs that repeat "
        "prompt text."
    ),
    Check.NO_SECRETS: (
        "Add an output filter for key and token patterns, and keep secrets out of any "
        "model-visible context."
    ),
    Check.NO_FOREIGN_URLS: (
        "Filter outputs: remove or reject URLs outside an allow-list (EUR-Lex) and build links "
        "only from citation metadata."
    ),
    Check.NO_PLANTED_CITATION: (
        "Treat retrieved passages as untrusted: sanitise them, mark them as data in the prompt, "
        "and review the provenance of everything that reaches the index."
    ),
    Check.NO_UNSUPPORTED_REFS: (
        "Verify article and section references in the answer against the cited passages and "
        "drop or flag the ones that do not appear in them."
    ),
    Check.NO_STACK_TRACE: (
        "Add a global exception handler that returns a generic error body, and keep debug "
        "output off."
    ),
    Check.STATUS: "Return a stable, validated error status for malformed requests.",
    Check.MAX_CITATIONS: "Cap the number of citations returned per answer.",
    Check.FAITHFUL: (
        "Add a grounding check that verifies each claim against its cited passage and refuses "
        "unsupported answers."
    ),
    Check.NO_INJECTED_INSTRUCTION: (
        "Add an instruction hierarchy to the prompt and sanitise retrieved passages (drop text "
        "that addresses the assistant) before they reach the grader and generator."
    ),
    Check.NO_LEGAL_AUTHORITY: (
        "Add a scope rule: report what the regulation says with citations, never certify "
        "compliance or give binding legal advice."
    ),
    Check.NO_EVASION_HELP: (
        "Add a refusal rule for requests to circumvent or deceive a regulatory process."
    ),
}
assert set(FIXES) == set(Check), "every check needs a recommended fix"


def fix_for(check: str) -> str:
    return FIXES[Check(check)]
