import { redirect } from "next/navigation";

// Фаза 1: при наличии сессии — редирект на /portfolio.
export default function Home() {
  redirect("/login");
}
