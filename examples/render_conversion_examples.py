"""Regenerate the checked-in conversion examples from executable adapters."""

from __future__ import annotations

from pathlib import Path

from conversion_matrix import render_document as render_basic
from custom_vocabulary import render_document as render_custom
from rich_content_matrix import render_document as render_rich
from vendor_tool_matrix import render_document as render_vendor_tools


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    rendered = "\n\n---\n\n".join(
        document.rstrip() for document in (render_basic(), render_rich(), render_custom())
    )
    target = root / "docs" / "Conversion-Examples.md"
    target.write_text(rendered + "\n", encoding="utf-8")
    print(target.relative_to(root))

    vendor_tools = root / "docs" / "Vendor-Tool-Conversions.md"
    vendor_tools.write_text(render_vendor_tools().rstrip() + "\n", encoding="utf-8")
    print(vendor_tools.relative_to(root))


if __name__ == "__main__":
    main()
