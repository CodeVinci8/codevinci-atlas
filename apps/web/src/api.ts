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

// --- VP-3 Product Map ------------------------------------------------------
export type TruthStatus =
  | "VERIFIED" | "OWNER_PROVIDED" | "INFERRED" | "HYPOTHESIS" | "STALE" | "UNKNOWN";
export type DecisionStatus = "proposed" | "accepted" | "rejected";
export type NodeType =
  | "goal" | "user_problem" | "brief_decision" | "vp" | "blocker"
  | "evidence_ref" | "next_action" | "parking_item";
export type EdgeType = "dependency" | "blocks" | "proves" | "includes" | "next";

export interface Fact { text: string; truth_status: TruthStatus; evidence_ref: string; evidence_hash: string; }
export interface Hypothesis { text: string; truth_status: TruthStatus; }
export interface BriefContent {
  product_statement: string; user_and_problem: string; current_alternative: string;
  promised_result: string; confirmed_facts: Fact[]; hypotheses: Hypothesis[];
  main_scenario: string; mvp_scope: string[]; out_of_scope: string[];
  success_metric: string; risks: string[]; minimum_validation: string;
  stop_criterion: string; linked_decisions: string[];
}
export interface Envelope { in_scope: string[]; out_of_scope: string[]; constraints: string[]; boundary_note: string; }
export interface FullBrief {
  id: string; version: number; parent_id: string; status: string;
  content_hash: string; envelope_hash: string; created_at: string;
  content: BriefContent; envelope: Envelope;
}
export interface BriefRef { version: number; id: string; status: string; content_hash: string; created_at: string; }
export interface DecisionRow {
  id: string; decision_key: string; title: string; detail: string;
  status: DecisionStatus; required: boolean; truth_status: TruthStatus;
  note: string; version: number; updated_at: string;
}
export interface ParkingRow {
  id: string; title: string; reason: string; return_condition: string;
  status: string; version: number; created_at: string;
}
export interface MapNode {
  node_key: string; node_type: NodeType; title: string; detail: string;
  truth_status: TruthStatus; evidence_ref: string; evidence_hash: string; data: unknown;
}
export interface MapEdge { edge_id: string; src_key: string; dst_key: string; edge_type: EdgeType; }
export interface MapView {
  id: string; version: number; status: string; content_hash: string;
  created_at: string; nodes: MapNode[]; edges: MapEdge[];
}
export interface ProductState {
  project: { id: string; name: string; status: string; source_kind: SourceKind };
  brief: FullBrief | null;
  approved_brief_version: number | null;
  brief_versions: BriefRef[];
  decisions: DecisionRow[];
  parking_lot: ParkingRow[];
  map: MapView | null;
  active_vp: string | null;
  stage: string;
  next_action: string;
}
export interface PortfolioRow {
  project_id: string; name: string; status: string; stage: string;
  active_vp: string; last_known_state: string; blocker: string; truth_state: string;
  brief_version: number | null; approved_version: number | null; next_action: string;
}
export interface BriefDiff {
  from: number; to: number; from_hash: string; to_hash: string;
  content: { added: Record<string, unknown>; removed: Record<string, unknown>; changed: Record<string, { from: unknown; to: unknown }> };
  envelope: { added: Record<string, unknown>; removed: Record<string, unknown>; changed: Record<string, { from: unknown; to: unknown }> };
}

export interface IntakeBody {
  idea?: string; target_user?: string; desired_result?: string;
  constraints?: string[]; risks?: string[]; links?: string[];
  baseline_refs?: string[]; permissions_notes?: string;
  parking_suggestions?: string[];
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

