import asyncio
import anthropic
import google.generativeai as genai
from ..config import settings

# ── Model-specific tuning hints ──────────────────────────────────────────────
_MODEL_HINTS: dict[str, str] = {
    "claude": (
        "The final prompt will be used with Claude (Anthropic). "
        "Claude responds well to XML-style tags for structure, clear personas, and explicit output format instructions."
    ),
    "gpt-4": (
        "The final prompt will be used with GPT-4 (OpenAI). "
        "GPT-4 responds well to markdown structure, numbered steps, and system/user role separation hints."
    ),
    "gemini": (
        "The final prompt will be used with Gemini (Google). "
        "Gemini responds well to clear context setting, bullet-point structure, and explicit task decomposition."
    ),
}

_SYSTEM = (
    "You are an expert Prompt Engineer with deep knowledge of how large language models process instructions. "
    "Your sole job is to transform a user's simple, informal request into a highly detailed, professional, and effective prompt.\n\n"
    "Rules you must always follow:\n"
    "1. Return ONLY the optimized prompt — no preamble, no explanations, no meta-commentary.\n"
    "2. Define a clear role/persona grounded in the provided project context.\n"
    "3. Specify the target audience and desired tone taken directly from the project context.\n"
    "4. Structure the output format explicitly (sections, bullet points, length guidance, etc.).\n"
    "5. Apply chain-of-thought or step-by-step reasoning instructions where appropriate.\n"
    "6. Incorporate relevant domain-specific terminology from the project context.\n"
    "7. End the prompt with a clear, actionable task statement."
)


def _build_user_message(user_input: str, project_context: str, target_model: str) -> str:
    context_block = (
        f"PROJECT CONTEXT:\n{project_context.strip()}"
        if project_context.strip()
        else "PROJECT CONTEXT:\n(No project context provided — generate a general-purpose optimized prompt.)"
    )
    model_hint = _MODEL_HINTS.get(target_model, "")
    return (
        f"{context_block}\n\n"
        f"TARGET MODEL NOTE:\n{model_hint}\n\n"
        f"USER'S SIMPLE REQUEST:\n{user_input.strip()}\n\n"
        "Now write the optimized prompt:"
    )


# ── Gemini engine (free tier) ─────────────────────────────────────────────────
async def _optimize_with_gemini(user_input: str, project_context: str, target_model: str) -> str:
    genai.configure(api_key=settings.google_api_key)
    model = genai.GenerativeModel(
        model_name=settings.gemini_model,
        system_instruction=_SYSTEM,
    )
    user_msg = _build_user_message(user_input, project_context, target_model)
    # google-generativeai is sync; run in a thread so we don't block the event loop
    response = await asyncio.to_thread(model.generate_content, user_msg)
    return response.text


# ── Anthropic engine ──────────────────────────────────────────────────────────
async def _optimize_with_anthropic(user_input: str, project_context: str, target_model: str) -> str:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=_SYSTEM,
        messages=[
            {"role": "user", "content": _build_user_message(user_input, project_context, target_model)}
        ],
    )
    return message.content[0].text


# ── Public entry-point ────────────────────────────────────────────────────────
async def optimize_prompt(user_input: str, project_context: str, target_model: str) -> str:
    if settings.optimizer_engine == "gemini":
        if not settings.google_api_key:
            raise ValueError("GOOGLE_API_KEY is not set. Add it to your .env file.")
        return await _optimize_with_gemini(user_input, project_context, target_model)
    else:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
        return await _optimize_with_anthropic(user_input, project_context, target_model)
