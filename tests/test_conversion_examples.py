"""Keep the recorded conversion matrix synchronized with executable adapters."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_recorded_conversion_matrix_is_current() -> None:
    root = Path(__file__).resolve().parents[1]
    outputs: list[str] = []
    for script in ("conversion_matrix.py", "rich_content_matrix.py", "custom_vocabulary.py"):
        completed = subprocess.run(
            [sys.executable, f"examples/{script}"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(completed.stdout.rstrip())

    recorded = (root / "docs" / "Conversion-Examples.md").read_text(encoding="utf-8")
    assert "\n\n---\n\n".join(outputs) == recorded.rstrip()
