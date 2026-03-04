"""
AI intake service — uses LangChain ChatOllama with Redis-backed chat history.

History is stored in Redis (key: zenflow:intake:{user_id}) with a 1-hour TTL.
This means intake context survives bot restarts and is accessible across processes.
History is explicitly cleared after the appointment is saved or the flow is cancelled.
"""
import asyncio
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from langchain_redis import RedisChatMessageHistory

from bot.config import OLLAMA_HOST, OLLAMA_MODEL
from bot.redis_client import get_sync_redis

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

FALLBACK_QUESTIONS = [
    "What's the main issue or discomfort bringing you in today?",
    "How long have you been experiencing this?",
    "Does it tend to get worse at a particular time of day?",
    "How would you rate the intensity on a scale of 1 to 10?",
    "Have you had any treatment for this before?",
]


# ── history helpers ──────────────────────────────────────────────────────────

def _get_history(user_id: int) -> RedisChatMessageHistory:
    return RedisChatMessageHistory(
        session_id=f"zenflow:intake:{user_id}",
        redis_client=get_sync_redis(),
        ttl=3600,  # 1 hour — cleared after appointment saved anyway
    )


def _llm() -> ChatOllama:
    return ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST)


# ── public API ───────────────────────────────────────────────────────────────

def initialize_intake(user_id: int, opening_question: str) -> None:
    """Start a fresh intake: clear old history and record the opening question."""
    hist = _get_history(user_id)
    hist.clear()
    hist.add_ai_message(opening_question)
    logger.info(f"[{user_id}] Intake history initialized")


async def get_next_question(user_id: int, user_answer: str) -> str:
    """Add user answer to history, ask LangChain for the next question."""
    hist = _get_history(user_id)
    hist.add_user_message(user_answer)

    messages = [SystemMessage(content=SYSTEM_PROMPT)] + hist.messages
    try:
        resp = await asyncio.wait_for(_llm().ainvoke(messages), timeout=OLLAMA_TIMEOUT)
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
    return fallback


async def generate_summary(user_id: int, final_answer: str) -> str:
    """Add final answer to history, then generate a clinical summary."""
    hist = _get_history(user_id)
    hist.add_user_message(final_answer)

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        *hist.messages,
        HumanMessage(content=SUMMARY_INSTRUCTION),
    ]
    try:
        resp = await asyncio.wait_for(_llm().ainvoke(messages), timeout=OLLAMA_TIMEOUT)
        logger.info(f"[{user_id}] clinical summary generated via LangChain")
        return resp.content.strip()
    except asyncio.TimeoutError:
        logger.warning(f"[{user_id}] Ollama timeout on summary")
    except Exception as e:
        logger.warning(f"[{user_id}] LangChain error on summary: {e}")
    return "Intake completed — see conversation history for details."


def get_history_dicts(user_id: int) -> list[dict]:
    """Export the LangChain history as plain dicts for the appointment JSON file."""
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
    logger.info(f"[{user_id}] Intake history cleared")
