import { prisma } from "./prisma";
import type { Project, ProjectMember } from "@prisma/client";

export type ProjectRole = "OWNER" | "EDITOR" | "VIEWER";

export interface ProjectAccess {
  project: Project & { members: ProjectMember[] };
  role: ProjectRole;
  isOwner: boolean;
  canEdit: boolean;          // context, description, name
  canManageMembers: boolean; // invite / remove / change role
}

/**
 * Returns the project + the caller's role, or null if they have no access.
 * Every project API route should call this instead of a raw prisma.project.findUnique.
 */
export async function getProjectAccess(
  projectId: string,
  userId: string
): Promise<ProjectAccess | null> {
  const project = await prisma.project.findUnique({
    where: { id: projectId },
    include: {
      members: {
        include: { user: { select: { id: true, name: true, email: true, image: true } } },
      },
    },
  });

  if (!project) return null;

  const isOwner = project.userId === userId;
  const membership = project.members.find((m) => m.userId === userId);

  if (!isOwner && !membership) return null; // no access at all

  const role: ProjectRole = isOwner
    ? "OWNER"
    : (membership!.role as ProjectRole);

  return {
    project,
    role,
    isOwner,
    canEdit: isOwner || role === "EDITOR",
    canManageMembers: isOwner,
  };
}
