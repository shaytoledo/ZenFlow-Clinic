"""Phase 12.2.4 — the therapist registry is the database, and registration is safe across processes.

Before: three module-level copies in `bot.config`, refreshed in place by whichever process wrote,
and `t{n}` ids picked under a lock that only one process could see. Several containers (AWS) would
have routed patients to stale therapists and could have handed two sign-ups the same id.
"""

from __future__ import annotations

import pytest

import bot.db as dbmod
from bot import therapists
from web.repositories import therapist_repo

pytestmark = pytest.mark.integration


def test_ids_are_allocated_in_sequence(db) -> None:
    assert therapist_repo.insert_new(name="A") == "t1"
    assert therapist_repo.insert_new(name="B") == "t2"


def test_two_processes_picking_the_same_id_both_succeed(db, monkeypatch) -> None:
    """Simulate the race: the other process inserted t1 after this one computed "t1 is free"."""
    assert therapist_repo.insert_new(name="Other process") == "t1"
    real_next_id = therapist_repo.next_id
    stale = iter(["t1"])  # the value this process computed before the other one's INSERT
    monkeypatch.setattr(therapist_repo, "next_id", lambda: next(stale, None) or real_next_id())

    assert therapist_repo.insert_new(name="This process") == "t2"
    names = {r["id"]: r["name"] for r in dbmod.get_db().execute("SELECT id, name FROM therapists")}
    assert names == {"t1": "Other process", "t2": "This process"}


def test_a_refusal_that_is_not_an_id_clash_is_not_retried(db) -> None:
    with pytest.raises(dbmod.IntegrityError):
        therapist_repo.insert_new(name=None)  # type: ignore[arg-type]  # NOT NULL
    assert dbmod.get_db().execute("SELECT COUNT(*) FROM therapists").fetchone()[0] == 0


def test_the_choice_list_is_in_registration_order(db) -> None:
    for n in range(1, 11):
        therapist_repo.insert_new(name=f"Dr {n}", active=True)
    assert [t["id"] for t in therapists.active()][-2:] == ["t9", "t10"]
    assert all(t["active"] is True for t in therapists.active())


def test_a_web_sign_up_activated_through_the_bot_is_routable_at_once(db) -> None:
    from bot.therapist_bot.handlers import _register_therapist_to_db
    from web.deps import _register_web_therapist

    entry = _register_web_therapist(name="Dr Web", email="web@example.test", password="pw-12345678")
    assert therapists.get(entry["id"]) is not None
    assert therapists.get_active(entry["id"]) is None, "inactive until the bot activation"

    activated = _register_therapist_to_db("Dr Web", telegram_id=555_001, email="WEB@example.test")
    assert activated["id"] == entry["id"] and activated["active"] is True
    assert therapists.get_by_telegram(555_001)["id"] == entry["id"]  # type: ignore[index]


def test_no_module_keeps_a_therapist_registry() -> None:
    """A module-level list/dict of therapists is a per-process copy — the bug 12.2.4 removed."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in [*root.glob("bot/**/*.py"), *root.glob("web/**/*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else []
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            value = getattr(node, "value", None)
            container = (ast.List, ast.Dict, ast.ListComp, ast.DictComp, ast.Call)
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and "THERAPIST" in target.id
                    and isinstance(value, container)
                ):
                    offenders.append(f"{path.relative_to(root)}:{target.id}")
    assert offenders == []
