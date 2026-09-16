export interface Acceptance {
  passed: number;
  total: number;
  achieved: boolean;
}
export interface ProjectSummary {
  id: string;
  name: string;
  description: string;
  is_demo: boolean;
  milestone_count: number;
  acceptance: Acceptance;
}
export interface Behavior {
  id: string;
  behavior_key: string;
  version: number;
  statement: string;
  owner: string;
  supersedes: string | null;
}
export interface Obligation {
  id: string;
  label: string;
  resolved: boolean;
  note: string;
}
export interface Milestone {
  id: string;
  title: string;
  intent: string;
  scope: string[];
  dependencies: string[];
  dependency_reasons: Record<string, string>;
  dependency_types: Record<string, string>;
  behavior_revision_ids: string[];
  resources: string[];
  change_types: string[];
  obligations: Obligation[];
  status: string;
  pinned_baseline: string | null;
  lease_active: boolean;
  position: { x: number; y: number } | null;
}
export interface Evidence {
  id: string;
  milestone_id: string;
  behavior_revision_ids: string[];
  baseline_id: string;
  fingerprint: string;
  command: string[];
  result: string;
  output: string;
  duration: number;
  created_at: string;
}
export interface Baseline {
  id: string;
  number: number;
  commit: string;
  fingerprint: string;
  file_count: number;
  complete: boolean;
  created_at: string;
}
export interface Message {
  id: string;
  role: string;
  content: string;
  created_at: string;
}
export interface Proposal {
  target: string;
  summary: string;
  milestones: {
    id: string;
    title: string;
    intent: string;
    scope: string[];
    dependencies: string[];
    behaviors: { key: string; statement: string }[];
  }[];
}
export interface Project extends ProjectSummary {
  question: PendingQuestion | null;
  repository: string;
  revision: number;
  targets: {
    number: number;
    statement: string;
    required_behavior_ids: string[];
  }[];
  plans: { number: number; summary: string }[];
  baselines: Baseline[];
  milestones: Milestone[];
  behaviors: Behavior[];
  evidence: Evidence[];
  evidence_validity: Record<string, string>;
  verified_behaviors: string[];
  readiness: Record<
    string,
    {
      logical_ready: boolean;
      safe_to_execute: boolean;
      blockers: string[];
      conflicts: string[];
    }
  >;
  proposal: Proposal | null;
  messages: Message[];
  events: { id: string; kind: string; detail: string; created_at: string }[];
  metrics: Record<string, number>;
}
export interface PendingQuestion {
  id: string;
  prompt: string;
  context: string;
  options: string[];
}
export interface AgentEvent {
  type: string;
  project?: Project;
  project_id?: string;
  node_id?: string;
  node_ids?: string[];
  effect?: string;
  label?: string;
  message?: string;
  question?: PendingQuestion;
  changed?: boolean;
}
export interface ProviderDescriptor {
  id: string;
  name: string;
  description: string;
  fields: {
    key: string;
    label: string;
    placeholder: string;
    default: string;
    required: boolean;
  }[];
  secret_label: string;
}
export interface Settings {
  adapters: ProviderDescriptor[];
  provider: {
    adapter: string;
    config: Record<string, string>;
    has_key: boolean;
  } | null;
}
