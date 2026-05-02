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
import os
import re

from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from bot.config import OLLAMA_HOST, OLLAMA_MODEL, REDIS_URL

USE_AI = os.getenv("USE_AI", "ollama")

logger = logging.getLogger(__name__)

OLLAMA_TIMEOUT = 180  # seconds

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

SYSTEM_PROMPT_HE = """\
אתה עוזר קבלה קליני של מרפאת ZenFlow, מרפאת דיקור סיני (TCM) מורשית.
תפקידך לנהל שיחת תשאול קצרה ומותאמת לפני הטיפול — \
לאסוף את המידע שהמטפל זקוק לו לאבחון ולטיפול.

כללים:
- שאל שאלה אחת בלבד בכל הודעה.
- כל שאלה חייבת להתאים לתשובה הקודמת של המטופל — אל תלך לפי סקריפט קבוע.
- שמור על שאלות קצרות ושיחתיות. אל תשתמש בז׳רגון רפואי.
- קבל תשובות קצרות ("כן", "לא", "yes", "no") והמשך בהתאם.
- שאף לסיים ב-5 החלפות. לא יותר מ-7.
- שאל תמיד בעברית.
- אל תכלול ברכות. פשוט שאל את השאלה ישירות.\
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
You are a senior TCM acupuncturist preparing an assessment for a single specific patient.
Use the ENTIRE intake transcript, clinical summary, and (if provided) the therapist's tongue & pulse
findings. Do not repeat textbook generalities — every field must reference SPECIFIC details the
patient mentioned (sleep pattern, stress source, pain location, menstrual cycle, digestion, etc.).

Return ONLY valid JSON (no prose, no code fences) with these exact keys:
- tcm_pattern               primary syndrome in standard TCM terminology
- treatment_principles      clinical strategy in 1-2 sentences
- diagnosis_certainty       integer 0-100
- recommendations           object with string values for "diet", "sleep", "exercise", "stress" — 1-2 personalised sentences each

Example shape (do NOT copy values):
{
  "tcm_pattern": "Liver Qi Stagnation with Blood Deficiency",
  "treatment_principles": "Move Liver Qi, nourish Blood, calm Shen",
  "diagnosis_certainty": 72,
  "recommendations": {
    "diet":     "Replace skipped breakfasts with warm congee; avoid raw salads and iced drinks.",
    "sleep":    "Wind down 30 min before bed with no screens; aim to sleep by 23:00.",
    "exercise": "Replace intense cardio with 4 km brisk walking + 20 min Qi Gong.",
    "stress":   "10-min box breathing before opening work email; journal before bed."
  }
}
Return ONLY the JSON with no additional text or markdown.\
"""

POINT_SELECTION_PROMPT = """\
You are a senior TCM acupuncturist. Build the FIRST HALF of a precise acupuncture formula for this patient.

DIAGNOSIS:
Pattern: {tcm_pattern}
Principles: {treatment_principles}

PATIENT CONTEXT (use SPECIFIC symptoms mentioned below for every rationale):
{intake_context}

REFERENCE POINTS (prefer these; add others with standard notation if needed):
ST36 Zusanli - Tonifies Qi/Blood, digestion, immunity
LI4  Hegu    - Clears Wind-Heat, stops pain (AVOID pregnancy)
PC6  Neiguan - Heart, nausea, opens chest
LR3  Taichong - Liver Qi, Yang, menstruation
SP6  Sanyinjiao - Blood/Yin, menstruation, Spleen
GV20 Baihui  - Mind, Yang, brain
HT7  Shenmen - Shen, insomnia, anxiety
KD3  Taixi   - Kidney Yin/Yang, lower back
GB20 Fengchi - Wind, headache, eyes
GB21 Jianjing - Shoulder/neck (AVOID pregnancy)
TE5  Waiguan - Wind-Heat, Yang Linking
BL23 Shenshu - Kidney, lumbar, ears
BL40 Weizhong - Lower back, sciatica
CV6  Qihai   - Original Qi, Yang
CV12 Zhongwan - Spleen/Stomach, Damp
CV17 Shanzhong - Chest, Shen
GV14 Dazhui  - Exterior, Heat, Yang
LU7  Lieque  - Exterior Wind, Lung
LI11 Quchi   - Heat, Blood, Damp
SP9  Yinlingquan - Dampness, Lower Burner
ST40 Fenglong - Phlegm/Damp, Shen
YINTANG - Shen, frontal headache, sleep

RULES:
- Select exactly 5 to 7 points for this first batch (NEVER fewer than 5, NEVER more than 7).
- Each rationale must cite THIS patient's symptom — no generic text.
- Include location (anatomical landmark) and needle_technique.

Output ONLY a raw JSON array. No markdown, no explanation, no wrapper object.
Example format (replace all values — do not copy):
[
  {{"code":"LR3","rationale":"specific reason for THIS patient","location":"dorsum of foot, 1st-2nd metatarsal junction","needle_technique":"perpendicular 0.5-1 cun"}},
  {{"code":"SP6","rationale":"specific reason for THIS patient","location":"3 cun above medial malleolus, posterior tibia border","needle_technique":"perpendicular 1-1.5 cun"}}
]
OUTPUT ONLY THE JSON ARRAY. NOTHING ELSE.\
"""

