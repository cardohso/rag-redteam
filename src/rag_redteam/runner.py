"""Run attack cases against the target, judge each response, and write results to JSON."""

from __future__ import annotations

import json
import platform
from collections import Counter
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
class Trial:
    """One run of a case when the suite is run with --repeat."""

    status: Status
    setup: list[Exchange] = field(default_factory=list)
    exchange: Exchange | None = None
    verdicts: list[Verdict] = field(default_factory=list)


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
    # With --repeat N: one Trial per run, and the counts. `exchange` and `verdicts` above then
    # hold the most telling trial (a failure if there is one), so single-run readers still work.
    trials: list[Trial] = field(default_factory=list)
    rates: dict | None = None


def case_status(verdicts: list[Verdict]) -> Status:
    outcomes = {v.outcome for v in verdicts}
    if Outcome.FAIL in outcomes:
        return Status.FAILED
    if Outcome.INCONCLUSIVE in outcomes:
        return Status.INCONCLUSIVE
    return Status.DEFENDED


def trial_rates(statuses: list[str]) -> tuple[str, dict]:
    """Case status and counts from the statuses of its trials. One failure makes the case
    failed (it got through at least once); otherwise one inconclusive trial leaves it
    inconclusive; only all-defended is defended."""
    counts = Counter(str(x) for x in statuses)
    rates = {
        "trials": len(statuses),
        "defended": counts[Status.DEFENDED],
        "failed": counts[Status.FAILED],
        "inconclusive": counts[Status.INCONCLUSIVE],
    }
    if rates["failed"]:
        return Status.FAILED.value, rates
    if rates["inconclusive"]:
        return Status.INCONCLUSIVE.value, rates
    return Status.DEFENDED.value, rates


def representative_index(statuses: list[str]) -> int:
    """The trial to show for a repeated case: the first failure, else the first inconclusive."""
    for wanted in (Status.FAILED, Status.INCONCLUSIVE):
        for i, status in enumerate(statuses):
            if str(status) == wanted:
                return i
    return 0


def finalize_trials(result: dict) -> None:
    """Recompute a results-file entry from its trials (after the LLM judge changed them)."""
    trials = result.get("trials") or []
    if not trials:
        return
    statuses = [t["status"] for t in trials]
    status, rates = trial_rates(statuses)
    rep = trials[representative_index(statuses)]
    result.update(
        status=status, rates=rates, setup=rep["setup"], exchange=rep["exchange"],
        verdicts=rep["verdicts"],
    )  # fmt: skip


def run_case_repeated(
    case: AttackCase,
    client: TargetClient,
    profile: TargetProfile,
    known_secrets: dict[str, str] | None = None,
    repeat: int = 1,
) -> CaseResult:
    runs = [run_case(case, client, profile, known_secrets) for _ in range(repeat)]
    if repeat == 1:
        return runs[0]
    statuses = [r.status for r in runs]
    status, rates = trial_rates(statuses)
    rep = runs[representative_index(statuses)]
    return CaseResult(
        rep.id, rep.category, rep.severity, rep.title, rep.private, Status(status),
        input=rep.input, setup=rep.setup, exchange=rep.exchange, verdicts=rep.verdicts,
        trials=[Trial(r.status, r.setup, r.exchange, r.verdicts) for r in runs], rates=rates,
    )  # fmt: skip


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
    repeat: int = 1,
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
            result = run_case_repeated(case, client, profile, known_secrets, repeat)
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
    repeat: int = 1,
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
            "repeat": repeat,
        },
        "results": [asdict(r) for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def merge_results(docs: list[dict], *, latest_wins: bool = False) -> dict:
    """One results document from several runs, e.g. a clean-index run and a planted-index run.

    A case that one run skipped (it needed an index that run did not have) is replaced by the
    run that actually executed it. Two runs that both executed a case are an error, unless
    `latest_wins`: then the later run replaces the earlier one (a re-run). Per-run settings are
    kept in meta["merged_from"], which is carried over when a merged file is merged again."""
    chosen: dict[str, dict] = {}
    for doc in docs:
        for r in doc["results"]:
            held = chosen.get(r["id"])
            if held is None or held["status"] == "skipped":
                chosen[r["id"]] = r
            elif r["status"] != "skipped":
                if not latest_wins:
                    raise ValueError(f"case ids run in more than one run: {r['id']}")
                chosen[r["id"]] = r
    generators = {(d["meta"].get("target_health") or {}).get("generator") for d in docs}
    if len(generators) > 1:
        raise ValueError(f"runs used different generators: {sorted(map(str, generators))}")
    planted = {bool(d["meta"]["planted_index"]) for d in docs}
    meta = {
        **docs[0]["meta"],
        "planted_index": next(iter(planted)) if len(planted) == 1 else "planted cases only",
        "cases_selected": len(chosen),
        "merged_from": [
            entry
            for d in docs
            for entry in d["meta"].get("merged_from")
            or [
                {
                    "started_at": d["meta"]["started_at"],
                    "target_url": d["meta"]["target_url"],
                    "planted_index": d["meta"]["planted_index"],
                    "cases": len(d["results"]),
                }
            ]
        ],
    }
    return {"meta": meta, "results": list(chosen.values())}
