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
export interface ArchivedProjectSummary extends ProjectSummary {
  repository: string;
  updated_at: string;
}
// Write responses are persisted aggregates, not hydrated workspace snapshots.
// Only metadata used to confirm a save is projected here; messages, acceptance,
// evidence validity and other computed view fields must come from projects.get.
export interface ProjectRecord {
  id: string;
  name: string;
  description: string;
  repository: string;
  revision: number;
  created_at: string;
  archived: boolean;
}
export interface ProjectCreationOutcome {
  outcome: 'created' | 'reused_request' | 'existing_repository';
  project: ProjectRecord;
}
export type ProjectOpenResult = 'accepted' | 'failed' | 'superseded';
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
  project_id?: string;
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
export interface TurnContractSide {
  active_id: string | null;
  required_id: string | null;
}
export interface TurnContractChange {
  behavior_key: string | null;
  before: TurnContractSide;
  after: TurnContractSide;
  fields: string[];
  restored: boolean;
}
export interface TurnContractDetails {
  project_id: string;
  project_created_at: string;
  before_target_version: number | null;
  after_target_version: number | null;
  behaviors: TurnContractChange[];
}
export interface TurnSummary {
  version: 1;
  turn_id: string;
  status: 'completed' | 'waiting' | 'stopped' | 'failed';
  history_warning?: string;
  candidate_outcome?: {
    id: string | null;
    status: PlanCandidate['status'] | 'not_admitted';
    canonical_unchanged: true;
    note: string;
  };
  contract_details?: TurnContractDetails;
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
export interface PlanRequirement {
  id: string;
  quote: string;
  source_id: string;
  origin: 'user' | 'legacy';
  active: boolean;
  kind: 'outcome' | 'constraint' | 'exclusion';
  retired_by?: { source_id: string; quote: string; reason: string } | null;
}
export interface PlanBinding {
  behavior_key: string;
  behavior_revision_id: string;
  requirement_ids: string[];
  component_ids: string[];
  mechanism: string;
  requires_behavior_keys: string[];
  provides?: { key: string; kind: 'command' | 'query'; action: string }[];
  steps?:
    | (
        | { kind: 'invoke_command' | 'invoke_query'; capability_key: string; quote: string }
        | { kind: 'inspect'; requirement_id: string; quote: string }
      )[]
    | null;
}
export interface PlanProcessConstraint {
  id: string;
  source_id: string;
  quote: string;
  rule: 'planning_only' | 'no_external_research' | 'other';
}
export interface DeliveryBrief {
  milestone_id: string;
  project_revision: number;
  repository: string;
  basis: 'saved_project_snapshot';
  baseline: {
    latest: Baseline | null;
    pinned: Baseline | null;
    pinned_id: string | null;
    changed_since_pin: boolean;
    label: string;
  };
  outcome: Pick<
    Milestone,
    'title' | 'intent' | 'scope' | 'resources' | 'change_types' | 'migration_steps'
  >;
  readiness: Project['readiness'][string];
  prerequisites: {
    id: string;
    title: string;
    origin: string;
    direct: boolean;
    via: { dependent_id: string; reason: string; kind: string }[];
    state: string;
    label: string;
    evidence_ids: string[];
    source_refs: string[];
    source_behaviors: NonNullable<Milestone['source_behaviors']>;
    source_baseline_id: string;
  }[];
  contracts: {
    behavior: Behavior;
    binding: PlanBinding | null;
    relation: 'own' | 'prerequisite' | 'unavailable';
    state: string;
    label: string;
    evidence_ids: string[];
  }[];
  requirements: PlanRequirement[];
  sources: { id: string; text: string; origin: string; message_id: string | null }[];
  global_requirement_ids: string[];
  warnings: string[];
}
export interface PlanContract {
  sources?: { id: string; text: string; origin: 'user' | 'legacy' }[];
  process_constraints?: PlanProcessConstraint[];
  requirements: PlanRequirement[];
  bindings: PlanBinding[];
}
export interface PlanHarnessDisclosure {
  schema_version: 'planning-harness-disclosure/v1';
  snapshot_id: string;
  policy_version: string;
  decision: 'apply' | 'hold';
  checks: {
    id: string;
    kind: 'deterministic' | 'model_opinion';
    version: string;
    execution: string;
    verdict: 'pass' | 'block' | 'unknown' | 'not_applicable' | null;
    findings: number;
    message: string;
  }[];
}
// Read-only fields used to bind detailed findings to their saved disclosure.
export interface PlanHarnessRun {
  schema_version: 'plan-harness-run/v1';
  candidate_id: string;
  candidate_hash: string;
  snapshot_id: string;
  policy_version: string;
  executions: {
    plugin_id: string;
    plugin_version: string;
    kind: PlanHarnessDisclosure['checks'][number]['kind'];
    status: string;
    result?: {
      schema_version: 'plan-harness-result/v1';
      plugin_id: string;
      plugin_version: string;
      snapshot_id: string;
      verdict: PlanHarnessDisclosure['checks'][number]['verdict'];
      findings: { severity?: 'error' | 'review' }[];
    } | null;
  }[];
}
export interface PlanValidationReceipt {
  schema_version: 'planning-validation/v1';
  candidate_hash: string;
  structural: { status: 'not_run' | 'clear' | 'issues'; finding_count: number | null };
  model: {
    kind: 'model_opinion';
    status: 'not_run' | 'unavailable' | 'no_issue_found' | 'issues';
    subject_count: number;
  };
  implementation: {
    scope: 'current_planning_turn';
    status: 'not_run';
    existing_record_count: number;
  };
  harness?: PlanHarnessDisclosure;
}
export interface PlanSemanticIssue {
  id: string;
  subjects: string[];
  verdict: 'contradicted' | 'unknown';
  reason: string;
  counterexample: string;
}
export interface PlanSemanticObservation {
  id: string;
  subjects: string[];
  kind: 'editorial' | 'implementation_latitude';
  reason: string;
  basis: 'model_inference' | 'source_statement' | 'existing_execution_record';
  execution_evidence_ids: string[];
  evidence_refs: string[];
}
export interface ReviewRecheck {
  version: string;
  candidate_id: string;
  candidate_revision: number;
  candidate_hash: string;
  record_hash: string;
  base_revision: number;
  base_hash: string;
  checker_version: string;
  policy_version: string;
}
export interface RepairFrom extends ReviewRecheck {
  candidate_fingerprint: string;
  base_fingerprint: string;
  schedule_hash: string;
  compiler_lineage_hash: string;
}
export interface PlanCandidate {
  repair_from?: RepairFrom | null;
  review_recheck?: ReviewRecheck | null;
  review_attempt?: {
    id: string;
    started_at: string;
    closed_at: string | null;
    status: string;
    write_version: number;
  };
  review_attempt_history?: {
    id: string;
    started_at: string;
    closed_at: string | null;
    status: string;
  }[];
  id: string;
  generation_progress?: {
    state: 'staged' | 'ready';
    checkpoint_count: number;
    last_revision?: number;
  };
  created_at?: string;
  base_revision: number;
  status:
    | 'generating'
    | 'reviewing'
    | 'needs_resolution'
    | 'ready'
    | 'applied'
    | 'stopped'
    | 'failed'
    | 'stale'
    | 'discarded';
  revision: number;
  candidate_hash: string;
  validation_receipt?: PlanValidationReceipt;
  harness_run?: PlanHarnessRun;
  current_harness_policy_version?: string;
  applied_revision?: number;
  turn_summary?: TurnSummary;
  inherited_semantic_findings?: {
    source_candidate_id: string;
    candidate_hash: string;
    applicability: 'historical_needs_recheck';
    issues: PlanSemanticIssue[];
  } | null;
  report: {
    findings: { code: string; subject: string; message: string; severity?: 'error' | 'review' }[];
    semantic_batch?: {
      candidate_hash: string;
      issues: PlanSemanticIssue[];
      observations?: PlanSemanticObservation[];
    };
    semantic?: {
      candidate_hash?: string;
      checks: {
        subject: string;
        verdict: 'supported' | 'contradicted' | 'unknown';
        basis?: 'model_inference' | 'source_statement' | 'existing_execution_record';
        execution_evidence_ids?: string[];
        reason: string;
        counterexample: string;
      }[];
      summary: string;
    };
  };
  project: Omit<Project, 'plan_candidate' | 'messages' | 'events'> & {
    messages?: Message[];
    events?: ProjectEvent[];
    snapshot_mode?: 'compact-v1';
  };
  input: string;
  metrics: {
    provider_calls: number;
    tokens: number;
    elapsed_seconds: number;
    usage_reported?: boolean;
  };
}
export interface PlanningJobLimits {
  max_phases: number;
  max_calls: number;
  max_input_bytes: number;
}
export type PlanningJobPins = Record<string, unknown>;
export type PlanningJobRequest =
  | { action: 'start'; job_id: string; limits: PlanningJobLimits; pins: PlanningJobPins }
  | { action: 'continue'; job_id: string; pins: PlanningJobPins };
export interface PlanningJobView {
  id: string;
  write_version: number;
  created_at?: string;
  status: 'authorized' | 'running' | 'paused' | 'stopped' | 'applied' | 'cancelled';
  source_id: string;
  phase_id: string;
  phase_number: number;
  stop_reason: string | null;
  can_continue: boolean;
  authorization_needed: boolean;
  continue_pins: PlanningJobPins | null;
  limits: PlanningJobLimits;
  spend: { calls: number; input_bytes: number; tokens: number; usage_complete: boolean };
  phase_spend: { calls: number; input_bytes: number };
  phase_limits: { max_calls: number; max_total_input_bytes: number };
  progress: {
    retained_checkpoints: number;
    new_checkpoints: number;
    completed_units: number;
    runnable_units: number;
    held_units: number;
    remaining_units: number;
    remaining_call_lower_bound: number | null;
  };
}
export interface Project extends ProjectSummary {
  unified_planning?: boolean;
  planning_job?: PlanningJobView | null;
  planning_job_start_pins?: PlanningJobPins | null;
  plan_contract?: PlanContract;
  plan_candidate?: PlanCandidate | null;
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
// Only explicitly negotiated stream frames may omit history. Commands and
// admission/terminal frames continue to carry complete project views.
export type CompactProjectSnapshot = Omit<Project, 'messages' | 'events'> & {
  snapshot_mode: 'compact-v1';
};
export type ProjectSnapshot = Project | CompactProjectSnapshot;
export interface AgentEvent {
  job?: PlanningJobView;
  source_analysis?: boolean;
  candidate?: PlanCandidate;
  snapshot_mode?: 'full' | 'compact-v1';
  saved_message?: Message;
  cancelled?: boolean;
  turn_id?: string;
  summary?: TurnSummary;
  view?: string;
  diagram_id?: string;
  diagram_kind?: string;
  type: string;
  project?: ProjectSnapshot;
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
