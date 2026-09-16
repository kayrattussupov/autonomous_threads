import Link from "next/link";
import { getSectors } from "@/lib/api-client";

export const dynamic = "force-dynamic";

function formatNumber(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

export default async function SectorsPage() {
  const sectors = await getSectors();
  const sorted = [...sectors].sort((a, b) => (b.probability ?? -1) - (a.probability ?? -1));

  return (
    <main>
      <h1>Сферы</h1>
      <p>Вероятность — шанс, что планировщик выберет сферу для следующего поста.</p>
      <table>
        <thead>
          <tr>
            <th>Сфера</th>
            <th>Источник</th>
            <th>Активна</th>
            <th>Опубликовано</th>
            <th>Средний score</th>
            <th>Медиана score</th>
            <th>Последний пост</th>
            <th>Вероятность</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => (
            <tr key={s.name}>
              <td>
                <Link href={`/posts?sector=${encodeURIComponent(s.name)}`}>{s.name}</Link>
              </td>
              <td>{s.source}</td>
              <td>{s.active ? "да" : "нет"}</td>
              <td>{s.published_n}</td>
              <td>{formatNumber(s.mean_score)}</td>
              <td>{formatNumber(s.median_score)}</td>
              <td>{s.last_post_at ?? "—"}</td>
              <td>{s.probability === null ? "—" : `${(s.probability * 100).toFixed(0)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
