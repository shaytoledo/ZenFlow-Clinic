# WhatsApp (Phase 7.4)

The WhatsApp channel, off until `ZF_CHANNEL_WHATSAPP=1`. Provider: **Meta's WhatsApp Cloud API**
(ADR-30 weighs it against Twilio — owner decision Q2). Contract: `docs/CHANNELS.md`.

Nothing here is reachable without credentials, and every test runs against a mocked Cloud API.

---

## 1. What WhatsApp makes different

Telegram will send anything to anyone who has met the bot. WhatsApp will not, and the adapter is
mostly about those four differences.

| | Telegram | WhatsApp |
|---|---|---|
| Free-form message | any time | only within **24 h** of the patient's last message |
| Outside that window | — | only **pre-approved templates** |
| Buttons | 100, any layout | **3** reply buttons, or a **10**-row list |
| Editing a sent message | yes | **no** |
| Typing indicator | any time | only in answer to a message (marks it read) |
| Recipient | numeric user id | phone number, E.164 without `+` |

### The 24-hour service window

A patient's message opens a 24-hour window; anything the clinic sends inside it is free-form.
Once it closes, only templates arrive.

- The window is tracked from inbound messages (`remember_inbound`, Redis, ~25 h).
- `send_text` outside it raises `ChannelError(code="outside_session_window", permanent=True)`,
  and the caller sends a `Template` instead.
- **An unknown window counts as open.** A cache miss must not withhold a real reply; if the
  window really is closed, WhatsApp refuses the message and the adapter maps that refusal to the
  same code.

This matters for the 24-hour check-in (Phase 6): it fires exactly when the window is closing, so
on WhatsApp it needs an approved template. Wiring that is 7.4b.

### Buttons

The check-in asks pain on a 0–10 scale — 11 options, more than WhatsApp shows. The adapter maps
by count:

| Options | Sent as |
|---|---|
| 1–3 | reply buttons |
| 4–10 | a list message |
| 11–24 | a **numbered text message** — the check-in already accepts a typed number (6.2) |
| more | refused (`too_many_buttons`) |

Labels are truncated by WhatsApp at 20 characters, so a longer one is refused up front
(`button_label`); button data (the reply id) may be 256 bytes.

## 2. Configuration

| Variable | Purpose |
|---|---|
| `ZF_CHANNEL_WHATSAPP` | `1` turns the channel on. Everything below is ignored while it is off |
| `WHATSAPP_PHONE_NUMBER_ID` | the clinic's sender, from the Meta app |
| `WHATSAPP_TOKEN` | system-user access token |
| `WHATSAPP_APP_SECRET` | signs webhook deliveries; **empty ⇒ every webhook is refused** |
| `WHATSAPP_VERIFY_TOKEN` | the subscription handshake's shared string |
| `WHATSAPP_API_VERSION` | Graph API version (default `v23.0`) |

## 3. Wire format

Everything goes to `POST https://graph.facebook.com/<version>/<phone-number-id>/messages` with
`Authorization: Bearer <token>`; bytes are uploaded to `…/media` first and sent by id.

```jsonc
// text
{"messaging_product":"whatsapp","recipient_type":"individual","to":"972500000001",
 "type":"text","text":{"body":"Hello there","preview_url":false}}

// up to three options
{"type":"interactive","interactive":{"type":"button","body":{"text":"Did it help?"},
 "action":{"buttons":[{"type":"reply","reply":{"id":"fu:1:3:y","title":"Yes"}}]}}}

// four to ten options
{"type":"interactive","interactive":{"type":"list","body":{"text":"Pain 0–5?"},
 "action":{"button":"Choose","sections":[{"title":"Options","rows":[{"id":"fu:1:1:0","title":"0"}]}]}}}

// a template (the only thing that reaches a closed window)
{"type":"template","template":{"name":"followup_checkin","language":{"code":"he"},
 "components":[{"type":"body","parameters":[{"type":"text","text":"Dana"}]}]}}
```

**Errors** come back as `{"error":{"message":…,"code":…}}`. The adapter maps 400/401/403/404 to
permanent, 429 to a retry with a delay, 5xx and network failures to retryable, and the
out-of-window codes (131047, 131026, 470) to `outside_session_window`. No token or secret ever
reaches an error message.

## 4. Inbound

`parse_inbound` reads the standard webhook envelope
(`entry[].changes[].value.messages[0]`) and returns the same `InboundMessage` as Telegram:

- **text** → `text`;
- **interactive** (`button_reply` / `list_reply`) → `button_data`, so the check-in's callbacks work unchanged;
- **button** (a template's quick reply) → `button_data` from its payload;
- **image / audio / video / document** → `media` with the provider's file id, caption as the text;
- `context.id` → `reply_to`, the contact's profile name → `display_name`.

Anything else — statuses, system messages, a payload without a sender — is `None`.

**Authenticity:**

- `verify_webhook` compares `X-Hub-Signature-256` with an HMAC-SHA256 of the raw body under the
  app secret, in constant time. No secret configured ⇒ refused.
- `verify_handshake(mode, token, challenge)` answers Meta's subscription check when the verify
  token matches.
- `statuses(payload)` reads delivery receipts (`sent`/`delivered`/`read`/`failed`).

The HTTP endpoint that calls these, and routing an inbound message into the check-in, are 7.4b.

## 5. Tests

`tests/contract/test_whatsapp_channel.py` runs the shared conformance suite against a mocked
Cloud API, plus WhatsApp's own behaviour: the wire format, the three button mappings, a refused
long label, the two-step media upload, the session window (open, closed, unknown, and the
provider's own refusal), typing with and without a message to answer, the handshake, delivery
receipts, and that `graph.facebook.com` appears in exactly one module.

The suite itself gained capability flags in 7.4 (`supports_edit`, `session_window_hours`,
templates), so a channel is checked against what it can do rather than against Telegram's shape.
