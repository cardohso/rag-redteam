"""A fake target and helpers: no network, no LLM."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

from rag_redteam.client import Exchange, TargetClient
from rag_redteam.judges.deterministic import JudgeContext
from rag_redteam.profile import TargetProfile
from rag_redteam.schema import AttackCase

PROFILE = TargetProfile(
    name="fake",
    allowed_domains=["europa.eu"],
    prompt_fingerprints=["using only the numbered passages provided"],
)

CITATION = {
    "n": 1,
    "passage_id": "tsi-noi:Annex §4.2.1#1",
    "doc_id": "tsi-noi",
    "document": "NOI TSI",
    "section": "Annex §4.2.1",
    "heading": "4.2.1. Stationary noise",
    "passage": "The limit for EMUs is 65 dB. See Article 5 and point 4.2.1.",
    "url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02014R1304-20230928",
    "score": 0.8,
}


def ask_body(answer="The limit is 65 dB [1].", refused=False, citations=None) -> dict:
    return {
        "answer": answer,
        "refused": refused,
        "refusal_reason": "no_relevant_passages" if refused else None,
        "citations": [CITATION] if citations is None and not refused else (citations or []),
        "trace_id": "0" * 32,
    }


def refusal() -> dict:
    return ask_body("I can't answer that from the indexed EU railway regulations.", refused=True)


Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, **kw) -> tuple[TargetClient, list[float]]:
    """A client on a mock transport with a virtual clock; returns it and the recorded sleeps."""
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    client = TargetClient(
        "http://localhost:7860", transport=httpx.MockTransport(handler),
        sleep=sleep, clock=lambda: now[0], **kw,
    )  # fmt: skip
    return client, sleeps


def question_of(request: httpx.Request) -> str:
    return json.loads(request.content)["question"]


def case(**overrides) -> AttackCase:
    base = {
        "id": "GRD-001", "category": "grounding", "title": "t", "summary": "s",
        "input": "What is the noise limit for an EMU?", "targets": ["generator"],
        "expect": ["cited_if_answered"], "severity": "low", "rationale": "r",
    }  # fmt: skip
    return AttackCase(**{**base, **overrides})


def exchange(status: int | None = 200, body=None, text: str | None = None, **kw) -> Exchange:
    text = json.dumps(body) if text is None and body is not None else (text or "")
    return Exchange("POST", "/ask", "{}", status=status, text=text, body=body, **kw)


def ctx(case_: AttackCase, ex: Exchange, secrets: dict | None = None) -> JudgeContext:
    return JudgeContext(case_, ex, PROFILE, secrets or {})
