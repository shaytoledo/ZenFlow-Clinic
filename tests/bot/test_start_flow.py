"""Phase 11.3 — the patient bot's entry-point handlers (start / change_therapist / back_to_main).

`start()` is where every patient conversation begins, and its branching (no therapists, one
therapist auto-selected, several to choose from, an already-chosen therapist, message vs. callback
delivery) was the least-covered bot code (~41%). These tests drive each handler through the fake
Telegram harness and assert the resulting conversation state and side effects, not brittle translated
strings.
"""

from __future__ import annotations

from bot.patient_bot import start
from bot.states import SELECTING, THERAPIST_SELECT
from tests.bot.conftest import FakeQuery, make_context, make_update

PATIENT = 900_000_777


def _reload() -> None:
    from bot import config as botcfg

    botcfg.reload_therapists()


async def test_no_active_therapists_greets_and_stays_in_menu() -> None:
    # Empty DB → no therapists; the patient is greeted and told to come back later.
    _reload()
    update = make_update("/start", user_id=PATIENT, full_name="Dana Levi")
    ctx = make_context()
    state = await start.start(update, ctx)
    assert state == SELECTING
    assert update.message.reply_texts(), "the patient still gets a message"


async def test_single_therapist_is_auto_selected(make_therapist) -> None:
    t = make_therapist(name="Dr Solo", therapist_id="t_solo")
    _reload()
    update = make_update("/start", user_id=PATIENT, full_name="Dana Levi")
    ctx = make_context()
    state = await start.start(update, ctx)
    assert state == SELECTING
    assert ctx.user_data["selected_therapist"] == t["id"], "the only therapist is chosen for them"
    assert "Dr Solo" in " ".join(update.message.reply_texts()), "the assigned therapist is named"


async def test_multiple_therapists_prompt_for_a_choice(make_therapist) -> None:
    make_therapist(name="Dr A", therapist_id="t_a")
    make_therapist(name="Dr B", therapist_id="t_b")
    _reload()
    update = make_update("/start", user_id=PATIENT)
    ctx = make_context()
    state = await start.start(update, ctx)
    assert state == THERAPIST_SELECT
    reply = update.message.replies[-1]
    buttons = reply["reply_markup"].inline_keyboard
    labels = [b.text for row in buttons for b in row]
    assert "Dr A" in labels and "Dr B" in labels, "both therapists are offered as buttons"


async def test_an_already_chosen_therapist_goes_straight_to_the_menu(make_therapist) -> None:
    t = make_therapist(name="Dr Chosen", therapist_id="t_chosen")
    _reload()
    update = make_update("hi", user_id=PATIENT, full_name="Dana Levi")
    ctx = make_context({"selected_therapist": t["id"]})
    state = await start.start(update, ctx)
    assert state == SELECTING
    assert update.message.reply_texts(), "the main menu is shown"


async def test_start_from_a_callback_query_edits_the_message(make_therapist) -> None:
    t = make_therapist(name="Dr Chosen", therapist_id="t_chosen")
    _reload()
    query = FakeQuery(data="back_main", user_id=PATIENT)
    update = make_update(query=query, user_id=PATIENT)
    ctx = make_context({"selected_therapist": t["id"]})
    state = await start.start(update, ctx)
    assert state == SELECTING
    assert query.answered and query.edits, "a callback is answered and the message edited in place"


async def test_single_therapist_auto_select_via_callback_edits_the_message(make_therapist) -> None:
    # A stale button (callback) with no therapist chosen yet still auto-selects the sole therapist.
    t = make_therapist(name="Dr Solo", therapist_id="t_solo")
    _reload()
    query = FakeQuery(data="noop", user_id=PATIENT)
    update = make_update(query=query, user_id=PATIENT)
    ctx = make_context()
    state = await start.start(update, ctx)
    assert state == SELECTING
    assert ctx.user_data["selected_therapist"] == t["id"]
    assert query.answered and query.edits, "the callback message is edited, not a new message sent"


async def test_multiple_therapists_via_callback_edits_with_buttons(make_therapist) -> None:
    make_therapist(name="Dr A", therapist_id="t_a")
    make_therapist(name="Dr B", therapist_id="t_b")
    _reload()
    query = FakeQuery(data="noop", user_id=PATIENT)
    update = make_update(query=query, user_id=PATIENT)
    ctx = make_context()
    state = await start.start(update, ctx)
    assert state == THERAPIST_SELECT
    labels = [b.text for row in query.edits[-1]["reply_markup"].inline_keyboard for b in row]
    assert "Dr A" in labels and "Dr B" in labels


async def test_change_therapist_clears_selection_and_lists_active(make_therapist) -> None:
    make_therapist(name="Dr A", therapist_id="t_a")
    make_therapist(name="Dr B", therapist_id="t_b")
    _reload()
    query = FakeQuery(data="change_therapist", user_id=PATIENT)
    update = make_update(query=query, user_id=PATIENT)
    ctx = make_context({"selected_therapist": "t_a"})
    state = await start.change_therapist(update, ctx)
    assert state == THERAPIST_SELECT
    assert "selected_therapist" not in ctx.user_data, "the prior choice is cleared"
    labels = [b.text for row in query.edits[-1]["reply_markup"].inline_keyboard for b in row]
    assert "Dr A" in labels and "Dr B" in labels


async def test_change_therapist_with_none_active_offers_back() -> None:
    _reload()  # empty DB — no active therapists
    query = FakeQuery(data="change_therapist", user_id=PATIENT)
    update = make_update(query=query, user_id=PATIENT)
    ctx = make_context({"selected_therapist": "gone"})
    state = await start.change_therapist(update, ctx)
    assert state == SELECTING
    assert query.edits, "the patient is shown a message with a back button"


async def test_back_to_main_returns_to_the_menu(make_therapist) -> None:
    t = make_therapist(therapist_id="t_x")
    _reload()
    query = FakeQuery(data="back_main", user_id=PATIENT)
    update = make_update(query=query, user_id=PATIENT)
    ctx = make_context({"selected_therapist": t["id"]})
    state = await start.back_to_main(update, ctx)
    assert state == SELECTING
    assert query.answered and query.edits
