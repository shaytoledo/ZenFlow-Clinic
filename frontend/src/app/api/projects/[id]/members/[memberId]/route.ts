import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { prisma } from "@/lib/prisma";
import { getProjectAccess } from "@/lib/project-access";

type Params = { params: Promise<{ id: string; memberId: string }> };

/** PATCH /api/projects/:id/members/:memberId — change role */
export async function PATCH(req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id, memberId } = await params;

  const access = await getProjectAccess(id, session.user.id);
  if (!access) return NextResponse.json({ error: "Not found" }, { status: 404 });
  if (!access.canManageMembers) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const body = await req.json();
  const { role } = body;
  if (!["EDITOR", "VIEWER"].includes(role))
    return NextResponse.json({ error: "role must be EDITOR or VIEWER" }, { status: 400 });

  const member = await prisma.projectMember.findUnique({ where: { id: memberId } });
  if (!member || member.projectId !== id)
    return NextResponse.json({ error: "Not found" }, { status: 404 });

  const updated = await prisma.projectMember.update({ where: { id: memberId }, data: { role } });
  return NextResponse.json(updated);
}

/** DELETE /api/projects/:id/members/:memberId — remove member */
export async function DELETE(_req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id, memberId } = await params;

  const access = await getProjectAccess(id, session.user.id);
  if (!access) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const member = await prisma.projectMember.findUnique({ where: { id: memberId } });
  if (!member || member.projectId !== id)
    return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Owner can remove anyone; members can remove themselves
  if (!access.isOwner && member.userId !== session.user.id)
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  await prisma.projectMember.delete({ where: { id: memberId } });
  return new NextResponse(null, { status: 204 });
}
