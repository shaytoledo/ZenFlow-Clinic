import NextAuth from "next-auth";
import { authConfig } from "@/auth.config";

// Use the lightweight config (no Prisma) so this stays under Vercel's 1 MB edge limit
const { auth } = NextAuth(authConfig);

export default auth;

export const config = {
  matcher: ["/dashboard/:path*", "/projects/:path*"],
};
