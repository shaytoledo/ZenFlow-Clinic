import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { prisma } from "@/lib/prisma";

export async function GET() {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const uid = session.user.id;

  // Return owned projects + projects where the user is a member
  const [owned, shared] = await Promise.all([
    prisma.project.findMany({
      where: { userId: uid },
      orderBy: { updatedAt: "desc" },
    }),
    prisma.projectMember.findMany({
      where: { userId: uid },
      include: { project: true },
      orderBy: { project: { updatedAt: "desc" } },
    }),
  ]);

  const ownedWithRole = owned.map((p) => ({ ...p, role: "OWNER" as const }));
  const sharedWithRole = shared.map((m) => ({ ...m.project, role: m.role as "EDITOR" | "VIEWER" }));

  return NextResponse.json([...ownedWithRole, ...sharedWithRole]);
}

export async function POST(req: Request) {
  const session = await auth();
  if (!session?.user?.id) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const body = await req.json();
  const { name, description = "", context = "" } = body;
  if (!name?.trim()) return NextResponse.json({ error: "Name is required" }, { status: 400 });

  const project = await prisma.project.create({
    data: { name: name.trim(), description, context, userId: session.user.id },
  });
  return NextResponse.json({ ...project, role: "OWNER" }, { status: 201 });
}
