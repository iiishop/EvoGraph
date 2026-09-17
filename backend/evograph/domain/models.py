from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def uid() -> str:
    return uuid4().hex[:16]


def now() -> str:
    return datetime.now(UTC).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Obligation(Model):
    id: str
    label: str
    resolved: bool = False
    note: str = ""
    fingerprint: str = ""
    investigator: str = "human"


class BehaviorRevision(Model):
    id: str = Field(default_factory=uid)
    behavior_key: str
    version: int
    statement: str
    owner: str
    supersedes: str | None = None


class Evidence(Model):
    id: str = Field(default_factory=uid)
    milestone_id: str
    behavior_revision_ids: list[str]
    baseline_id: str
    fingerprint: str
    architecture_revision: int = 0
    command: list[str]
    result: Literal["PASS", "FAIL", "ERROR"]
    output: str
    duration: float
    provider: str = "local"
    request_id: str = ""
    created_at: str = Field(default_factory=now)


class Baseline(Model):
    id: str = Field(default_factory=uid)
    number: int
    commit: str
    fingerprint: str
    file_count: int
    complete: bool
    created_at: str = Field(default_factory=now)


Status = Literal[
    "PLANNED", "IN_PROGRESS", "AWAITING_ACCEPTANCE", "REVALIDATION_REQUIRED", "VERIFIED_COMPLETE"
]


class MigrationStep(Model):
    component_id: str
    milestone_id: str
    instruction: str = Field(min_length=1, max_length=2000)
    from_revision: int = 0


class Milestone(Model):
    id: str
    title: str
    intent: str
    architecture_components: list[str] = Field(default_factory=list)
    architecture_revision: int = 0
    attachment_ids: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    dependency_reasons: dict[str, str] = Field(default_factory=dict)
    dependency_types: dict[str, Literal["implementation", "migration", "verification"]] = Field(
        default_factory=dict
    )
    behavior_revision_ids: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    change_types: list[str] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    status: Status = "PLANNED"
    pinned_baseline: str | None = None
    lease_active: bool = False
    position: dict[str, float] | None = None
    origin: Literal["plan", "source"] = "plan"
    source_refs: list[str] = Field(default_factory=list)
    migration_steps: list[MigrationStep] = Field(default_factory=list)


class ProposedBehavior(Model):
    key: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=1000)


class ProposedMilestone(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    title: str = Field(min_length=1, max_length=120)
    intent: str = Field(min_length=1, max_length=1500)
    scope: list[str] = Field(min_length=1, max_length=30)
    dependencies: list[str] = Field(default_factory=list, max_length=24)
    dependency_reasons: dict[str, str] = Field(default_factory=dict)
    architecture_components: list[str] = Field(default_factory=list, max_length=30)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    behaviors: list[ProposedBehavior] = Field(min_length=1, max_length=12)
    resources: list[str] = Field(default_factory=list, max_length=30)
    change_types: list[str] = Field(default_factory=list)


class PlanProposal(Model):
    target: str = Field(min_length=1, max_length=4000)
    summary: str = Field(min_length=1, max_length=3000)
    milestones: list[ProposedMilestone] = Field(min_length=1, max_length=24)


class TargetVersion(Model):
    number: int
    statement: str
    required_behavior_ids: list[str]
    created_at: str = Field(default_factory=now)


class PlanningRevision(Model):
    number: int
    target_version: int
    summary: str
    milestone_ids: list[str]
    created_at: str = Field(default_factory=now)


class PendingQuestion(Model):
    id: str = Field(default_factory=uid)
    prompt: str
    category: Literal["missing_design_input", "agent_blocked", "decision"]
    options: list[str] = Field(default_factory=list)
    context: str = ""
    verification_milestone: str | None = None
    created_at: str = Field(default_factory=now)

    @model_validator(mode="before")
    @classmethod
    def _fill_legacy_category(cls, data: Any) -> Any:
        """Questions persisted before `category` existed infer one from their options.

        Without this, one stored question without the field makes the whole project
        list unreadable and the app reports a connection failure on startup.
        """
        if isinstance(data, dict) and not data.get("category"):
            inferred = "decision" if data.get("options") else "missing_design_input"
            return {**data, "category": inferred}
        return data


class Attachment(Model):
    id: str = Field(default_factory=uid)
    name: str
    media_type: str
    size: int
    excerpt: str = ""
    created_at: str = Field(default_factory=now)


class DiagramNode(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1500)
    role: Literal["frontend", "backend", "database", "security", "cloud", "message", "external"] = (
        "backend"
    )
    source_refs: list[str] = Field(default_factory=list, max_length=40)


class DiagramEdge(Model):
    source: str
    target: str
    label: str = Field(min_length=1, max_length=120)


class Diagram(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["architecture", "state", "workflow", "ui"] = "architecture"
    nodes: list[DiagramNode] = Field(min_length=1, max_length=40)
    edges: list[DiagramEdge] = Field(default_factory=list, max_length=100)
    milestone_ids: list[str] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)


class Technology(Model):
    area: str = Field(min_length=1, max_length=100)
    choice: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=1500)


