import { getFunnel } from "@/lib/api-client";

export const dynamic = "force-dynamic";

export default async function FunnelPage() {
  const months = await getFunnel(6);

  return (
    <main>
      <h1>Воронка</h1>
      <table>
        <thead>
          <tr>
            <th>Месяц</th>
            <th>Посты</th>
            <th>Просмотры</th>
            <th>Ответы</th>
            <th>Диалоги</th>
            <th>Лиды</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.month}>
              <td>{m.month}</td>
              <td>{m.posts}</td>
              <td>{m.views}</td>
              <td>{m.replies}</td>
              <td>{m.conversations}</td>
              <td>{m.leads}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
