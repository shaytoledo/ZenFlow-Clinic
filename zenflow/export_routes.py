"""Write the route authorization table to `docs/AUTHZ.md` (Phase 9.1).

    python -m zenflow.export_routes [--check]

`web/authz.py` is the source; the document is generated so it cannot drift from what the tests
enforce. A security test runs `--check`, so changing the table means exporting it deliberately.
"""

from __future__ import annotations

import sys
from pathlib import Path

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "AUTHZ.md"
START = "<!-- generated: python -m zenflow.export_routes -->"
END = "<!-- /generated -->"

_LEVELS = {
    "public": "public",
    "session": "signed in",
    "active": "signed in + activated",
    "machine": "API key or session",
    "signature": "provider signature",
}
_SCOPES = {
    "none": "—",
    "own": "own rows only",
    "appointment": "own appointment",
    "conversation": "own conversation",
    "patient": "own patient",
    "self": "own account",
}


def table() -> str:
    from web.authz import ROUTES

    lines = [
        "| Method | Route | Who may call it | Object-level check | Proven by |",
        "|---|---|---|---|---|",
    ]
    for (method, path), access in sorted(ROUTES.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        proof = f"`{access.covered_by}`" if access.covered_by else "—"
        note = f" <br><sub>{access.note}</sub>" if access.note else ""
        lines.append(
            f"| `{method}` | `{path}`{note} | {_LEVELS[access.auth]} | "
            f"{_SCOPES[access.scope]} | {proof} |"
        )
    return "\n".join(lines)


def document() -> str:
    body = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else _SKELETON
    head, _, rest = body.partition(START)
    _old, _, tail = rest.partition(END)
    return f"{head}{START}\n\n{table()}\n\n{END}{tail}"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = document()
    if "--check" in argv:
        current = DOC_PATH.read_text(encoding="utf-8") if DOC_PATH.exists() else ""
        if current == text:
            print(f"{DOC_PATH.name} is up to date")
            return 0
        print(f"{DOC_PATH.name} is stale — run: python -m zenflow.export_routes", file=sys.stderr)
        return 1
    DOC_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {DOC_PATH}")
    return 0


_SKELETON = f"""# Route authorization (Phase 9.1)

{START}

{END}
"""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
