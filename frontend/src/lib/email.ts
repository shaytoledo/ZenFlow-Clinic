/**
 * Email utility — uses Resend when RESEND_API_KEY is set.
 * Falls back to a no-op (invite link is shown in the UI instead).
 */

interface InviteEmailParams {
  to: string;
  inviterName: string;
  projectName: string;
  role: string;
  acceptUrl: string;
}

export async function sendInviteEmail(params: InviteEmailParams): Promise<void> {
  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) {
    // No email provider configured — the invite link is shown in the UI
    console.info("[email] RESEND_API_KEY not set, skipping invite email to", params.to);
    return;
  }

  const roleLabel = params.role === "EDITOR" ? "Editor" : "Viewer";

  const html = `
    <div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:32px 24px">
      <h2 style="margin:0 0 8px">You've been invited to a project</h2>
      <p style="color:#555;margin:0 0 24px">
        <strong>${params.inviterName}</strong> has invited you to collaborate on
        <strong>"${params.projectName}"</strong> on ContextPrompt as a <strong>${roleLabel}</strong>.
      </p>
      <a href="${params.acceptUrl}"
         style="display:inline-block;background:#4f6ef7;color:#fff;text-decoration:none;
                padding:12px 28px;border-radius:10px;font-weight:600;font-size:15px">
        Accept invitation
      </a>
      <p style="color:#999;font-size:13px;margin-top:24px">
        This invitation expires in 7 days. If you don't have a ContextPrompt account yet,
        you'll be asked to create one when you click the link.
      </p>
    </div>
  `;

  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: process.env.RESEND_FROM ?? "ContextPrompt <onboarding@resend.dev>",
      to: [params.to],
      subject: `${params.inviterName} invited you to "${params.projectName}" on ContextPrompt`,
      html,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    console.error("[email] Resend error:", err);
  }
}
