"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { signOut, useSession } from "next-auth/react";
import { FolderOpen, Plus, Trash2, Zap, LogOut, Users } from "lucide-react";
import { clsx } from "clsx";
import { api, type Project } from "@/lib/api";

interface SidebarProps {
  projects: Project[];
  activeProjectId?: string;
  onProjectsChange: () => void;
}

export default function Sidebar({ projects, activeProjectId, onProjectsChange }: SidebarProps) {
  const router = useRouter();
  const { data: session } = useSession();
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [loading, setLoading] = useState(false);

  const owned = projects.filter((p) => p.role === "OWNER");
  const shared = projects.filter((p) => p.role !== "OWNER");

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setLoading(true);
    try {
      const project = await api.projects.create({ name: newName.trim() });
      setNewName("");
      setCreating(false);
      onProjectsChange();
      router.push(`/projects/${project.id}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("Delete this project and all its history?")) return;
    await api.projects.delete(id);
    onProjectsChange();
    if (activeProjectId === id) router.push("/dashboard");
  }

  function ProjectLink({ p, showDelete }: { p: Project; showDelete: boolean }) {
    return (
      <Link
        key={p.id}
        href={`/projects/${p.id}`}
        className={clsx(
          "group flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors",
          activeProjectId === p.id
            ? "bg-brand-50 text-brand-700 font-medium"
            : "text-gray-700 hover:bg-gray-100"
        )}
      >
        <div className="flex items-center gap-2 min-w-0">
          <FolderOpen size={15} className="shrink-0" />
          <span className="truncate">{p.name}</span>
          {p.role === "EDITOR" && (
            <span className="text-[10px] font-medium text-brand-500 bg-brand-50 px-1.5 py-0.5 rounded shrink-0">
              Editor
            </span>
          )}
          {p.role === "VIEWER" && (
            <span className="text-[10px] font-medium text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded shrink-0">
              Viewer
            </span>
          )}
        </div>
        {showDelete && (
          <button
            onClick={(e) => handleDelete(e, p.id)}
            className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 transition-all shrink-0 ml-1"
          >
            <Trash2 size={13} />
          </button>
        )}
      </Link>
    );
  }

  return (
    <aside className="w-64 shrink-0 bg-white border-r border-gray-200 flex flex-col h-screen sticky top-0">
      {/* Logo */}
      <div className="p-4 border-b border-gray-200">
        <div className="flex items-center gap-2">
          <Zap className="text-brand-500" size={22} />
          <span className="text-lg font-bold text-gray-900">ContextPrompt</span>
        </div>
        <p className="text-xs text-gray-500 mt-1">Prompt engineering, automated</p>
      </div>

      {/* Projects list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-1">
        {/* My Projects */}
        <div className="flex items-center justify-between px-2 mb-2">
          <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">My Projects</span>
          <button
            onClick={() => setCreating(true)}
            className="text-gray-400 hover:text-brand-500 transition-colors"
            title="New project"
          >
            <Plus size={16} />
          </button>
        </div>

        {creating && (
          <form onSubmit={handleCreate} className="mb-2">
            <input
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === "Escape" && setCreating(false)}
              placeholder="Project name..."
              className="w-full text-sm px-3 py-1.5 rounded-lg border border-brand-500 outline-none"
              disabled={loading}
            />
          </form>
        )}

        {owned.length === 0 && !creating && (
          <p className="text-sm text-gray-400 px-2 py-4 text-center">
            No projects yet.
            <br />
            <button onClick={() => setCreating(true)} className="text-brand-500 hover:underline mt-1">
              Create one
            </button>
          </p>
        )}

        {owned.map((p) => (
          <ProjectLink key={p.id} p={p} showDelete />
        ))}

        {/* Shared with me */}
        {shared.length > 0 && (
          <>
            <div className="flex items-center gap-1.5 px-2 mt-4 mb-2">
              <Users size={12} className="text-gray-400" />
              <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                Shared with me
              </span>
            </div>
            {shared.map((p) => (
              <ProjectLink key={p.id} p={p} showDelete={false} />
            ))}
          </>
        )}
      </div>

      {/* User footer */}
      {session?.user && (
        <div className="p-3 border-t border-gray-200">
          <div className="flex items-center gap-2 px-2 py-1.5">
            {session.user.image && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={session.user.image}
                alt={session.user.name ?? "User"}
                className="w-7 h-7 rounded-full"
              />
            )}
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-gray-700 truncate">{session.user.name}</p>
              <p className="text-xs text-gray-400 truncate">{session.user.email}</p>
            </div>
            <button
              onClick={() => signOut({ callbackUrl: "/login" })}
              title="Sign out"
              className="text-gray-400 hover:text-red-500 transition-colors shrink-0"
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