  // VP-3 Product Map
  productState: (id: string) => getJSON<ProductState>(`/api/v1/projects/${id}/product-state`),
  submitIntake: (id: string, body: IntakeBody) =>
    sendJSON<ProductState>(`/api/v1/projects/${id}/intake`, "POST", body),
  reviseBrief: (id: string, briefId: string, changes: Record<string, unknown>, expected: number) =>
    sendJSON<FullBrief>(`/api/v1/projects/${id}/briefs/${briefId}/revise`, "POST",
      { changes, expected_version: expected }),
  approveBrief: (id: string, briefId: string, expected: number) =>
    sendJSON<Record<string, string>>(`/api/v1/projects/${id}/briefs/${briefId}/approve`, "POST",
      { expected_version: expected }),
  decide: (id: string, decisionId: string, action: "accept" | "reject", note: string, expected: number) =>
    sendJSON<DecisionRow>(`/api/v1/projects/${id}/decisions/${decisionId}/${action}`, "POST",
      { note, expected_version: expected }),
  addParking: (id: string, body: { title: string; reason?: string; return_condition?: string }) =>
    sendJSON<ParkingRow>(`/api/v1/projects/${id}/parking-lot`, "POST", body),
  activateVp: (id: string, vpKey: string) =>
    sendJSON<{ active_vp: string | null }>(`/api/v1/projects/${id}/map/vps/activate`, "POST", { vp_key: vpKey }),
  briefDiff: (id: string, from: number, to: number) =>
    getJSON<BriefDiff>(`/api/v1/projects/${id}/briefs/diff?from=${from}&to=${to}`),
  portfolio: () => getJSON<{ projects: PortfolioRow[] }>("/api/v1/portfolio"),
  exportUrl: (id: string, format: "json" | "md") => `/api/v1/projects/${id}/export?format=${format}`,

  // --- VP-4 Work Orders & Context ---
  listVpSpecs: (id: string) => getJSON<{ vp_specs: VpSpecSummary[] }>(`/api/v1/projects/${id}/vp-specs`),
  getVpSpec: (id: string, sid: string) => getJSON<VpSpecFull>(`/api/v1/projects/${id}/vp-specs/${sid}`),
  createVpSpec: (id: string, vpKey: string) =>
    sendJSON<VpSpecFull>(`/api/v1/projects/${id}/vp-specs`, "POST", { vp_key: vpKey }),
  listWorkOrders: (id: string) =>
    getJSON<{ work_orders: WorkOrderRow[] }>(`/api/v1/projects/${id}/work-orders`),
  getWorkOrder: (id: string, wid: string) =>
    getJSON<WorkOrderFull>(`/api/v1/projects/${id}/work-orders/${wid}`),
  createWorkOrder: (id: string, body: { vp_spec_id: string; goal: string; role?: string }) =>
    sendJSON<WorkOrderFull>(`/api/v1/projects/${id}/work-orders`, "POST", body),
  transitionWo: (id: string, wid: string, toStatus: string, expected: number) =>
    sendJSON<WorkOrderFull>(`/api/v1/projects/${id}/work-orders/${wid}/transition`, "POST",
      { to_status: toStatus, expected_version: expected }),
  buildJobPackage: (id: string, wid: string) =>
    sendJSON<JobPackage>(`/api/v1/projects/${id}/work-orders/${wid}/job-package`, "POST"),
  buildCheckpoint: (id: string, wid: string, body: Record<string, unknown>) =>
    sendJSON<CheckpointRow>(`/api/v1/projects/${id}/work-orders/${wid}/checkpoints`, "POST", body),
  buildHandoff: (id: string, wid: string, checkpointId: string) =>
    sendJSON<HandoffRow>(`/api/v1/projects/${id}/work-orders/${wid}/handoffs`, "POST",
      { checkpoint_id: checkpointId }),
  listHandoffs: (id: string, wid: string) =>
    getJSON<{ handoffs: HandoffRow[] }>(`/api/v1/projects/${id}/handoffs?work_order_id=${wid}`),
  reconstruct: (id: string, hid: string) =>
    sendJSON<ReconstructResult>(`/api/v1/projects/${id}/handoffs/${hid}/reconstruct`, "POST", {}),
  evaluate: (id: string, woIds: string[]) =>
    sendJSON<OptimizerDecision>(`/api/v1/projects/${id}/optimizer/evaluate`, "POST",
      { work_order_ids: woIds }),
  mergePreview: (id: string, woIds: string[]) =>
    sendJSON<MergePreview>(`/api/v1/projects/${id}/optimizer/merge/preview`, "POST",
      { work_order_ids: woIds }),
  listCheckpoints: (id: string, wid: string) =>
    getJSON<{ checkpoints: CheckpointRow[] }>(`/api/v1/projects/${id}/checkpoints?work_order_id=${wid}`),

