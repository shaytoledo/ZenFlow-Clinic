"""
AI intake service — uses LangChain ChatOllama with Redis-backed chat history.

History is stored in Redis (key: zenflow:intake:{user_id}) with a 30-min TTL.
This means intake context survives bot restarts and is accessible across processes.
History is explicitly cleared after the appointment is saved or the flow is cancelled.

Speed optimisations applied:
- Singleton LLM: ChatOllama created once at module level (not per call)
- ConversationSummaryBuffer: rolls up old messages into a summary to keep context short
- In-process history cache: RedisChatMessageHistory object reused per user (avoids LRANGE on every call)
"""
import asyncio
import json
import logging
import re

from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

from bot.config import OLLAMA_HOST, OLLAMA_MODEL, REDIS_URL

logger = logging.getLogger(__name__)

OLLAMA_TIMEOUT = 100  # seconds

SYSTEM_PROMPT = """\
You are a clinical intake assistant for ZenFlow, a licensed Traditional Chinese Medicine (TCM) acupuncture clinic.
Your role is to conduct a SHORT adaptive intake conversation before the patient's session — \
gathering the information the acupuncturist needs to diagnose and treat.

CORE RULES:
- Ask ONE question per message, never more.
- Every question must adapt to the patient's previous answer — do NOT follow a fixed script.
- Keep questions short and conversational. No medical jargon.
- Accept short answers ("yes", "no", "כן", "לא") and follow up meaningfully.
- Aim to conclude in 5 exchanges. NEVER exceed 7.
- ALWAYS ask your questions in English. If the patient replies in Hebrew, that is fine — continue in English.
- Do NOT include greetings or pleasantries. Just ask the question directly.\
"""

SUMMARY_INSTRUCTION = """\
Based on this intake conversation, write a concise clinical summary for the acupuncturist.
Include: chief complaint, key symptoms, relevant TCM patterns if apparent, and suggested focus areas.
Keep it to 4–5 bullet points in English.\
"""

BUFFER_SUMMARIZE_PROMPT = """\
You are summarising a partial medical intake conversation for an acupuncturist.
Condense the following exchange into 2-3 sentences that capture the key clinical information so far.
Be concise and factual. Do not add new information.\
"""

TCM_DIAGNOSIS_PROMPT = """\
Based on the intake conversation and clinical summary above, provide a structured TCM clinical assessment.
Return ONLY valid JSON with these exact keys:
- tcm_pattern (string: primary TCM pattern/syndrome)
- treatment_principles (string)
- diagnosis_certainty (integer 0-100: your confidence in this diagnosis given the available information)
- suggested_points (list of 4-8 objects, each with "code" and "rationale" keys explaining why this point for this patient)
- recommendations (object with string values for keys: diet, sleep, exercise, stress)

Example format:
{
  "tcm_pattern": "Liver Qi Stagnation with Blood Deficiency",
  "treatment_principles": "Move Liver Qi, nourish Blood, calm Shen",
  "diagnosis_certainty": 72,
  "suggested_points": [
    {"code": "LR3", "rationale": "Primary point to move Liver Qi and descend Liver Yang"},
    {"code": "SP6", "rationale": "Nourishes Blood and Yin, calms the Shen, regulates menstruation"},
    {"code": "HT7", "rationale": "Directly calms the Shen and tonifies Heart Blood"},
    {"code": "PC6", "rationale": "Opens the chest and relieves emotional tension"},
    {"code": "ST36", "rationale": "Strengthens Qi and supports Blood production"}
  ],
  "recommendations": {
    "diet": "Favour warm, cooked foods with dark leafy greens and beets to nourish Blood.",
    "sleep": "Establish a consistent sleep schedule; avoid screens 1 hour before bed.",
    "exercise": "Gentle movement such as Tai Chi or walking — avoid intense exercise.",
    "stress": "Daily 10-minute breathing or meditation practice to regulate Liver Qi."
  }
}
Return ONLY the JSON with no additional text or markdown.\
"""

FALLBACK_QUESTIONS = [
    "What's the main issue or discomfort bringing you in today?",
    "How long have you been experiencing this?",
    "Does it tend to get worse at a particular time of day?",
    "How would you rate the intensity on a scale of 1 to 10?",
    "Have you had any treatment for this before?",
]

# ── singleton LLM — created once, reused for all calls ───────────────────────
_LLM = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST)

# ── Ollama health check at startup ────────────────────────────────────────────
def _check_ollama_health() -> None:
    """Log a clear warning if Ollama is unreachable at startup."""
    import urllib.request, urllib.error
    try:
        urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3)
        logger.info(f"Ollama reachable at {OLLAMA_HOST}, model: {OLLAMA_MODEL}")
    except Exception as e:
        logger.warning(
            f"Ollama is NOT reachable at {OLLAMA_HOST}: {e}\n"
            f"  → Intake will use fallback questions.\n"
            f"  → Start Ollama with: ollama serve"
        )

