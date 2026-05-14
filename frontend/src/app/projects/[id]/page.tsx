"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import ContextEditor from "@/components/ContextEditor";
import PromptOptimizer from "@/components/PromptOptimizer";
import { api, type Project, type ProjectWithHistory } from "@/lib/api";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const [project, setProject] = useState<ProjectWithHistory | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [notFound, setNotFound] = useState(false);

  const loadProjects = useCallback(async () => {
    const data = await api.projects.list();
    setProjects(data);
  }, []);

  const loadProject = useCallback(async () => {
    try {
      const data = await api.projects.get(id);
      setProject(data);
    } catch {
      setNotFound(true);
    }
  }, [id]);

  useEffect(() => {
    loadProjects();
    loadProject();
  }, [loadProjects, loadProject]);

  if (notFound) {
    return (
      <div className="flex min-h-screen">
        <Sidebar projects={projects} onProjectsChange={loadProjects} />
        <main className="flex-1 flex items-center justify-center">
          <p className="text-gray-400">Project not found.</p>
        </main>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex min-h-screen">
        <Sidebar projects={projects} onProjectsChange={loadProjects} />
        <main className="flex-1 flex items-center justify-center">
          <div className="w-6 h-6 rounded-full border-2 border-brand-500 border-t-transparent animate-spin" />
        </main>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar projects={projects} activeProjectId={id} onProjectsChange={loadProjects} />
      <main className="flex-1 p-8 max-w-4xl">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">{project.name}</h1>
          {project.description && (
            <p className="text-sm text-gray-500 mt-1">{project.description}</p>
          )}
        </div>
        <div className="space-y-5">
          <ContextEditor
            project={project}
            onSaved={(updated) => setProject((prev) => prev ? { ...prev, ...updated } : prev)}
          />
          <PromptOptimizer projectId={id} initialHistory={project.prompts} />
        </div>
      </main>
    </div>
  );
}
