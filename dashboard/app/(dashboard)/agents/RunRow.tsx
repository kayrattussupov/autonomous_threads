"use client";

import { useState } from "react";
import type { AgentRun, AgentStep } from "@/lib/api-client";
import { fetchStepsAction } from "./actions";

export function RunRow({ run }: { run: AgentRun }) {
  const [expanded, setExpanded] = useState(false);
  const [steps, setSteps] = useState<AgentStep[] | null>(null);
  const [loading, setLoading] = useState(false);

  async function toggle() {
    if (!expanded && steps === null) {
      setLoading(true);
      try {
        const result = await fetchStepsAction(run.id);
        setSteps(result);
      } catch {
        setSteps([]);
      } finally {
        setLoading(false);
      }
    }
    setExpanded((value) => !value);
  }

  return (
    <>
      <tr onClick={toggle} style={{ cursor: "pointer" }}>
        <td>{run.agent}</td>
        <td>{run.trigger}</td>
        <td>{run.started_at}</td>
        <td>{run.status}</td>
        <td>{run.steps_count ?? 0}</td>
        <td>{(run.tokens_in ?? 0) + (run.tokens_out ?? 0)}</td>
        <td>{(run.cost_usd ?? 0).toFixed(4)}</td>
        <td>{expanded ? "▲" : "▼"}</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8}>
            {loading && <p>Загрузка...</p>}
            {steps?.map((step) => (
              <div key={step.id} style={{ borderTop: "1px solid #e0e0e0", padding: 8 }}>
                <strong>
                  Шаг {step.step_no}: {step.tool_name ?? "—"}
                </strong>{" "}
                ({step.tool_ok === false ? "ошибка" : "ok"}, {step.tool_ms ?? 0} мс)
                <p>{step.thought}</p>
              </div>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}
