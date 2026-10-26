"""The README and docs must be finished and their relative links must resolve."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), ROOT / "results" / "README.md"]
LINK = re.compile(r"\]\(([^)\s]+)\)")


def test_no_unfinished_markers():
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        assert "<!--" not in text, f"{doc.name} still has a placeholder comment"
        assert "TODO" not in text, f"{doc.name} has a TODO"


def test_relative_links_resolve():
    missing = []
    for doc in DOCS:
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#", 1)[0]
            if path and not (doc.parent / path).exists():
                missing.append(f"{doc.relative_to(ROOT)} -> {target}")
    assert not missing, missing


def test_readme_has_the_required_sections():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for heading in (
        "30-second summary", "Threat model", "Run it locally", "Results",
        "What was fixed, and what was not", "Limitations", "Responsible disclosure",
    ):  # fmt: skip
        assert f"## {heading}" in text, heading
