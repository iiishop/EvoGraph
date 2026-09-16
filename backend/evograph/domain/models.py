from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


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
    command: list[str]
    result: Literal["PASS", "FAIL", "ERROR"]
    output: str
    duration: float
    created_at: str = Field(default_factory=now)


class Baseline(Model):
    id: str = Field(default_factory=uid)
    number: int
    commit: str
    fingerprint: str
    file_count: int
    complete: bool
    created_at: str = Field(default_factory=now)


Status = Literal["PLANNED", "IN_PROGRESS", "REVALIDATION_REQUIRED", "VERIFIED_COMPLETE"]


class Milestone(Model):
    id: str
    title: str
    intent: str
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
    options: list[str] = Field(default_factory=list)
    context: str = ""
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
