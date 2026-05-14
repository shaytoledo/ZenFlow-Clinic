import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { prisma } from "@/lib/prisma";
import { getProjectAccess } from "@/lib/project-access";
import { sendInviteEmail } from "@/lib/email";
import crypto from "crypto";

type Params = { params: Promise<{ id: string }> };

/** GET /api/projects/:id/members — list all members (owner + shared users) */
export async function GET(_req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await params;

  const access = await getProjectAccess(id, session.user.id);
  if (!access) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Fetch owner info
  const owner = await prisma.user.findUnique({
    where: { id: access.project.userId },
    select: { id: true, name: true, email: true, image: true },
  });

  // Members already loaded in access.project.members (includes user via select)
  const members = (access.project.members as Array<{
    id: string;
    role: string;
    createdAt: Date;
    user: { id: string; name: string | null; email: string | null; image: string | null };
  }>).map((m) => ({
    id: m.id,
    role: m.role,
    createdAt: m.createdAt,
    user: m.user,
  }));

  // Fetch pending invites (owner only)
  const invites = access.isOwner
    ? await prisma.projectInvite.findMany({
        where: { projectId: id, expiresAt: { gt: new Date() } },
        orderBy: { createdAt: "desc" },
      })
    : [];

  return NextResponse.json({ owner, members, invites, role: access.role });
}

/** POST /api/projects/:id/members — invite someone by email */
export async function POST(req: Request, { params }: Params) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await params;

  const access = await getProjectAccess(id, session.user.id);
  if (!access) return NextResponse.json({ error: "Not found" }, { status: 404 });
  if (!access.canManageMembers) return NextResponse.json({ error: "Forbidden" }, { status: 403 });

  const body = await req.json();
  const { email, role = "VIEWER" } = body;

  if (!email?.trim()) return NextResponse.json({ error: "email is required" }, { status: 400 });
  if (!["EDITOR", "VIEWER"].includes(role))
    return NextResponse.json({ error: "role must be EDITOR or VIEWER" }, { status: 400 });

  const normalizedEmail = email.trim().toLowerCase();

  // Check if invitee is already a member (or the owner)
  const invitee = await prisma.user.findUnique({ where: { email: normalizedEmail } });
  if (invitee) {
    if (invitee.id === access.project.userId)
      return NextResponse.json({ error: "That user is already the owner" }, { status: 409 });
    const existing = access.project.members.find((m) => m.userId === invitee.id);
    if (existing)
      return NextResponse.json({ error: "That user is already a member" }, { status: 409 });
  }

  // Upsert invite (replace existing pending invite for the same email)
  await prisma.projectInvite.deleteMany({
    where: { projectId: id, email: normalizedEmail },
  });

  const token = crypto.randomBytes(32).toString("hex");
  const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000); // 7 days

  const invite = await prisma.projectInvite.create({
    data: { projectId: id, email: normalizedEmail, role, token, expiresAt },
  });

  const baseUrl = process.env.NEXTAUTH_URL ?? process.env.VERCEL_URL
    ? `https://${process.env.VERCEL_URL}`
    : "http://localhost:3000";
  const acceptUrl = `${baseUrl}/invites/${token}`;

  // Send email (no-op if RESEND_API_KEY not set)
  await sendInviteEmail({
    to: normalizedEmail,
    inviterName: session.user.name ?? session.user.email ?? "Someone",
    projectName: access.project.name,
    role,
    acceptUrl,
  });

  return NextResponse.json({ invite, acceptUrl }, { status: 201 });
}
