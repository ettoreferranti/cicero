import type {
  Chamber,
  DebateSettings,
  ParticipantMetrics,
  Provider,
  Stance,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface ServerConfig {
  web_access_enabled: boolean;
}

export function getServerConfig(): Promise<ServerConfig> {
  return request<ServerConfig>("/config");
}

export function listChambers(): Promise<Chamber[]> {
  return request<Chamber[]>("/chambers");
}

export function getChamber(id: string): Promise<Chamber> {
  return request<Chamber>(`/chambers/${id}`);
}

export function createChamber(input: {
  topic: string;
  category?: string;
  description?: string;
}): Promise<Chamber> {
  return request<Chamber>("/chambers", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateSettings(
  id: string,
  settings: Partial<DebateSettings>,
): Promise<Chamber> {
  return request<Chamber>(`/chambers/${id}/settings`, {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export function addNote(id: string, content: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/chambers/${id}/notes`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export function deleteChamber(id: string): Promise<void> {
  return request<void>(`/chambers/${id}`, { method: "DELETE" });
}

export function cloneChamber(id: string): Promise<Chamber> {
  return request<Chamber>(`/chambers/${id}/clone`, { method: "POST" });
}

export function addParticipant(
  id: string,
  input: { display_name: string; provider: Provider; model: string; stance: Stance },
): Promise<Chamber> {
  return request<Chamber>(`/chambers/${id}/participants`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function startDebate(id: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/chambers/${id}/run`, { method: "POST" });
}

export function stopDebate(id: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/chambers/${id}/stop`, { method: "POST" });
}

export function resumeDebate(id: string): Promise<{ status: string }> {
  return request<{ status: string }>(`/chambers/${id}/resume`, { method: "POST" });
}

export function getMetrics(id: string): Promise<ParticipantMetrics[]> {
  return request<ParticipantMetrics[]>(`/chambers/${id}/metrics`);
}

export async function listModels(provider: Provider): Promise<string[]> {
  const body = await request<{ provider: string; models: string[] }>(
    `/providers/${provider}/models`,
  );
  return body.models;
}

export function eventsUrl(id: string): string {
  return `/chambers/${id}/events`;
}

export function exportUrl(id: string, format: "json" | "markdown"): string {
  return `/chambers/${id}/export?format=${format}`;
}
