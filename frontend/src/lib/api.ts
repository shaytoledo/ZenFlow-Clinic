const BASE = "/api";

export type TargetModel = "claude" | "gpt-4" | "gemini";
export type ProjectRole = "OWNER" | "EDITOR" | "VIEWER";

export interface Project {
  id: string;
  name: string;
  description: string;
  context: string;
  createdAt: string;
  updatedAt: string;
  role: ProjectRole;
}

export interface PromptHistoryItem {
  id: string;
  userInput: string;
  optimizedPrompt: string;
  targetModel: string;
  createdAt: string;
}

export interface ProjectWithHistory extends Project {
  prompts: PromptHistoryItem[];
  owner: { id: string; name: string | null; email: string | null; image: string | null } | null;
}

export interface OptimizeResponse {
  optimized_prompt: string;
  target_model: TargetModel;
  history_id: string;
}

export interface ProjectMemberUser {
  id: string;
  name: string | null;
  email: string | null;
  image: string | null;
}

export interface ProjectMember {
  id: string;
  role: string;
  createdAt: string;
  user: ProjectMemberUser;
}

export interface ProjectInvite {
  id: string;
  email: string;
  role: string;
  token: string;
  expiresAt: string;
}

export interface MembersResponse {
  owner: ProjectMemberUser | null;
  members: ProjectMember[];
  invites: ProjectInvite[];
  role: ProjectRole;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error ?? "Request failed");
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  projects: {
    list: () => request<Project[]>("/projects/"),
    get: (id: string) => request<ProjectWithHistory>(`/projects/${id}`),
    create: (data: { name: string; description?: string; context?: string }) =>
      request<Project>("/projects/", { method: "POST", body: JSON.stringify(data) }),
    update: (id: string, data: Partial<Pick<Project, "name" | "description" | "context">>) =>
      request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
    delete: (id: string) => request<void>(`/projects/${id}`, { method: "DELETE" }),
  },
  prompts: {
    optimize: (projectId: string, userInput: string, targetModel: TargetModel) =>
      request<OptimizeResponse>(`/projects/${projectId}/prompts/optimize`, {
        method: "POST",
        body: JSON.stringify({ user_input: userInput, target_model: targetModel }),
      }),
    list: (projectId: string) =>
      request<PromptHistoryItem[]>(`/projects/${projectId}/prompts/`),
    delete: (projectId: string, historyId: string) =>
      request<void>(`/projects/${projectId}/prompts/${historyId}`, { method: "DELETE" }),
  },
  members: {
    list: (projectId: string) => request<MembersResponse>(`/projects/${projectId}/members`),
    invite: (projectId: string, email: string, role: string) =>
      request<{ invite: ProjectInvite; acceptUrl: string }>(`/projects/${projectId}/members`, {
        method: "POST",
        body: JSON.stringify({ email, role }),
      }),
    updateRole: (projectId: string, memberId: string, role: string) =>
      request<ProjectMember>(`/projects/${projectId}/members/${memberId}`, {
        method: "PATCH",
        body: JSON.stringify({ role }),
      }),
    remove: (projectId: string, memberId: string) =>
      request<void>(`/projects/${projectId}/members/${memberId}`, { method: "DELETE" }),
  },
};
