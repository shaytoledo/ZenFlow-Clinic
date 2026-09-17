"""The channel conformance suite (plan 7.1) — every `ChannelAdapter` must pass it.

An adapter's test module subclasses `ChannelConformance` and overrides the `harness` fixture
with a `ChannelHarness` for its provider (a fake of the provider's API — never the network).
Adding WhatsApp (7.4) is then "make this suite green", not "hope it works".

The suite speaks only the adapter's public contract; anything provider-specific (payload shapes,
header names, limits) comes from the harness.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

import pytest

from bot.interfaces.channel import (
    ChannelAdapter,
    ChannelError,
    InboundMessage,
    OutboundMedia,
    OutboundMessage,
    SentMessage,
)
from zenflow import clock

pytestmark = pytest.mark.contract

Failure = Literal["blocked", "rate_limited", "network"]


@dataclass
class Delivered:
    """One outbound message as the provider received it, normalised by the harness."""

    recipient: str
    text: str | None
    markdown: bool = False
    buttons: list[list[tuple[str, str]]] | None = None
    reply_to: str | None = None
    media_kind: str | None = None
    media_url: str | None = None
    media_data: bytes | None = None
    filename: str | None = None


@dataclass
class Edited:
    recipient: str
    message_id: str
    text: str
    buttons: list[list[tuple[str, str]]] | None


def _flat(buttons: Any) -> list[tuple[str, str]]:
    """Buttons in order, without the row layout each channel chooses for itself."""
    return [button for row in (buttons or []) for button in row]


class ChannelHarness(ABC):
    """What an adapter's test module provides to the suite."""

    adapter: ChannelAdapter
    #: a recipient id this channel accepts
    recipient: str
    #: secrets configured on the adapter — none may ever appear in an error message
    secrets: tuple[str, ...]
    #: a template this channel would accept, or None when it has no templates
    template: Any = None
    #: does the provider have a distinct formatting mode? (WhatsApp formats inline instead)
    marks_markdown: bool = True

    # ── what the provider saw ──
    @abstractmethod
    def delivered(self) -> list[Delivered]: ...

    @abstractmethod
    def edited(self) -> list[Edited]: ...

    @abstractmethod
    def typing(self) -> list[str]: ...

    @abstractmethod
    def fail_next(self, failure: Failure) -> None: ...

    # ── provider payloads (what its webhook would post) ──
    @abstractmethod
    def inbound_text(self, user: str, text: str, message_id: str) -> Any: ...

    @abstractmethod
    def inbound_reply(self, user: str, text: str, reply_to: str) -> Any: ...

    @abstractmethod
    def inbound_button(self, user: str, data: str) -> Any: ...

    @abstractmethod
    def inbound_media(self, user: str, kind: str, caption: str | None) -> Any: ...

    @abstractmethod
    def ignored_payloads(self) -> list[Any]:
        """Payloads the adapter must turn into None (unsupported updates and garbage)."""

    # ── webhook authenticity ──
    @abstractmethod
    def signed(self, body: bytes) -> dict[str, str]:
        """Headers the genuine provider sends with `body`."""

    @abstractmethod
    def forged(self, body: bytes) -> list[dict[str, str]]:
        """Header sets that must be refused (wrong, missing, tampered)."""

    @abstractmethod
    def unconfigured(self) -> ChannelAdapter:
        """The same adapter with no webhook secret configured."""


