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

// --- VP-2 Project Workspace ------------------------------------------------
export type SourceKind = "local_git" | "github" | "archive" | "empty";
export type ProjectState =
  | "clean" | "dirty" | "stale" | "empty" | "pending" | "error" | "disconnected";

export interface ProjectSummary {
  id: string;
  name: string;
  source_kind: SourceKind;
  source_location: string;
  source_ref: string;
  status: string;
  created_at: string;
  updated_at: string;
  disconnected_at: string | null;
  dirty?: boolean | null;
  branch?: string | null;
  has_baseline?: boolean;
}

export interface Remote { name: string; url: string; }
export interface PorcelainEntry { code: string; path: string; }
export interface Instruction {
  path: string; scope: string; precedence: number; read_ok: boolean;
  bytes: number; summary: string;
}
export interface PackageManager { name: string; evidence: string; }
export interface BaselineCommand { source: string; name: string; command: string; executed: boolean; }

export interface Baseline {
  branch: string;
  head: string;
  remotes: Remote[];
  dirty: boolean;
  porcelain: PorcelainEntry[];
  porcelain_truncated: boolean;
  tracked_total: number;
  tracked_changes: number;
  untracked: number;
  instructions: Instruction[];
  package_managers: PackageManager[];
  baseline_commands: BaselineCommand[];
  secret_scan: { scanned_files: number; clean: boolean };
  content_hash: string;
  observed_at: string;
}

export interface WorktreeRow {
  id: string; project_id: string; branch: string; path: string;
  status: string; created_at: string; removed_at: string | null;
}

export interface LeaseState {
  active: boolean;
  leases: { worktree: string; lease_id: string; holder: string; role: string }[];
}

export interface Overview {
  project: ProjectSummary;
  baseline: Baseline | null;
  live: { accessible: boolean; head: string; dirty: boolean };
  worktrees: WorktreeRow[];
  lease: LeaseState;
  state: ProjectState;
  stale: boolean;
  next_action: string;
}

export interface ApiError { code: string; reason: string; }

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

async function sendJSON<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (data as { error?: ApiError }).error;
    throw new Error(err ? `${err.code}: ${err.reason}` : `HTTP ${res.status}`);
  }
  return data as T;
}

export const api = {
  health: () => getJSON<Health>("/api/v1/health"),
  audit: () => getJSON<AuditPage>("/api/v1/audit?limit=20"),
  listProjects: () => getJSON<{ projects: ProjectSummary[] }>("/api/v1/projects"),
  getProject: (id: string) => getJSON<Overview>(`/api/v1/projects/${id}`),
  connectProject: (body: {
    name: string; source_kind: SourceKind;
    path?: string; github_ref?: string; archive_path?: string;
  }) => sendJSON<Overview>("/api/v1/projects", "POST", body),
  refreshBaseline: (id: string) =>
    sendJSON<Overview>(`/api/v1/projects/${id}/baseline/refresh`, "POST"),
  createWorktree: (id: string, branch: string) =>
    sendJSON<Overview>(`/api/v1/projects/${id}/worktrees`, "POST", { branch }),
  disconnect: (id: string) => sendJSON<Overview>(`/api/v1/projects/${id}`, "DELETE"),
};
