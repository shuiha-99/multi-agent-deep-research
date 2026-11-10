import type {
  Artifact,
  AppConfig,
  DatasetResource,
  DocumentResource,
  Evidence,
  Health,
  Metrics,
  Mode,
  ResearchEvent,
  Task,
} from "./types";

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message =
        typeof body.detail === "string"
          ? body.detail
          : Array.isArray(body.detail)
            ? body.detail.map((i: { msg?: string }) => i.msg).join("；")
            : message;
    } catch {
      /* Keep the HTTP status when a proxy returns non-JSON. */
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/health"),
  config: () => request<AppConfig>("/config"),
  tasks: () => request<Task[]>("/research/tasks"),
  task: (id: string) =>
    request<Task>(`/research/tasks/${encodeURIComponent(id)}`),
  events: (id: string, after = 0) =>
    request<ResearchEvent[]>(
      `/research/tasks/${encodeURIComponent(id)}/events?after_seq=${after}`,
    ),
  sources: (id: string) =>
    request<Evidence[]>(`/research/tasks/${encodeURIComponent(id)}/sources`),
  report: (id: string) =>
    request<{ markdown: string }>(
      `/research/tasks/${encodeURIComponent(id)}/report`,
    ),
  documents: () => request<DocumentResource[]>("/documents"),
  datasets: () => request<DatasetResource[]>("/datasets"),
  metrics: () => request<Metrics>("/metrics"),
  samples: () =>
    request<{ documents: DocumentResource[]; datasets: DatasetResource[] }>(
      "/samples/load",
      { method: "POST" },
    ),
  create: (payload: {
    question: string;
    title?: string;
    mode: Mode;
    dataset_ids: string[];
    document_ids: string[];
    scope: Record<string, string>;
  }) =>
    request<Task>("/research/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  cancel: (id: string) =>
    request<Task>(`/research/tasks/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),
  retry: (id: string) =>
    request<Task>(`/research/tasks/${encodeURIComponent(id)}/retry`, {
      method: "POST",
    }),
  upload: <T>(kind: "documents" | "datasets", file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<T>(`/${kind}`, { method: "POST", body });
  },
};

export const activeStatus = (status: string) =>
  ["queued", "running", "recovering", "cancel_requested"].includes(status);
export function safeLink(value?: string | null): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export function artifactLink(
  value: string | undefined,
  artifacts: Artifact[] | undefined,
  imageOnly = false,
): string | undefined {
  if (!value || !artifacts) return undefined;
  const artifact = artifacts.find(
    (item) => value === `/api/artifacts/${encodeURIComponent(item.id)}`,
  );
  if (!artifact || (imageOnly && !artifact.media_type.startsWith("image/")))
    return undefined;
  return `/api/artifacts/${encodeURIComponent(artifact.id)}`;
}