class ChannelConformance:
    """Subclass as `TestXxxConformance` and override the `harness` fixture."""

    @pytest.fixture
    def harness(self) -> ChannelHarness:
        raise NotImplementedError("override the `harness` fixture")

    # ── outbound text ──
    async def test_send_text_reaches_the_recipient(self, harness: ChannelHarness) -> None:
        sent = await harness.adapter.send_text(harness.recipient, "Hello there")
        assert isinstance(sent, SentMessage)
        assert sent.channel == harness.adapter.name
        assert sent.recipient_id == harness.recipient
        assert sent.message_id, "the provider's message id is returned"
        (got,) = harness.delivered()
        assert (got.recipient, got.text, got.buttons, got.media_kind) == (
            harness.recipient,
            "Hello there",
            None,
            None,
        )

    async def test_text_is_plain_unless_markdown_is_asked_for(
        self, harness: ChannelHarness
    ) -> None:
        await harness.adapter.send_text(harness.recipient, "a_b *c*")
        await harness.adapter.send_text(harness.recipient, "*bold*", markdown=True)
        plain, marked = harness.delivered()
        assert (plain.text, plain.markdown) == ("a_b *c*", False), "never parsed by default (B2)"
        assert marked.text == "*bold*", "the text is delivered as written"
        if harness.marks_markdown:
            assert marked.markdown is True, "the provider is told to parse it"

    async def test_a_reply_names_the_message_it_answers(self, harness: ChannelHarness) -> None:
        first = await harness.adapter.send_text(harness.recipient, "question")
        await harness.adapter.send_text(harness.recipient, "answer", reply_to=first.message_id)
        assert harness.delivered()[-1].reply_to == first.message_id

    async def test_numeric_recipients_are_accepted(self, harness: ChannelHarness) -> None:
        if not harness.recipient.lstrip("+").isdigit():
            pytest.skip("this channel's recipients are not numeric")
        sent = await harness.adapter.send_text(int(harness.recipient.lstrip("+")), "hi")
        assert sent.message_id and len(harness.delivered()) == 1

    @pytest.mark.parametrize("text", ["", "   "])
    async def test_empty_text_is_refused_before_sending(
        self, harness: ChannelHarness, text: str
    ) -> None:
        with pytest.raises(ChannelError) as err:
            await harness.adapter.send_text(harness.recipient, text)
        assert err.value.permanent
        assert harness.delivered() == []

    async def test_text_over_the_limit_is_refused_before_sending(
        self, harness: ChannelHarness
    ) -> None:
        limit = harness.adapter.max_text_len
        await harness.adapter.send_text(harness.recipient, "x" * limit)  # exactly the limit is fine
        with pytest.raises(ChannelError) as err:
            await harness.adapter.send_text(harness.recipient, "x" * (limit + 1))
        assert err.value.permanent
        assert len(harness.delivered()) == 1

    # ── buttons ──
    async def test_buttons_arrive_in_order(self, harness: ChannelHarness) -> None:
        rows = [[("Yes", "fu:1:3:y"), ("No", "fu:1:3:n")], [("Skip", "fu:1:3:skip")]]
        sent = await harness.adapter.send_buttons(harness.recipient, "Did it help?", rows)
        assert sent.message_id
        (got,) = harness.delivered()
        assert got.text == "Did it help?"
        assert _flat(got.buttons) == _flat(rows), "every option, in order (rows are the channel's)"

    async def test_button_data_over_the_limit_is_refused(self, harness: ChannelHarness) -> None:
        too_long = "d" * (harness.adapter.max_button_data_len + 1)
        with pytest.raises(ChannelError) as err:
            await harness.adapter.send_buttons(harness.recipient, "?", [[("A", too_long)]])
        assert err.value.permanent and harness.delivered() == []

    async def test_button_data_is_measured_in_bytes(self, harness: ChannelHarness) -> None:
        limit = harness.adapter.max_button_data_len
        wide = "א" * (limit // 2 + 1)  # 2 bytes each in UTF-8: over the limit in bytes only
        assert len(wide) <= limit < len(wide.encode())
        with pytest.raises(ChannelError):
            await harness.adapter.send_buttons(harness.recipient, "?", [[("A", wide)]])

    async def test_too_many_buttons_are_refused(self, harness: ChannelHarness) -> None:
        count = harness.adapter.max_buttons + 1
        rows = [[(f"B{i}", f"b{i}")] for i in range(count)]
        with pytest.raises(ChannelError) as err:
            await harness.adapter.send_buttons(harness.recipient, "?", rows)
        assert err.value.permanent and harness.delivered() == []

    async def test_buttons_without_labels_are_refused(self, harness: ChannelHarness) -> None:
        with pytest.raises(ChannelError):
            await harness.adapter.send_buttons(harness.recipient, "?", [[("", "x")]])
        with pytest.raises(ChannelError):
            await harness.adapter.send_buttons(harness.recipient, "?", [])
        assert harness.delivered() == []

    async def test_the_generic_send_carries_buttons(self, harness: ChannelHarness) -> None:
        rows = [[("0", "fu:9:1:0"), ("10", "fu:9:1:10")]]
        sent = await harness.adapter.send(
            OutboundMessage(recipient_id=harness.recipient, text="Pain?", extra={"buttons": rows})
        )
        assert sent.message_id
        assert _flat(harness.delivered()[0].buttons) == _flat(rows)

    # ── media ──
    async def test_media_by_url(self, harness: ChannelHarness) -> None:
        url = "https://media.example.org/points/LI4.webp"
        sent = await harness.adapter.send_media(
            harness.recipient, OutboundMedia(kind="image", url=url, caption="LI4")
        )
        assert sent.message_id
        (got,) = harness.delivered()
        assert (got.media_kind, got.media_url, got.text) == ("image", url, "LI4")

    async def test_media_by_upload(self, harness: ChannelHarness) -> None:
        data = b"%PDF-1.4 recommendations"
        await harness.adapter.send_media(
            harness.recipient,
            OutboundMedia(
                kind="document", data=data, filename="plan.pdf", mime_type="application/pdf"
            ),
        )
        (got,) = harness.delivered()
        assert (got.media_kind, got.media_data, got.filename) == ("document", data, "plan.pdf")

    @pytest.mark.parametrize(
        "media",
        [
            OutboundMedia(kind="image"),  # neither url nor data
            OutboundMedia(kind="image", url="https://x.example/a.png", data=b"a", filename="a.png"),
            OutboundMedia(kind="hologram", url="https://x.example/a.png"),
            OutboundMedia(kind="image", url="http://x.example/a.png"),  # not https
            OutboundMedia(kind="document", data=b"a"),  # an upload needs a file name
        ],
    )
    async def test_bad_media_is_refused_before_sending(
        self, harness: ChannelHarness, media: OutboundMedia
    ) -> None:
        with pytest.raises(ChannelError) as err:
            await harness.adapter.send_media(harness.recipient, media)
        assert err.value.permanent and harness.delivered() == []

    # ── edits and typing ──
    async def test_edit_replaces_text_and_buttons(self, harness: ChannelHarness) -> None:
        if not harness.adapter.supports_edit:
            pytest.skip("this channel cannot replace a sent message")
        sent = await harness.adapter.send_buttons(harness.recipient, "Pick", [[("A", "a")]])
        assert sent.message_id is not None
        edited = await harness.adapter.edit_message(
            harness.recipient, sent.message_id, "Picked A", buttons=[[("Undo", "u")]]
        )
        assert edited.message_id == sent.message_id
        await harness.adapter.edit_message(harness.recipient, sent.message_id, "Done")
        first, second = harness.edited()
        assert (first.message_id, first.text, first.buttons) == (
            sent.message_id,
            "Picked A",
            [[("Undo", "u")]],
        )
        assert (second.text, second.buttons) == ("Done", None), "no buttons = buttons removed"

    async def test_an_unsupported_edit_is_refused(self, harness: ChannelHarness) -> None:
        if harness.adapter.supports_edit:
            pytest.skip("this channel can replace a sent message")
        sent = await harness.adapter.send_text(harness.recipient, "hello")
        assert sent.message_id is not None
        with pytest.raises(ChannelError) as err:
            await harness.adapter.edit_message(harness.recipient, sent.message_id, "changed")
        assert err.value.permanent and err.value.code == "unsupported"

    async def test_templates_are_declared_or_refused(self, harness: ChannelHarness) -> None:
        """A channel with a session window must have templates; one without refuses them."""
        from bot.interfaces.channel import Template

        template = harness.template
        if template is None:
            assert (
                harness.adapter.session_window_hours is None
            ), "a channel with a session window needs templates to reach anyone outside it"
            with pytest.raises(ChannelError) as err:
                await harness.adapter.send_template(harness.recipient, Template(name="whatever"))
            assert err.value.permanent and err.value.code == "unsupported"
            return
        sent = await harness.adapter.send_template(harness.recipient, template)
        assert sent.message_id
        assert harness.delivered(), "the template went out"

    async def test_typing_is_shown(self, harness: ChannelHarness) -> None:
        await harness.adapter.set_typing(harness.recipient)
        assert harness.typing() == [harness.recipient]

    @pytest.mark.parametrize("failure", ["blocked", "rate_limited", "network"])
    async def test_typing_never_raises(self, harness: ChannelHarness, failure: Failure) -> None:
        harness.fail_next(failure)
        await harness.adapter.set_typing(harness.recipient)  # best effort

    # ── provider failures ──
    async def test_a_blocked_recipient_is_a_permanent_failure(
        self, harness: ChannelHarness
    ) -> None:
        harness.fail_next("blocked")
        with pytest.raises(ChannelError) as err:
            await harness.adapter.send_text(harness.recipient, "hi")
        assert err.value.permanent and err.value.retry_after is None
        assert str(err.value), "the provider's reason is kept for the delivery log"

    async def test_rate_limiting_says_when_to_retry(self, harness: ChannelHarness) -> None:
        harness.fail_next("rate_limited")
        with pytest.raises(ChannelError) as err:
            await harness.adapter.send_text(harness.recipient, "hi")
        assert not err.value.permanent
        assert err.value.retry_after is not None and err.value.retry_after > 0

    async def test_a_network_failure_is_retryable(self, harness: ChannelHarness) -> None:
        harness.fail_next("network")
        with pytest.raises(ChannelError) as err:
            await harness.adapter.send_text(harness.recipient, "hi")
        assert not err.value.permanent

    @pytest.mark.parametrize("failure", ["blocked", "rate_limited", "network"])
    async def test_errors_never_carry_secrets(
        self, harness: ChannelHarness, failure: Failure
    ) -> None:
        harness.fail_next(failure)
        with pytest.raises(ChannelError) as err:
            await harness.adapter.send_text(harness.recipient, "hi")
        rendered = f"{err.value} {err.value!r} {err.value.__cause__!r} {err.value.code}"
        for secret in harness.secrets:
            assert secret not in rendered

    # ── inbound ──
    def test_parse_a_text_message(self, harness: ChannelHarness) -> None:
        msg = harness.adapter.parse_inbound(harness.inbound_text(harness.recipient, "Hi!", "77"))
        assert isinstance(msg, InboundMessage)
        assert (msg.channel, msg.external_user_id, msg.text, msg.message_id) == (
            harness.adapter.name,
            harness.recipient,
            "Hi!",
            "77",
        )
        assert (msg.kind, msg.button_data, msg.media, msg.reply_to) == ("text", None, (), None)
        assert clock.normalize(msg.received_at) == msg.received_at, "canonical UTC instant"
        assert msg.raw, "the provider payload is kept for debugging"

    def test_parse_a_reply(self, harness: ChannelHarness) -> None:
        msg = harness.adapter.parse_inbound(harness.inbound_reply(harness.recipient, "yes", "41"))
        assert msg is not None and (msg.text, msg.reply_to) == ("yes", "41")

    def test_parse_a_button_tap(self, harness: ChannelHarness) -> None:
        msg = harness.adapter.parse_inbound(harness.inbound_button(harness.recipient, "fu:5:2:4"))
        assert msg is not None
        assert (msg.kind, msg.button_data, msg.external_user_id) == (
            "button",
            "fu:5:2:4",
            harness.recipient,
        )

    @pytest.mark.parametrize("kind", ["image", "audio", "document"])
    def test_parse_media(self, harness: ChannelHarness, kind: str) -> None:
        msg = harness.adapter.parse_inbound(harness.inbound_media(harness.recipient, kind, "look"))
        assert msg is not None and msg.kind == "media"
        (media,) = msg.media
        assert media.kind == kind and media.file_id
        assert msg.text == "look", "a caption is the message text"

    def test_parse_media_without_caption(self, harness: ChannelHarness) -> None:
        msg = harness.adapter.parse_inbound(harness.inbound_media(harness.recipient, "audio", None))
        assert msg is not None and msg.text is None and len(msg.media) == 1

    def test_unsupported_and_malformed_payloads_are_ignored(self, harness: ChannelHarness) -> None:
        for payload in [
            None,
            "",
            "text",
            42,
            [],
            {},
            {"unexpected": True},
            *harness.ignored_payloads(),
        ]:
            assert harness.adapter.parse_inbound(payload) is None, payload

    # ── webhook authenticity ──
    def test_a_genuine_webhook_is_accepted(self, harness: ChannelHarness) -> None:
        body = b'{"update_id": 1}'
        assert harness.adapter.verify_webhook(harness.signed(body), body) is True

    def test_header_names_are_case_insensitive(self, harness: ChannelHarness) -> None:
        body = b'{"update_id": 2}'
        headers = {k.upper(): v for k, v in harness.signed(body).items()}
        assert harness.adapter.verify_webhook(headers, body) is True

    def test_forged_webhooks_are_refused(self, harness: ChannelHarness) -> None:
        body = b'{"update_id": 3}'
        for headers in [{}, *harness.forged(body)]:
            assert harness.adapter.verify_webhook(headers, body) is False, headers

    def test_without_a_secret_every_webhook_is_refused(self, harness: ChannelHarness) -> None:
        body = b'{"update_id": 4}'
        assert harness.unconfigured().verify_webhook(harness.signed(body), body) is False
