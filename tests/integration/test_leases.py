"""zenflow.leases — a named, expiring, database-backed lock (Phase 3.1).

Used so two generations for one appointment never run at once, even with a second worker
process on the same database.
"""

from __future__ import annotations

from zenflow import leases


def test_one_holder_at_a_time(db) -> None:
    assert leases.acquire("gen:1", "job-1", ttl_seconds=60)
    assert not leases.acquire("gen:1", "job-2", ttl_seconds=60)


def test_the_same_holder_may_renew(db) -> None:
    assert leases.acquire("gen:1", "job-1", ttl_seconds=60)
    assert leases.acquire("gen:1", "job-1", ttl_seconds=60)


def test_release_frees_it_for_others(db) -> None:
    leases.acquire("gen:1", "job-1", ttl_seconds=60)
    leases.release("gen:1", "job-1")
    assert leases.acquire("gen:1", "job-2", ttl_seconds=60)


def test_only_the_holder_can_release(db) -> None:
    leases.acquire("gen:1", "job-1", ttl_seconds=60)
    leases.release("gen:1", "job-2")
    assert not leases.acquire("gen:1", "job-3", ttl_seconds=60)


def test_an_expired_lease_can_be_taken_over(db) -> None:
    """A worker that crashed while holding the lease must not block the appointment forever."""
    from bot.db import get_db

    leases.acquire("gen:1", "crashed", ttl_seconds=60)
    get_db().execute("UPDATE leases SET expires_at='2000-01-01T00:00:00Z'")
    assert leases.acquire("gen:1", "job-2", ttl_seconds=60)


def test_different_names_do_not_interfere(db) -> None:
    assert leases.acquire("gen:1", "job-1", ttl_seconds=60)
    assert leases.acquire("gen:2", "job-2", ttl_seconds=60)


def test_held_as_a_context_manager(db) -> None:
    with leases.held("gen:1", "job-1", ttl_seconds=60) as got:
        assert got
        assert not leases.acquire("gen:1", "job-2", ttl_seconds=60)
    assert leases.acquire("gen:1", "job-2", ttl_seconds=60)
