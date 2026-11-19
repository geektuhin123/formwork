"""Write docs/openapi.json from the FastAPI app: uv run python scripts/export_openapi.py"""

from __future__ import annotations

import json
from pathlib import Path

from formwork_api.app import create_app
from formwork_core.config import Settings

if __name__ == "__main__":
    app = create_app(Settings(env="dev", queue="inline"))
    out = Path(__file__).parents[1] / "docs" / "openapi.json"
    out.write_text(json.dumps(app.openapi(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {out} ({len(app.openapi()['paths'])} paths)")
