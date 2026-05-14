# ContextPrompt — Feature Roadmap

Full product specification for each planned feature, ordered by priority.

---

## 🔥 TIER 1 — High Impact

---

### Feature 1: Send Prompt to AI & Show Response ("Run It")

**The Problem**
Today the user copies the optimized prompt, opens a new tab, pastes it into ChatGPT or Claude, reads the response, and comes back. That's 5 manual steps. The app generates a great prompt but forces the user to leave to actually use it.

**The Solution**
A **"Run it"** button appears below every optimized prompt. One click sends the prompt directly to the selected model's API and streams the response back inside the app — no tab switching, no copy-pasting.

**User Flow**
1. User types a simple request → clicks "Optimize Prompt"
2. Optimized prompt appears as usual
3. User clicks **"▶ Run it"** (with the target model shown — e.g. "Run with Gemini")
4. A response panel slides open below and the AI response streams in word-by-word
5. User can copy the response, regenerate, or go back and tweak the prompt

**UI Details**
- Streaming text with a blinking cursor (same UX as ChatGPT)
- "Stop" button to cancel mid-stream
- "Regenerate" button to re-run the same prompt
- Response is saved alongside the prompt in history
- The panel is collapsible so the optimized prompt stays visible

**Technical Notes**
- Use Gemini's streaming API (`generateContentStream`) for the "gemini" target model
- For Claude/GPT-4: use Anthropic streaming or OpenAI streaming SDK
- Backend: `POST /api/projects/[id]/prompts/[historyId]/run` — returns a Server-Sent Events stream
- Frontend: `fetch` with `ReadableStream` to render tokens as they arrive
- Store the final response in a new `aiResponse` column on `PromptHistory`

**Why It Matters**
This turns ContextPrompt from a "prompt generator" into a complete AI workspace. Users never need to leave the app. It also increases session time and stickiness dramatically.

---

### Feature 2: Prompt Variables / Placeholders

**The Problem**
Context is great for setting the persona and tone, but many users repeat the same prompt structure across different clients, cities, products, or dates. For example, a marketing agency writes Instagram posts for 10 different clients — the context is the same but the client name changes every time. Today they have to manually edit the request every time.

**The Solution**
Users can define **variables** directly inside the context or the request using `{{double_braces}}` syntax. Before optimization runs, a lightweight form pops up asking the user to fill in each variable. The filled values are injected into the prompt before it's sent to the optimizer.

**User Flow**
1. User sets up context with variables:
   > "I am a marketing consultant working for **{{client_name}}**, a brand in the **{{industry}}** space targeting **{{audience}}**."
2. User types a request:
   > "Write a product launch post for {{product_name}} launching on {{launch_date}}"
3. User clicks "Optimize Prompt"
4. A small modal appears: **"Fill in your variables"**
   - `client_name` → input field
   - `industry` → input field
   - `audience` → input field
   - `product_name` → input field
   - `launch_date` → date picker
5. User fills in values → clicks "Continue"
6. Variables are replaced → optimizer runs → optimized prompt is generated

**Variable Types**
- **Text** — default, free-form input
- **Date** — renders a date picker
- **Select** — user predefines options in context (e.g. `{{tone|professional|casual|funny}}`)

**UI Details**
- Variables are highlighted in the context editor with a colored chip
- The variable fill-in modal shows only when variables are detected
- Previously used values for each variable are remembered (autofill suggestions)
- Variables defined in the context carry over to every request in that project

**Technical Notes**
- Parse variables with regex: `/\{\{([^}]+)\}\}/g`
- Store variable definitions per project in a new `ProjectVariable` model
- Store last-used values per user per variable for autofill
- No backend changes needed for the optimizer itself — substitution happens before the API call

**Why It Matters**
This is the feature that makes ContextPrompt a professional tool for agencies and power users. A marketing team that manages 10 clients can have one project per client OR one project with variables — and switch between clients in seconds.

---

### Feature 3: Prompt Templates Library

**The Problem**
The biggest drop-off point for new users is the blank context editor. They sign up, create a project, and stare at an empty text box not knowing what to write. The concept of a "context brain" is powerful but abstract — users need examples to understand it and get started fast.

**The Solution**
A curated **gallery of context templates** built into the app. When creating a new project, the user can either start from scratch OR browse the template library and one-click import a template into their project's context.

**Template Categories & Examples**

| Category | Template Name |
|----------|--------------|
| Marketing | Social Media Manager |
| Marketing | Email Copywriter |
| Marketing | SEO Content Writer |
| Business | Sales Email Specialist |
| Business | Customer Support Agent |
| Tech | Software Developer (React) |
| Tech | Code Reviewer |
| Tech | Technical Writer |
| Health | Medical Professional |
| Health | Fitness & Nutrition Coach |
| Education | Online Course Creator |
| Legal | Legal Document Drafter |
| Creative | Fiction Writer |
| Creative | Screenwriter |

