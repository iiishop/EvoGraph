"""An explicit planning-only service facade; never swap the shared application's DB."""
import copy
import threading
import time

from ..domain.models import now
from ..domain.plan_contracts import PLANNING_FIELDS, planning_fingerprint
from ..domain.typed_capabilities import declared_consumption_keys, declared_typed_keys
from ..infrastructure.database import ConflictError
from .baseline_milestones import BaselineMilestoneService
from .design import DesignService
from .graph_editor import GraphEditor
from .projects import ProjectService
from .references import ReferenceService
from .research import ResearchService


class StagedDatabase:
    is_candidate = True

    def __init__(self, db, store, record, source_id):
        from ..domain.models import Project
        self.canonical, self.store, self.record, self.source_id = db, store, record, source_id
        self.project = Project.model_validate(record["project"])
        self.saved_events = []
        self.requirement_source_ids = set(record.get("allowed_requirement_source_ids", [source_id]))
        self.user_message_id = record.get("source_message_id")
        self.metrics_ref = None
        self.started = time.monotonic()

    def checkpoint_metrics(self, *, request=None, dispatch=False):
        record = copy.deepcopy(self.record)
        if self.metrics_ref is not None:
            record["metrics"] = copy.deepcopy({**self.metrics_ref, "elapsed_seconds": time.monotonic() - self.started})
        if request is not None:
            record.setdefault("model_inputs", []).append(request)
        self.record = self.store.save(record, dispatch=True) if dispatch else self.store.save(record)

    def get(self, project_id):
        if project_id != self.project.id:
            raise ValueError("候选不能访问其他项目")
        return self.project.model_copy(deep=True)

    def save(self, project, kind, detail="", *, compiler_audit=None):
        if project.id != self.project.id or project.revision != self.project.revision:
            raise ConflictError("候选版本已改变")
        ignored = {*PLANNING_FIELDS, "revision", "updated_at", "metrics", "question"}
        if project.model_dump(exclude=ignored) != self.project.model_dump(exclude=ignored):
            raise ValueError("此操作超出规划候选的可写范围")
        updated = project.model_copy(deep=True)
        updated.revision += 1
        updated.updated_at = now()
        if self.metrics_ref is not None:
            self.record["metrics"] = copy.deepcopy({**self.metrics_ref, "elapsed_seconds": time.monotonic() - self.started})
        record = {**self.record, "project": updated.model_dump(), "revision": updated.revision,
                  "typed_obligation_keys": sorted(set(self.record.get("typed_obligation_keys", []))
                                                   | declared_typed_keys(self.project)
                                                   | declared_typed_keys(updated)),
                  "consumption_obligation_keys": sorted(
                      {tuple(key) for key in self.record.get("consumption_obligation_keys", [])}
                      | declared_consumption_keys(self.project) | declared_consumption_keys(updated))}
        if compiler_audit is not None:
            record = copy.deepcopy(record)
            record.setdefault("compilations", []).append(compiler_audit)
            progress = record.get("generation_progress", {})
            record["generation_progress"] = {
                "state": "staged", "checkpoint_count": progress.get("checkpoint_count", 0) + 1,
                "last_revision": updated.revision, "last_summary": detail,
                "planning_fingerprint": planning_fingerprint(updated),
            }
            record["validation_requested"] = False
        if getattr(self, "segmented_planning", False):
            from .plan_units import advance_unit_checkpoint
            record = advance_unit_checkpoint(record, self.project, updated, compiler_audit)
        # No in-memory revision advance before the durable checkpoint succeeds.
        record = self.store.save(record)
        self.project, self.record = updated, record
        project.revision, project.updated_at = updated.revision, updated.updated_at
        self.saved_events.append({"kind": kind, "detail": detail})
        return project

    def record_compilation_noop(self, audit):
        record = copy.deepcopy(self.record)
        record.setdefault("compilations", []).append(audit)
        if getattr(self, "segmented_planning", False):
            from .plan_units import advance_unit_checkpoint
            record = advance_unit_checkpoint(record, self.project, self.project, audit)
        self.record = self.store.save(record)

    def start_tool_attempt(self, name, arguments):
        from .plan_batch_policy import PINNED_CONTINUATIONS
        if self.record.get("planning_experiment", {}).get("version") in PINNED_CONTINUATIONS:
            call = (self.metrics_ref or {}).get("provider_calls", 0)
            if (name not in {"submit_plan_delta", "ask_user"}
                    or any(attempt.get("generation_call") == call
                           for attempt in self.record.get("tool_attempts", []))):
                if self.metrics_ref is not None:
                    self.metrics_ref["experiment_stopped"] = "experiment_request_sequence"
                raise ValueError("pinned continuation permits one assigned-unit attempt per request; no router or retries")
        if getattr(self, "segmented_planning", False) and name == "submit_plan_delta":
            self.record["validation_requested"] = False
            self.record["generation_progress"] = {**self.record.get("generation_progress", {}), "state": "staged"}
        identity = len(self.record.get("tool_attempts", [])) + 1
        self.record.setdefault("tool_attempts", []).append({
            "id": identity, "name": name, "raw_arguments": arguments,
            "status": "started", "source_id": self.source_id,
            "generation_call": (self.metrics_ref or {}).get("provider_calls", 0),
            **({"phase_id": self.metrics_ref["job_phase_id"]} if (self.metrics_ref or {}).get("job_phase_id") else {}),
        })
        for source in self.project.plan_contract.sources:
            if source.id == self.source_id:
                source.activity.append({"id": identity, "name": name, "status": "started"})
        self.record["project"] = self.project.model_dump()
        self.record = self.store.save(self.record)
        return identity

    def finish_tool_attempt(self, identity, status, payload):
        for attempt in self.record.get("tool_attempts", []):
            if attempt["id"] == identity:
                attempt.update(status=status, payload=payload)
        for source in self.project.plan_contract.sources:
            if source.id == self.source_id:
                for activity in source.activity:
                    if activity["id"] == identity:
                        activity["status"] = status
        self.record["project"] = self.project.model_dump()
        self.record = self.store.save(self.record)

    def message(self, project_id, role, content, composer_document=None):
        self.get(project_id)
        result = self.canonical.message(project_id, role, content, composer_document)
        if role == "user":
            self.user_message_id = result["id"]
            for source in self.project.plan_contract.sources:
                if source.id == self.source_id:
                    source.message_id = result["id"]
            self.record["project"] = self.project.model_dump()
            self.record = self.store.save(self.record)
        return result

    def record_tool(self, name, arguments, result):
        entry = {"name": name, "arguments": arguments, "result": result}
        self.record.setdefault("tool_calls", []).append(entry)
        if name in {"read_repository_file", "read_reference", "web_search", "web_fetch", "inspect_repository"}:
            for source in self.project.plan_contract.sources:
                if source.id == self.source_id:
                    source.evidence.append(entry)
            self.record["project"] = self.project.model_dump()
        self.record = self.store.save(self.record)

    def messages(self, project_id):
        self.get(project_id)
        return self.canonical.messages(project_id)

    def events(self, project_id):
        self.get(project_id)
        return self.canonical.events(project_id)

    def setting(self, key, default=None):
        return self.canonical.setting(key, default)

    def turn_result_detail(self, project_id, turn_id):
        return self.canonical.turn_result_detail(project_id, turn_id)


def staged_application(application, db, metrics):
    """Only services in the allowlisted planner are exposed with write capabilities."""
    from .agent_runtime import AgentRuntime
    facade = copy.copy(application)
    facade.db = db
    facade.projects = ProjectService(db)
    facade.references = ReferenceService(db)
    facade.graph = GraphEditor(db)
    facade.design = DesignService(db)
    # Only read_attachment and context are exposed; uploads are not in this tool set.
    facade.attachments = application.attachments
    facade.baseline_milestones = BaselineMilestoneService(db)
    facade.research = ResearchService(db, application.settings.secrets)
    from .plan_budget import BudgetedSettings
    db.metrics_ref = metrics
    facade.settings = BudgetedSettings(application.settings, metrics, db.checkpoint_metrics)
    facade._locks, facade._lock_guard = {}, threading.Lock()
    facade.agent = AgentRuntime(facade)
    return facade
