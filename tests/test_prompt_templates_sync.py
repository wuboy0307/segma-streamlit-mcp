"""The catalogue segma-mcp serves still says exactly what the app says.

The app's `PROMPT_TEMPLATES` is the source; `segma-mcp/prompts/templates.yaml`
is generated from it so that agents get the same templates people get. Two
copies of the same strings is a drift risk, and the drift is silent: the app
keeps showing the edited template while every MCP client keeps handing out the
old one, and nothing errors.

This test is the guard. It runs the extractor in --check mode, which rebuilds
the YAML in memory and compares every (category, label, text) triple in both
languages against what is committed.

    .venv/bin/python -m pytest tests/test_prompt_templates_sync.py

Editing a template? Change streamlit_app.py, then re-run
`tools/extract_prompt_templates.py` and commit the regenerated YAML with it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXTRACTOR = REPO / "tools" / "extract_prompt_templates.py"
YAML = REPO.parent / "segma-mcp" / "prompts" / "templates.yaml"


def test_extractor_exists():
    assert EXTRACTOR.exists(), f"{EXTRACTOR} is missing"


def test_generated_catalogue_is_committed():
    assert YAML.exists(), (
        f"{YAML} is missing — run tools/extract_prompt_templates.py and commit it. "
        "Without it the MCP server has no templates to serve."
    )


def test_catalogue_matches_the_app():
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--check"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        "segma-mcp/prompts/templates.yaml no longer matches streamlit_app.py's "
        "PROMPT_TEMPLATES. Re-run tools/extract_prompt_templates.py and commit "
        f"the result.\n\n{result.stdout}{result.stderr}"
    )