**User Flow**
1. User clicks "New Project" in the sidebar
2. Instead of just a name field, a modal opens:
   - Option A: "Start from scratch" → just enter a name
   - Option B: "Browse templates" → opens the template gallery
3. Gallery shows cards with template name, category tag, and a short description
4. User clicks a template → sees a preview of the full context
5. User clicks "Use this template" → project is created with context pre-filled
6. User customizes the imported context to match their specific details

**Template Card Details**
Each template card shows:
- 🏷️ Category badge
- 📝 Template name
- 💬 One-line description
- 👁️ "Preview" button to see the full context before importing

**Technical Notes**
- Templates are stored as a static JSON file in the codebase (no DB needed)
- `GET /api/templates` returns the full list
- `POST /api/projects` accepts an optional `templateId` — context is pre-filled server-side
- Templates are maintained in `/data/templates.json`

**Why It Matters**
Reduces time-to-value from "minutes of thinking" to "30 seconds". A new user can sign up, pick a template, type one sentence, and get a professional prompt — all within 2 minutes. This is the feature that drives word-of-mouth because the first experience is magical.

---

## 🚀 TIER 2 — Growth Features

---

### Feature 4: Favorites / Pinned Prompts

**The Problem**
Users run many optimizations and over time the history grows long. The best prompts — the ones they want to reuse regularly — get buried. There's no way to mark or organize the prompts that actually worked well.

**The Solution**
A **star icon** on every history entry. Starred prompts are pinned to the top of history and accessible from a dedicated "Favorites" tab. Users can also add a personal note to any favorite (e.g. "Best post format for athlete content").

**User Flow**
1. User sees a great optimized prompt → clicks the ⭐ star icon
2. The entry moves to the "Favorites" section at the top of the history panel
3. User can add a short note: "This format gets the most engagement"
4. In future sessions, favorites are immediately visible without scrolling

**UI Details**
- Star icon on every history card (empty = not starred, filled = starred)
- Favorites section is collapsible, appears above the regular history list
- Notes are editable inline with a single click
- Filter button to show "All" / "Favorites only"

**Technical Notes**
- Add `isFavorite Boolean @default(false)` and `note String?` to `PromptHistory` model
- `PATCH /api/projects/[id]/prompts/[historyId]` — update favorite status and note
- Frontend: optimistic UI update on star click (no loading state needed)

**Why It Matters**
Simple to build (< 1 day), immediately useful, increases daily active usage because users come back to their saved prompts.

---

### Feature 5: Team / Workspace Sharing

**The Problem**
Right now each user's projects are completely private. But most professional use cases are collaborative — a marketing team shares a "Brand Voice" context, a dev team shares a "Code Review" context, an agency shares client contexts with junior staff. Without sharing, ContextPrompt is limited to solo use.

**The Solution**
Users can **invite others to a project** by email. Invited members can view and use the project's context and run optimizations. The project owner can assign roles:
- **Viewer** — can run optimizations, read history
- **Editor** — can also edit the context and add/delete history
- **Owner** — full control including inviting/removing members

**User Flow**
1. Project owner opens a project → clicks "Share" button (top right)
2. A share panel opens showing current members
3. Owner types an email address → selects a role → clicks "Invite"
4. Invited user receives an email: *"[Name] has invited you to the '[Project Name]' project on ContextPrompt"*
5. If they don't have an account, the link takes them to the register page (project join is queued)
6. Invited user accepts → project appears in their sidebar under "Shared with me"

**UI Details**
- "Share" button in the project header (people icon with count)
- Member list shows avatar, name, role badge, and "Remove" button
- Role can be changed via a dropdown on each member row
- Projects shared with the user show a small "shared" badge in the sidebar
- Owner badge on the original owner's entry

**Technical Notes**
- New models: `ProjectMember { id, projectId, userId, role, invitedAt, acceptedAt }`
- New model: `ProjectInvite { id, projectId, email, role, token, expiresAt }`
- `POST /api/projects/[id]/members` — send invite
- `GET /api/invites/[token]` — accept invite page
- Update all project queries to return projects where `userId = me OR ProjectMember.userId = me`
- Email sending: use Resend (free tier, Next.js-friendly) or Nodemailer

**Why It Matters**
This is the feature that makes ContextPrompt a B2B product. A team of 5 sharing one "Company Voice" project is worth 5x more than a solo user. It also creates natural virality — every invite is a new user acquisition.

---

### Feature 6: Export History

**The Problem**
Users want a record of their best prompts outside the app. A copywriter might want to paste their month's best prompts into a Notion doc. A developer might want to archive prompt iterations. Currently there's no way to get data out.

