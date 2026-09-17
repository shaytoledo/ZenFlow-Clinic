# Messaging Channels (Phase 7)

How ZenFlow reaches patients and therapists, and how to add a channel. Plan: `docs/MASTER_PLAN_EN.md`
Phase 7. Decision record: ADR-27 in `docs/TECHNICAL_DECISIONS.md`.

---

## 1. The contract: `ChannelAdapter` (7.1)

`bot/interfaces/channel.py` defines what every chat backend implements.

| Method | Does | Notes |
|---|---|---|
| `send_text(recipient, text, reply_to=, markdown=)` | one message | plain text unless `markdown=True` — user-typed words are never parsed (B2) |
| `send_buttons(recipient, text, buttons)` | a message with buttons | `buttons` = rows of `(label, data)`; a tap comes back as `InboundMessage.button_data` |
| `send_media(recipient, OutboundMedia)` | an image, audio, video or document | by `https://` URL, or by bytes with a file name |
| `edit_message(recipient, message_id, text, buttons=)` | replace a sent message | `buttons=None` removes the buttons |
| `set_typing(recipient)` | typing indicator | best effort, never raises |
| `parse_inbound(payload)` | provider webhook JSON → `InboundMessage` | returns `None` for anything unsupported or malformed; never raises |
| `verify_webhook(headers, body)` | is the request really from the provider? | no secret configured → always `False` |
| `send(OutboundMessage)` | the older generic entry point | buttons via `extra["buttons"]`; Markdown unless `extra["markdown"]` is false |

**What comes back:**

- **A successful send** returns a `SentMessage`: `channel`, `recipient_id`, the provider's `message_id`, and `raw`.
- **A failed send** raises `ChannelError`, which carries:
  - `permanent`: `True` when retrying will not help (blocked, unknown chat, invalid content);
  - `retry_after`: seconds the provider asked us to wait (rate limiting);
  - `code`: the provider's status, or `network` / `not_configured`;
  - the provider's reason as the message. It is scrubbed of the channel's token and secret, so the delivery log (`message_log`) can store it.

**Validation.** Each adapter declares its limits:

- `max_text_len` and `max_caption_len`;
- `max_button_data_len`, counted in bytes;
- `max_buttons`.

Empty text, oversized content, bad buttons and bad media are refused **before** anything is sent, as permanent `ChannelError`s.

**Inbound messages.** `InboundMessage` has these fields:

- `channel` and `external_user_id`;
- `text`, which is the caption for media;
- `received_at`, a canonical UTC instant;
- `message_id` and `reply_to`;
- `button_data`;
- `media`: a tuple of `InboundMedia(kind, file_id, mime_type)`;
- `display_name`;
- `raw`, the full provider payload.

`kind` is `text`, `media` or `button`. A media `file_id` is the provider's handle: download through the provider, never from a URL in the payload.

## 2. Getting a channel

`bot/interfaces/factory.py`: callers never build a channel themselves.

| Function | Returns |
|---|---|
| `get_channel("telegram")` | the patient bot on Telegram |
| `get_channel("whatsapp")` | refused unless `ZF_CHANNEL_WHATSAPP=1`; the adapter itself arrives in 7.4 |
| `get_default_channel()` | the channel named by `MESSAGING_CHANNEL` (default `telegram`) |
| `get_staff_channel()` | the therapist bot: how the system reaches therapists |

## 3. Telegram (`bot/interfaces/telegram_channel.py`)

`telegram_channel.py` is the **only** module that talks to the Telegram Bot API. A test fails if `api.telegram.org` appears in any other source file.

**Two ways in, one behaviour.** Both pass the conformance suite:

- `TelegramChannel(token=…)`: HTTPS with httpx. Used by the web process and queued jobs: web replies, Send Now, follow-ups, recommendations, the reply echo, and the bot-name and status checks.
- `TelegramChannel(bot=…)`: borrows a running python-telegram-bot application's own client, which the application initialises, rate-limits and shuts down (BOT_AUDIT B14). `bot.main.wire_bots()` gives the relay one channel over each application:
  - patient → therapist forwards (`bot/patient_bot/therapist.py: _therapist_channel`);
  - therapist → patient deliveries (`bot/therapist_bot/handlers.py: _patient_channel`).

