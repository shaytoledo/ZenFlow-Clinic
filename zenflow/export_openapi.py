"""Write the booking API's OpenAPI schema to `docs/api/booking-v1.openapi.json` (Phase 7.3).

    python -m zenflow.export_openapi [--check]

The file is what clients are generated from, so it is committed. A contract test compares it with
what the app serves: a change to the API has to be exported deliberately.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "api" / "booking-v1.openapi.json"


def schema_text() -> str:
    from web.routers.api.v1 import booking_openapi

    return json.dumps(booking_openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = schema_text()
    if "--check" in argv:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        if current == text:
            print(f"{SCHEMA_PATH.name} is up to date")
            return 0
        print(
            f"{SCHEMA_PATH.name} is stale — run: python -m zenflow.export_openapi", file=sys.stderr
        )
        return 1
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
