"use server";

import { getRunSteps, type AgentStep } from "@/lib/api-client";

export async function fetchStepsAction(runId: number): Promise<AgentStep[]> {
  return getRunSteps(runId);
}
