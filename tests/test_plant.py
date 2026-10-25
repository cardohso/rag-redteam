import json

import pytest

from rag_redteam.corpus import Corpus
from rag_redteam.plant import MARKER, PlantError, build_copy
from rag_redteam.schema import PlantedPassage


def passage(id_="PL-001", doc="tsi-prm", text="planted text") -> PlantedPassage:
    return PlantedPassage(
        id=id_, doc_id=doc, section=f"Annex §1-{id_.replace('-', '')}", heading="h",
        summary="s", text=text,
    )  # fmt: skip


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "data"
    (src / "processed").mkdir(parents=True)
    (src / "index" / "qdrant").mkdir(parents=True)
    (src / "sources.yaml").write_text("documents: []\n")
    row = {"doc_id": "tsi-prm", "celex": "02014R1300", "section": "Article 1", "heading": "H",
           "text": "real text"}  # fmt: skip
    (src / "processed" / "tsi-prm.jsonl").write_text(json.dumps(row) + "\n")
    (src / "index" / "qdrant" / "meta.json").write_text("{}")
    return src


def corpus(*passages) -> Corpus:
    return Corpus(threat_models={}, cases=[], planted={p.id: p for p in passages})


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_appends_planted_passage_to_host_document(source, tmp_path):
    out = tmp_path / "work" / "planted"
    ids = build_copy(source, out, corpus(passage()))
    rows = lines(out / "processed" / "tsi-prm.jsonl")
    assert ids == ["PL-001"] and len(rows) == 2
    assert rows[1]["section"] == "Annex §1-PL001" and rows[1]["celex"] == "02014R1300"
    assert rows[1]["text"] == "planted text" and (out / MARKER).exists()


def test_source_is_never_modified(source, tmp_path):
    before = (source / "processed" / "tsi-prm.jsonl").read_text()
    build_copy(source, tmp_path / "out", corpus(passage()))
    assert (source / "processed" / "tsi-prm.jsonl").read_text() == before
    assert not (source / MARKER).exists()


def test_clean_copy_has_no_planted_passages_and_can_copy_the_index(source, tmp_path):
    out = tmp_path / "clean"
    ids = build_copy(source, out, corpus(passage()), plant=False, copy_index=True)
    assert ids == [] and len(lines(out / "processed" / "tsi-prm.jsonl")) == 1
    assert (out / "index" / "qdrant" / "meta.json").exists()


@pytest.mark.parametrize("where", ["same", "inside", "parent"])
def test_refuses_to_overlap_the_source(source, tmp_path, where):
    out = {"same": source, "inside": source / "copy", "parent": tmp_path}[where]
    with pytest.raises(PlantError, match="overlaps"):
        build_copy(source, out, corpus(passage()))


def test_refuses_to_overwrite_a_folder_it_did_not_make(source, tmp_path):
    out = tmp_path / "precious"
    out.mkdir()
    (out / "notes.txt").write_text("keep me")
    with pytest.raises(PlantError, match="not made by this tool"):
        build_copy(source, out, corpus(passage()))
    assert (out / "notes.txt").read_text() == "keep me"


def test_rebuilds_its_own_folder(source, tmp_path):
    out = tmp_path / "planted"
    build_copy(source, out, corpus(passage()))
    build_copy(source, out, corpus(passage()))
    assert len(lines(out / "processed" / "tsi-prm.jsonl")) == 2  # not appended twice


def test_missing_private_text_is_an_error(source, tmp_path):
    private = PlantedPassage(id="PL-002", doc_id="tsi-prm", section="s", heading="h",
                             summary="s", private=True)  # fmt: skip
    with pytest.raises(PlantError, match="PL-002"):
        build_copy(source, tmp_path / "out", corpus(private))
    assert not (tmp_path / "out").exists()


def test_unknown_host_document(source, tmp_path):
    with pytest.raises(PlantError, match="not in corpus"):
        build_copy(source, tmp_path / "out", corpus(passage(doc="tsi-nope")))
