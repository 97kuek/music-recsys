"""Keep the requested five-document structure and local source links usable."""

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def test_documentation_has_five_current_documents_and_bounded_size():
    documents = list((ROOT / "docs").rglob("*.md"))
    assert {p.name for p in documents} == {
        "architecture.md",
        "evaluation.md",
        "ui.md",
        "todo.md",
        "discussion.md",
    }
    for path in documents + [ROOT / "README.md", ROOT / "README.en.md", ROOT / "AGENTS.md"]:
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 200, path


def test_local_document_links_resolve_to_current_files_and_headings():
    documents = list((ROOT / "docs").rglob("*.md")) + [ROOT / "README.md", ROOT / "README.en.md"]
    for document in documents:
        for target in re.findall(r"\]\(([^\s)]+)\)", document.read_text(encoding="utf-8")):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                continue
            name, _, fragment = unquote(target.strip("<>")).partition("#")
            path = (document.parent / name).resolve() if name else document
            if path.is_relative_to(ROOT / "data"):
                continue  # Immutable research artifacts are intentionally not committed.
            assert path.exists(), (document, target)
            if fragment and path.suffix == ".md":
                headings = re.findall(
                    r"^#{1,6}\s+(.+?)\s*$", path.read_text(encoding="utf-8"), re.M
                )
                slugs = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
                assert fragment in slugs, (document, target)