POINT_SELECTION_PROMPT_BATCH2 = """\
You are a senior TCM acupuncturist. You have already selected the first batch of acupuncture points for this patient.
Now select 5 to 7 ADDITIONAL complementary points to complete the formula.

DIAGNOSIS:
Pattern: {tcm_pattern}
Principles: {treatment_principles}

PATIENT CONTEXT:
{intake_context}

ALREADY SELECTED POINTS (DO NOT repeat any of these):
{existing_codes}

Select complementary points that address aspects of the pattern not yet covered by the first batch.

RULES:
- Select exactly 5 to 7 NEW points (NEVER fewer than 5, NEVER more than 7).
- Do NOT repeat any point from the already-selected list above.
- Each rationale must cite THIS patient's symptom — no generic text.
- Include location (anatomical landmark) and needle_technique.
- Maintain clinical coherence with the first batch.

Output ONLY a raw JSON array. No markdown, no explanation, no wrapper object.
[
  {{"code":"HT7","rationale":"specific reason for THIS patient","location":"wrist crease, radial side of flexor carpi ulnaris","needle_technique":"perpendicular 0.3-0.5 cun"}}
]
OUTPUT ONLY THE JSON ARRAY. NOTHING ELSE.\
"""

TCM_DIAGNOSIS_PROMPT_HE = """\
אתה מטפל TCM בכיר המכין הערכה למטופל ספציפי יחיד.
השתמש בתמליל התשאול המלא, בסיכום הקליני ובממצאי הלשון/הדופק של המטפל (אם סופקו).
כל שדה חייב להתייחס לפרטים ספציפיים שהמטופל ציין — אל תחזור על טקסט ספר לימוד.

החזר JSON תקין בלבד (ללא פרוזה, ללא גדרות קוד) עם המפתחות הבאים:
- tcm_pattern               תסמונת ראשית בטרמינולוגיה TCM סטנדרטית (בעברית)
- treatment_principles      אסטרטגיה קלינית ב-1-2 משפטים (בעברית)
- diagnosis_certainty       מספר שלם 0-100
- recommendations           אובייקט עם ערכי מחרוזת עבור "diet", "sleep", "exercise", "stress" — 1-2 משפטים מותאמים אישית כל אחד (בעברית)

החזר את ה-JSON בלבד, ללא טקסט נוסף.\
"""

