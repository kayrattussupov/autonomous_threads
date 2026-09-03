import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { ApiError, approveStyle, getStyles, rejectStyle, type StyleVariant } from "@/lib/api-client";

export const dynamic = "force-dynamic";

async function approveAction(formData: FormData) {
  "use server";
  const id = Number(formData.get("id"));
  try {
    await approveStyle(id);
  } catch (e) {
    const message = e instanceof ApiError ? e.message : "Не удалось выполнить действие";
    redirect(`/styles?error=${encodeURIComponent(message)}`);
  }
  revalidatePath("/styles");
}

async function rejectAction(formData: FormData) {
  "use server";
  const id = Number(formData.get("id"));
  try {
    await rejectStyle(id);
  } catch (e) {
    const message = e instanceof ApiError ? e.message : "Не удалось выполнить действие";
    redirect(`/styles?error=${encodeURIComponent(message)}`);
  }
  revalidatePath("/styles");
}

function VariantRow({ variant }: { variant: StyleVariant }) {
  return (
    <tr>
      <td>{variant.name}</td>
      <td>{variant.status}</td>
      <td>{variant.median_score ?? "—"}</td>
      <td>{variant.posts_n ?? 0}</td>
      <td>{variant.rationale ?? "—"}</td>
      <td>{variant.parent_id ?? "—"}</td>
      <td>
        {variant.status === "draft" && (
          <>
            <form action={approveAction} style={{ display: "inline" }}>
              <input type="hidden" name="id" value={variant.id} />
              <button type="submit">Принять</button>
            </form>{" "}
            <form action={rejectAction} style={{ display: "inline" }}>
              <input type="hidden" name="id" value={variant.id} />
              <button type="submit">Отклонить</button>
            </form>
          </>
        )}
      </td>
    </tr>
  );
}

export default async function StylesPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const params = await searchParams;
  const variants = await getStyles();

  return (
    <main>
      <h1>Стили</h1>
      {params.error && (
        <p style={{ color: "crimson", padding: 8, border: "1px solid crimson" }}>{params.error}</p>
      )}
      <table>
        <thead>
          <tr>
            <th>Название</th>
            <th>Статус</th>
            <th>Медиана score</th>
            <th>Постов</th>
            <th>Обоснование</th>
            <th>Родитель</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {variants.map((variant) => (
            <VariantRow key={variant.id} variant={variant} />
          ))}
        </tbody>
      </table>
    </main>
  );
}