  // --- VP-5 Agent Pipeline ---
  listRuns: (projectId?: string) =>
    getJSON<{ runs: RunRow[] }>(`/api/v1/runs${projectId ? `?project_id=${projectId}` : ""}`),
  getRun: (rid: string) => getJSON<{ run: RunDetail }>(`/api/v1/runs/${rid}`),
  runEvents: (rid: string) => getJSON<{ events: RunEventRow[] }>(`/api/v1/runs/${rid}/events`),
  runRouter: (rid: string) =>
    getJSON<{ decisions: RouterDecisionRow[]; sessions: ProviderSessionRow[] }>(`/api/v1/runs/${rid}/router`),
  createRun: (body: { project_id: string; work_order_id?: string; vp_key?: string }) =>
    sendJSON<{ run: RunDetail }>("/api/v1/runs", "POST", body),
  pauseRun: (rid: string, expected: number) =>
    sendJSON<{ run: RunDetail }>(`/api/v1/runs/${rid}/pause`, "POST", { expected_version: expected }),
  resumeRun: (rid: string, expected: number) =>
    sendJSON<{ run: RunDetail }>(`/api/v1/runs/${rid}/resume`, "POST", { expected_version: expected }),
  cancelRun: (rid: string, expected: number) =>
    sendJSON<{ run: RunDetail }>(`/api/v1/runs/${rid}/cancel`, "POST", { expected_version: expected }),
  listProfiles: () =>
    getJSON<{ profiles: ProfileView[]; summary: Record<string, number> }>("/api/v1/profiles"),
  listModels: () => getJSON<{ models: ModelRow[] }>("/api/v1/models"),
  systemSummary: () => getJSON<{ summary: SystemSummary }>("/api/v1/system/summary"),
};

// --- VP-5 types ------------------------------------------------------------
export type RunState =
  | "QUEUED" | "PREPARING" | "RUNNING" | "COLLECTING" | "SUCCEEDED"
  | "RATE_LIMITED" | "AUTH_REQUIRED" | "PAUSED" | "INTERRUPTED"
  | "FAILED" | "CANCELLED" | "OWNER_REQUIRED";

export interface RunRow {
  id: string; project_id: string; work_order_id: string; vp_key: string;
  state: RunState; next_action: string; blocker: string; failure_class: string;
  version: number; created_at: string; updated_at: string;
}
export interface RoleStep {
  id: string; run_id: string; role: string; seq: number;
  requested_model: string; effective_model: string;
  requested_profile: string; effective_profile: string; provider: string;
  session_ref: string; status: string; verdict: string; reason_code: string;
}
export interface RunDetail extends RunRow {
  role_steps: RoleStep[];
  events_count: number;
  active_lease: { profile_id: string; role: string; worktree: string }[];
}
export interface RunEventRow {
  id: string; run_id: string; seq: number; type: string;
  occurred_at: string; payload: Record<string, unknown>; schema_version: number;
}
export interface RouterDecisionRow {
  id: string; run_id: string; role: string;
  requested_model: string; requested_profile: string;
  effective_model: string; effective_profile: string;
  reason_code: string; candidates: unknown[]; decided_at: string;
}
export interface ProviderSessionRow {
  id: string; run_id: string; role: string; provider: string;
  profile_id: string; session_id: string; status: string; started_at: string;
}
export interface CapacityView {
  status: string; five_h_used_pct: number | null; seven_d_used_pct: number | null;
  reset_at: string | null; source: string; observed_at: string | null;
}
export interface ProfileHealthView {
  auth_status: string; plan_label: string; cli_version: string;
  permissions_ok: boolean; observed_at: string;
}
export interface ProfileView {
  id: string; alias: string; provider: string; unix_label: string;
  schedulable: boolean; enabled: boolean; state: string;
  cooldown_until: string | null; drain: boolean;
  current_run_id: string; current_role: string; next_action: string;
  health: ProfileHealthView | null;
  capacity: CapacityView;
  active_lease: { run_id: string; role: string; worktree: string } | null;
}
export interface ModelRow {
  id: string; provider: string; model_id: string; alias: string; display: string;
  availability: string; source: string; confidence: string; discovered_at: string;
}
export interface SystemSummary {
  collected_at: string; atlas_version: string; db_migration: string | null;
  cpu: { logical_cores: number | null; load_avg: number[] | null };
  memory: { total_bytes: number | null; used_bytes: number | null };
  disk: { total_bytes: number | null; used_bytes: number | null };
  os: { os_name: string | null; os_version: string | null; kernel: string | null;
        arch: string | null; machine_id: string | null };
  host_uptime_s: number | null;
  services: Record<string, { status: string; uptime_s: number | null; note?: string }>;
  backup_age_s: number | null;
  runs: { active: number | null; queued: number | null; paused: number | null;
          owner_required: number | null; status?: string };
  leases: { worktree_writers: number | null; profile_leases: number | null; status?: string };
}

