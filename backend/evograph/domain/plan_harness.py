"""Immutable harness records and the central, versioned publication policy.

Plugins report findings; only this policy decides whether their executions permit
publication. Serialized input prevents one plugin from changing another's view.
"""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Verdict = Literal["pass", "block", "unknown", "not_applicable"]
ExecutionStatus = Literal[
    "completed", "prerequisite_skipped", "disabled_by_policy", "unavailable", "timeout",
    "budget_exhausted", "invalid_output", "error", "cancelled",
]
PluginKind = Literal["deterministic", "model_opinion"]
SNAPSHOT_SCHEMA = "plan-harness-snapshot/v1"
RESULT_SCHEMA = "plan-harness-result/v1"
RUN_SCHEMA = "plan-harness-run/v1"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def content_hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class HarnessSnapshot(FrozenRecord):
    schema_version: Literal["plan-harness-snapshot/v1"] = SNAPSHOT_SCHEMA
    snapshot_id: str
    candidate_id: str
    project_id: str
    candidate_hash: str
    before_hash: str
    before_revision: int
    expected_revision: int
    candidate_revision: int
    before_json: str
    candidate_json: str
    record_json: str

    def before_project(self):
        from .models import Project
        return Project.model_validate_json(self.before_json)

    def candidate_project(self):
        from .models import Project
        return Project.model_validate_json(self.candidate_json)

    def record_data(self):
        return json.loads(self.record_json)

    @model_validator(mode="after")
    def sealed_identity(self):
        for field in ("before_json", "candidate_json", "record_json"):
            value = getattr(self, field)
            if canonical_json(json.loads(value)) != value:
                raise ValueError("Harness input must use canonical sealed JSON")
        payload = self.model_dump(exclude={"snapshot_id"})
        if content_hash(payload) != self.snapshot_id:
            raise ValueError("Harness snapshot identity does not match its sealed input")
        return self


class PluginManifest(FrozenRecord):
    id: str
    version: str
    kind: PluginKind
    prerequisites: tuple[str, ...] = ()
    scope: str


class HarnessFinding(FrozenRecord):
    code: str
    subject: str
    message: str
    severity: Literal["error", "review"] = "error"
    basis: Literal[
        "deterministic", "model_inference", "source_statement", "existing_execution_record"
    ] = "deterministic"
    evidence_refs: tuple[str, ...] = ()


class PluginResult(FrozenRecord):
    schema_version: Literal["plan-harness-result/v1"] = RESULT_SCHEMA
    plugin_id: str
    plugin_version: str
    snapshot_id: str
    scope: str
    covered_subjects: tuple[str, ...]
    verdict: Verdict
    findings: tuple[HarnessFinding, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    applicability_reason: str = ""

    @model_validator(mode="after")
    def consistent_result(self):
        if len(self.covered_subjects) != len(set(self.covered_subjects)):
            raise ValueError("Harness check subjects must be unique")
        if self.verdict == "pass" and any(f.severity == "error" for f in self.findings):
            raise ValueError("A passing check cannot carry blocking findings")
        if self.verdict == "not_applicable" and not self.applicability_reason:
            raise ValueError("Not-applicable requires an explicit applicability reason")
        return self


class PluginExecution(FrozenRecord):
    plugin_id: str
    plugin_version: str
    kind: PluginKind
    status: ExecutionStatus
    result: PluginResult | None = None
    elapsed_seconds: float = Field(default=0.0, ge=0)
    request_refs: tuple[str, ...] = ()
    certificate_id: str = ""
    detail: str = ""

    @model_validator(mode="after")
    def completed_result(self):
        if (self.status == "completed") != (self.result is not None):
            raise ValueError("Only completed executions can carry a check result")
        if self.result and (self.result.plugin_id != self.plugin_id
                            or self.result.plugin_version != self.plugin_version):
            raise ValueError("Execution/result plugin identity differs")
        return self


# No plugin can self-declare optionality or change this required set. An internal
# policy revision can make the model advisory without relaxing deterministic safety.
DETERMINISTIC_REQUIRED = (
    "source_completeness", "graph_identity", "contract_source_history", "design_consistency",
    "declared_availability", "typed_capability_flow",
)


class HarnessPolicy(FrozenRecord):
    version: str
    deterministic_required: tuple[str, ...]
    require_model_opinion: bool
    # N/A exceptions are centrally reviewed/versioned, never self-authorized.
    not_applicable_reasons: tuple[tuple[str, str], ...] = ()

    @property
    def required(self):
        return self.deterministic_required + (("semantic_review",) if self.require_model_opinion else ())


CURRENT_POLICY = HarnessPolicy(version="plan-harness-policy/v6",
                               deterministic_required=DETERMINISTIC_REQUIRED,
                               require_model_opinion=True,
                               not_applicable_reasons=(("typed_capability_flow",
                                                        "no_declared_typed_actions"),))
POLICY_HASH = content_hash(CURRENT_POLICY.model_dump(mode="json"))


def execution_satisfied(row, *, policy=CURRENT_POLICY):
    """One policy-owned rule for prerequisite admission and publication."""
    if row is None or row.status != "completed" or row.result is None:
        return False
    result = row.result
    if any(f.severity == "error" for f in result.findings):
        return False
    return result.verdict == "pass" or (
        result.verdict == "not_applicable"
        and (row.plugin_id, result.applicability_reason) in policy.not_applicable_reasons
    )


def policy_decision(executions, *, policy=CURRENT_POLICY):
    """Recompute apply/hold from findings and actual execution, never stored green UI."""
    if policy.deterministic_required != DETERMINISTIC_REQUIRED:
        return "hold"
    rows = {row.plugin_id: row for row in executions}
    if len(rows) != len(executions):
        return "hold"
    for plugin_id in policy.required:
        if not execution_satisfied(rows.get(plugin_id), policy=policy):
            return "hold"
    return "apply"


class HarnessRun(FrozenRecord):
    schema_version: Literal["plan-harness-run/v1"] = RUN_SCHEMA
    run_id: str
    snapshot_id: str
    candidate_id: str
    candidate_hash: str
    policy_version: str
    policy_hash: str
    registry_hash: str
    status: Literal["running", "completed", "cancelled"]
    executions: tuple[PluginExecution, ...] = ()
    decision: Literal["apply", "hold"] = "hold"
    semantic_review_json: str | None = None
    model_certificate_json: str | None = None

    def findings(self):
        """Compatibility projection; advisory findings retain their severity."""
        return [finding.model_dump(mode="json")
                for row in self.executions if row.result for finding in row.result.findings]
