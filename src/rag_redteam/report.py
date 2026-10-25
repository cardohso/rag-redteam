"""Markdown report from a judged results file.

Every number comes from the results JSON. The report is public, so the redaction rule lives
here: a result that is high severity or private never has its payload, the target's answer or an
LLM judge's reason in the report, because those can quote the attack. Its public summary (from
the corpus) and the names of the failed checks are shown instead.
"""

from __future__ import annotations

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
        f"{'yes' if meta['planted_index'] else 'no'}",
        f"- LLM judge: `{judge['judge']}` ({judge['families']})"
        if judge
        else "- LLM judge: not run (LLM-judged checks are inconclusive)",
    ]
    if latencies:
        lines.append(
            f"- Latency per request: median {statistics.median(latencies):.0f} ms, "
            f"max {max(latencies):.0f} ms"
        )
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
    failures = [r for r in results if r["status"] == "failed"]
    out += ["## Failures", ""]
    if failures:
        for r in sorted(failures, key=lambda r: ("high", "medium", "low").index(r["severity"])):
            out += _failure(r, corpus)
    else:
        out += ["No attack got through in this run.", ""]

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
