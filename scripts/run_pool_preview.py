"""Run a full source-pool collection and write an unsent local preview."""
from __future__ import annotations

import json
from pathlib import Path

from app.pipeline import run_full


def main():
    root = Path(__file__).resolve().parents[1]
    sources = json.loads((root / "config/sources.json").read_text(encoding="utf-8"))
    output = root / "output/source-pool-preview-2026-09-25"
    try:
        result = run_full(sources, output_dir=str(output), preview_only=True, max_articles=2)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # run_full saves full-collection-status.json and collection-preflight.json
        # before any AI call; errors are surfaced without invoking SMTP.
        print(json.dumps({"error_type": type(exc).__name__, "error": str(exc), "preflight": str(output / "collection-preflight.json"), "sent": False}, ensure_ascii=False, indent=2))
        raise


if __name__ == "__main__":
    main()
