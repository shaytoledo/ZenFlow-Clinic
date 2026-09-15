# Hosting & Log-Monitoring Options (research note, 2026-09-15)

Owner questions: *"Can Vercel run our local LLM? What else can host this system? What can
monitor all the logs for free (Logz.io-style, but free)?"* This note answers them so the plan's
Phase 12 (AWS readiness) and 8.4 (metrics & health) start from facts. Prices and free-tier limits
change; every figure below carries the date it was read and must be re-checked before committing.

---

## 1. What this system needs from a host

| Component | Shape | Consequence |
|---|---|---|
| Patient bot + therapist bot | **long-running polling processes** (`startup/run_bots.py`) — no inbound HTTP until Phase 12.2.5 adds webhook mode | needs an *always-on* process; scale-to-zero platforms kill it |
| Web dashboard | FastAPI/uvicorn, session cookies, Jinja pages | any container/VM host; serverless is possible but pointless while the bots need a VM anyway |
| Follow-up scheduler | in-process asyncio loop (Phase 1 makes it a durable job queue) | same process as the bots today |
| SQLite (WAL) | a **file** on local disk, both processes open it | needs a persistent disk; incompatible with multi-instance / serverless |
| Redis | cache + relay + LLM history | a Redis server (managed or a container) |
| Ollama (`gemma3`) | local LLM, **RAM-bound** (≈ 4–8 GB for a small model, more for larger) | needs a VM with RAM (or a GPU); no serverless platform runs it |
| Google OAuth callbacks | need a stable **https://** URL (ADR-14) | TLS termination + a domain |

## 2. Vercel — and the "local LLM on Vercel" idea

**Vercel cannot host this system, and cannot run a local LLM.** Vercel runs *serverless functions*
(short-lived, request-scoped, memory- and time-limited, no persistent disk, no GPU, no background
processes). That rules out the polling bots, the scheduler loop, SQLite-on-disk, and Ollama.
What Vercel *does* offer is the **AI SDK / AI Gateway**, i.e. calling a *hosted* model (OpenAI,
Anthropic, Google, …) from a function — that is a cloud API, not a local model. If we ever want a
Vercel-hosted *front end*, the bots, DB, Redis and LLM would still live on a VM, so the split buys
nothing today. Conclusion: the connected Vercel project is a leftover (it fails on every commit);
`vercel.json` now disables its Git deployments; disconnect it in the Vercel dashboard.

If "the LLM answers our requests" is the goal without owning a GPU, the supported path is
`ZF_AI_PROVIDER=anthropic` (already scaffolded, Phase 0.4) — the Anthropic API replaces Ollama
and the host no longer needs RAM for a model. Cost is per token; clinical prompts here are small.

## 3. Hosting options that fit (long-running processes)

