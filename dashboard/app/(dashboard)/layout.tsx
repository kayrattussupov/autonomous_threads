import type { ReactNode } from "react";
import Link from "next/link";
import { getSpend } from "@/lib/api-client";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: ReactNode }) {
  const spend = await getSpend();

  return (
    <>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: 16,
          borderBottom: "1px solid #e0e0e0",
        }}
      >
        <nav style={{ display: "flex", gap: 16 }}>
          <Link href="/">Воронка</Link>
          <Link href="/posts">Посты</Link>
          <Link href="/agents">Агенты</Link>
          <Link href="/styles">Стили</Link>
          <Link href="/playbook">Playbook</Link>
        </nav>
        <div>
          Расход: ${spend.month_to_date_usd.toFixed(2)} / ${spend.cap_usd.toFixed(2)}
        </div>
      </header>
      <div style={{ padding: 16 }}>{children}</div>
    </>
  );
}
