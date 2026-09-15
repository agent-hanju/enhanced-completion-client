"""Regenerate the checked-in conversion examples from executable adapters."""

from __future__ import annotations

from pathlib import Path

from conversion_matrix import render_document as render_basic
from custom_vocabulary import render_document as render_custom
from rich_content_matrix import render_document as render_rich


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    rendered = "\n\n---\n\n".join(
        document.rstrip() for document in (render_basic(), render_rich(), render_custom())
    )
    target = root / "docs" / "Conversion-Examples.md"
    target.write_text(rendered + "\n", encoding="utf-8")
    print(target.relative_to(root))


if __name__ == "__main__":
    main()
