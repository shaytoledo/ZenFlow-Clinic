"""Phase 4.2d — the point-card density is a per-therapist preference, saved on the server.

Preferences live in `therapists.ui_prefs` as a JSON object restricted to a whitelist
(`web/repositories/therapist_repo.UI_PREF_CHOICES`); the treatment page gets the value in its
config island so the first paint already has the therapist's density.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.integration import treatment_source as ts

pytestmark = pytest.mark.integration

PW = "pw-Test-123"
URL = "/api/my/preferences"


async def test_the_default_is_detailed(make_therapist, login_as, fake_redis) -> None:
    client = await login_as(make_therapist(email="prefs-default@example.com", password=PW))
    resp = await client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {"point_density": "detailed"}


async def test_a_change_is_saved_for_that_therapist_only(
    make_therapist, login_as, fake_redis
) -> None:
    alice = await login_as(make_therapist(email="prefs-a@example.com", password=PW))
    resp = await alice.patch(URL, json={"point_density": "compact"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"point_density": "compact"}
    assert (await alice.get(URL)).json() == {"point_density": "compact"}

    bob = await login_as(make_therapist(email="prefs-b@example.com", password=PW))
    assert (await bob.get(URL)).json() == {"point_density": "detailed"}


@pytest.mark.parametrize(
    "body",
    [
        {"point_density": "tiny"},
        {"point_density": 3},
        {"theme": "dark"},
        {"point_density": "compact", "password_hash": "x"},
        [],
        {},
    ],
)
async def test_only_whitelisted_values_are_accepted(
    body, make_therapist, login_as, fake_redis
) -> None:
    client = await login_as(make_therapist(email="prefs-bad@example.com", password=PW))
    resp = await client.patch(URL, json=body)
    assert resp.status_code == 422, resp.text
    assert (await client.get(URL)).json() == {"point_density": "detailed"}, "nothing was saved"


async def test_a_corrupt_stored_value_falls_back_to_the_default(
    make_therapist, login_as, fake_redis
) -> None:
    from bot.db import get_db

    therapist = make_therapist(email="prefs-corrupt@example.com", password=PW)
    client = await login_as(therapist)
    for stored in ("not json", json.dumps({"point_density": "huge"}), json.dumps([1])):
        get_db().execute("UPDATE therapists SET ui_prefs=? WHERE id=?", (stored, therapist["id"]))
        assert (await client.get(URL)).json() == {"point_density": "detailed"}


async def test_preferences_need_a_session(client, fake_redis) -> None:
    assert (await client.get(URL)).status_code == 401
    assert (await client.patch(URL, json={"point_density": "compact"})).status_code == 401


async def test_the_treatment_page_starts_with_the_saved_density(
    make_therapist, make_appointment, login_as, fake_redis
) -> None:
    therapist = make_therapist(email="prefs-page@example.com", password=PW)
    apt = make_appointment(therapist=therapist)
    client = await login_as(therapist)
    page = f"/treatment/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"

    def island(html: str) -> dict[str, object]:
        found = re.search(r'id="treatment-config">(.*?)</script>', html)
        assert found is not None
        loaded = json.loads(found.group(1))
        assert isinstance(loaded, dict)
        return loaded

    assert island((await client.get(page)).text)["point_density"] == "detailed"
    await client.patch(URL, json={"point_density": "compact"})
    assert island((await client.get(page)).text)["point_density"] == "compact"


def test_compact_cards_still_show_what_matters() -> None:
    """Compact trims detail, never the point's identity, its selection or its caution."""
    css = (ts.WEB / "static/css/treatment.css").read_text(encoding="utf-8")
    hidden = re.findall(r'\[data-density="compact"\] \.([\w-]+)[^{]*\{[^}]*display: none', css)
    compact_rules = re.findall(r'\[data-density="compact"\] \.([\w-]+)', css)
    assert compact_rules, "no compact rules found"
    assert set(hidden) & {"pc-code", "pc-name", "pc-toggle", "pc-channel", "pc-caution"} == set()