POINT_SELECTION_PROMPT_HE = """\
אתה מטפל TCM בכיר. בנה את החלק הראשון של פורמולת דיקור מדויקת למטופל זה.

אבחנה:
דפוס: {tcm_pattern}
עקרונות טיפול: {treatment_principles}

הקשר המטופל (השתמש בתסמינים הספציפיים שלמטה לכל נימוק):
{intake_context}

נקודות ייחוס (העדף אלה; הוסף אחרות בסימון סטנדרטי אם נדרש):
ST36 Zusanli - מטפח Qi/דם, עיכול, חסינות
LI4  Hegu    - מפנה רוח-חום, עוצר כאב (הימנע בהריון)
PC6  Neiguan - לב, בחילה, פותח חזה
LR3  Taichong - Liver Qi, Yang, מחזור
SP6  Sanyinjiao - דם/Yin, מחזור, Spleen
GV20 Baihui  - נפש, Yang, מוח
HT7  Shenmen - Shen, נדודי שינה, חרדה
KD3  Taixi   - Kidney Yin/Yang, גב תחתון
GB20 Fengchi - רוח, כאב ראש, עיניים
BL23 Shenshu - כליה, מותני, אוזניים
CV6  Qihai   - Qi מקורי, Yang
CV12 Zhongwan - Spleen/Stomach, לחות
CV17 Shanzhong - חזה, Shen
GV14 Dazhui  - חיצוני, חום, Yang
LU7  Lieque  - רוח חיצוני, ריאה
LI11 Quchi   - חום, דם, לחות
SP9  Yinlingquan - לחות, Burner תחתון
ST40 Fenglong - ליחה/לחות, Shen
YINTANG - Shen, כאב ראש קדמי, שינה

כללים:
- בחר בדיוק 5 עד 7 נקודות לאצווה זו (לא פחות מ-5, לא יותר מ-7).
- כל נימוק (rationale) חייב להצביע על תסמין ספציפי של מטופל זה — ללא טקסט גנרי.
- שמור על קודי הנקודות באנגלית (למשל ST36, LR3, YINTANG) — אל תתרגם אותם.
- כתוב את שדות rationale, location ו-needle_technique בעברית.

פלט מערך JSON גולמי בלבד. ללא markdown, ללא הסבר, ללא עטיפה.
[
  {{"code":"LR3","rationale":"נימוק ספציפי למטופל זה בעברית","location":"גב כף הרגל, חיבור מטטרסלים 1 ו-2","needle_technique":"אנכי 0.5-1 cun"}},
  {{"code":"SP6","rationale":"נימוק ספציפי למטופל זה בעברית","location":"3 cun מעל הקרסול המדיאלי, לאחור משפת הטיביה","needle_technique":"אנכי 1-1.5 cun"}}
]
פלט מערך ה-JSON בלבד.\
"""

POINT_SELECTION_PROMPT_BATCH2_HE = """\
אתה מטפל TCM בכיר. כבר בחרת את האצווה הראשונה של נקודות למטופל זה.
כעת בחר 5 עד 7 נקודות משלימות להשלמת הפורמולה.

אבחנה:
דפוס: {tcm_pattern}
עקרונות טיפול: {treatment_principles}

הקשר המטופל:
{intake_context}

נקודות שנבחרו כבר (אל תחזור על אף אחת):
{existing_codes}

כללים:
- בחר בדיוק 5 עד 7 נקודות חדשות (לא פחות מ-5, לא יותר מ-7).
- אל תחזור על נקודה שנמצאת ברשימה לעיל.
- כל נימוק חייב להצביע על תסמין ספציפי של מטופל זה.
- שמור על קודי הנקודות באנגלית — אל תתרגם אותם.
- כתוב את שדות rationale, location ו-needle_technique בעברית.
- שמור על קוהרנטיות קלינית עם האצווה הראשונה.

פלט מערך JSON גולמי בלבד.
[
  {{"code":"HT7","rationale":"נימוק ספציפי בעברית","location":"קמט פרק כף היד, הצד הרדיאלי של flexor carpi ulnaris","needle_technique":"אנכי 0.3-0.5 cun"}}
]
פלט מערך ה-JSON בלבד.\
"""


def get_diagnosis_prompt(lang: str = "en") -> str:
    return TCM_DIAGNOSIS_PROMPT_HE if lang == "he" else TCM_DIAGNOSIS_PROMPT


FALLBACK_QUESTIONS = [
    "What's the main issue or discomfort bringing you in today?",
    "How long have you been experiencing this?",
    "Does it tend to get worse at a particular time of day?",
    "How would you rate the intensity on a scale of 1 to 10?",
    "Have you had any treatment for this before?",
]

FALLBACK_QUESTIONS_HE = [
    "מה הבעיה העיקרית או הכאב שהביא אותך לטיפול היום?",
    "כמה זמן אתה חווה את זה?",
    "האם זה מחמיר בשעה מסוימת ביום?",
    "כיצד היית מדרג את העוצמה בסולם של 1 עד 10?",
    "האם קיבלת טיפול כלשהו בגלל זה בעבר?",
]


