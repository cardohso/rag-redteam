# Results

Public, redacted reports from the runs described in the main README. Raw results (full requests
and responses, including the high-severity cases) are not committed: `results/raw/` is ignored.

| File | What it is | Made with |
|---|---|---|
| `report.md` | The unmodified target, 26 cases, the latest run of each case | `report results/raw/before.judged.json` |
| `before-after.md` | The same 26 cases on the unmodified target vs the branch with both fixes, one run each | `report <after> --compare <before>` |
| `before-after-repeat.md` | The three planted cases, five trials each, same two code versions | `report <after> --compare <before>` on `--repeat 5` runs |
| `grounding-v2.md` | The eight second-group grounding cases on the unmodified target, one run each, re-judged after the judge fix | `report results/raw/grd2-main.rejudged.json` |
| `batch3.md` | The 20 third-batch cases on the unmodified target, one run each (clean and planted runs merged) | `report <merged>` |
| `batch3-before-after.md` | The four third-batch cases that got through, re-run once on the branch with both fixes | `report <after> --compare <before>` |
| `ipi008-repeat.md` | IPI-008, five trials each on the unmodified target and the branch with both fixes | `report <after> --compare <before>` on `--repeat 5` runs |
| `controls-before-after.md` | The six over-refusal controls, three trials each, same two code versions | `report <after> --compare <before>` on `--category controls --repeat 3` runs |
| `final-run-all60.md` | All 60 cases once each: the target's current `main` vs `main` plus Fix 2 | `report <after> --compare <before>` on two `merge`d runs |
| `final-run-indirect-x5.md` | The 10 indirect cases, five trials each, same two code versions | `report <after> --compare <before>` on `--repeat 5 --planted` runs |
| `final-run-controls-x3.md` | The 10 controls, three trials each, same two code versions | `report <after> --compare <before>` on `--category controls --repeat 3` runs |
| `pin-indirect-x5.md` | The corpus pin (a target branch) vs the unmodified `main`, 10 indirect cases, five trials each | `report <pin> --compare <main>` on `--repeat 5 --planted` runs |
| `pin-controls-x3.md` | The corpus pin vs `main`, 10 controls, three trials each | `report <pin> --compare <main>` on `--category controls --repeat 3` runs |

`final-run-indirect-x5.md` and `final-run-all60.md` were regenerated on 31 October 2026 after a judge
error was corrected (README, "Correction to v0.2.0").

High-severity and private cases appear by id, status and category only. Re-running needs the
steps in the main README, and the four high-severity cases need `attacks/private/payloads.yaml`.
