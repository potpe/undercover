import re
from pathlib import Path
from typing import Any

import yaml

CONTEXT_DIR = Path(__file__).resolve().parent.parent / "context"
DOCUMENT_FILES = {
    "policy_wording": "policy_wording.md",
    "claims_manual": "claims_manual.md",
}
FRONT_MATTER = re.compile(
    r"\A---\s*\n(?P<metadata>.*?)\n---\s*\n(?P<content>.*)\Z", re.DOTALL
)


def load_context_documents() -> list[dict[str, Any]]:
    """Load complete context documents and their front matter for database seeding."""
    documents = []
    for name, filename in DOCUMENT_FILES.items():
        text = (CONTEXT_DIR / filename).read_text(encoding="utf-8")
        match = FRONT_MATTER.match(text)
        if match is None:
            raise ValueError(f"Context document {filename} has no front matter")
        metadata = yaml.safe_load(match.group("metadata"))
        documents.append(
            {
                "document": name,
                "version": metadata["version"],
                "source": metadata["source"],
                "content": match.group("content"),
            }
        )
    return documents