_check_ollama_health()

# ── in-process history cache — avoids Redis LRANGE on every call ─────────────
_history_cache: dict[int, RedisChatMessageHistory] = {}

# ── rolling summaries for ConversationSummaryBuffer pattern ──────────────────
_rolling_summaries: dict[int, str] = {}

# Number of messages to keep in live history before compressing older ones
_BUFFER_KEEP = 4
_BUFFER_MAX  = 6


def _get_history(user_id: int) -> RedisChatMessageHistory:
    if user_id not in _history_cache:
        _history_cache[user_id] = RedisChatMessageHistory(
            session_id=f"zenflow:intake:{user_id}",
            url=REDIS_URL,
            ttl=1800,  # 30 minutes — cleared after appointment saved anyway
        )
    return _history_cache[user_id]


async def _maybe_compress(user_id: int) -> None:
    """If history is getting long, summarise the oldest messages and trim."""
    hist = _get_history(user_id)
    msgs = hist.messages
    if len(msgs) <= _BUFFER_MAX:
        return

    # Messages to compress: everything except the most recent _BUFFER_KEEP
    to_compress = msgs[:len(msgs) - _BUFFER_KEEP]
    recent      = msgs[len(msgs) - _BUFFER_KEEP:]

    # Build a short textual transcript for the summariser
    transcript_parts = []
    for m in to_compress:
        role = "Patient" if isinstance(m, HumanMessage) else "Assistant"
        transcript_parts.append(f"{role}: {m.content}")
    transcript = "\n".join(transcript_parts)

    existing = _rolling_summaries.get(user_id, "")
    if existing:
        prompt_text = (
            f"{BUFFER_SUMMARIZE_PROMPT}\n\n"
            f"Previous summary: {existing}\n\n"
            f"New exchanges:\n{transcript}"
        )
    else:
        prompt_text = f"{BUFFER_SUMMARIZE_PROMPT}\n\n{transcript}"

    try:
        resp = await asyncio.wait_for(
            _LLM.ainvoke([SystemMessage(content=prompt_text)]),
            timeout=30,
        )
        _rolling_summaries[user_id] = resp.content.strip()
        logger.info(f"[{user_id}] History compressed: {len(to_compress)} msgs → summary")
    except Exception as e:
        logger.warning(f"[{user_id}] Compression failed: {e} — keeping full history")
        return

    # Replace history with just the recent messages
    hist.clear()
    for m in recent:
        if isinstance(m, HumanMessage):
            hist.add_user_message(m.content)
        elif isinstance(m, AIMessage):
            hist.add_ai_message(m.content)


# ── public API ────────────────────────────────────────────────────────────────

def initialize_intake(user_id: int, opening_question: str) -> None:
    """Start a fresh intake: clear old history and record the opening question."""
    _history_cache.pop(user_id, None)  # drop stale cache entry first
    _rolling_summaries.pop(user_id, None)
    hist = _get_history(user_id)
    hist.clear()
    hist.add_ai_message(opening_question)
    logger.info(f"[{user_id}] Intake history initialized")


async def get_next_question(user_id: int, user_answer: str) -> str:
    """Add user answer to history, ask LangChain for the next question."""
    hist = _get_history(user_id)
    hist.add_user_message(user_answer)

    # Compress history if it's growing too long
    await _maybe_compress(user_id)

    # Build context: system prompt + optional rolling summary + recent messages
    summary = _rolling_summaries.get(user_id)
    if summary:
        context_messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            SystemMessage(content=f"[Conversation so far: {summary}]"),
            *hist.messages[-_BUFFER_KEEP:],
        ]
    else:
        context_messages = [SystemMessage(content=SYSTEM_PROMPT)] + hist.messages[-3:]

    try:
        resp = await asyncio.wait_for(_LLM.ainvoke(context_messages), timeout=OLLAMA_TIMEOUT)
        question = resp.content.strip()
        hist.add_ai_message(question)
        logger.info(f"[{user_id}] next question generated via LangChain")
        return question
    except asyncio.TimeoutError:
        logger.warning(f"[{user_id}] Ollama timeout — using fallback question")
    except Exception as e:
        logger.warning(f"[{user_id}] LangChain error: {e} — using fallback question")

    answered = sum(1 for m in hist.messages if isinstance(m, HumanMessage))
    fallback = FALLBACK_QUESTIONS[min(answered, len(FALLBACK_QUESTIONS) - 1)]
    hist.add_ai_message(fallback)
    logger.info(f"[{user_id}] fallback question {answered + 1} sent")
    return fallback