| Option | Free? (read 2026-09-15) | Fit | Notes |
|---|---|---|---|
| **Clinic's own PC / mini-PC** (today's setup: `startup/launch.py`) | free | ★★★★ | Ollama on real hardware, data stays on-prem (good for patient data). Needs: HTTPS for OAuth (Cloudflare Tunnel or a tunnel/reverse proxy), backups, a watchdog. |
| **Oracle Cloud "Always Free"** ARM VM (up to 4 OCPU / 24 GB RAM, 200 GB disk) | free, credit card for identity only | ★★★★ | Enough RAM to run a small Ollama model on CPU (slow but works) + web + bots + Redis via docker-compose. Region availability of the ARM shape fluctuates; expect to retry. You operate it yourself. |
| **Hetzner / other small VPS** (≈ €4–8 / month, 4–8 GB RAM) | no | ★★★★ | Cheapest *reliable* always-on box; docker-compose; EU data residency. Same operational model as Oracle. |
| **Railway** (`railway.toml` + `Procfile` already in repo) | trial credits only; Hobby ≈ $5 / month + usage | ★★★ | Runs web + worker services and Redis; **no Ollama** (no GPU, RAM-priced) ⇒ pair with `ZF_AI_PROVIDER=anthropic`. Persistent volume needed for SQLite (or move to Postgres, Phase 12.2.2). |
| **Render** | free web service **sleeps after 15 min idle** | ★ | Polling bots never receive HTTP ⇒ they sleep immediately; paid "background worker" needed. |
| **Fly.io** | no free tier for new accounts | ★★★ | Small Machines are cheap and always-on; volumes for SQLite; no GPU at the low end. |
| **Google Cloud Run / Cloudflare Workers** | free tiers exist | ★★ | Only after Phase 12.2.5 (webhook mode) and only for the web + webhook receivers; Redis/DB/LLM still elsewhere. |
| **AWS** (the plan's Phase 12 target) | free tier is 12 months / limited | ★★★ | Right long-term shape (ECS/Fargate or a single EC2 with compose); no reason to start there before Phase 12. |

**Recommendation for now:** stay on-prem (clinic PC) or one small always-on box (Oracle Always
Free if the ARM shape is obtainable, otherwise a €4–8 VPS), running the docker-compose stack that
Phase 12.2.1 defines, with **Cloudflare Tunnel** (free) for an `https://` hostname so OAuth
callbacks and the dashboard satisfy ADR-14 without opening ports. Switch to `ZF_AI_PROVIDER=
anthropic` on any host without ≥ 8 GB spare RAM. Add the decision as an ADR when the box exists.

## 4. Free log monitoring ("like Logz.io, but free")

Our logs are already structured JSON with `request_id` (Phase 0.5), so any of these ingests them
directly. Two families:

### 4a. Hosted SaaS with a real free tier (read 2026-09-15 — re-verify)

| Service | Free tier | Fit | Notes |
|---|---|---|---|
| **Grafana Cloud** (Loki + Grafana) | free forever: ~50 GB logs / month, 14-day retention, 3 users | ★★★★ | Best "free Logz.io": dashboards, alerts, LogQL on our JSON fields. Ship with **Grafana Alloy** or **Vector** agent, or push via Loki HTTP API. Also gives metrics (Phase 8.4) in the same place. |
| **Better Stack (Logs)** | free: small monthly volume (~1–3 GB), short retention | ★★★ | Nicest UI, live tail, built-in uptime/incident alerts; the free volume is tight but our clinic volume is tiny. |
| **Axiom** | free "Personal": hundreds of GB / month ingest, 30-day retention (limits changed several times) | ★★★ | Very generous ingest; SQL-like APL queries; ship via HTTP/Vector. |
| **New Relic** | free: 100 GB / month, 1 full user | ★★ | Heavyweight; fine if we also want APM later. |
| **Papertrail** | free: ~100 MB / month | ★ | Too small even for us once JSON lines are on. |
| **Sentry** (errors, not logs) | free developer plan | ★★★ | Complements a log store: exceptions with request context. Consider in Phase 8. |

### 4b. Self-hosted, open source, free (runs next to the app in docker-compose)

| Tool | Footprint | Fit | Notes |
|---|---|---|---|
| **OpenObserve** | one Rust binary, ~100 MB RAM, SQL queries, logs+metrics+traces | ★★★★ | Best self-hosted fit for a one-box deployment: single container, built-in UI, alerts, HTTP JSON ingest. |
| **Grafana + Loki (+ Alloy/Promtail)** | 2–3 containers | ★★★ | The standard; same stack as Grafana Cloud, so moving between self-hosted and hosted is a config change. |
| **Dozzle** | tiny | ★★ | Live tail of Docker container logs in a browser — zero setup, no history/alerts. Good as the "first look" tool. |
| **SigNoz** | ClickHouse-backed, heavier | ★★ | Full OTel observability; more than we need until Phase 8.4/12. |
| **Seq** | .NET, free single-user licence | ★★ | Excellent structured-log UI; single-user free licence fits a one-clinic deployment. |

**Recommendation:** **Grafana Cloud free tier** as the hosted choice (dashboards + alerts, and the
same Loki query language if we later self-host), with **OpenObserve** as the self-hosted
alternative when data must not leave the clinic (patient identifiers appear in logs — see 4c).
Ship logs with **Vector** (single static binary, reads the JSON files in `logs/` or container
stdout, adds host labels, ships to either target) — flag-driven so dev keeps writing local files.

### 4c. Before shipping logs anywhere: privacy

Our access lines carry `therapist_id`, `patient_id` (a Telegram user id), `appointment_id`; event
text can carry patient names. Under GDPR / Israeli privacy law (open question Q5) that is personal
data. Rules for any external log store: EU/IL region where offered, shortest retention that still
supports debugging (14 days), no message bodies in logs (Phase 9.11 log-redaction test), and a
DPA with the vendor. Self-hosting sidesteps the vendor question entirely.

## 5. What this changes in the plan

- Phase 8.4: implement `/readyz` + Prometheus exposition so Grafana (Cloud or self-hosted) can
  scrape; add a **Vector** shipper config behind `ZF_LOG_SHIP=grafana|openobserve|off`.
- Phase 12.2.1: docker-compose stack gains an optional `openobserve` (or `loki`+`grafana`)
  service and a `vector` sidecar.
- Phase 12.2.8: cost estimate compares (a) clinic PC + Cloudflare Tunnel, (b) Oracle Always Free,
  (c) €4–8 VPS, (d) Railway + Anthropic API, (e) AWS.

## Sources (read 2026-09-15)
- OpenObserve, [Best Log Management Tools in 2026](https://openobserve.ai/blog/log-management-tools/)
- Uptrace, [6 Free & Open-Source Log Management Tools in 2026](https://uptrace.dev/blog/open-source-log-management)
- Toolradar, [Best Free Log Management Tools in 2026](https://toolradar.com/best/free/log-management)
- 401 Clicks, [Log tools with generous free tiers 2026](https://401clicks.com/blog/best-log-management-tools-with-generous-free-tiers-2026)
- Dash0, [Papertrail alternatives](https://www.dash0.com/comparisons/best-papertrail-alternatives)
- Render, [Platforms with a real free tier for developers in 2026](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026)
- PandaStack, [Best Telegram bot hosting platforms 2026](https://pandastack.io/blog/best-telegram-bot-hosting-2026)
- Soumyadeep765, [free-telegram-bot-hosting guide](https://github.com/Soumyadeep765/free-telegram-bot-hosting)
- Jangwook, [Ollama + FastAPI production deployment guide](https://jangwook.net/en/blog/en/ollama-fastapi-production-deployment-guide-2026/)
- DEV, [8 best free & open source log management tools (2026)](https://dev.to/ankit01oss/8-best-free-open-source-log-management-tools-2026-562n)
