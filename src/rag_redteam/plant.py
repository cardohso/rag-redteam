"""Make a copy of the target's data folder, optionally with the planted passages added.

The target reads its corpus from RAIL_RAG_DATA_DIR, so a copy lets indirect-injection cases run
against a poisoned corpus without touching the real one. The tool only ever writes inside `out`
and refuses to write over the source or into a folder it did not create.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rag_redteam.corpus import Corpus

MARKER = ".rag-redteam-copy"


class PlantError(ValueError):
    pass


def _check_destination(source: Path, out: Path) -> None:
    source, out = source.resolve(), out.resolve()
    if out == source or source in out.parents or out in source.parents:
        raise PlantError(f"{out} overlaps the source {source}; refusing to write there")
    if out.exists() and any(out.iterdir()) and not (out / MARKER).exists():
        raise PlantError(f"{out} exists and was not made by this tool; refusing to overwrite")


def build_copy(
    source: Path,
    out: Path,
    corpus: Corpus,
    *,
    plant: bool = True,
    copy_index: bool = False,
) -> list[str]:
    """Copy sources.yaml and processed/ (and the index folder if asked) from `source` to `out`.
    With `plant`, append every planted passage to its host document. Returns the planted ids."""
    _check_destination(source, out)
    if plant:
        missing = [p.id for p in corpus.planted.values() if p.text is None]
        if missing:
            raise PlantError(f"no text for planted passages {missing} (attacks/private/?)")
    if out.exists():
        shutil.rmtree(out)  # safe: it is empty or carries our marker
    out.mkdir(parents=True)
    (out / MARKER).write_text("made by rag_redteam.plant; safe to delete\n", encoding="utf-8")
    shutil.copy2(source / "sources.yaml", out / "sources.yaml")
    shutil.copytree(source / "processed", out / "processed")
    if copy_index:
        shutil.copytree(source / "index", out / "index")

    planted_ids: list[str] = []
    if plant:
        for passage in corpus.planted.values():
            path = out / "processed" / f"{passage.doc_id}.jsonl"
            if not path.exists():
                raise PlantError(f"{passage.id}: host document {passage.doc_id!r} not in corpus")
            first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            line = {
                "doc_id": passage.doc_id,
                "celex": first["celex"],
                "section": passage.section,
                "heading": passage.heading,
                "text": passage.text,
            }
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            planted_ids.append(passage.id)
    return planted_ids
