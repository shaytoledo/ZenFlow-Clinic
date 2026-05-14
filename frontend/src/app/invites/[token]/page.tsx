"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useSession, signIn } from "next-auth/react";
import { Zap, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import Link from "next/link";

interface InviteInfo {
  projectId: string;
  projectName: string;
  projectDescription: string;
  email: string;
  role: string;
}

type PageState = "loading" | "ready" | "wrong-account" | "accepting" | "success" | "error";

export default function AcceptInvitePage() {
  const { token } = useParams<{ token: string }>();
  const { data: session, status } = useSession();
  const router = useRouter();

  const [invite, setInvite] = useState<InviteInfo | null>(null);
  const [pageState, setPageState] = useState<PageState>("loading");
  const [errorMsg, setErrorMsg] = useState("");

  // 1. Fetch invite info
  useEffect(() => {
    fetch(`/api/invites/${token}`)
      .then(async (res) => {
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          throw new Error(d.error ?? "Invalid invite");
        }
        return res.json();
      })
      .then((data) => {
        setInvite(data);
        setPageState("ready");
      })
      .catch((err) => {
        setErrorMsg(err.message);
        setPageState("error");
      });
  }, [token]);

  // 2. Once session loads, check email match
  useEffect(() => {
    if (pageState !== "ready" || status === "loading" || !invite) return;
    if (status === "authenticated" && session?.user?.email) {
      if (session.user.email.toLowerCase() !== invite.email) {
        setPageState("wrong-account");
      }
    }
  }, [pageState, status, session, invite]);

  async function handleAccept() {
    if (!invite) return;
    setPageState("accepting");
    try {
      const res = await fetch(`/api/invites/${token}`, { method: "POST" });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.error ?? "Failed to accept invite");
      }
      setPageState("success");
      setTimeout(() => router.push(`/projects/${invite.projectId}`), 1800);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Something went wrong");
      setPageState("error");
    }
  }

  const roleLabel = invite?.role === "EDITOR" ? "Editor" : "Viewer";

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col items-center justify-center p-6">
      <div className="mb-6 flex items-center gap-2">
        <Zap className="text-brand-500" size={26} />
        <span className="text-xl font-bold text-gray-900">ContextPrompt</span>
      </div>

      <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-8 w-full max-w-md text-center">

        {/* Loading invite */}
        {(pageState === "loading" || (pageState === "ready" && status === "loading")) && (
          <div className="flex flex-col items-center gap-3 py-4">
            <Loader2 className="animate-spin text-brand-500" size={32} />
            <p className="text-gray-500">Loading invitation…</p>
          </div>
        )}

        {/* Error */}
        {pageState === "error" && (
          <div className="flex flex-col items-center gap-3 py-4">
            <XCircle className="text-red-400" size={40} />
            <h2 className="text-lg font-semibold text-gray-800">Invite unavailable</h2>
            <p className="text-sm text-gray-500">{errorMsg}</p>
            <Link href="/dashboard" className="mt-2 text-brand-500 hover:underline text-sm">
              Go to dashboard
            </Link>
          </div>
        )}

        {/* Success */}
        {pageState === "success" && invite && (
          <div className="flex flex-col items-center gap-3 py-4">
            <CheckCircle2 className="text-green-500" size={40} />
            <h2 className="text-lg font-semibold text-gray-800">You&apos;re in!</h2>
            <p className="text-sm text-gray-500">
              Redirecting you to <strong>{invite.projectName}</strong>…
            </p>
          </div>
        )}

        {/* Wrong account */}
        {pageState === "wrong-account" && invite && (
          <div className="flex flex-col items-center gap-4 py-4">
            <XCircle className="text-yellow-400" size={40} />
            <h2 className="text-lg font-semibold text-gray-800">Wrong account</h2>
            <p className="text-sm text-gray-500">
              This invite was sent to <strong>{invite.email}</strong>, but you&apos;re signed in as{" "}
              <strong>{session?.user?.email}</strong>.
            </p>
            <button
              onClick={() => signIn(undefined, { callbackUrl: `/invites/${token}` })}
              className="px-5 py-2.5 bg-brand-500 text-white rounded-xl font-medium text-sm hover:bg-brand-600 transition-colors"
            >
              Sign in with the correct account
            </button>
          </div>
        )}

        {/* Ready — not signed in */}
        {pageState === "ready" && status === "unauthenticated" && invite && (
          <div className="flex flex-col items-center gap-4">
            <h2 className="text-xl font-bold text-gray-900">You&apos;ve been invited!</h2>
            <p className="text-sm text-gray-600">
              You&apos;ve been invited to join{" "}
              <strong className="text-gray-900">{invite.projectName}</strong> as a{" "}
              <span className="font-semibold text-brand-600">{roleLabel}</span>.
            </p>
            {invite.projectDescription && (
              <p className="text-sm text-gray-400 italic">"{invite.projectDescription}"</p>
            )}
            <p className="text-xs text-gray-400">
              Sign in with <strong>{invite.email}</strong> to accept.
            </p>
            <button
              onClick={() => signIn(undefined, { callbackUrl: `/invites/${token}` })}
              className="w-full px-5 py-3 bg-brand-500 text-white rounded-xl font-semibold text-sm hover:bg-brand-600 transition-colors"
            >
              Sign in to accept
            </button>
          </div>
        )}

        {/* Ready — signed in with correct account */}
        {pageState === "ready" && status === "authenticated" && invite &&
          session?.user?.email?.toLowerCase() === invite.email && (
          <div className="flex flex-col items-center gap-4">
            <h2 className="text-xl font-bold text-gray-900">You&apos;ve been invited!</h2>
            <p className="text-sm text-gray-600">
              Join <strong className="text-gray-900">{invite.projectName}</strong> as a{" "}
              <span className="font-semibold text-brand-600">{roleLabel}</span>.
            </p>
            {invite.projectDescription && (
              <p className="text-sm text-gray-400 italic">"{invite.projectDescription}"</p>
            )}
            <button
              onClick={handleAccept}
              className="w-full px-5 py-3 bg-brand-500 text-white rounded-xl font-semibold text-sm hover:bg-brand-600 transition-colors"
            >
              Accept invitation
            </button>
            <Link href="/dashboard" className="text-xs text-gray-400 hover:underline">
              Decline
            </Link>
          </div>
        )}

        {/* Accepting */}
        {pageState === "accepting" && (
          <div className="flex flex-col items-center gap-3 py-4">
            <Loader2 className="animate-spin text-brand-500" size={32} />
            <p className="text-gray-500">Accepting invitation…</p>
          </div>
        )}
      </div>
    </div>
  );
}
