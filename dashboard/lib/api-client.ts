import type {
  AgentRun,
  AgentStep,
  FunnelMonth,
  PlaybookRule,
  Post,
  PostsPage,
  Spend,
  StyleVariant,
} from "./api-types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const baseUrl = process.env.API_BASE_URL;
  const token = process.env.API_BEARER_TOKEN;
  if (!baseUrl || !token) {
    throw new Error("API_BASE_URL and API_BEARER_TOKEN must be set");
  }

  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      ...init?.headers,
      Authorization: `Bearer ${token}`,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }

  return response.json() as Promise<T>;
}

export function getPosts(params: {
  category?: string;
  style_variant_id?: number;
  model_used?: string;
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<PostsPage> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  return apiFetch<PostsPage>(`/posts?${query.toString()}`);
}

export function getRuns(limit = 50): Promise<AgentRun[]> {
  return apiFetch<AgentRun[]>(`/runs?limit=${limit}`);
}

export function getRunSteps(runId: number): Promise<AgentStep[]> {
  return apiFetch<AgentStep[]>(`/runs/${runId}/steps`);
}

export function getStyles(): Promise<StyleVariant[]> {
  return apiFetch<StyleVariant[]>("/styles");
}

export function approveStyle(id: number): Promise<StyleVariant> {
  return apiFetch<StyleVariant>(`/styles/${id}/approve`, { method: "POST" });
}

export function rejectStyle(id: number): Promise<StyleVariant> {
  return apiFetch<StyleVariant>(`/styles/${id}/reject`, { method: "POST" });
}

export function getPlaybook(): Promise<PlaybookRule[]> {
  return apiFetch<PlaybookRule[]>("/playbook");
}

export function approvePlaybookRule(id: number): Promise<PlaybookRule> {
  return apiFetch<PlaybookRule>(`/playbook/${id}/approve`, { method: "POST" });
}

export function rejectPlaybookRule(id: number): Promise<PlaybookRule> {
  return apiFetch<PlaybookRule>(`/playbook/${id}/reject`, { method: "POST" });
}

export function getFunnel(months = 6): Promise<FunnelMonth[]> {
  return apiFetch<FunnelMonth[]>(`/funnel?months=${months}`);
}

export function getSpend(): Promise<Spend> {
  return apiFetch<Spend>("/spend");
}

export type {
  AgentRun,
  AgentStep,
  FunnelMonth,
  PlaybookRule,
  Post,
  PostsPage,
  Spend,
  StyleVariant,
};
