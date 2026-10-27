"""Markdown report from a judged results file.

Every number comes from the results JSON. The report is public, so the redaction rule lives
here: a result that is high severity or private never has its payload, the target's answer or an
LLM judge's reason in the report, because those can quote the attack. Its public summary (from
the corpus) and the names of the failed checks are shown instead.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter

from rag_redteam.corpus import Corpus
from rag_redteam.fixes import fix_for
from rag_redteam.schema import Category

STATUSES = ("defended", "failed", "inconclusive")
WITHHELD = "withheld: high severity or private, see the local results file"


def _redacted(result: dict) -> bool:
    return result["severity"] == "high" or result["private"]


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _fence(text: str) -> str:
    longest = max((len(run) for run in _backtick_runs(text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}text\n{text}\n{ticks}"


def _backtick_runs(text: str) -> list[str]:
    runs, current = [], ""
    for ch in text:
        if ch == "`":
            current += ch
        elif current:
            runs.append(current)
            current = ""
    return runs + ([current] if current else [])


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k events in n trials. With the few trials a free quota
    allows it is wide, and that width is the honest part of the number."""
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate_text(rates: dict) -> str:
    k, n = rates["failed"], rates["trials"]
    lo, hi = wilson(k, n)
    return f"{k} of {n} (95% interval {lo:.0%} to {hi:.0%})"


def category_counts(results: list[dict]) -> dict[str, Counter]:
    counts: dict[str, Counter] = {}
    for r in results:
        counts.setdefault(r["category"], Counter())[r["status"]] += 1
    return counts


def _ordered(counts: dict) -> list[str]:
    return [c.value for c in Category if c.value in counts]