class ArchitectureSpec(Model):
    summary: str = Field(min_length=1, max_length=4000)
    technologies: list[Technology] = Field(min_length=1, max_length=30)
    decisions: list[str] = Field(default_factory=list, max_length=30)
    diagram: Diagram
    research_ids: list[str] = Field(default_factory=list, max_length=30)
    retirements: list[MigrationStep] = Field(default_factory=list)


class AcceptanceRequest(Model):
    id: str = Field(default_factory=uid)
    milestone_id: str
    baseline_id: str
    fingerprint: str
    behavior_revision_ids: list[str]
    architecture_revision: int
    consumed: bool = False


class AcceptanceCheck(Model):
    behavior_id: str
    result: Literal["PASS", "FAIL", "ERROR"]
    method: str = Field(min_length=1, max_length=4000)
    evidence: str = Field(min_length=1, max_length=12000)


class AcceptanceReport(Model):
    request_id: str
    provider: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=12000)
    checks: list[AcceptanceCheck] = Field(min_length=1)


class UmlDiagram(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["class", "sequence", "activity", "state"]
    source: str = Field(min_length=1, max_length=100000)
    scope: str = Field(min_length=1, max_length=1000)
    origin: Literal["source", "design"] = "design"
    source_refs: list[str] = Field(default_factory=list)
    milestone_ids: list[str] = Field(default_factory=list)
    revision: int = 0
    baseline_id: str = ""


class LightCheck(Model):
    id: str = Field(default_factory=uid)
    milestone_id: str
    baseline_id: str
    fingerprint: str
    kind: str
    rationale: str
    result: Literal["PASS", "FAIL", "ERROR", "REVIEW"]
    output: str
    command: list[str] = Field(default_factory=list)
    duration: float = 0
    created_at: str = Field(default_factory=now)


class ResearchSource(Model):
    id: str = Field(default_factory=uid)
    title: str
    url: str
    excerpt: str
    query: str
    created_at: str = Field(default_factory=now)


class ArchitectureRevision(ArchitectureSpec):
    number: int
    created_at: str = Field(default_factory=now)


class Project(Model):
    id: str = Field(default_factory=uid)
    name: str
    description: str = ""
    repository: str = ""
    is_demo: bool = False
    archived: bool = False
    creation_key: str = ""
    target_draft: str | None = None
    revision: int = 0
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
    targets: list[TargetVersion] = Field(default_factory=list)
    plans: list[PlanningRevision] = Field(default_factory=list)
    baselines: list[Baseline] = Field(default_factory=list)
    milestones: list[Milestone] = Field(default_factory=list)
    behaviors: list[BehaviorRevision] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    proposal: PlanProposal | None = None
    proposal_revision: int | None = None
    question: PendingQuestion | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    diagrams: list[Diagram] = Field(default_factory=list)
    architectures: list[ArchitectureRevision] = Field(default_factory=list)
    source_milestones: list[Milestone] = Field(default_factory=list)
    source_diagram: Diagram | None = None
    source_summary: str = ""
    source_fingerprint: str = ""
    light_checks: list[LightCheck] = Field(default_factory=list)
    research: list[ResearchSource] = Field(default_factory=list)
    acceptance_requests: list[AcceptanceRequest] = Field(default_factory=list)
    uml_diagrams: list[UmlDiagram] = Field(default_factory=list)
    metrics: dict[str, float] = Field(
        default_factory=lambda: {
            "planning_seconds": 0,
            "verification_seconds": 0,
            "model_tokens": 0,
            "blocked_attempts": 0,
        }
    )

    @property
    def baseline(self) -> Baseline | None:
        return self.baselines[-1] if self.baselines else None

    def milestone(self, milestone_id: str) -> Milestone:
        return next((m for m in self.milestones if m.id == milestone_id), None) or _missing()


def _missing():
    raise ValueError("里程碑不存在")