async def generate_summary(user_id: int, final_answer: str) -> str:
    """Add final answer to history, then generate a clinical summary."""
    hist = _get_history(user_id)
    hist.add_user_message(final_answer)

    # Include rolling summary if one exists, so the acupuncturist sees everything
    summary = _rolling_summaries.get(user_id)
    if summary:
        prefix = [
            SystemMessage(content=SYSTEM_PROMPT),
            SystemMessage(content=f"[Earlier conversation summary: {summary}]"),
        ]
    else:
        prefix = [SystemMessage(content=SYSTEM_PROMPT)]

    messages = [
        *prefix,
        *hist.messages,
        HumanMessage(content=SUMMARY_INSTRUCTION),
    ]
    try:
        resp = await asyncio.wait_for(_LLM.ainvoke(messages), timeout=OLLAMA_TIMEOUT)
        logger.info(f"[{user_id}] clinical summary generated via LangChain")
        return resp.content.strip()
    except asyncio.TimeoutError:
        logger.warning(f"[{user_id}] Ollama timeout on summary")
    except Exception as e:
        logger.warning(f"[{user_id}] LangChain error on summary: {e}")
    return "Intake completed — see conversation history for details."


async def generate_tcm_diagnosis(user_id: int, clinical_summary: str) -> dict:
    """Generate a structured TCM diagnosis from the intake conversation and clinical summary."""
    hist = _get_history(user_id)
    summary = _rolling_summaries.get(user_id)

    # Build full context
    context_parts = [SystemMessage(content=SYSTEM_PROMPT)]
    if summary:
        context_parts.append(SystemMessage(content=f"[Earlier conversation summary: {summary}]"))
    context_parts.extend(hist.messages)
    context_parts.append(HumanMessage(content=f"Clinical summary:\n{clinical_summary}\n\n{TCM_DIAGNOSIS_PROMPT}"))

    fallback: dict = {
        "tcm_pattern": "",
        "treatment_principles": "",
        "diagnosis_certainty": 0,
        "suggested_points": [],
        "recommendations": {"diet": "", "sleep": "", "exercise": "", "stress": ""},
    }

    try:
        resp = await asyncio.wait_for(_LLM.ainvoke(context_parts), timeout=OLLAMA_TIMEOUT)
        raw = resp.content.strip()

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        # Extract JSON object from response (in case there's surrounding text)
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            raw = match.group(0)

        parsed = json.loads(raw)

        # Normalise suggested_points: accept list of dicts or list of strings
        raw_points = parsed.get("suggested_points", [])
        suggested_points = []
        for pt in raw_points:
            if isinstance(pt, dict):
                suggested_points.append({
                    "code": str(pt.get("code", "")).upper().strip(),
                    "rationale": str(pt.get("rationale", "")),
                })
            elif isinstance(pt, str) and pt.strip():
                suggested_points.append({"code": pt.strip().upper(), "rationale": ""})

        # Validate and normalise structure
        raw_certainty = parsed.get("diagnosis_certainty", 0)
        result: dict = {
            "tcm_pattern": str(parsed.get("tcm_pattern", "")),
            "treatment_principles": str(parsed.get("treatment_principles", "")),
            "diagnosis_certainty": int(raw_certainty) if isinstance(raw_certainty, (int, float)) else 0,
            "suggested_points": suggested_points,
            "recommendations": {
                "diet":     str(parsed.get("recommendations", {}).get("diet", "")),
                "sleep":    str(parsed.get("recommendations", {}).get("sleep", "")),
                "exercise": str(parsed.get("recommendations", {}).get("exercise", "")),
                "stress":   str(parsed.get("recommendations", {}).get("stress", "")),
            },
        }
        logger.info(f"[{user_id}] TCM diagnosis generated: {result['tcm_pattern']} ({result['diagnosis_certainty']}%)")
        return result

    except asyncio.TimeoutError:
        logger.warning(f"[{user_id}] Ollama timeout on TCM diagnosis")
    except json.JSONDecodeError as e:
        logger.warning(f"[{user_id}] TCM diagnosis JSON parse error: {e}")
    except Exception as e:
        logger.warning(f"[{user_id}] TCM diagnosis error: {e}")

    return fallback


def get_history_dicts(user_id: int) -> list[dict]:
    """Export the LangChain history as plain dicts for the appointment record."""
    result = []
    for msg in _get_history(user_id).messages:
        if isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            result.append({"role": "assistant", "content": msg.content})
    return result


def clear_intake(user_id: int) -> None:
    """Drop the Redis intake history after the appointment is saved."""
    _get_history(user_id).clear()
    _history_cache.pop(user_id, None)
    _rolling_summaries.pop(user_id, None)
    logger.info(f"[{user_id}] Intake history cleared")
