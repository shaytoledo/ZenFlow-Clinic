import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { prisma } from "@/lib/prisma";
import { getProjectAccess } from "@/lib/project-access";
import { optimizePrompt } from "@/lib/gemini";

type Params = { params: Promise<{ id: string }> };

export async function POST(req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await params;

  // Both editors and viewers can run optimizations
  const access = await getProjectAccess(id, session.user.id);
  if (!access) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const body = await req.json();
  const { user_input, target_model = "claude" } = body;
  if (!user_input?.trim()) return NextResponse.json({ error: "user_input is required" }, { status: 400 });

  try {
    const optimizedPrompt = await optimizePrompt(user_input, access.project.context, target_model);
    const history = await prisma.promptHistory.create({
      data: { projectId: id, userInput: user_input, optimizedPrompt, targetModel: target_model },
    });
    return NextResponse.json({ optimized_prompt: optimizedPrompt, target_model, history_id: history.id });
  } catch (err) {
    console.error("Gemini error:", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Optimization failed" },
      { status: 500 }
    );
  }
}