// --- VP-4 types ------------------------------------------------------------
export type WoStatus =
  | "draft" | "ready" | "active" | "checkpointed" | "handoff_ready"
  | "blocked" | "completed" | "cancelled";

export interface SpecBinding {
  approval_id: string; brief_id?: string; brief_hash: string;
  map_version_id?: string; map_hash: string; envelope_hash: string;
  decisions_hash?: string; baseline_branch: string; baseline_head: string;
}
export interface VpSpecSummary {
  id: string; vp_key: string; version: number; status: string;
  content_hash: string; created_at: string; binding: SpecBinding;
}
export interface Criterion { id: string; text: string; required: boolean; shared?: boolean; source?: string; }
export interface VpSpecFull extends VpSpecSummary {
  content: {
    result: string; definition_of_done: string[]; acceptance_criteria: Criterion[];
    immutable_constraints: string[]; out_of_scope: string[]; stop_conditions: string[];
    required_checks: { id: string; name: string; command: string }[];
    exact_next_action: string; user_scenario: string;
  };
}
export interface WorkOrderRow {
  id: string; vp_spec_id: string; vp_key: string; role: string; status: WoStatus;
  goal: string; origin: string; version: number; content_hash: string;
  lease_active: boolean; writer_holder: string; created_at: string; binding: SpecBinding;
}
export interface WorkOrderFull extends WorkOrderRow {
  content: {
    role: string; goal: string; source_of_truth: string[];
    scope: { files: string[]; components: string[] }; out_of_scope: string[];
    acceptance_criteria: Criterion[]; required_checks: { id: string; name: string }[];
    capabilities: string[]; prohibited_actions: string[]; stop_conditions: string[];
    test_impact: string[]; exact_next_action: string; report_schema: string;
  };
  history: { from: string; to: string; reason: string; note: string; at: string }[];
}
export interface JobPackage {
  id: string; work_order_id: string; content_hash: string; byte_size: number;
  compact: boolean; counts: Record<string, number>; capabilities: string[];
  provenance: { source: string; ref: string; hash: string }[]; content: Record<string, unknown>;
}
export interface CheckpointRow {
  id: string; work_order_id?: string; content_hash: string; current_head: string;
  cause?: string; remaining_criteria?: string[]; completed_criteria?: string[]; created_at: string;
}
export interface HandoffRow {
  id: string; work_order_id: string; content_hash: string; status: string;
  compact: boolean; current_head?: string; created_at: string;
  content?: { acceptance_matrix?: { id: string; status: string }[] };
}
export interface ReconstructResult {
  ok: boolean; handoff_id: string; run_result_valid?: boolean; isolated?: boolean;
  stage?: string; rejections?: { code: string; reason: string }[];
  reconstruction?: Record<string, unknown>; next_action?: string;
  ack?: { result: string; content_hash: string } | null;
}
export interface OptimizerDecision {
  id?: string; decision: string; reason_code: string; explanation?: string;
  affected_work_orders?: string[]; exact_next_action: string;
}
export interface MergePreview {
  compatible: boolean; reason: string; work_order_ids: string[];
  criterion_mapping: Record<string, string[]>; merged_criteria: Criterion[];
  shared_criteria: string[]; criterion_conservation: boolean;
}