**The Solution**
An **Export** button on each project that downloads all prompt history in the user's chosen format.

**Export Formats**
- **Markdown (.md)** — each prompt pair formatted as a readable document with headers
- **CSV (.csv)** — spreadsheet with columns: Date, Simple Request, Optimized Prompt, Target Model
- **JSON (.json)** — full structured export for developers

**User Flow**
1. User opens a project → clicks "Export" (download icon in the header)
2. A small dropdown appears: "Markdown / CSV / JSON"
3. File downloads immediately — no loading screen needed for reasonable history sizes

**Technical Notes**
- `GET /api/projects/[id]/export?format=csv|md|json`
- Backend generates the file in-memory and returns it with appropriate `Content-Disposition` header
- No new DB models needed
- For large history (1000+ prompts), stream the response

**Why It Matters**
Low effort (half a day), high perceived value. Users trust a product more when they know they can get their data out. Also a common ask that blocks people from adopting new tools.

---

## 💡 TIER 3 — Monetization & Power Users

---

### Feature 7: Usage Limits + Pro Subscription (Stripe)

**The Problem**
The app currently has no revenue model. Gemini API calls cost money (even if small on the free tier). As the user base grows, costs grow. There needs to be a sustainable model.

**The Solution**
A **freemium model** with Stripe integration:

| Plan | Price | Projects | Optimizations/month | Run It | Team Sharing |
|------|-------|----------|---------------------|--------|--------------|
| **Free** | $0 | 2 | 30 | ❌ | ❌ |
| **Pro** | $9/month | Unlimited | Unlimited | ✅ | Up to 5 members |
| **Team** | $29/month | Unlimited | Unlimited | ✅ | Unlimited members |

**User Flow**
1. Free user hits their limit → a banner appears: "You've used 30/30 optimizations this month. Upgrade to Pro for unlimited."
2. Clicking "Upgrade" opens a Stripe Checkout page
3. After payment, plan is updated instantly — no waiting for email
4. Pro badge appears in the sidebar next to the user's name
5. User can manage/cancel subscription from a "Billing" settings page

**Technical Notes**
- Stripe Checkout + Stripe Webhooks for payment confirmation
- New model: `Subscription { id, userId, stripeCustomerId, stripePriceId, status, currentPeriodEnd }`
- New model: `UsageRecord { id, userId, month, optimizationCount }`
- Middleware checks quota before every `POST /optimize`
- Stripe webhook handler at `POST /api/webhooks/stripe`

**Why It Matters**
Without this, ContextPrompt is a hobby project. With it, it's a SaaS business. The free tier drives acquisition; Pro converts power users. $9/month is an easy purchase for anyone using it professionally.

---

### Feature 8: Bring Your Own API Key (BYOK)

**The Problem**
Some users are power users who already pay for their own Claude or OpenAI API access and want to use those models — not Gemini. Others are privacy-conscious and don't want their prompts going through a shared API key. Currently they're locked into Gemini.

**The Solution**
A **"Connections"** settings page where users can add their own API keys for any supported provider. When a user has their own key set, all calls for that model go through their key, not the platform's key.

