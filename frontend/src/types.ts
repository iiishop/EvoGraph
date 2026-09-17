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
  migration_steps: { component_id: string; instruction: string; from_revision: number }[];
  origin: 'plan' | 'source';
  source_refs: string[];
  id: string;
  title: string;
  intent: string;
  architecture_components: string[];
  attachment_ids: string[];
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
export interface Attachment {
  id: string;
  name: string;
  media_type: string;
  size: number;
  excerpt: string;
}
export interface Diagram {
  id: string;
  title: string;
  kind: string;
  nodes: {
    id: string;
    label: string;
    description: string;
    role?: string;
    source_refs?: string[];
  }[];
  edges: { source: string; target: string; label: string }[];
  milestone_ids: string[];
  attachment_ids: string[];
}
export interface Architecture {
  research_ids: string[];
  number: number;
  summary: string;
  technologies: { area: string; choice: string; rationale: string }[];
  decisions: string[];
  diagram: Diagram;
}
export interface Project extends ProjectSummary {
  uml_diagrams: UmlDiagram[];
  source_milestones: Milestone[];
  source_diagram: Diagram | null;
  source_summary: string;
  source_fingerprint: string;
  light_checks: LightCheck[];
  research: { id: string; title: string; url: string; excerpt: string; created_at: string }[];
  attachments: Attachment[];
  diagrams: Diagram[];
  architectures: Architecture[];
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
export interface LightCheck {
  id: string;
  milestone_id: string;
  fingerprint: string;
  kind: string;
  rationale: string;
  result: string;
  output: string;
  command: string[];
  duration: number;
  created_at: string;
}
export interface PendingQuestion {
  id: string;
  prompt: string;
  category: 'missing_design_input' | 'agent_blocked' | 'decision';
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
export interface UmlDiagram {
  id: string;
  title: string;
  kind: 'class' | 'sequence' | 'activity' | 'state';
  source: string;
  scope: string;
  origin: 'source' | 'design';
  source_refs: string[];
  milestone_ids: string[];
  revision: number;
  baseline_id: string;
}
