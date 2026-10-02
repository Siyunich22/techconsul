import { redirect } from "next/navigation";

// Без сессии AppShell сам отправит на /login.
export default function Home() {
  redirect("/portfolio");
}
