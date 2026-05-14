import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import Credentials from "next-auth/providers/credentials";
import { PrismaAdapter } from "@auth/prisma-adapter";
import bcrypt from "bcryptjs";
import { prisma } from "@/lib/prisma";
import { authConfig } from "./auth.config";

export const { handlers, auth, signIn, signOut } = NextAuth({
  ...authConfig,
  adapter: PrismaAdapter(prisma),
  // JWT strategy: session stored in a cookie — readable by Edge middleware
  // without a database round-trip. Prisma adapter still persists users &
  // OAuth accounts; only the session record moves to the JWT cookie.
  session: { strategy: "jwt" },
  providers: [
    Google({
      clientId: process.env.AUTH_GOOGLE_ID!,
      clientSecret: process.env.AUTH_GOOGLE_SECRET!,
    }),
    Credentials({
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) return null;

        const user = await prisma.user.findUnique({
          where: { email: credentials.email as string },
        });

        if (!user || !user.password) return null;

        const valid = await bcrypt.compare(
          credentials.password as string,
          user.password
        );
        return valid ? user : null;
      },
    }),
  ],
  callbacks: {
    ...authConfig.callbacks,
    // Encode the database user id into the JWT on first sign-in
    jwt({ token, user }) {
      if (user?.id) token.id = user.id;
      return token;
    },
    // Expose the id on the session object so API routes can read it
    session({ session, token }) {
      if (token?.id) session.user.id = token.id;
      return session;
    },
  },
});
