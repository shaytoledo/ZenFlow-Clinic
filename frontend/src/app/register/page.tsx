import { auth } from "@/auth";
import { redirect } from "next/navigation";
import AuthCard from "@/components/AuthCard";

export default async function RegisterPage() {
  const session = await auth();
  if (session) redirect("/dashboard");
  return <AuthCard mode="register" />;
}
