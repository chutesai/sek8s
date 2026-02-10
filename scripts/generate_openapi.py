"""Generate the system manager OpenAPI spec and write it to docs/system_manager_openapi.json."""

import json
import os
from pathlib import Path


def _repo_root() -> Path:
    """Return the repository root (directory containing pyproject.toml)."""
    root = Path(__file__).resolve().parent.parent
    if not (root / "pyproject.toml").exists():
        raise RuntimeError(f"Repository root not found (expected pyproject.toml in {root})")
    return root


def main() -> None:
    os.environ.setdefault("VALIDATOR_BASE_URL", "https://api.example.com")

    from sek8s.services.manager import create_app

    app = create_app()
    schema = app.openapi()

    root = _repo_root()
    out_path = root / "docs" / "system_manager_openapi.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as f:
        json.dump(schema, f, indent=2)

    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
