import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { prisma } from "@/lib/prisma";
import { getProjectAccess } from "@/lib/project-access";

type Params = { params: Promise<{ id: string }> };

export async function GET(_req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await params;

  const access = await getProjectAccess(id, session.user.id);
  if (!access) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const prompts = await prisma.promptHistory.findMany({
    where: { projectId: id },
    orderBy: { createdAt: "desc" },
  });

  // Attach role and member list for the frontend
  const owner = await prisma.user.findUnique({
    where: { id: access.project.userId },
    select: { id: true, name: true, email: true, image: true },
  });

  return NextResponse.json({
    ...access.project,
    prompts,
    role: access.role,
    owner,
  });
}

export async function PATCH(req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await params;

  const access = await getProjectAccess(id, session.user.id);
  if (!access) return NextResponse.json({ error: "Not found" }, { status: 404 });
  if (!access.canEdit) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const body = await req.json();
  const updated = await prisma.project.update({
    where: { id },
    data: {
      ...(body.name !== undefined && { name: body.name }),
      ...(body.description !== undefined && { description: body.description }),
      ...(body.context !== undefined && { context: body.context }),
    },
  });
  return NextResponse.json({ ...updated, role: access.role });
}

export async function DELETE(_req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await params;

  const access = await getProjectAccess(id, session.user.id);
  if (!access) return NextResponse.json({ error: "Not found" }, { status: 404 });
  if (!access.isOwner) return NextResponse.json({ error: "Only the owner can delete a project" }, { status: 403 });

  await prisma.project.delete({ where: { id } });
  return new NextResponse(null, { status: 204 });
}
