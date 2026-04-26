"""
bot/services/
──────────────
Background services that live alongside the patient ConversationHandler but
do *not* belong to a single user-driven flow:

- `followup_scheduler` — fires the 24h post-treatment check-in via the
  `MessagingChannel` abstraction (Telegram today, WhatsApp tomorrow).

These services are started from `bot/main.py` after the bots are running.
"""
