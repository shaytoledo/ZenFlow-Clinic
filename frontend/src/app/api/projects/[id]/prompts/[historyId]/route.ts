import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { prisma } from "@/lib/prisma";

type Params = { params: Promise<{ id: string; historyId: string }> };

export async function DELETE(_req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id, historyId } = await params;

  const history = await prisma.promptHistory.findUnique({
    where: { id: historyId },
    include: { project: true },
  });

  if (!history || history.project.userId !== session.user.id || history.projectId !== id)
    return NextResponse.json({ error: "Not found" }, { status: 404 });

  await prisma.promptHistory.delete({ where: { id: historyId } });
  return new NextResponse(null, { status: 204 });
}
