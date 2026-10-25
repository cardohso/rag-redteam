"""Command line.

uv run python -m rag_redteam run --url http://localhost:7860
uv run python -m rag_redteam judge results/raw/run-<timestamp>.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from rag_redteam.client import TargetClient, is_local
from rag_redteam.corpus import load_corpus
from rag_redteam.judges.family import SameFamilyError
from rag_redteam.judges.llm import DEFAULT_JUDGE_MODEL, GroqJudge, JudgeCache
from rag_redteam.judging import judge_results
from rag_redteam.plant import PlantError, build_copy
from rag_redteam.profile import DEFAULT_PROFILE, REPO_ROOT, load_profile, read_env_file
from rag_redteam.report import render, render_comparison
from rag_redteam.runner import CaseResult, merge_results, run_suite, select, write_results
from rag_redteam.schema import Category, Severity

LIVE_CASE_CAP = 5  # a smoke test, not a run: the live demo has a daily LLM budget


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rag_redteam")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run attack cases against a target and write results JSON")
    run.add_argument("--url", default=os.environ.get("RAG_TARGET_URL", "http://localhost:7860"))
    run.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    run.add_argument("--out", type=Path, help="default: results/raw/run-<timestamp>.json")
    run.add_argument("--category", action="append", choices=[c.value for c in Category])
    run.add_argument("--severity", action="append", choices=[s.value for s in Severity])
    run.add_argument("--id", action="append", help="run only this case id (repeatable)")
    run.add_argument("--limit", type=int, help="run at most this many cases")
    run.add_argument("--rpm", type=float, default=10.0, help="requests per minute (default 10)")
    run.add_argument("--retries", type=int, default=3)
    run.add_argument(
        "--planted", action="store_true", help="the target's index has the planted passages"
    )
    run.add_argument(
        "--target-env",
        type=Path,
        help="the target's .env: its secret values are checked for in responses, never printed",
    )
    run.add_argument(
        "--allow-live",
        action="store_true",
        help=f"allow a non-local target (capped at {LIVE_CASE_CAP} cases)",
    )
    judge = sub.add_parser("judge", help="run the LLM judge on a results file's deferred checks")
    judge.add_argument("results", type=Path)
    judge.add_argument("--out", type=Path, help="default: <results>.judged.json")
    judge.add_argument("--model", default=os.environ.get("JUDGE_MODEL", DEFAULT_JUDGE_MODEL))
    judge.add_argument("--rpm", type=float, default=20.0, help="judge requests per minute")
    judge.add_argument("--cache", type=Path, default=REPO_ROOT / ".cache" / "judge.json")
    judge.add_argument("--rejudge", action="store_true", help="redo earlier LLM verdicts too")
    judge.add_argument(
        "--allow-same-family",
        action="store_true",
        help="let a judge from the generator's model family grade it (recorded in the results)",
    )
    report = sub.add_parser("report", help="write the Markdown report from a judged results file")
    report.add_argument("results", type=Path)
    report.add_argument("--out", type=Path, default=Path("results/report.md"))
    report.add_argument(
        "--compare", type=Path, metavar="BEFORE", help="write a before/after comparison instead"
    )
    report.add_argument("--label", default="", help="name of the fix, for --compare")
    plant = sub.add_parser("plant", help="copy the target's data folder, with planted passages")
    plant.add_argument("--source", type=Path, required=True, help="the target's data folder")
    plant.add_argument("--out", type=Path, required=True)
    plant.add_argument(
        "--clean",
        action="store_true",
        help="no planted passages; copy the built index too (stand-in for the real one)",
    )
    merge = sub.add_parser("merge", help="combine results files from several runs into one")
    merge.add_argument("results", type=Path, nargs="+")
    merge.add_argument("--out", type=Path, required=True)
    merge.add_argument(
        "--latest-wins",
        action="store_true",
        help="a case in a later file replaces the same case in an earlier one (a re-run)",
    )
    return ap


def _progress(i: int, total: int, r: CaseResult) -> None:
    print(f"[{i:>3}/{total}] {r.id:<9} {r.status.value}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "judge":
        return judge_main(args)
    if args.command == "report":
        return report_main(args)
    if args.command == "plant":
        return plant_main(args)
    if args.command == "merge":
        return merge_main(args)
    return run_main(args)


def plant_main(args: argparse.Namespace) -> int:
    try:
        ids = build_copy(
            args.source, args.out, load_corpus(), plant=not args.clean, copy_index=args.clean
        )
    except PlantError as exc:
        print(f"plant: {exc}", file=sys.stderr)
        return 2
    print(f"{args.out}: copied, {len(ids)} planted passage(s) {ids}", file=sys.stderr)
    return 0


def merge_main(args: argparse.Namespace) -> int:
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in args.results]
    merged = merge_results(docs, latest_wins=args.latest_wins)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(merged['results'])} results -> {args.out}", file=sys.stderr)
    return 0


def report_main(args: argparse.Namespace) -> int:
    doc = json.loads(args.results.read_text(encoding="utf-8"))
    corpus = load_corpus()
    if args.compare:
        before = json.loads(args.compare.read_text(encoding="utf-8"))
        text = render_comparison(before, doc, corpus, label=args.label)
    else:
        text = render(doc, corpus)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"report -> {args.out}", file=sys.stderr)
    return 0


def _groq_key() -> str:
    if key := os.environ.get("GROQ_API_KEY"):
        return key
    env = REPO_ROOT / ".env"
    return read_env_file(env, ["GROQ_API_KEY"]).get("GROQ_API_KEY", "") if env.exists() else ""


def judge_main(args: argparse.Namespace) -> int:
    doc = json.loads(args.results.read_text(encoding="utf-8"))
    key = _groq_key()
    if not key:
        print("no GROQ_API_KEY in the environment or .env", file=sys.stderr)
        return 2
    judge = GroqJudge(key, args.model, rpm=args.rpm)
    try:
        judge_results(
            doc, load_corpus(), judge,
            cache=JudgeCache(args.cache), allow_same_family=args.allow_same_family,
            rejudge=args.rejudge, progress=lambda i, s: print(f"{i:<9} {s}", file=sys.stderr),
        )  # fmt: skip
    except SameFamilyError as exc:
        print(f"refusing to judge: {exc}", file=sys.stderr)
        return 2
    finally:
        judge.close()
    out = args.out or args.results.with_suffix(".judged.json")
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    meta = doc["meta"]["llm_judge"]
    print(
        f"{meta['verdicts']} LLM verdicts by {meta['judge']} "
        f"(cache {meta['cache_hits']} hits) -> {out}",
        file=sys.stderr,
    )
    return 0


def run_main(args: argparse.Namespace) -> int:
    if not is_local(args.url):
        if not args.allow_live:
            print(
                f"refusing to run against {args.url}: not a local address. The suite runs against "
                "a local copy of the target; pass --allow-live only for a short smoke test.",
                file=sys.stderr,
            )
            return 2
        if args.limit is None or args.limit > LIVE_CASE_CAP:
            print(f"--allow-live needs --limit {LIVE_CASE_CAP} or less", file=sys.stderr)
            return 2

    corpus = load_corpus()
    profile = load_profile(args.profile)
    cases = select(
        corpus.cases,
        categories={Category(c) for c in args.category or []},
        ids=set(args.id or []),
        severities={Severity(s) for s in args.severity or []},
        limit=args.limit,
    )
    if not cases:
        print("no cases match the filters", file=sys.stderr)
        return 2

    secrets = {n: os.environ[n] for n in profile.secret_env if os.environ.get(n)}
    if args.target_env:
        secrets |= read_env_file(args.target_env, profile.secret_env)

    client = TargetClient(
        args.url, ask_path=profile.ask_path, health_path=profile.health_path,
        rpm=args.rpm, max_retries=args.retries,
    )  # fmt: skip
    try:
        health = client.health()
        if health is None:
            print(f"target not healthy at {args.url}{profile.health_path}", file=sys.stderr)
            return 2
        results = run_suite(
            cases, client, profile,
            planted_index=args.planted, known_secrets=secrets, progress=_progress,
        )  # fmt: skip
    finally:
        client.close()

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = args.out or Path("results/raw") / f"run-{stamp}.json"
    write_results(
        out, results, target_url=args.url, health=health, corpus=corpus, planted_index=args.planted
    )
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status.value] = counts.get(r.status.value, 0) + 1
    print(f"{len(results)} cases -> {out}  {counts}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
