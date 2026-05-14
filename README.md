# ContextPrompt

**Live app → [https://contextprompt.vercel.app](https://contextprompt.vercel.app)**

A smart prompt-engineering tool that turns simple, everyday requests into professional, model-optimised prompts — powered by your own project context.

---

## What it does

Most people write vague prompts and get vague results. ContextPrompt fixes that.

You define a **project** (e.g. "My Clinic", "SaaS Marketing", "App Development") and fill in a **context brain** — your persona, target audience, tone of voice, and domain terminology. From then on, you just type what you want in plain language. The app combines your request with your context and uses Gemini to generate a detailed, structured prompt ready to drop into Claude, GPT-4, or Gemini.

### Example

**Context you set once:**
> I am a Traditional Chinese Medicine practitioner specialising in acupuncture for athletes. Target audience: active adults aged 30–55. Tone: professional, scientific yet accessible and empathetic.

**You type:**
> Write an Instagram post about acupuncture after a workout

**ContextPrompt generates:**
> Act as an expert TCM practitioner specialising in sports acupuncture. Your audience is athletes and active adults aged 30–55. Write a highly engaging, scientifically accurate Instagram post explaining the benefits of acupuncture for muscle recovery after intense training. Structure it with a hook, 3 bullet points on physiological benefits (pain relief, inflammation, circulation), and a CTA to book a session. Tone: encouraging and professional.

---

## Features

- **Google sign-in** — secure auth, each user gets their own private workspace
- **Multiple projects** — separate context brains for different domains or clients
- **Dynamic context** — update your context at any time; the next prompt picks it up instantly
- **Target model selector** — Claude, GPT-4, or Gemini; the optimizer tailors phrasing to the chosen model
- **Prompt history** — every optimized prompt is saved per project; click any entry to reload it
- **Free to use** — powered by Gemini 2.0 Flash (Google's free-tier model)

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Frontend & API | Next.js 15 (App Router) |
| Auth | NextAuth v5 — Google OAuth |
| Database | Prisma ORM + Neon PostgreSQL |
| AI engine | Google Gemini 2.0 Flash (`@google/generative-ai`) |
| Hosting | Vercel |

---

## Local development

### Prerequisites
- Node.js 20+
- A [Neon](https://neon.tech) PostgreSQL database (free tier)
- A [Google Cloud](https://console.cloud.google.com) OAuth 2.0 client
- A [Google AI Studio](https://aistudio.google.com/app/apikey) Gemini API key

### Setup

```bash
cd frontend
npm install
cp .env.example .env.local
# Fill in all values in .env.local
npm run db:push   # push schema to your Neon database
npm run dev       # starts on http://localhost:3000
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Neon pooled connection string |
| `DIRECT_URL` | Neon direct connection string (for migrations) |
| `AUTH_SECRET` | Random secret — generate with `openssl rand -base64 32` |
| `AUTH_GOOGLE_ID` | Google OAuth client ID |
| `AUTH_GOOGLE_SECRET` | Google OAuth client secret |
| `GOOGLE_API_KEY` | Gemini API key (free at aistudio.google.com) |
| `GEMINI_MODEL` | Model name — default: `gemini-2.0-flash` |

Add `http://localhost:3000/api/auth/callback/google` to your Google OAuth **Authorized redirect URIs** for local development.

---

## Deploy to Vercel

```bash
cd frontend
npx vercel --prod
```

Add all environment variables in the Vercel dashboard, and add `https://your-app.vercel.app/api/auth/callback/google` to your Google OAuth redirect URIs.
