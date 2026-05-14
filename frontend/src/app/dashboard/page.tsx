"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { FolderPlus, Zap } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import { api, type Project } from "@/lib/api";

export default function Dashboard() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);

  const loadProjects = useCallback(async () => {
    try {
      const data = await api.projects.list();
      setProjects(data);
    } catch {
      // Session expired — middleware will redirect
    }
  }, []);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  async function handleCreate() {
    const p = await api.projects.create({ name: "New Project" });
    await loadProjects();
    router.push(`/projects/${p.id}`);
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar projects={projects} onProjectsChange={loadProjects} />
      <main className="flex-1 flex items-center justify-center">
        <div className="text-center max-w-sm">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-brand-50 mb-4">
            <Zap size={28} className="text-brand-500" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">Welcome to ContextPrompt</h1>
          <p className="text-gray-500 mt-2 text-sm leading-relaxed">
            Create a project, add your context, and start generating professional prompts instantly.
          </p>
          <button
            onClick={handleCreate}
            className="mt-6 inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors"
          >
            <FolderPlus size={16} />
            Create first project
          </button>
        </div>
      </main>
    </div>
  );
}
