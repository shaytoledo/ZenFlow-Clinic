import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { prisma } from "@/lib/prisma";

type Params = { params: Promise<{ token: string }> };

/** GET /api/invites/:token — fetch invite details (project name, role, expiry) */
export async function GET(_req: Request, { params }: Params) {
  const { token } = await params;

  const invite = await prisma.projectInvite.findUnique({
    where: { token },
    include: { project: { select: { id: true, name: true, description: true } } },
  });

  if (!invite) return NextResponse.json({ error: "Invite not found" }, { status: 404 });
  if (invite.expiresAt < new Date())
    return NextResponse.json({ error: "Invite has expired" }, { status: 410 });

  return NextResponse.json({
    projectId: invite.projectId,
    projectName: invite.project.name,
    projectDescription: invite.project.description,
    email: invite.email,
    role: invite.role,
    expiresAt: invite.expiresAt,
  });
}

/** POST /api/invites/:token — accept invite (must be signed in) */
export async function POST(_req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { token } = await params;

  const invite = await prisma.projectInvite.findUnique({
    where: { token },
    include: { project: true },
  });

  if (!invite) return NextResponse.json({ error: "Invite not found" }, { status: 404 });
  if (invite.expiresAt < new Date())
    return NextResponse.json({ error: "Invite has expired" }, { status: 410 });

  // Check the signed-in user's email matches the invite (case-insensitive)
  const userEmail = session.user.email?.toLowerCase();
  if (userEmail !== invite.email)
    return NextResponse.json(
      { error: `This invite was sent to ${invite.email}. Please sign in with that account.` },
      { status: 403 }
    );

  // Don't add if already the owner
  if (invite.project.userId === session.user.id)
    return NextResponse.json({ error: "You are already the owner of this project" }, { status: 409 });

  // Upsert membership
  await prisma.projectMember.upsert({
    where: { projectId_userId: { projectId: invite.projectId, userId: session.user.id } },
    update: { role: invite.role },
    create: { projectId: invite.projectId, userId: session.user.id, role: invite.role },
  });

  // Delete the used invite
  await prisma.projectInvite.delete({ where: { token } });

  return NextResponse.json({ projectId: invite.projectId, role: invite.role });
}
