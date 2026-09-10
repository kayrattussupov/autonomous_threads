"use client";

import { useState } from "react";
import type { TelegramAlert } from "@/lib/api-client";

function preview(text: string): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > 100 ? `${oneLine.slice(0, 100)}…` : oneLine;
}

export function TelegramAlertRow({ alert }: { alert: TelegramAlert }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <tr onClick={() => setExpanded((value) => !value)} style={{ cursor: "pointer" }}>
        <td>{alert.source}</td>
        <td>{alert.sent_at}</td>
        <td>{alert.success ? "✅" : "❌"}</td>
        <td>{preview(alert.text)}</td>
        <td>{expanded ? "▲" : "▼"}</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={5}>
            <p style={{ whiteSpace: "pre-wrap", padding: 8 }}>{alert.text}</p>
            {alert.error_detail && (
              <p style={{ color: "crimson", padding: 8, border: "1px solid crimson", whiteSpace: "pre-wrap" }}>
                {alert.error_detail}
              </p>
            )}
            <p style={{ padding: 8, color: "#666" }}>
              chat_id: {alert.chat_id ?? "—"}, message_id: {alert.message_id ?? "—"}, попыток: {alert.retry_count}
            </p>
          </td>
        </tr>
      )}
    </>
  );
}
