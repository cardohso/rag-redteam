"""Verdict types shared by the deterministic and LLM judges."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    PASS = "pass"  # defended
    FAIL = "fail"  # the attack got through
    INCONCLUSIVE = "inconclusive"  # no usable response, or the check could not decide


@dataclass(frozen=True)
class Verdict:
    check: str
    outcome: Outcome
    reason: str  # every verdict says why; never contains a secret value
    judge: str = "deterministic"
    evidence: str | None = None  # short excerpt of what triggered a failure


def passed(check, reason: str, **kw) -> Verdict:
    return Verdict(str(check), Outcome.PASS, reason, **kw)


def failed(check, reason: str, evidence: str | None = None, **kw) -> Verdict:
    return Verdict(str(check), Outcome.FAIL, reason, evidence=evidence, **kw)


def inconclusive(check, reason: str, **kw) -> Verdict:
    return Verdict(str(check), Outcome.INCONCLUSIVE, reason, **kw)
