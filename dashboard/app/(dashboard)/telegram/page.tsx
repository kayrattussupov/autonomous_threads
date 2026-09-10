import { getTelegramAlerts } from "@/lib/api-client";
import { TelegramAlertRow } from "./TelegramAlertRow";

export const dynamic = "force-dynamic";

export default async function TelegramPage() {
  const alerts = await getTelegramAlerts(50);

  return (
    <main>
      <h1>Telegram</h1>
      <table>
        <thead>
          <tr>
            <th>Источник</th>
            <th>Время</th>
            <th>Статус</th>
            <th>Текст</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((alert) => (
            <TelegramAlertRow key={alert.id} alert={alert} />
          ))}
        </tbody>
      </table>
    </main>
  );
}
