export type Post = {
  id: number;
  text: string;
  category: string;
  status: string;
  source_url: string | null;
  style_variant_id: number | null;
  experiment_id: string | null;
  playbook_version: number | null;
  model_used: string | null;
  scheduled_at: string | null;
  posted_at: string | null;
  threads_media_id: string | null;
  views: number | null;
  likes: number | null;
  replies_count: number | null;
  quotes: number | null;
  score: number | null;
  metrics_updated_at: string | null;
  created_at: string;
};

export type PostsPage = {
  items: Post[];
  total: number;
  page: number;
  page_size: number;
  median_score: number | null;
};

export type AgentRun = {
  id: number;
  agent: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  steps_count: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  error: string | null;
  output_ref: string | null;
};

export type AgentStep = {
  id: number;
  run_id: number;
  step_no: number;
  thought: string | null;
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_result: Record<string, unknown> | null;
  tool_ok: boolean | null;
  tool_ms: number | null;
  created_at: string;
};

export type StyleVariant = {
  id: number;
  name: string;
  genome: string;
  status: string;
  created_by: string;
  parent_id: number | null;
  rationale: string | null;
  posts_n: number | null;
  median_score: number | null;
  created_at: string;
};

export type PlaybookRule = {
  id: number;
  rule_text: string;
  status: string;
  hypothesis: string | null;
  target_metric: string | null;
  evidence_n: number | null;
  median_before: number | null;
  median_after: number | null;
  version: number;
  introduced_at: string;
};

export type FunnelMonth = {
  month: string;
  posts: number;
  views: number;
  replies: number;
  conversations: number;
  leads: number;
};

export type Spend = {
  month_to_date_usd: number;
  cap_usd: number;
};