**Telegram specifics:**

- **Limits:**
  - text: 4096 UTF-16 code units, so an emoji counts twice;
  - captions: 1024;
  - button data: 64 bytes;
  - buttons: 100.
- **Markdown** maps to `parse_mode: "Markdown"`. Replies use `reply_parameters` with `allow_sending_without_reply`, so a deleted original doesn't block the reply.
- **Recipients:** a numeric recipient (including a negative group id) is sent as a number; `@name` is passed through as-is.
- **Error mapping:**

  | Telegram answer | Result |
  |---|---|
  | 400 / 401 / 403 / 404 | permanent |
  | 429 with `retry_after` | rate limited, retry later |
  | 5xx or a network failure | retryable |

  Network errors are raised `from None`, because an httpx error can carry the URL, and the URL carries the token.
- **Inbound:**
  - Only private chats with a human sender are parsed.
  - Group messages, edits, bot senders, service messages and member updates are ignored.
  - A photo is its largest size.
  - A voice note counts as audio.
  - A button tap is dated on arrival, because Telegram does not date taps.
- **Webhooks:** Telegram echoes the `secret_token` set with `setWebhook` in the `X-Telegram-Bot-Api-Secret-Token` header. `verify_webhook` compares it in constant time with `TELEGRAM_WEBHOOK_SECRET`; if the setting is empty, every webhook is refused. The bots poll today, so nothing calls this yet; it is ready for `ZF_WEBHOOK_MODE` (Phase 12).
- **Status:**
  - `bot_info()` returns getMe, or `None`.
  - `check()` returns `(ok, "@username" or the reason)` for `/api/status`: `Unauthorized`, or `Unreachable` for a network failure.

**What stays outside the adapter.** Replies *inside* a Telegram conversation (`update.message.reply_text`, `query.edit_message_text`) belong to the Telegram driver in `bot/patient_bot/` and `bot/therapist_bot/`. python-telegram-bot owns that event loop. A WhatsApp front end gets its own driver on top of the booking API (7.3). Everything the *system* initiates goes through an adapter.

## 4. The conformance suite

`tests/contract/channel_conformance.py`: an adapter's test module subclasses `ChannelConformance` and provides a `ChannelHarness` for its provider:

- the adapter;
- a normalised view of what the fake provider received: deliveries, edits, typing;
- failure scripting: blocked, rate limited, network;
- sample webhook payloads: text, reply, button, media, ignored;
- genuine and forged webhook headers.

The suite has 43 checks per adapter, covering:

- **Delivery:** text, Markdown, replies, numeric recipients, buttons, media by URL and by upload, edits, typing.
- **Refused before sending:** empty text, over-limit text, button data measured in bytes, too many buttons, unlabelled buttons, five kinds of bad media.
- **Error behaviour:** blocked is permanent, rate limiting gives `retry_after`, network failures are retryable, and no secret ever appears in an error.
- **Inbound parsing:** text, reply, button, three media kinds, caption-less media, garbage.
- **Webhook authenticity:** genuine, case-insensitive header names, forged, and no secret configured.

Telegram runs the suite twice (`tests/contract/test_telegram_channel.py`), once per way in, against one offline Bot API (`tests/telegram_fake.py`). That fake speaks both httpx (a mock transport) and python-telegram-bot (a `BaseRequest`), so the real request encoding runs.

**Adding WhatsApp (7.4)** means writing `WhatsAppChannel(ChannelAdapter)`, a harness over a mocked provider, and making the suite green.

## 5. Tests never reach Telegram

`tests/conftest.py` puts a refusing transport under `TelegramChannel` for every test (`block_real_telegram`). The `fake_telegram` fixture swaps in the offline Bot API:

- `api_calls` records every call, any method, failed ones included;
- `calls` shows delivered `sendMessage`s in the older shape;
- `fail_next(description, status=, retry_after=, times=, bot=)` scripts errors;
- `down = True` simulates a network failure.

Before 7.1, three kinds of call went to the real network with a fake token and were never recorded:

- the reply echo;
- the bot-name lookups;
- the status check.

