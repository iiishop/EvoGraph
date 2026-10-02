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
  acceptance_scope?: 'target' | 'milestone';
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
  source_behaviors?: { key: string; statement: string; source_refs: string[] }[];
  source_baseline_id?: string;
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
  composer_document?: ComposerDocument | null;
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
    behaviors: {
      key: string;
      statement: string;
      acceptance_scope?: 'target' | 'milestone';
    }[];
  }[];
}
export interface Attachment {
  id: string;
  name: string;
  media_type: string;
  size: number;
  excerpt: string;
}
export type ReferenceKind =
  | 'milestone'
  | 'source_milestone'
  | 'architecture_component'
  | 'source_component'
  | 'attachment'
  | 'repository';
export interface ComposerReferencePart {
  type: 'reference';
  kind: ReferenceKind;
  id: string;
  project_id: string;
  label: string;
}
export type ComposerPart = { type: 'text'; text: string } | ComposerReferencePart;
export interface ComposerDocument {
  version: 1;
  parts: ComposerPart[];
}
export interface ReferenceItem {
  id: string;
  kind: ReferenceKind;
  project_id: string;
  name: string;
  label: string;
  detail: string;
  path: string;
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
    source_ref_count?: number;
  }[];
  edges: { source: string; target: string; label: string }[];
  groups: {
    id: string;
    label: string;
    description: string;
    kind: 'client' | 'server' | 'data' | 'external' | 'shared' | 'domain';
    member_node_ids: string[];
  }[];
  milestone_ids: string[];
  attachment_ids: string[];
}
export interface Architecture {
  quality_scenarios?: { concern: string; scenario: string; measure: string; approach: string }[];
  risks?: string[];
  research_ids: string[];
  number: number;
  summary: string;
  technologies: { area: string; choice: string; rationale: string }[];
  decisions: string[];
  diagram: Diagram;
}
export interface ProjectEvent {
  id: string;
  kind: string;
  detail: string;
  created_at: string;
}
export interface TurnSummary {
  version: 1;
  turn_id: string;
  status: 'completed' | 'waiting' | 'stopped' | 'failed';
  changed: boolean;
  before_revision: number;
  after_revision: number;
  changes: {
    milestones: {
      added: { id: string; title: string; fields: string[] }[];
      updated: { id: string; title: string; fields: string[] }[];
      removed: { id: string; title: string; fields: string[] }[];
    };
    dependencies: {
      added: { source: string; target: string; reason: string; type: string }[];
      updated: {
        source: string;
        target: string;
        fields: string[];
        before: { reason: string; type: string };
        after: { reason: string; type: string };
      }[];
      removed: { source: string; target: string; reason: string; type: string }[];
    };
    target: {
      before_version: number | null;
      after_version: number | null;
      statement_changed: boolean;
      required_behavior_ids: { added: string[]; removed: string[] };
      required_behavior_changes: {
        behavior_key: string;
        before_id: string | null;
        after_id: string | null;
        fields: string[];
      }[];
    } | null;
    architecture: { before_revision: number | null; after_revision: number | null } | null;
    other: string[];
  };
}
export interface Project extends ProjectSummary {
  created_at: string;
  class_model_state: { current: boolean; reasons: string[] };
  uml_diagrams: UmlDiagram[];
  source_milestones: Milestone[];
  source_diagram: Diagram | null;
  source_summary: string;
  source_fingerprint: string;
  source_analysis_baseline_id: string;
  source_analysis_summary: string;
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
  events: ProjectEvent[];
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
  verification_milestone?: string | null;
  prompt: string;
  category: 'missing_design_input' | 'agent_blocked' | 'decision';
  context: string;
  options: string[];
}
export interface AgentEvent {
  cancelled?: boolean;
  turn_id?: string;
  summary?: TurnSummary;
  view?: string;
  diagram_id?: string;
  diagram_kind?: string;
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
export interface ClassDetailResult {
  status: 'ready' | 'empty' | 'unmapped' | 'too_large' | 'unsupported';
  message: string;
  diagram?: UmlDiagram;
  semantic?: SourceClassModel;
  image?: string;
  render_status?: 'ready' | 'unavailable';
  render_error?: string;
  files: string[];
  component_ids: string[];
  boundaries: string[];
  limitations: string[];
}
export interface UmlDiagram {
  component_ids?: string[];
  architecture_revision?: number;
  id: string;
  title: string;
  kind: 'class' | 'sequence' | 'activity' | 'state';
  source: string;
  scope: string;
  origin: 'source' | 'design' | 'mixed';
  design_elements: string[];
  design_fingerprint: string;
  source_refs: string[];
  milestone_ids: string[];
  revision: number;
  baseline_id: string;
}

/** Bounded syntax declarations from selected source files, never a parsed PlantUML document. */
export interface SourceLocation {
  path: string;
  line: number;
  end_line: number;
}
export interface SourceClassMember {
  id: string;
  name: string;
  kind: 'field' | 'method';
  text: string;
  visibility: 'public' | 'protected' | 'private' | 'unspecified';
  qualifiers: string[];
  location: SourceLocation;
}
export interface SourceClass {
  id: string;
  name: string;
  declaration?: string;
  kind: string;
  language: string;
  package_ids: string[];
  location: SourceLocation;
  members: SourceClassMember[];
}
export interface SourceBoundary {
  id: string;
  label: string;
  kind: 'import' | 'base' | 'architecture';
  origin: 'source' | 'design';
  reason?: string;
}
export interface SourceRelation {
  id: string;
  source: string;
  target: string;
  kind: 'extends' | 'implements' | 'import' | 'architecture';
  label: string;
  origin: 'source' | 'design';
  resolution?: 'selected' | 'boundary' | 'architecture';
  location?: SourceLocation;
}
export interface SourceClassModel {
  schema_version: 1;
  origin: 'source';
  project_id: string;
  architecture_revision: number;
  component_ids: string[];
  files: string[];
  baseline_id: string;
  source_fingerprints: Record<string, string>;
  classes: SourceClass[];
  packages: { id: string; component_id: string; label: string }[];
  boundaries: SourceBoundary[];
  relations: SourceRelation[];
  limitations: string[];
}
