# Results

Public, redacted reports from the runs described in the main README. Raw results (full requests
and responses, including the high-severity cases) are not committed: `results/raw/` is ignored.

| File | What it is | Made with |
|---|---|---|
| `report.md` | The unmodified target, 26 cases, the latest run of each case | `report results/raw/before.judged.json` |
| `before-after.md` | The same 26 cases on the unmodified target vs the branch with both fixes, one run each | `report <after> --compare <before>` |
| `before-after-repeat.md` | The three planted cases, five trials each, same two code versions | `report <after> --compare <before>` on `--repeat 5` runs |
| `grounding-v2.md` | The eight second-group grounding cases on the unmodified target, one run each, re-judged after the judge fix | `report results/raw/grd2-main.rejudged.json` |
| `controls-before-after.md` | The six over-refusal controls, three trials each, same two code versions | `report <after> --compare <before>` on `--category controls --repeat 3` runs |

High-severity and private cases appear by id, status and category only. Re-running needs the
steps in the main README, and the four high-severity cases need `attacks/private/payloads.yaml`.
