export type Mode = "demo" | "live";
export type TaskStatus =
  | "queued"
  | "running"
  | "recovering"
  | "succeeded"
  | "failed"
  | "cancel_requested"
  | "cancelled";
export interface PlanItem {
  id: string;
  agent: string;
  title: string;
  status: string;
}
export interface Artifact {
  id: string;
  name: string;
  media_type: string;
  kind: string;
  metadata?: Record<string, unknown>;
}
export interface Task {
  id: string;
  title: string;
  question: string;
  mode: Mode;
  status: TaskStatus;
  phase: string;
  progress: number;
  attempt: number;
  created_at: string;
  updated_at: string;
  dataset_ids: string[];
  document_ids: string[];
  scope: Record<string, string>;
  plan: PlanItem[];
  report_markdown?: string | null;
  error?: string | null;
  metrics?: Metrics;
  artifacts?: Artifact[];
}
export interface ResearchEvent {
  id: string;
  task_id: string;
  seq: number;
  event_type: string;
  agent: string;
  tool?: string | null;
  data: Record<string, unknown>;
  created_at: string;
}
export interface DocumentResource {
  id: string;
  name: string;
  chunk_count: number;
  created_at: string;
  metadata?: Record<string, unknown>;
}
export interface DatasetResource {
  id: string;
  name: string;
  columns: string[];
  row_count: number;
  created_at: string;
  metadata?: Record<string, unknown>;
}
export interface Evidence {
  id: string;
  task_id: string;
  source_type: string;
  title: string;
  content: string;
  url?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
}
export interface Metrics {
  total_calls: number;
  success_rate: number;
  average_duration_ms: number;
  cache_hits: number;
  cache_hit_rate: number;
  exact_hits: number;
  semantic_hits: number;
  by_tool: {
    tool: string;
    calls: number;
    success_rate: number;
    average_duration_ms: number;
  }[];
  by_agent: { agent: string; calls: number }[];
  tasks?: { total: number; succeeded: number; failed: number; running: number };
}
export interface Health {
  status: string;
  mode: string;
  version: string;
  services: Record<
    string,
    { available?: boolean; configured?: boolean; error?: string }
  >;
}
export interface AppConfig {
  missing_live_config: string[];
  live_ready?: boolean;
  redis?: {
    available: boolean;
    vector_search_available?: boolean;
    note?: string;
  };
  tools?: unknown;
  [key: string]: unknown;
}