## 6. Patient identity (7.2)

Before 7.2 a patient **was** a Telegram user id, and a manual booking was a negative millisecond
stamp. About a dozen places decided "can we message this patient?" from the sign of the id or
from how the session was booked. Now:

| Table | Holds |
|---|---|
| `patients` | one row per person: `id` (internal), `full_name`, `phone`, `email`, `lang`, `notes`, `legacy_id`, timestamps |
| `patient_channels` | how to message them: `(channel, external_id)` unique, `is_primary`, `verified_at` |
| `patient_contacts` (view) | each patient's messaging contact: the primary channel, else the newest |

**The rules:**

- `appointments.patient_id` holds `patients.id`, and so do `treatment_notes`, `intake_sessions`,
  `followups`, `message_log` and `notifications`. `treatment_repo.upsert` always takes the
  appointment's patient, whatever the caller passes.
- **Reachability is a property of the patient**, never of the id or of the booking source:
  `patient_repo.messaging_contact(patient_id)` returns `Contact(channel, external_id)` or `None`.
  - Follow-ups, queued recommendations and Send Now go to `get_channel(contact.channel)` at
    `contact.external_id`.
  - With no contact: the follow-up is `no_channel` (the 6.4 call alert), and recommendations go
    by email, or raise the missing-contact alert.
  - A manual booking of a Telegram patient is reachable, which is a change: it used to be treated
    as unreachable.
- **Where patient rows come from:**
  - **A bot booking** calls `patient_repo.for_channel("telegram", user_id, name)`, which creates
    the patient on first contact. A name the clinic already knows is not overwritten.
  - **A manual booking** creates a patient with no channel.
  - **Booking an existing patient** by `existing_patient_id` requires that the patient has an
    appointment with this therapist. Anything else is a 404 (SF-013).
- **Incoming messages** name a channel identity: `consume_followup_conversation(sender_id, text,
  channel)` looks the patient up in `patient_channels`. An internal id is never a sender.
- **Linking a channel later** (`patient_repo.link_channel`) makes the same patient reachable
  without changing their id. A channel identity belongs to one patient (`ChannelTaken`).
- **Still keyed by the Telegram user id**, because they are Telegram conversations: the relay
  keys and the web messages page, the intake history, and the pre-6.3 follow-up keys. They become
  channel-agnostic with WhatsApp (7.4).

### The migration

The migration runs once, at start-up (`bot/db.py → _create_patients`), recorded as
`0001_patient_identity` in `schema_migrations`.

**Steps:**

1. **Backup.** If the database has appointments, it is first copied to
   `zenflow.db.pre-patient-identity-<UTC stamp>`.
2. **Transaction.** Everything below runs in one transaction (`BEGIN IMMEDIATE`).
3. **Patient rows.** Every distinct old id found in the six tables becomes a `patients` row. The
   old value is kept in `legacy_id`. The name, phone and email come from the newest booking that
   has them.
4. **Channels.** Positive old ids get a primary `telegram` channel; negative ones (manual
   bookings) get none.
5. **Rewrite.** All six tables are rewritten to the new ids.

**If any step fails:**

- the transaction is rolled back and the process refuses to start;
- the database is unchanged, and the error names the backup.

Mixing old and new ids would be worse than not starting.

### One release of compatibility

Pages and tabs opened before the upgrade still carry old ids:

- **HTML pages** (`/treatment/<old>/…`, `/patients/<old>[/session/…]`) redirect with **308** to
  the new id. The redirect happens after the tenant check, so another therapist's patient still
  goes back to the list.
- **API paths** (`/api/treatment-notes/<old>/<date>/…`, `/api/appointment/<old>/<date>/…`,
  `/api/patients/<old>`) are rewritten to the new id before routing
  (`web/legacy_patient_ids.py`). The routes keep their own tenant checks.

`patient_repo.canonical_id()` prefers an existing internal id; an old id is only used when no
patient has that id.

**Remove in the next release:**

- `web/legacy_patient_ids.py` and its registration in `web/app.py`;
- the `canonical_id` calls in the two page routers;
- the `patients.legacy_id` column, after a backup.
