import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import {
  ApiError,
  approvePlaybookRule,
  getPlaybook,
  rejectPlaybookRule,
  type PlaybookRule,
} from "@/lib/api-client";

export const dynamic = "force-dynamic";

async function approveAction(formData: FormData) {
  "use server";
  const id = Number(formData.get("id"));
  try {
    await approvePlaybookRule(id);
  } catch (e) {
    const message = e instanceof ApiError ? e.message : "Не удалось выполнить действие";
    redirect(`/playbook?error=${encodeURIComponent(message)}`);
  }
  revalidatePath("/playbook");
}

async function rejectAction(formData: FormData) {
  "use server";
  const id = Number(formData.get("id"));
  try {
    await rejectPlaybookRule(id);
  } catch (e) {
    const message = e instanceof ApiError ? e.message : "Не удалось выполнить действие";
    redirect(`/playbook?error=${encodeURIComponent(message)}`);
  }
  revalidatePath("/playbook");
}

function RuleRow({ rule }: { rule: PlaybookRule }) {
  return (
    <tr>
      <td>{rule.rule_text}</td>
      <td>{rule.status}</td>
      <td>{rule.hypothesis ?? "—"}</td>
      <td>{rule.evidence_n ?? 0}</td>
      <td>
        {rule.median_before ?? "—"} → {rule.median_after ?? "—"}
      </td>
      <td>
        {rule.status === "proposed" && (
          <>
            <form action={approveAction} style={{ display: "inline" }}>
              <input type="hidden" name="id" value={rule.id} />
              <button type="submit">Принять</button>
            </form>{" "}
            <form action={rejectAction} style={{ display: "inline" }}>
              <input type="hidden" name="id" value={rule.id} />
              <button type="submit">Отклонить</button>
            </form>
          </>
        )}
      </td>
    </tr>
  );
}

export default async function PlaybookPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const params = await searchParams;
  const rules = await getPlaybook();

  return (
    <main>
      <h1>Playbook</h1>
      {params.error && (
        <p style={{ color: "crimson", padding: 8, border: "1px solid crimson" }}>{params.error}</p>
      )}
      <table>
        <thead>
          <tr>
            <th>Правило</th>
            <th>Статус</th>
            <th>Гипотеза</th>
            <th>Evidence</th>
            <th>Медиана до → после</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <RuleRow key={rule.id} rule={rule} />
          ))}
        </tbody>
      </table>
    </main>
  );
}
