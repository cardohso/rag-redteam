"""Run attack cases against the target, judge each response, and write results to JSON."""

from __future__ import annotations

import json
import platform
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from rag_redteam import __version__
from rag_redteam.client import Exchange, TargetClient
from rag_redteam.corpus import Corpus
from rag_redteam.judges.base import Outcome, Verdict
from rag_redteam.judges.deterministic import JudgeContext, judge_case
from rag_redteam.profile import TargetProfile
from rag_redteam.schema import AttackCase, Category, Severity


class Status(StrEnum):
    DEFENDED = "defended"
    FAILED = "failed"  # got through
    INCONCLUSIVE = "inconclusive"
    SKIPPED = "skipped"  # not run: private payload missing, or needs a planted index


@dataclass
class CaseResult:
    id: str
    category: str
    severity: str
    title: str
    private: bool
    status: Status
    input: str | None = None  # full payload: raw results are local-only (results/raw is ignored)
    skipped_reason: str | None = None
    setup: list[Exchange] = field(default_factory=list)
    exchange: Exchange | None = None
    verdicts: list[Verdict] = field(default_factory=list)


def case_status(verdicts: list[Verdict]) -> Status:
    outcomes = {v.outcome for v in verdicts}
    if Outcome.FAIL in outcomes:
        return Status.FAILED
    if Outcome.INCONCLUSIVE in outcomes:
        return Status.INCONCLUSIVE
    return Status.DEFENDED


def run_case(
    case: AttackCase,
    client: TargetClient,
    profile: TargetProfile,
    known_secrets: dict[str, str] | None = None,
) -> CaseResult:
    result = CaseResult(
        case.id, case.category.value, case.severity.value, case.title, case.private,
        Status.INCONCLUSIVE, input=case.input,
    )  # fmt: skip
    result.setup = [client.ask(q) for q in case.setup]
    if case.request is not None:
        result.exchange = client.send(case.request.method, case.request.path, case.request.body)
    else:
        result.exchange = client.ask(case.input or "")
    ctx = JudgeContext(case, result.exchange, profile, known_secrets or {})
    result.verdicts = judge_case(ctx)
    result.status = case_status(result.verdicts)
    return result


def skip_reason(case: AttackCase, planted_index: bool) -> str | None:
    if not case.runnable:
        return "private payload not available (attacks/private/payloads.yaml)"
    if case.planted and not planted_index:
        return "needs the planted index (start the target on the planted copy, pass --planted)"
    return None


def select(
    cases: list[AttackCase],
    *,
    categories: set[Category] | None = None,
    ids: set[str] | None = None,
    severities: set[Severity] | None = None,
    limit: int | None = None,
) -> list[AttackCase]:
    chosen = [
        c
        for c in cases
        if (not categories or c.category in categories)
        and (not ids or c.id in ids)
        and (not severities or c.severity in severities)
    ]
    return chosen[:limit] if limit else chosen


def run_suite(
    cases: list[AttackCase],
    client: TargetClient,
    profile: TargetProfile,
    *,
    planted_index: bool = False,
    known_secrets: dict[str, str] | None = None,
    progress: Callable[[int, int, CaseResult], None] | None = None,
) -> list[CaseResult]:
    results = []
    for i, case in enumerate(cases, start=1):
        reason = skip_reason(case, planted_index)
        if reason:
            result = CaseResult(
                case.id, case.category.value, case.severity.value, case.title, case.private,
                Status.SKIPPED, input=case.input, skipped_reason=reason,
            )  # fmt: skip
        else:
            result = run_case(case, client, profile, known_secrets)
        results.append(result)
        if progress:
            progress(i, len(cases), result)
    return results


def write_results(
    path: Path,
    results: list[CaseResult],
    *,
    target_url: str,
    health: dict | None,
    corpus: Corpus,
    planted_index: bool,
) -> None:
    """Write the full record of a run. Raw results contain payloads and responses and stay local."""
    doc = {
        "meta": {
            "suite_version": __version__,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "python": platform.python_version(),
            "target_url": target_url,
            "target_health": health,  # includes the generator model name, no secrets
            "planted_index": planted_index,
            "private_payloads_loaded": corpus.private_loaded,
            "cases_selected": len(results),
        },
        "results": [asdict(r) for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