def _summary_table(results: list[dict]) -> list[str]:
    counts = category_counts(results)
    lines = [
        "| Category | Attacks run | Defended | Got through | Inconclusive | Skipped |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    total = Counter()
    for cat in _ordered(counts):
        c = counts[cat]
        ran = sum(c[s] for s in STATUSES)
        lines.append(
            f"| {cat} | {ran} | {c['defended']} | {c['failed']} | {c['inconclusive']} "
            f"| {c['skipped']} |"
        )
        total.update(c)
    ran = sum(total[s] for s in STATUSES)
    lines.append(
        f"| **Total** | **{ran}** | **{total['defended']}** | **{total['failed']}** "
        f"| **{total['inconclusive']}** | **{total['skipped']}** |"
    )
    return lines


def _failure(result: dict, corpus: Corpus) -> list[str]:
    case = next((c for c in corpus.cases if c.id == result["id"]), None)
    redact = _redacted(result)
    failing = [v for v in result["verdicts"] if v["outcome"] == "fail"]
    lines = [
        f"#### {result['id']}: {_cell(result['title'])}",
        f"- Category: `{result['category']}`; severity: **{result['severity']}**",
    ]
    if case is not None:
        lines.append(f"- Attack: {_cell(case.summary)}")
    if result.get("rates"):
        lines.append(f"- Got through in {rate_text(result['rates'])} trials")
    if redact:
        lines.append("- Payload and answer: " + WITHHELD)
    elif result.get("input"):
        lines += ["- Payload:", "", _fence(result["input"]), ""]
    lines.append("- What happened:")
    for v in failing:
        if redact:
            lines.append(f"  - `{v['check']}` failed (details withheld)")
        else:
            by = "" if v["judge"] == "deterministic" else f" ({v['judge']})"
            lines.append(f"  - `{v['check']}`{by}: {_cell(v['reason'])}")
            if v.get("evidence"):
                evidence = _cell(v["evidence"])[:160].replace("`", "'")
                lines.append(f"    - Evidence: `{evidence}`")
    lines.append("- Recommended fix:")
    for check in dict.fromkeys(v["check"] for v in failing):
        lines.append(f"  - {fix_for(check)}")
    return lines + [""]


def _planted_label(value: object) -> str:
    return value if isinstance(value, str) else ("yes" if value else "no")


def _meta_lines(doc: dict, results: list[dict]) -> list[str]:
    meta = doc["meta"]
    health = meta.get("target_health") or {}
    judge = meta.get("llm_judge") or {}
    latencies = [r["exchange"]["latency_ms"] for r in results if r.get("exchange")]
    latencies = [x for x in latencies if x is not None]
    lines = [
        f"- Run: {meta['started_at']} UTC, suite {meta['suite_version']}, Python {meta['python']}",
        f"- Target: generator `{health.get('generator', 'unknown')}`, "
        f"search mode `{health.get('search_mode', 'unknown')}`, "
        f"{health.get('passages', '?')} passages; planted index: "
        f"{_planted_label(meta['planted_index'])}",
        f"- LLM judge: `{judge['judge']}` ({judge['families']})"
        if judge
        else "- LLM judge: not run (LLM-judged checks are inconclusive)",
    ]
    if latencies:
        lines.append(
            f"- Latency per request: median {statistics.median(latencies):.0f} ms, "
            f"max {max(latencies):.0f} ms"
        )
    merged = meta.get("merged_from") or []
    if len(merged) > 1:
        days = sorted({m["started_at"][:10] for m in merged})
        lines.append(
            f"- Merged from {len(merged)} runs on {', '.join(days)} (some cases were re-run "
            "after their test was fixed; the latest run of each case is shown)"
        )
    if (meta.get("repeat") or 1) > 1:
        lines.append(f"- Each case was run {meta['repeat']} times; rates are over those trials")
    skipped = sum(r["status"] == "skipped" for r in results)
    if skipped:
        lines.append(f"- {skipped} case(s) skipped (private payload missing or planted index off)")
    return lines


def render(doc: dict, corpus: Corpus) -> str:
    results = doc["results"]
    counts = category_counts(results)
    out = ["# Red-team report", "", *_meta_lines(doc, results), "", "## Summary", ""]
    out += _summary_table(results) + [""]
    out += [
        "Defended = the check held. Got through = at least one check failed. Inconclusive = the "
        "attack was not properly tested (no usable response, or a judge could not decide); it "
        "is never counted as defended.",
        "",
    ]
    if any(r["category"] == "controls" for r in results):
        out += [
            "For the *controls* category, which holds normal questions, *got through* means the "
            "target refused one or answered from the wrong document: over-refusal, not an attack.",
            "",
        ]
    failures = [r for r in results if r["status"] == "failed"]
    out += ["## Failures", ""]
    if failures:
        for r in sorted(failures, key=lambda r: ("high", "medium", "low").index(r["severity"])):
            out += _failure(r, corpus)
    else:
        out += ["No attack got through in this run.", ""]

    repeated = [r for r in results if r.get("rates")]
    if repeated:
        out += [
            "## Repeated runs",
            "",
            "The target's output varies between runs, so these cases were run several times. "
            "A case is *got through* if any trial failed. The interval is a 95% Wilson interval; "
            "with so few trials it is wide.",
            "",
            "| Id | Severity | Trials | Got through | 95% interval | Defended | Inconclusive |",
            "|---|---|---:|---:|---|---:|---:|",
        ]
        for r in repeated:
            x = r["rates"]
            lo, hi = wilson(x["failed"], x["trials"])
            out.append(
                f"| {r['id']} | {r['severity']} | {x['trials']} | {x['failed']} "
                f"| {lo:.0%} to {hi:.0%} | {x['defended']} | {x['inconclusive']} |"
            )
        out.append("")

    out += ["## Results by category", ""]
    for cat in _ordered(counts):
        out += [f"### {cat}", ""]
        if tm := corpus.threat_models.get(Category(cat)):
            out += [f"_Threat model:_ {' '.join(tm.split())}", ""]
        out += ["| Id | Severity | Status | Note |", "|---|---|---|---|"]
        for r in (r for r in results if r["category"] == cat):
            out.append(f"| {r['id']} | {r['severity']} | {r['status']} | {_note(r)} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _note(result: dict) -> str:
    if result["status"] == "skipped":
        return _cell(result.get("skipped_reason") or "")
    if result["status"] == "inconclusive":
        why = [v for v in result["verdicts"] if v["outcome"] == "inconclusive"]
        return _cell(why[0]["check"] + (": " + why[0]["reason"] if not _redacted(result) else ""))
    return ""


def _rate_cell(result: dict) -> str:
    rates = result.get("rates")
    if not rates:
        return "single run: " + result["status"]
    lo, hi = wilson(rates["failed"], rates["trials"])
    return f"{rates['failed']}/{rates['trials']} ({lo:.0%} to {hi:.0%})"


def _shape(result: dict) -> str:
    """What the target did, in a few words: refused, answered with n citations, or no answer."""
    ex = result.get("exchange")
    if not ex:
        return "not run"
    body = ex.get("body") if isinstance(ex.get("body"), dict) else {}
    if ex.get("status") != 200:
        return f"HTTP {ex.get('status')}"
    if body.get("refused"):
        return "refused"
    return f"answered, {len(body.get('citations') or [])} citation(s)"


def render_comparison(before: dict, after: dict, corpus: Corpus, *, label: str = "") -> str:
    """Before/after table for a fix: category totals and the cases whose status changed."""
    b = {r["id"]: r for r in before["results"]}
    a = {r["id"]: r for r in after["results"]}
    common = [i for i in a if i in b]
    out = [f"# Before and after{': ' + label if label else ''}", ""]
    out += [
        f"- Before: {before['meta']['started_at']} UTC, generator "
        f"`{(before['meta'].get('target_health') or {}).get('generator', 'unknown')}`",
        f"- After: {after['meta']['started_at']} UTC, generator "
        f"`{(after['meta'].get('target_health') or {}).get('generator', 'unknown')}`",
        f"- {len(common)} cases present in both runs",
        "",
        "| Category | Got through before | Got through after | Defended before | Defended after |",
        "|---|---:|---:|---:|---:|",
    ]
    for cat in _ordered(category_counts([a[i] for i in common])):
        ids = [i for i in common if a[i]["category"] == cat]
        row = [
            sum(r[i]["status"] == s for i in ids) for s in ("failed", "defended") for r in (b, a)
        ]
        out.append(f"| {cat} | {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
    out += [
        "",
        "## Cases that changed",
        "",
        "| Id | Severity | Before | After |",
        "|---|---|---|---|",
    ]
    changed = [i for i in common if b[i]["status"] != a[i]["status"]]
    out += [f"| {i} | {a[i]['severity']} | {b[i]['status']} | {a[i]['status']} |" for i in changed]
    if not changed:
        out.append("| (none) | | | |")
    rated = [i for i in common if b[i].get("rates") or a[i].get("rates")]
    if rated:
        out += [
            "",
            "## Failure rate per case (repeated runs)",
            "",
            "Trials in which the attack got through. Intervals are 95% Wilson intervals; with so "
            "few trials they overlap, so read these as a pattern, not a proof.",
            "",
            "| Id | Severity | Before | After |",
            "|---|---|---|---|",
        ]
        out += [
            f"| {i} | {a[i]['severity']} | {_rate_cell(b[i])} | {_rate_cell(a[i])} |" for i in rated
        ]
    shifts = [i for i in common if _shape(b[i]) != _shape(a[i])]
    out += [
        "",
        "## Behaviour changes",
        "",
        "How the target responded, whatever the verdict. A refusal turning into an answer (or "
        "back) is a change in behaviour that a verdict can hide. Each side shows one "
        "trial per case (the first failure if there was one), and the model's output varies "
        "between trials, so a change here is a lead, not a measured effect.",
        "",
        "| Id | Severity | Before | After |",
        "|---|---|---|---|",
    ]
    out += [f"| {i} | {a[i]['severity']} | {_shape(b[i])} | {_shape(a[i])} |" for i in shifts]
    if not shifts:
        out.append("| (none) | | | |")
    regressions = [i for i in changed if a[i]["status"] == "failed"]
    out += [
        "",
        f"Regressions (defended or inconclusive before, got through after): {len(regressions)}",
    ]
    if regressions:
        out += ["", ", ".join(regressions)]
    only = sorted(set(b) ^ set(a))
    if only:
        out += ["", f"Not comparable (in only one run): {', '.join(only)}"]
    return "\n".join(out) + "\n"
