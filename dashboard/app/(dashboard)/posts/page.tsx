import { getPosts } from "@/lib/api-client";

export const dynamic = "force-dynamic";

export default async function PostsPage({
  searchParams,
}: {
  searchParams: Promise<{
    category?: string;
    status?: string;
    model_used?: string;
    page?: string;
  }>;
}) {
  const params = await searchParams;
  const page = Number(params.page ?? "1");

  const data = await getPosts({
    category: params.category,
    status: params.status,
    model_used: params.model_used,
    page,
    page_size: 25,
  });

  return (
    <main>
      <h1>Посты</h1>
      <form method="GET" style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <select name="status" defaultValue={params.status ?? ""}>
          <option value="">Все статусы</option>
          <option value="draft">draft</option>
          <option value="needs_review">needs_review</option>
          <option value="scheduled">scheduled</option>
          <option value="published">published</option>
          <option value="failed">failed</option>
        </select>
        <input type="text" name="category" placeholder="Категория" defaultValue={params.category ?? ""} />
        <input type="text" name="model_used" placeholder="Модель" defaultValue={params.model_used ?? ""} />
        <button type="submit">Фильтр</button>
      </form>
      <p>
        Всего: {data.total} · Медиана score: {data.median_score ?? "—"}
      </p>
      <table>
        <thead>
          <tr>
            <th>Текст</th>
            <th>Категория</th>
            <th>Статус</th>
            <th>Модель</th>
            <th>Просмотры</th>
            <th>Score</th>
            <th>Запланирован</th>
            <th>Опубликован</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((post) => (
            <tr key={post.id}>
              <td>{post.text.slice(0, 80)}</td>
              <td>{post.category}</td>
              <td>{post.status}</td>
              <td>{post.model_used ?? "—"}</td>
              <td>{post.views ?? "—"}</td>
              <td>{post.score ?? "—"}</td>
              <td>{post.scheduled_at ?? "—"}</td>
              <td>{post.posted_at ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
