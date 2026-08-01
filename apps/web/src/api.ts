// Клиент Core API. Ошибки честные (не выдуманное «онлайн»).

export type HealthStatus =
  | "READY"
  | "DEGRADED"
  | "OFFLINE"
  | "UNAUTHORIZED"
  | "UNKNOWN";

export interface Health {
  status: HealthStatus;
  version: string;
  time: string;
  core: { non_root: boolean; db: { ok: boolean; reason: string } };
  runner: { status: HealthStatus; reason?: string; non_root?: boolean; version?: string };
}

export interface AuditEvent {
  id: string;
  event_type: string;
  actor: string;
  message: string;
  created_at: string;
}

export interface AuditPage {
  total: number;
  events: AuditEvent[];
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  health: () => getJSON<Health>("/api/v1/health"),
  audit: () => getJSON<AuditPage>("/api/v1/audit?limit=20"),
};
