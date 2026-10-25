"""Load the attack corpus: attacks/<category>.yaml, attacks/planted.yaml and, when present
locally, the private payloads in attacks/private/payloads.yaml (gitignored)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from rag_redteam.schema import AttackCase, Category, PlantedPassage

REPO_ROOT = Path(__file__).resolve().parents[2]
ATTACKS_DIR = REPO_ROOT / "attacks"
PRIVATE_FILE = ATTACKS_DIR / "private" / "payloads.yaml"


class CorpusError(ValueError):
    pass


@dataclass
class Corpus:
    threat_models: dict[Category, str]
    cases: list[AttackCase]
    planted: dict[str, PlantedPassage]
    private_loaded: bool = False
    missing_private: list[str] = field(default_factory=list)  # private ids with no payload

    def by_category(self, category: Category) -> list[AttackCase]:
        return [c for c in self.cases if c.category is category]


def _read(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise CorpusError(f"{path.name}: expected a mapping at the top level")
    return data


def load_corpus(root: Path = ATTACKS_DIR, private_file: Path | None = None) -> Corpus:
    private_file = private_file if private_file is not None else root / "private" / "payloads.yaml"
    private = _read(private_file) if private_file.exists() else {}
    private_cases: dict = private.get("cases") or {}
    private_planted: dict = private.get("planted") or {}

    planted: dict[str, PlantedPassage] = {}
    planted_path = root / "planted.yaml"
    for raw in (_read(planted_path).get("passages") or []) if planted_path.exists() else []:
        if raw.get("private") and "text" in raw:
            raise CorpusError(f"{raw.get('id')}: private planted text must not be in planted.yaml")
        if raw.get("private") and raw.get("id") in private_planted:
            raw = {**raw, "text": private_planted[raw["id"]]}
        passage = PlantedPassage(**raw)
        if passage.id in planted:
            raise CorpusError(f"duplicate planted id {passage.id}")
        planted[passage.id] = passage

    threat_models: dict[Category, str] = {}
    cases: list[AttackCase] = []
    for category in Category:
        path = root / f"{category.value}.yaml"
        if not path.exists():
            continue
        data = _read(path)
        if data.get("category") != category.value:
            raise CorpusError(f"{path.name}: header category must be {category.value!r}")
        threat_models[category] = str(data.get("threat_model") or "").strip()
        for raw in data.get("cases") or []:
            if "category" in raw:
                raise CorpusError(f"{raw.get('id')}: category comes from the file header")
            if raw.get("private") and ("input" in raw or "request" in raw):
                # The guard that keeps high-severity payloads out of the public repo.
                raise CorpusError(f"{raw.get('id')}: private payload must not be in {path.name}")
            if raw.get("private") and raw.get("id") in private_cases:
                raw = {**raw, "input": private_cases[raw["id"]]}
            cases.append(AttackCase(**raw, category=category))

    ids = [c.id for c in cases]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise CorpusError(f"duplicate case ids: {dupes}")
    for case in cases:
        if unknown := [p for p in case.planted if p not in planted]:
            raise CorpusError(f"{case.id}: unknown planted passages {unknown}")

    return Corpus(
        threat_models=threat_models,
        cases=cases,
        planted=planted,
        private_loaded=private_file.exists(),
        missing_private=[c.id for c in cases if not c.runnable]
        + [p.id for p in planted.values() if p.text is None],
    )