def get_fallback_questions(lang: str = "en") -> list[str]:
    return FALLBACK_QUESTIONS_HE if lang == "he" else FALLBACK_QUESTIONS


def get_system_prompt(lang: str = "en") -> str:
    return SYSTEM_PROMPT_HE if lang == "he" else SYSTEM_PROMPT

# ── LLM singleton — selected by USE_AI env var ────────────────────────────────
if USE_AI == "anthropic":
    try:
        from langchain_anthropic import ChatAnthropic
        _ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
        _LLM        = ChatAnthropic(model="claude-haiku-4-5-20251001", api_key=_ANTHROPIC_KEY, max_tokens=150)
        _LLM_LONG   = ChatAnthropic(model="claude-haiku-4-5-20251001", api_key=_ANTHROPIC_KEY, max_tokens=800)
        # Point selection needs more tokens for 6-10 point objects with rationale
        _LLM_POINTS = ChatAnthropic(model="claude-haiku-4-5-20251001", api_key=_ANTHROPIC_KEY, max_tokens=1800)
        logger.info("AI backend: Anthropic Claude (claude-haiku-4-5-20251001)")
    except ImportError:
        logger.warning("langchain-anthropic not installed — falling back to Ollama")
        USE_AI = "ollama"

if USE_AI != "anthropic":
    try:
        from langchain_ollama import ChatOllama
        _LLM        = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST, num_predict=100,  num_ctx=512)
        _LLM_LONG   = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST, num_predict=800,  num_ctx=2048)
        # Dedicated high-context instance for point selection:
        # num_ctx=4096 gives room for prompt (~400 tok) + 10 detailed points (~1500 tok output)
        _LLM_POINTS = ChatOllama(model=OLLAMA_MODEL, base_url=OLLAMA_HOST, num_predict=1800, num_ctx=4096)
        logger.info(f"AI backend: Ollama ({OLLAMA_MODEL} @ {OLLAMA_HOST})")
    except ImportError:
        logger.warning("langchain-ollama not installed — intake will use fallback questions only")
        _LLM        = None
        _LLM_LONG   = None
        _LLM_POINTS = None

# ── Ollama health check at startup (only in ollama mode) ──────────────────────
if USE_AI != "anthropic":
    def _check_ollama_health() -> None:
        import urllib.request
        try:
            urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3)
            logger.info(f"Ollama reachable at {OLLAMA_HOST}, model: {OLLAMA_MODEL}")
        except Exception as e:
            logger.warning(
                f"Ollama is NOT reachable at {OLLAMA_HOST}: {e}\n"
                f"  → Intake will use fallback questions.\n"
                f"  → Set USE_AI=anthropic + ANTHROPIC_API_KEY for cloud AI."
            )
    _check_ollama_health()

# ── in-process history cache — avoids Redis LRANGE on every call ─────────────
_history_cache: dict[int, RedisChatMessageHistory] = {}

# ── rolling summaries for ConversationSummaryBuffer pattern ──────────────────
_rolling_summaries: dict[int, str] = {}

# Number of messages to keep in live history before compressing older ones.
# Max intake is 5 questions = 10 messages — set buffer above that so compression
# never fires mid-intake (it would add an extra Ollama round-trip).
_BUFFER_KEEP = 4
_BUFFER_MAX  = 12


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
    if _LLM is None:
        return
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


