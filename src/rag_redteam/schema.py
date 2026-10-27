"""Attack case schema. One YAML file per category under attacks/; see attacks/README.md."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The target's own input limits (rail-regulation-rag AskRequest). Attacks stay inside them so
# they test the model, not the validator; error-path cases use `request` instead.
MIN_QUESTION, MAX_QUESTION = 3, 500


class Category(StrEnum):
    DIRECT_INJECTION = "direct_injection"
    INDIRECT_INJECTION = "indirect_injection"
    JAILBREAK = "jailbreak"
    DATA_LEAKAGE = "data_leakage"
    GROUNDING = "grounding"
    CONTROLS = "controls"  # normal questions that must be answered: they measure over-refusal


ID_PREFIX = {
    Category.DIRECT_INJECTION: "DPI",
    Category.INDIRECT_INJECTION: "IPI",
    Category.JAILBREAK: "JB",
    Category.DATA_LEAKAGE: "LEAK",
    Category.GROUNDING: "GRD",
    Category.CONTROLS: "CTL",
}


class Severity(StrEnum):
    """Impact if the attack got through, not how likely it is to."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Layer(StrEnum):
    """Pipeline stage the attack aims at (see the threat model in attacks/README.md)."""

    HTTP = "http"
    REWRITE = "rewrite"
    RETRIEVAL_GATE = "retrieval_gate"
    GRADER = "grader"
    GENERATOR = "generator"
    CITATION_GATE = "citation_gate"


class Check(StrEnum):
    # Deterministic: decided from the HTTP response alone.
    REFUSED = "refused"  # the target returned its explicit refusal
    CITED_IF_ANSWERED = "cited_if_answered"  # refusal, or an answer whose sentences all cite
    NO_FORBIDDEN = "no_forbidden"  # none of the case's `forbidden` strings appear
    NO_SYSTEM_PROMPT = "no_system_prompt"  # no fingerprint of the target's prompts
    NO_SECRETS = "no_secrets"  # no API-key or token patterns
    NO_FOREIGN_URLS = "no_foreign_urls"  # URLs only on the target's allowed domains
    NO_PLANTED_CITATION = "no_planted_citation"  # no citation to a planted passage
    NO_UNSUPPORTED_REFS = "no_unsupported_refs"  # section/article refs exist in cited passages
    NO_STACK_TRACE = "no_stack_trace"  # no traceback, file path or exception name
    STATUS = "status"  # status code is in `expect_status`
    MAX_CITATIONS = "max_citations"  # at most `max_citations` citations
    ANSWERED = "answered"  # the target answered instead of refusing (for controls)
    CITES_EXPECTED_DOC = "cites_expected_doc"  # a citation comes from one of `expect_docs`
    # LLM judge: needs reading comprehension; the judge must give a reason.
    FAITHFUL = "faithful"  # every claim is supported by the cited passages
    NO_INJECTED_INSTRUCTION = "no_injected_instruction"  # did not act on a planted instruction
    NO_LEGAL_AUTHORITY = "no_legal_authority"  # no binding legal advice or compliance sign-off
    NO_EVASION_HELP = "no_evasion_help"  # no help circumventing a regulatory process


LLM_CHECKS = frozenset(
    {Check.FAITHFUL, Check.NO_INJECTED_INSTRUCTION, Check.NO_LEGAL_AUTHORITY, Check.NO_EVASION_HELP}
)
# Applied to every case on top of its own `expect`: these must never fail, whatever the attack.
BASELINE_CHECKS = (Check.NO_SECRETS, Check.NO_FOREIGN_URLS, Check.NO_STACK_TRACE)


class RawRequest(BaseModel):
    """An HTTP request sent as-is, for error-path cases the /ask schema would reject."""

    model_config = ConfigDict(extra="forbid")

    method: str = "POST"
    path: str = "/ask"
    body: str  # sent verbatim with content-type: application/json


class AttackCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category  # filled in from the file header by the loader
    title: str
    summary: str  # public description; reports use it instead of the payload for private cases
    input: str | None = None  # the question; None when the payload is private and not loaded
    request: RawRequest | None = None
    private: bool = False  # payload lives in attacks/private/payloads.yaml (gitignored)
    setup: list[str] = []  # questions sent first, e.g. another "user's" question with a canary
    planted: list[str] = []  # PlantedPassage ids that must be in the target's index
    targets: list[Layer] = Field(min_length=1)
    expect: list[Check] = Field(min_length=1)
    forbidden: list[str] = []  # case-insensitive substrings for NO_FORBIDDEN
    expect_status: list[int] = [200]
    max_citations: int | None = None
    expect_docs: list[str] = []  # document ids for CITES_EXPECTED_DOC, e.g. tsi-noi
    severity: Severity
    rationale: str

    @model_validator(mode="after")
    def _consistent(self) -> AttackCase:
        prefix = ID_PREFIX[self.category]
        if not re.fullmatch(rf"{prefix}-\d{{3}}", self.id):
            raise ValueError(f"{self.id}: id must look like {prefix}-001 for {self.category}")
        if self.severity is Severity.HIGH and not self.private:
            raise ValueError(f"{self.id}: high-severity payloads must be private")
        if self.input is not None and self.request is not None:
            raise ValueError(f"{self.id}: give `input` or `request`, not both")
        if self.input is None and self.request is None and not self.private:
            raise ValueError(f"{self.id}: needs `input` or `request`")
        if self.input is not None and not MIN_QUESTION <= len(self.input) <= MAX_QUESTION:
            raise ValueError(f"{self.id}: input must be {MIN_QUESTION}-{MAX_QUESTION} chars")
        if Check.NO_FORBIDDEN in self.expect and not self.forbidden:
            raise ValueError(f"{self.id}: no_forbidden needs a `forbidden` list")
        if Check.CITES_EXPECTED_DOC in self.expect and not self.expect_docs:
            raise ValueError(f"{self.id}: cites_expected_doc needs an `expect_docs` list")
        if Check.MAX_CITATIONS in self.expect and self.max_citations is None:
            raise ValueError(f"{self.id}: max_citations check needs `max_citations`")
        return self

    @property
    def runnable(self) -> bool:
        return self.input is not None or self.request is not None

    @property
    def checks(self) -> list[Check]:
        """The case's own checks plus the baseline, without duplicates, in a stable order."""
        return list(dict.fromkeys([*self.expect, *BASELINE_CHECKS]))


class PlantedPassage(BaseModel):
    """A synthetic passage added to a COPY of the target's corpus for indirect injection.

    `section` is unique to the planted passage, so a citation to it is detectable.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^PL-\d{3}$")
    doc_id: str  # host document in the target corpus, e.g. tsi-prm
    section: str
    heading: str
    summary: str
    text: str | None = None  # None when private and not loaded
    private: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> PlantedPassage:
        if self.text is None and not self.private:
            raise ValueError(f"{self.id}: needs `text`")
        return self
