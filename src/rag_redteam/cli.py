"""Command line: uv run python -m rag_redteam run --url http://localhost:7860"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from rag_redteam.client import TargetClient, is_local
from rag_redteam.corpus import load_corpus
from rag_redteam.profile import DEFAULT_PROFILE, load_profile, read_env_file
from rag_redteam.runner import CaseResult, run_suite, select, write_results
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
    return ap


def _progress(i: int, total: int, r: CaseResult) -> None:
    print(f"[{i:>3}/{total}] {r.id:<9} {r.status.value}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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
