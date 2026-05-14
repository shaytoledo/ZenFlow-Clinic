"use client";

import { useState, useEffect, useRef } from "react";
import { useSession } from "next-auth/react";
import {
  Users, X, Mail, Copy, Check, ChevronDown, Trash2, Crown, Pencil, Eye,
} from "lucide-react";
import { clsx } from "clsx";

interface Member {
  id: string;
  role: string;
  createdAt: string;
  user: { id: string; name: string | null; email: string | null; image: string | null };
}

interface Invite {
  id: string;
  email: string;
  role: string;
  expiresAt: string;
  token: string;
}

interface Owner {
  id: string;
  name: string | null;
  email: string | null;
  image: string | null;
}

interface SharePanelProps {
  projectId: string;
  projectRole: string; // caller's role
  onClose: () => void;
}

export default function SharePanel({ projectId, projectRole, onClose }: SharePanelProps) {
  const { data: session } = useSession();
  const isOwner = projectRole === "OWNER";

  const [owner, setOwner] = useState<Owner | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [loading, setLoading] = useState(true);

  // Invite form
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<"VIEWER" | "EDITOR">("VIEWER");
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState("");
  const [lastInviteLink, setLastInviteLink] = useState("");
  const [copied, setCopied] = useState(false);

  const panelRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  async function loadMembers() {
    try {
      const res = await fetch(`/api/projects/${projectId}/members`);
      if (!res.ok) return;
      const data = await res.json();
      setOwner(data.owner);
      setMembers(data.members);
      setInvites(data.invites ?? []);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadMembers(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    setInviting(true);
    setInviteError("");
    setLastInviteLink("");
    try {
      const res = await fetch(`/api/projects/${projectId}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: inviteEmail.trim(), role: inviteRole }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Failed to invite");
      setInviteEmail("");
      setLastInviteLink(data.acceptUrl);
      await loadMembers();
    } catch (err) {
      setInviteError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setInviting(false);
    }
  }

  async function handleRoleChange(memberId: string, role: string) {
    await fetch(`/api/projects/${projectId}/members/${memberId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    });
    setMembers((prev) => prev.map((m) => m.id === memberId ? { ...m, role } : m));
  }

  async function handleRemove(memberId: string) {
    if (!confirm("Remove this member from the project?")) return;
    await fetch(`/api/projects/${projectId}/members/${memberId}`, { method: "DELETE" });
    setMembers((prev) => prev.filter((m) => m.id !== memberId));
  }

  function copyLink(link: string) {
    navigator.clipboard.writeText(link);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const roleIcon = (role: string) => {
    if (role === "OWNER") return <Crown size={13} className="text-yellow-500" />;
    if (role === "EDITOR") return <Pencil size={13} className="text-brand-500" />;
    return <Eye size={13} className="text-gray-400" />;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-end pt-16 pr-6 pointer-events-none">
      <div
        ref={panelRef}
        className="pointer-events-auto bg-white rounded-2xl shadow-xl border border-gray-200 w-96 max-h-[80vh] flex flex-col overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <Users size={18} className="text-brand-500" />
            <h2 className="font-semibold text-gray-900 text-sm">Share project</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors">
            <X size={18} />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 p-5 space-y-5">
          {/* Invite form (owner only) */}
          {isOwner && (
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Invite by email
              </p>
              <form onSubmit={handleInvite} className="space-y-2">
                <div className="flex gap-2">
                  <input
                    type="email"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    placeholder="colleague@example.com"
                    className="flex-1 text-sm px-3 py-2 rounded-lg border border-gray-200 outline-none focus:border-brand-400 transition-colors"
                    disabled={inviting}
                  />
                  <RoleDropdown value={inviteRole} onChange={setInviteRole} />
                </div>
                <button
                  type="submit"
                  disabled={inviting || !inviteEmail.trim()}
                  className="w-full py-2 bg-brand-500 text-white rounded-lg text-sm font-medium hover:bg-brand-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                  <Mail size={14} />
                  {inviting ? "Inviting…" : "Send invite"}
                </button>
                {inviteError && <p className="text-xs text-red-500">{inviteError}</p>}
              </form>

              {/* Copy invite link */}
              {lastInviteLink && (
                <div className="mt-3 bg-gray-50 rounded-lg p-3 flex items-center gap-2">
                  <p className="flex-1 text-xs text-gray-500 truncate">{lastInviteLink}</p>
                  <button
                    onClick={() => copyLink(lastInviteLink)}
                    className="shrink-0 text-gray-400 hover:text-brand-500 transition-colors"
                    title="Copy link"
                  >
                    {copied ? <Check size={15} className="text-green-500" /> : <Copy size={15} />}
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Members list */}
          <div>
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
              Members
            </p>
            {loading ? (
              <p className="text-sm text-gray-400">Loading…</p>
            ) : (
              <div className="space-y-1">
                {/* Owner row */}
                {owner && (
                  <MemberRow
                    avatar={owner.image}
                    name={owner.name ?? owner.email ?? "Unknown"}
                    email={owner.email}
                    roleBadge={
                      <span className="flex items-center gap-1 text-xs text-yellow-600 font-medium">
                        {roleIcon("OWNER")} Owner
                      </span>
                    }
                    isCurrentUser={owner.id === session?.user?.id}
                  />
                )}

                {/* Shared members */}
                {members.map((m) => (
                  <MemberRow
                    key={m.id}
                    avatar={m.user.image}
                    name={m.user.name ?? m.user.email ?? "Unknown"}
                    email={m.user.email}
                    isCurrentUser={m.user.id === session?.user?.id}
                    roleBadge={
                      isOwner ? (
                        <div className="flex items-center gap-1">
                          <RoleDropdown
                            value={m.role as "EDITOR" | "VIEWER"}
                            onChange={(r) => handleRoleChange(m.id, r)}
                            compact
                          />
                          <button
                            onClick={() => handleRemove(m.id)}
                            className="text-gray-300 hover:text-red-500 transition-colors ml-1"
                            title="Remove"
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      ) : (
                        <span className={clsx("flex items-center gap-1 text-xs font-medium",
                          m.role === "EDITOR" ? "text-brand-600" : "text-gray-500")}>
                          {roleIcon(m.role)} {m.role === "EDITOR" ? "Editor" : "Viewer"}
                        </span>
                      )
                    }
                  />
                ))}
              </div>
            )}
          </div>

          {/* Pending invites */}
          {isOwner && invites.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Pending invites
              </p>
              <div className="space-y-1">
                {invites.map((inv) => (
                  <div key={inv.id} className="flex items-center gap-3 px-2 py-1.5">
                    <div className="w-7 h-7 rounded-full bg-gray-100 flex items-center justify-center text-gray-400 text-xs font-medium shrink-0">
                      <Mail size={13} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-gray-700 truncate">{inv.email}</p>
                      <p className="text-xs text-gray-400">Pending · {inv.role === "EDITOR" ? "Editor" : "Viewer"}</p>
                    </div>
                    <button
                      onClick={() => copyLink(`${window.location.origin}/invites/${inv.token}`)}
                      className="text-gray-300 hover:text-brand-500 transition-colors"
                      title="Copy invite link"
                    >
                      <Copy size={13} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Helper sub-components ── */

function MemberRow({
  avatar, name, email, roleBadge, isCurrentUser,
}: {
  avatar: string | null | undefined;
  name: string;
  email: string | null | undefined;
  roleBadge: React.ReactNode;
  isCurrentUser: boolean;
}) {
  return (
    <div className="flex items-center gap-3 px-2 py-1.5 rounded-lg hover:bg-gray-50 transition-colors">
      {avatar ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={avatar} alt={name} className="w-7 h-7 rounded-full shrink-0 object-cover" />
      ) : (
        <div className="w-7 h-7 rounded-full bg-brand-100 flex items-center justify-center text-brand-600 text-xs font-semibold shrink-0">
          {name.charAt(0).toUpperCase()}
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-800 font-medium truncate">
          {name}{isCurrentUser && <span className="text-gray-400 font-normal"> (you)</span>}
        </p>
        {email && <p className="text-xs text-gray-400 truncate">{email}</p>}
      </div>
      <div className="shrink-0">{roleBadge}</div>
    </div>
  );
}

function RoleDropdown({
  value, onChange, compact = false,
}: {
  value: "EDITOR" | "VIEWER";
  onChange: (r: "EDITOR" | "VIEWER") => void;
  compact?: boolean;
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as "EDITOR" | "VIEWER")}
        className={clsx(
          "appearance-none rounded-lg border border-gray-200 bg-white text-xs font-medium text-gray-700",
          "focus:outline-none focus:border-brand-400 pr-5 cursor-pointer",
          compact ? "px-2 py-1" : "px-3 py-2"
        )}
      >
        <option value="VIEWER">Viewer</option>
        <option value="EDITOR">Editor</option>
      </select>
      <ChevronDown
        size={10}
        className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none"
      />
    </div>
  );
}