async def get_next_question(user_id: int, user_answer: str, lang: str = "en") -> str:
    """Add user answer to history, ask LangChain for the next question."""
    hist = _get_history(user_id)
    hist.add_user_message(user_answer)

    # Compress history if it's growing too long
    await _maybe_compress(user_id)

    sys_prompt = get_system_prompt(lang)

    # Build context: system prompt + optional rolling summary + recent messages
    summary = _rolling_summaries.get(user_id)
    if summary:
        context_messages = [
            SystemMessage(content=sys_prompt),
            SystemMessage(content=f"[Conversation so far: {summary}]"),
            *hist.messages[-_BUFFER_KEEP:],
        ]
    else:
        context_messages = [SystemMessage(content=sys_prompt)] + hist.messages[-3:]

    if _LLM is not None:
        try:
            resp = await asyncio.wait_for(_LLM.ainvoke(context_messages), timeout=OLLAMA_TIMEOUT)
            question = resp.content.strip()
            hist.add_ai_message(question)
            logger.info(f"[{user_id}] next question generated via LangChain ({USE_AI})")
            return question
        except asyncio.TimeoutError:
            logger.warning(f"[{user_id}] AI timeout — using fallback question")
        except Exception as e:
            logger.warning(f"[{user_id}] LangChain error: {e} — using fallback question")

    fallback_qs = get_fallback_questions(lang)
    answered = sum(1 for m in hist.messages if isinstance(m, HumanMessage))
    fallback = fallback_qs[min(answered, len(fallback_qs) - 1)]
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
    if _LLM_LONG is not None:
        try:
            resp = await asyncio.wait_for(_LLM_LONG.ainvoke(messages), timeout=OLLAMA_TIMEOUT)
            logger.info(f"[{user_id}] clinical summary generated via LangChain ({USE_AI})")
            return resp.content.strip()
        except asyncio.TimeoutError:
            logger.warning(f"[{user_id}] AI timeout on summary")
        except Exception as e:
            logger.warning(f"[{user_id}] LangChain error on summary: {e}")
    return "Intake completed — see conversation history for details."


def _strip_json(raw: str) -> str:
    """Strip markdown fences and return the first JSON object or array found."""
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    # Try array first, then object
    m = re.search(r"\[[\s\S]*\]", raw)
    if m:
        return m.group(0)
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        return m.group(0)
    return raw


def _normalise_points(raw_points: list) -> list[dict]:
    """Coerce a list of AI point objects or strings into clean {code, rationale} dicts."""
    out = []
    for pt in raw_points:
        if isinstance(pt, dict):
            code = str(pt.get("code") or pt.get("point") or "").upper().strip()
            if code:
                out.append({"code": code, "rationale": str(pt.get("rationale") or pt.get("why") or "")})
        elif isinstance(pt, str) and pt.strip():
            out.append({"code": pt.strip().upper(), "rationale": ""})
    return out


def _parse_points_response(raw: str, log_tag: str) -> list[dict]:
    """Parse the AI point-selection response into a clean list of point dicts.

    Handles: plain array, object-wrapped array, partial JSON (truncated output).
    Each returned dict has: code, rationale, location, needle_technique.
    """
    stripped = _strip_json(raw)
    logger.debug(f"[{log_tag}] raw point response (first 500 chars): {raw[:500]!r}")

    # Try direct parse first
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        # Attempt to recover a partial array — find all complete point objects
        object_pattern = re.compile(
            r'\{\s*"code"\s*:\s*"([^"]+)"[^}]*"rationale"\s*:\s*"([^"]*)"[^}]*\}',
            re.DOTALL,
        )
        matches = object_pattern.findall(stripped)
        if matches:
            logger.warning(f"[{log_tag}] Partial JSON recovery: extracted {len(matches)} point(s) via regex")
            recovered = []
            for m in object_pattern.finditer(stripped):
                try:
                    # Re-parse the individual object to get all fields
                    obj_str = m.group(0)
                    recovered.append(json.loads(obj_str))
                except json.JSONDecodeError:
                    recovered.append({"code": m.group(1), "rationale": m.group(2)})
            parsed = recovered
        else:
            logger.warning(f"[{log_tag}] Point selection JSON fully unparseable — raw: {raw[:300]!r}")
            return []

    # Unwrap object envelope if model ignored instructions
    if isinstance(parsed, dict):
        for key in ("suggested_points", "points", "formula", "acupuncture_points"):
            if key in parsed and isinstance(parsed[key], list):
                parsed = parsed[key]
                logger.info(f"[{log_tag}] Unwrapped object envelope via key '{key}'")
                break
        else:
            logger.warning(f"[{log_tag}] Unexpected dict shape — keys: {list(parsed.keys())}")
            return []

    if not isinstance(parsed, list):
        logger.warning(f"[{log_tag}] Point response is neither list nor dict")
        return []

    # Normalise each point to the extended schema
    out: list[dict] = []
    for pt in parsed:
        if isinstance(pt, dict):
            code = str(pt.get("code") or pt.get("point") or "").strip().upper()
            if not code:
                continue
            out.append({
                "code":             code,
                "rationale":        str(pt.get("rationale")        or pt.get("why")            or ""),
                "location":         str(pt.get("location")         or pt.get("anatomical_location") or ""),
                "needle_technique": str(pt.get("needle_technique") or pt.get("technique")      or ""),
            })
        elif isinstance(pt, str) and pt.strip():
            out.append({"code": pt.strip().upper(), "rationale": "", "location": "", "needle_technique": ""})

    logger.info(f"[{log_tag}] Parsed {len(out)} valid points from AI response")
    return out


