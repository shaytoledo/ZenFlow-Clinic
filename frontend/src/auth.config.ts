import type { NextAuthConfig } from "next-auth";
import Google from "next-auth/providers/google";

// Minimal config — NO Prisma import — safe to run in the Edge runtime (middleware)
export const authConfig: NextAuthConfig = {
  providers: [
    Google({
      clientId: process.env.AUTH_GOOGLE_ID!,
      clientSecret: process.env.AUTH_GOOGLE_SECRET!,
    }),
  ],
  pages: {
    signIn: "/login",
  },
  callbacks: {
    authorized({ auth, request: { nextUrl } }) {
      const isLoggedIn = !!auth?.user;
      const isProtected =
        nextUrl.pathname.startsWith("/dashboard") ||
        nextUrl.pathname.startsWith("/projects");
      if (isProtected && !isLoggedIn) return false;
      return true;
    },
  },
};
