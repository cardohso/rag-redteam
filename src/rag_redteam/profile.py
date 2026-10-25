"""Target profile: the facts about the system under test that the judges need."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = REPO_ROOT / "targets" / "rail-rag.yaml"


class TargetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ask_path: str = "/ask"
    health_path: str = "/health"
    allowed_domains: list[str] = []
    planted_marker: str = r"-PL\d{3}"
    prompt_fingerprints: list[str] = []
    secret_env: list[str] = []


def load_profile(path: Path = DEFAULT_PROFILE) -> TargetProfile:
    return TargetProfile(**yaml.safe_load(path.read_text(encoding="utf-8")))


def read_env_file(path: Path, names: list[str]) -> dict[str, str]:
    """Values for `names` from a KEY=VALUE file (the target's .env). Never printed or stored."""
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        value = value.strip().strip("'\"")
        if sep and key in names and value:
            found[key] = value
    return found
