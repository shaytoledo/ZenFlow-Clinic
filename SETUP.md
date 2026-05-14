# ContextPrompt — Setup Guide

## Prerequisites
- Python 3.11+
- Node.js 20+
- An Anthropic API key

---

## Backend

```bash
cd backend

# 1. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create the .env file
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 4. Run the API server (auto-creates the SQLite DB on first start)
python -m uvicorn backend.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`

---

## Frontend

```bash
cd frontend

# 1. Install dependencies
npm install

# 2. Start the dev server
npm run dev
```

The app will be available at `http://localhost:3000`.

The Next.js dev server proxies all `/api/*` requests to `http://localhost:8000`.

---

## API Overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects/` | List all projects |
| POST | `/api/projects/` | Create a project |
| GET | `/api/projects/{id}` | Get project + history |
| PATCH | `/api/projects/{id}` | Update project name / context |
| DELETE | `/api/projects/{id}` | Delete project |
| POST | `/api/projects/{id}/prompts/optimize` | Optimize a prompt |
| GET | `/api/projects/{id}/prompts/` | List prompt history |
| DELETE | `/api/projects/{id}/prompts/{hid}` | Delete a history entry |

## Optimize request body

```json
{
  "user_input": "Write an Instagram post about acupuncture for athletes",
  "target_model": "claude"   // or "gpt-4" / "gemini"
}
```
