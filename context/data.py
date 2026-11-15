"""Load the packaged governance data once for the context service."""

from pathlib import Path
from typing import Any

import yaml

DATA_DIRECTORY = Path(__file__).parent
SOURCE_FILES = (
    "data_dictionary.yaml",
    "plans.yaml",
    "claim_rules.yaml",
    "glossary.yaml",
    "reference_data.yaml",
    "claims_manual.md",
    "policy_wording.md",
)


def _load_sources() -> dict[str, dict[str, Any]]:
    loaded: dict[str, dict[str, Any]] = {}
    for filename in SOURCE_FILES:
        path = DATA_DIRECTORY / filename
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".yaml":
            loaded[filename] = yaml.safe_load(text)
            continue

        frontmatter, content = text.split("---", 2)[1:]
        loaded[filename] = {
            "metadata": yaml.safe_load(frontmatter),
            "content": content.lstrip(),
        }
    return loaded


SOURCES = _load_sources()


def provenance(filename: str) -> dict[str, str]:
    source = SOURCES[filename].get("source") or SOURCES[filename]["metadata"]["source"]
    return {
        "file": filename,
        "system": source["system"],
        "owner": source["owner"],
        "reviewed": source["reviewed"],
    }
