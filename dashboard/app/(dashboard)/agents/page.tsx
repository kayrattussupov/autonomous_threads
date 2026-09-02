import { getRuns } from "@/lib/api-client";
import { RunRow } from "./RunRow";

export const dynamic = "force-dynamic";

export default async function AgentsPage() {
  const runs = await getRuns(50);

  return (
    <main>
      <h1>Агенты</h1>
      <table>
        <thead>
          <tr>
            <th>Агент</th>
            <th>Триггер</th>
            <th>Начало</th>
            <th>Статус</th>
            <th>Шаги</th>
            <th>Токены</th>
            <th>Стоимость</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <RunRow key={run.id} run={run} />
          ))}
        </tbody>
      </table>
    </main>
  );
}
