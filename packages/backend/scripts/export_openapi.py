#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.main import app

if __name__ == "__main__":
    output_path = Path(__file__).parent.parent.parent.parent / "dist/packages/backend/openapi/openapi.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    openapi_spec = app.openapi()
    with open(output_path, "w") as f:
        json.dump(openapi_spec, f, indent=2)

    print(f"OpenAPI spec exported to {output_path}")
