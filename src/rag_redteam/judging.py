"""Second pass over a results file: the LLM judge decides the checks the first pass deferred.

Separate from the run so a judge change or a re-judge costs no target (Gemini) calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime

from rag_redteam.client import Exchange
from rag_redteam.corpus import Corpus
from rag_redteam.judges.base import Outcome, Verdict
from rag_redteam.judges.family import check_families
from rag_redteam.judges.llm import JudgeCache, JudgeLLM, judge_check
from rag_redteam.runner import Status, case_status, finalize_trials
from rag_redteam.schema import LLM_CHECKS, Check

LLM_CHECK_NAMES = {c.value for c in LLM_CHECKS}


def _pending(verdict: dict, rejudge: bool) -> bool:
    if verdict["check"] not in LLM_CHECK_NAMES:
        return False
    return rejudge or verdict["judge"] == "deterministic"


def judge_results(
    doc: dict,
    corpus: Corpus,
    judge: JudgeLLM,
    *,
    cache: JudgeCache | None = None,
    allow_same_family: bool = False,
    rejudge: bool = False,
    progress: Callable[[str, str], None] | None = None,
) -> dict:
    """Fill in LLM verdicts in a results document (in place) and recompute case statuses.

    Raises SameFamilyError before any call if the judge shares the generator's family.
    """
    generator = (doc["meta"].get("target_health") or {}).get("generator")
    families = check_families(generator, judge.name, allow_same_family=allow_same_family)
    cases = {c.id: c for c in corpus.cases}
    judged = 0
    for result in doc["results"]:
        case = cases.get(result["id"])
        if result["status"] == Status.SKIPPED or result.get("exchange") is None or case is None:
            continue
        if result.get("input"):  # the payload as sent, even if private payloads are not loaded now
            case = case.model_copy(update={"input": result["input"]})
        planted = [corpus.planted[p].text for p in case.planted if corpus.planted[p].text]
        # A repeated case has one trial per run; a single run is its own only unit.
        for unit in result.get("trials") or [result]:
            exchange = Exchange(**unit["exchange"])
            verdicts = []
            wanted = {c.value for c in case.checks}
            for v in unit["verdicts"]:
                # An LLM check the case no longer lists (the spec changed after the run) is dropped.
                if v["check"] in LLM_CHECK_NAMES and v["check"] not in wanted:
                    continue
                if _pending(v, rejudge):
                    v = asdict(
                        judge_check(
                            Check(v["check"]), case, exchange, judge, planted=planted, cache=cache
                        )
                    )
                    judged += 1
                verdicts.append(v)
            # An LLM check added to the case after the run is judged from the stored exchange.
            present = {v["check"] for v in verdicts}
            for check in case.checks:
                if check in LLM_CHECKS and check.value not in present:
                    v = judge_check(check, case, exchange, judge, planted=planted, cache=cache)
                    verdicts.append(asdict(v))
                    judged += 1
            unit["verdicts"] = verdicts
            unit["status"] = case_status(
                [Verdict(**{**v, "outcome": Outcome(v["outcome"])}) for v in verdicts]
            ).value
        finalize_trials(result)  # no-op for a single run
        if progress:
            progress(result["id"], result["status"])
    doc["meta"]["llm_judge"] = {
        "judge": judge.name,
        "families": families,
        "judged_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "verdicts": judged,
        "cache_hits": cache.hits if cache else 0,
        "cache_misses": cache.misses if cache else 0,
    }
    return doc