**Supported Providers**
- Google Gemini (default platform key — always available)
- Anthropic Claude (user's own key)
- OpenAI GPT-4 (user's own key)

**User Flow**
1. User goes to Settings → Connections
2. Sees a list of providers with "Connect" buttons
3. Clicks "Connect" next to Anthropic → paste API key field appears
4. User pastes their `sk-ant-...` key → clicks "Save"
5. Key is validated with a test call → green checkmark appears
6. From now on, when the user selects "Claude" as target model, the optimizer also runs on Claude using their key

**UI Details**
- Keys are shown masked (`sk-ant-••••••••••••••••••`)
- "Test connection" button to validate the key is still working
- "Disconnect" button to remove the key
- Warning: "Your key is encrypted at rest and never logged"

**Technical Notes**
- Keys are encrypted before storing in the DB using AES-256 (use `node:crypto`)
- New model: `UserApiKey { id, userId, provider, encryptedKey, createdAt }`
- The encryption key is an env variable — never stored in the DB
- Keys are decrypted only at request time in the serverless function
- Never return the decrypted key to the frontend

**Why It Matters**
Unlocks a whole segment of power users who want full control. Also reduces platform cost since BYOK users consume their own quota. Builds trust through transparency.

---

### Feature 9: Public REST API

**The Problem**
Developers want to integrate ContextPrompt's optimization engine into their own apps, scripts, or workflows. Currently there's no programmatic access — everything is UI-only.

**The Solution**
A documented **REST API** that any user can call with a personal API token. Developers can optimize prompts from their own code, CI pipelines, n8n workflows, or browser extensions.

**Endpoints**
```
POST   /api/v1/optimize          — optimize a prompt (project context optional)
GET    /api/v1/projects          — list user's projects
GET    /api/v1/projects/:id      — get project + context
POST   /api/v1/projects          — create a project
```

**Authentication**
Personal API tokens (like GitHub PATs):
- Generated in Settings → API Keys
- Sent as `Authorization: Bearer cp_live_xxxx` header
- Tokens can be named, scoped (read-only vs. full), and revoked

**Example Request**
```bash
curl -X POST https://contextprompt.vercel.app/api/v1/optimize \
  -H "Authorization: Bearer cp_live_xxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "user_input": "Write a blog intro about AI in healthcare",
    "project_id": "clx123abc",
    "target_model": "claude"
  }'
```

**Technical Notes**
- New model: `ApiToken { id, userId, name, hashedToken, lastUsedAt, createdAt }`
- Tokens are hashed (SHA-256) before storage — original shown once at creation
- New middleware: `withApiAuth` — checks `Authorization` header, falls back to session
- Rate limiting per token (e.g. 60 req/min on Free, 300 req/min on Pro)
- Auto-generated API docs page at `/docs`

**Why It Matters**
Opens an entirely new developer market. A developer who integrates ContextPrompt into their tool brings their entire user base. Also positions the product for enterprise deals.

---

## 🛠️ TIER 4 — Polish & UX

---

### Feature 10: Prompt Diff View

**The Problem**
Users know the optimizer improves their prompt, but they can't *see* what changed. The value is invisible. When they understand what the optimizer added — the persona, the structure, the detail — they learn how to write better prompts themselves and trust the tool more.

**The Solution**
A **"Show changes"** toggle on the optimized prompt output. When enabled, the display switches to a diff view: text that was added is highlighted in green, the original words from the user's request are shown underlined. Think of it like GitHub's diff view but readable.

**UI Details**
- Toggle button: "Show changes" / "Show clean"
- Green highlighted text = added by the optimizer
- Original words from the user's request = shown with underline
- Side-by-side mode (optional): user input on the left, optimized on the right

**Technical Notes**
- Use the `diff` npm package to compute word-level diffs between `userInput` and `optimizedPrompt`
- Render diff as styled `<span>` elements — no backend changes needed
- Pure frontend feature

**Why It Matters**
Makes the value of the product visible and educational. Users who see the diff understand *why* the optimized prompt is better, which builds trust and reduces churn.

---

### Feature 11: Mobile-Friendly / PWA

**The Problem**
The current layout is desktop-only. The sidebar takes up 25% of the screen on mobile, the text areas are cramped, and the overall UX breaks on phones. Many users will want to generate prompts on the go — at a coffee shop, in a meeting, on public transport.

**The Solution**
A fully **responsive redesign** where:
- On mobile: the sidebar collapses into a slide-out drawer (hamburger menu)
- The prompt input and output stack vertically
- The context editor collapses by default on mobile (can expand)
- Text areas auto-grow instead of fixed height
- The app is installable as a **PWA** (add to home screen, works offline for history viewing)

**Technical Notes**
- Tailwind responsive prefixes (`sm:`, `md:`, `lg:`) for layout changes
- Sidebar becomes `fixed` with slide-in animation on mobile
- PWA: add `manifest.json`, `next-pwa` package, service worker for caching static assets
- Offline: history is readable offline via service worker cache; optimization requires connection

**Why It Matters**
Mobile traffic is typically 40-60% of web traffic. A broken mobile experience means losing half the potential audience. PWA also enables "Add to Home Screen" which dramatically increases retention.

---

## Implementation Priority

| # | Feature | Effort | Revenue Impact | User Impact |
|---|---------|--------|---------------|-------------|
| 1 | Run It (send to AI) | 3 days | 🔥🔥 | 🔥🔥🔥 |
| 2 | Prompt Variables | 2 days | 🔥🔥 | 🔥🔥🔥 |
| 3 | Template Library | 2 days | 🔥🔥🔥 | 🔥🔥🔥 |
| 4 | Favorites | 0.5 days | 🔥 | 🔥🔥 |
| 5 | Team Sharing | 5 days | 🔥🔥🔥 | 🔥🔥🔥 |
| 6 | Export History | 0.5 days | 🔥 | 🔥🔥 |
| 7 | Stripe Subscription | 4 days | 🔥🔥🔥 | 🔥 |
| 8 | BYOK API Keys | 2 days | 🔥🔥 | 🔥🔥 |
| 9 | Public REST API | 3 days | 🔥🔥🔥 | 🔥🔥 |
| 10 | Diff View | 1 day | 🔥 | 🔥🔥 |
| 11 | Mobile / PWA | 3 days | 🔥🔥 | 🔥🔥🔥 |
