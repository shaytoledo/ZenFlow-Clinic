import { GoogleGenerativeAI } from "@google/generative-ai";

const MODEL_HINTS: Record<string, string> = {
  claude:
    "The final prompt will be used with Claude (Anthropic). Claude responds well to XML-style tags for structure, clear personas, and explicit output format instructions.",
  "gpt-4":
    "The final prompt will be used with GPT-4 (OpenAI). GPT-4 responds well to markdown structure, numbered steps, and system/user role separation hints.",
  gemini:
    "The final prompt will be used with Gemini (Google). Gemini responds well to clear context setting, bullet-point structure, and explicit task decomposition.",
};

const SYSTEM_INSTRUCTION = `You are an expert Prompt Engineer with deep knowledge of how large language models process instructions.
Your sole job is to transform a user's simple, informal request into a highly detailed, professional, and effective prompt.

Rules you must always follow:
1. Return ONLY the optimized prompt — no preamble, no explanations, no meta-commentary.
2. Define a clear role/persona grounded in the provided project context.
3. Specify the target audience and desired tone taken directly from the project context.
4. Structure the output format explicitly (sections, bullet points, length guidance, etc.).
5. Apply chain-of-thought or step-by-step reasoning instructions where appropriate.
6. Incorporate relevant domain-specific terminology from the project context.
7. End the prompt with a clear, actionable task statement.`;

export async function optimizePrompt(
  userInput: string,
  projectContext: string,
  targetModel: string
): Promise<string> {
  const apiKey = process.env.GOOGLE_API_KEY;
  if (!apiKey) throw new Error("GOOGLE_API_KEY is not configured.");

  const genAI = new GoogleGenerativeAI(apiKey);
  const model = genAI.getGenerativeModel({
    model: process.env.GEMINI_MODEL ?? "gemini-2.0-flash",
    systemInstruction: SYSTEM_INSTRUCTION,
  });

  const contextBlock = projectContext.trim()
    ? `PROJECT CONTEXT:\n${projectContext.trim()}`
    : "PROJECT CONTEXT:\n(No project context provided — generate a general-purpose optimized prompt.)";

  const modelHint = MODEL_HINTS[targetModel] ?? "";

  const prompt = `${contextBlock}

TARGET MODEL NOTE:
${modelHint}

USER'S SIMPLE REQUEST:
${userInput.trim()}

Now write the optimized prompt:`;

  const result = await model.generateContent(prompt);
  return result.response.text();
}