async def select_points_for_diagnosis(
    tcm_pattern: str,
    treatment_principles: str,
    intake_context: str,
    log_tag: str = "?",
    _retry: int = 0,
    batch_number: int = 1,
    existing_codes: list[str] | None = None,
    lang: str = "en",
) -> list[dict]:
    """Stage 2 — given an established diagnosis, ask the AI for acupuncture points.

    batch_number=1: first batch of 5-7 points.
    batch_number=2: second batch of 5-7 complementary points (pass existing_codes to avoid duplication).
    Uses a dedicated high-context LLM (_LLM_POINTS) to avoid token truncation.
    Retries once automatically on empty result before giving up.
    Returns a list of extended point dicts: {code, rationale, location, needle_technique}.
    """
    llm = _LLM_POINTS  # type: ignore[name-defined]
    if llm is None:
        logger.error(f"[{log_tag}] _LLM_POINTS is None — point selection skipped")
        return []

    if batch_number == 2 and existing_codes:
        existing_list = ", ".join(existing_codes)
        tmpl = POINT_SELECTION_PROMPT_BATCH2_HE if lang == "he" else POINT_SELECTION_PROMPT_BATCH2
        prompt = tmpl.format(
            tcm_pattern=tcm_pattern or "Unknown pattern",
            treatment_principles=treatment_principles or "Restore balance",
            intake_context=(intake_context or "No prior intake on file.")[:800],
            existing_codes=existing_list,
        )
    else:
        tmpl = POINT_SELECTION_PROMPT_HE if lang == "he" else POINT_SELECTION_PROMPT
        prompt = tmpl.format(
            tcm_pattern=tcm_pattern or "Unknown pattern",
            treatment_principles=treatment_principles or "Restore balance",
            intake_context=(intake_context or "No prior intake on file.")[:1000],
        )
    logger.info(f"[{log_tag}] Stage 2 batch {batch_number} start — pattern='{tcm_pattern}' attempt={_retry + 1}/2")

    try:
        resp = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=OLLAMA_TIMEOUT,
        )
        points = _parse_points_response(resp.content.strip(), log_tag)

        # Retry once if the model returned nothing (parse failed or empty array)
        if not points and _retry == 0:
            logger.warning(f"[{log_tag}] Stage 2 batch {batch_number} returned 0 points on attempt 1 — retrying")
            return await select_points_for_diagnosis(
                tcm_pattern, treatment_principles, intake_context,
                log_tag=log_tag, _retry=1,
                batch_number=batch_number, existing_codes=existing_codes, lang=lang,
            )

        logger.info(f"[{log_tag}] Stage 2 batch {batch_number} complete — {len(points)} points selected")
        return points

    except asyncio.TimeoutError:
        logger.warning(f"[{log_tag}] Ollama timeout on point selection batch {batch_number} (attempt {_retry + 1})")
        if _retry == 0:
            return await select_points_for_diagnosis(
                tcm_pattern, treatment_principles, intake_context,
                log_tag=log_tag, _retry=1,
                batch_number=batch_number, existing_codes=existing_codes, lang=lang,
            )
    except Exception as e:
        logger.error(f"[{log_tag}] Point selection unexpected error: {e}", exc_info=True)

    return []


async def generate_diagnosis_only(
    context_parts: list,
    intake_context: str,
    log_tag: str = "?",
) -> dict:
    """Stage 1 of the two-stage pipeline: diagnosis fields only (no points).

    Returns a dict with tcm_pattern, treatment_principles, diagnosis_certainty,
    and recommendations — but suggested_points is always an empty list.
    Callers save this to the DB immediately so the dashboard can show the
    diagnosis block before Stage 2 (point selection) completes.
    """
    fallback: dict = {
        "tcm_pattern": "",
        "treatment_principles": "",
        "diagnosis_certainty": 0,
        "suggested_points": [],
        "recommendations": {"diet": "", "sleep": "", "exercise": "", "stress": ""},
    }
    if _LLM_LONG is None:
        return fallback
    try:
        resp = await asyncio.wait_for(_LLM_LONG.ainvoke(context_parts), timeout=OLLAMA_TIMEOUT)
        raw = _strip_json(resp.content.strip())
        parsed = json.loads(raw)
        raw_certainty = parsed.get("diagnosis_certainty", 0)
        result: dict = {
            "tcm_pattern":          str(parsed.get("tcm_pattern", "")),
            "treatment_principles": str(parsed.get("treatment_principles", "")),
            "diagnosis_certainty":  int(raw_certainty) if isinstance(raw_certainty, (int, float)) else 0,
            "suggested_points":     [],
            "recommendations": {
                "diet":     str(parsed.get("recommendations", {}).get("diet", "")),
                "sleep":    str(parsed.get("recommendations", {}).get("sleep", "")),
                "exercise": str(parsed.get("recommendations", {}).get("exercise", "")),
                "stress":   str(parsed.get("recommendations", {}).get("stress", "")),
            },
        }
        logger.info(f"[{log_tag}] Stage-1 diagnosis: {result['tcm_pattern']} ({result['diagnosis_certainty']}%)")
        return result
    except asyncio.TimeoutError:
        logger.warning(f"[{log_tag}] Ollama timeout — diagnosis stage 1")
    except json.JSONDecodeError as e:
        logger.warning(f"[{log_tag}] Diagnosis stage 1 JSON parse error: {e}")
    except Exception as e:
        logger.warning(f"[{log_tag}] Diagnosis stage 1 error: {e}")
    return fallback


async def generate_tcm_diagnosis(user_id: int, clinical_summary: str) -> dict:
    """Generate a structured TCM diagnosis then select 6-15 acupuncture points in a second call."""
    hist = _get_history(user_id)
    summary = _rolling_summaries.get(user_id)

    # Build intake context string for the point-selection step
    intake_lines = []
    if summary:
        intake_lines.append(f"[Conversation summary: {summary}]")
    for m in hist.messages:
        if isinstance(m, HumanMessage):
            intake_lines.append(f"Patient: {m.content}")
        elif isinstance(m, AIMessage):
            intake_lines.append(f"Assistant: {m.content}")
    intake_context = "\n".join(intake_lines)

    # ── Step 1: diagnosis (pattern, principles, certainty, recommendations) ──
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

    if _LLM_LONG is None:
        return fallback

    try:
        resp = await asyncio.wait_for(_LLM_LONG.ainvoke(context_parts), timeout=OLLAMA_TIMEOUT)
        raw = _strip_json(resp.content.strip())
        parsed = json.loads(raw)

        raw_certainty = parsed.get("diagnosis_certainty", 0)
        result: dict = {
            "tcm_pattern": str(parsed.get("tcm_pattern", "")),
            "treatment_principles": str(parsed.get("treatment_principles", "")),
            "diagnosis_certainty": int(raw_certainty) if isinstance(raw_certainty, (int, float)) else 0,
            "suggested_points": [],
            "recommendations": {
                "diet":     str(parsed.get("recommendations", {}).get("diet", "")),
                "sleep":    str(parsed.get("recommendations", {}).get("sleep", "")),
                "exercise": str(parsed.get("recommendations", {}).get("exercise", "")),
                "stress":   str(parsed.get("recommendations", {}).get("stress", "")),
            },
        }
        logger.info(f"[{user_id}] TCM diagnosis: {result['tcm_pattern']} ({result['diagnosis_certainty']}%)")

    except asyncio.TimeoutError:
        logger.warning(f"[{user_id}] Ollama timeout on TCM diagnosis (step 1)")
        return fallback
    except json.JSONDecodeError as e:
        logger.warning(f"[{user_id}] TCM diagnosis JSON parse error: {e}")
        return fallback
    except Exception as e:
        logger.warning(f"[{user_id}] TCM diagnosis error: {e}")
        return fallback

    # ── Step 2: point selection — separate focused call ──────────────────────
    result["suggested_points"] = await select_points_for_diagnosis(
        tcm_pattern=result["tcm_pattern"],
        treatment_principles=result["treatment_principles"],
        intake_context=intake_context,
        log_tag=str(user_id),
    )

    return result


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
