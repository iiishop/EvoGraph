"""One explicit bounded intent, ordinary generation phases, no semantic repair loop."""
from contextlib import aclosing
from copy import deepcopy

from ..domain.models import Project
from ..infrastructure.plan_jobs import PlanningJobStore, digest, phase_metrics, spend_of
from .plan_recheck import recheck_offer, review_projection, stream_recheck
from .plan_units import all_units_complete, schedule_findings


def _clean_calls(record):
    calls = record.get("metrics", {}).get("calls", [])
    return bool(calls) and all(c.get("normal_stream_end") and c.get("termination_reason") == "stream_end"
                               and not c.get("error_type") for c in calls)


def boundary_kind(record, phase):
    if not record or record["status"] in {"applied", "discarded", "stale"}:
        return None
    current = review_projection(record)
    project = Project.model_validate(record["project"])
    if (project.question or not _clean_calls(current) or schedule_findings(record, project)
            or any(a.get("status") != "succeeded" for a in record.get("tool_attempts", [])
                   if a.get("phase_id") == phase["id"])):
        return None
    rejections = current.get("metrics", {}).get("admission_rejections", [])
    reasons = {r["reason"] for r in rejections}
    if reasons - {"call_limit", "total_input_limit"}:
        return None
    boundary = (record.get("generation_pause_reason") == "call_budget_reserved_for_review"
                or bool(reasons & {"call_limit", "total_input_limit"}))
    if not boundary:
        return None
    if all_units_complete(record):
        # A genuine deterministic harness receipt, never a fabricated previous
        # review, is the narrow admission basis for a first semantic review.
        run = current.get("harness_run", {})
        semantic = [r for r in run.get("executions", []) if r.get("kind") == "model"]
        if (phase["kind"] == "generation" and run.get("status") == "completed"
                and any(r.get("status") == "budget_exhausted" for r in run.get("executions", []))
                and not any(r.get("status") in {"error", "timeout", "invalid_output"} for r in semantic)):
            return "review"
        return None
    progress = record.get("generation_progress", {}).get("checkpoint_count", 0)
    first_manifest = phase["number"] == 1 and record.get("work_units")
    if progress <= phase["starting_checkpoints"] and not first_manifest:
        return None
    units = record.get("work_units", {}).get("units", [])
    complete = set(record.get("work_units", {}).get("completed_ids", []))
    if any(u["state"] == "pending" and not u.get("holds") and set(u["depends_on"]) <= complete for u in units):
        return "generation"
    return None


def stop_reason(record):
    if not record:
        return "phase_not_admitted"
    current = review_projection(record)
    if record["project"].get("question"):
        return "question"
    calls = current.get("metrics", {}).get("calls", [])
    if any(c.get("termination_reason") == "timeout" for c in calls):
        return "provider_timeout"
    if any(c.get("error_type") or not c.get("normal_stream_end") for c in calls):
        return "provider_incomplete_or_uncertain"
    rejections = current.get("metrics", {}).get("admission_rejections", [])
    if rejections:
        return rejections[-1]["reason"]
    if schedule_findings(record, Project.model_validate(record["project"])):
        return "held_or_invalid_manifest"
    if current.get("harness_run"):
        return "review_not_accepted"
    if any(a.get("status") == "failed" for a in record.get("tool_attempts", [])):
        return "invalid_tool_output"
    return "no_safe_progress"


class PlanningJobService:
    def __init__(self, app):
        self.app = app
        self.store = PlanningJobStore(app.db)

    def view(self, job):
        with self.app.db.connect() as c:
            job = self.store.read(c, job["id"])
            metrics = phase_metrics(c, job)
            spend = spend_of(metrics)
        phase = job["phases"][-1] if job["phases"] else None
        record = self.app.unified.store.get(phase["candidate_id"]) if phase and metrics[-1] else None
        schedule = (record or {}).get("work_units", {})
        units = schedule.get("units", [])
        completed = set(schedule.get("completed_ids", []))
        remaining = sum(u["state"] != "completed" for u in units)
        held = sum(bool(u.get("holds")) for u in units if u["state"] != "completed")
        runnable = sum(u["state"] == "pending" and not u.get("holds") and set(u["depends_on"]) <= completed for u in units)
        can_continue = job["status"] == "paused" and not job["cancelled"]
        budget_left = (len(job["phases"]) < job["limits"]["max_phases"]
                       and spend["calls"] < job["limits"]["max_calls"]
                       and spend["input_bytes"] < job["limits"]["max_input_bytes"])
        return {**{k: deepcopy(job[k]) for k in ("id", "created_at", "write_version", "status", "source_id", "stop_reason", "limits")},
                "phase_id": phase["id"] if phase else None, "phase_number": len(job["phases"]),
                "can_continue": can_continue and budget_left,
                "authorization_needed": bool(job["stop_reason"] and job["stop_reason"].startswith("aggregate_")) or can_continue and not budget_left,
                "continue_pins": self.store.pins(job) if can_continue and budget_left else None,
                "spend": spend, "phase_spend": spend_of(metrics[-1:]),
                "phase_limits": {"max_calls": (metrics[-1].get("budget", {}).get("max_calls",
                    1 if phase and phase["kind"] == "review" else 5) if metrics else 5),
                    "max_total_input_bytes": 393216},
                "progress": {"retained_checkpoints": job["retained_checkpoints"],
                    "new_checkpoints": max(0, (record or {}).get("generation_progress", {}).get("checkpoint_count", 0) - job["retained_checkpoints"]),
                    "completed_units": len(completed), "runnable_units": runnable, "held_units": held,
                    "remaining_units": remaining,
                    "remaining_call_lower_bound": (remaining + 1 if units and not held and job["status"] != "applied" else 0 if job["status"] == "applied" else None)}}

    def event(self, job):
        return {"type": "planning_job_changed", "project_id": job["project_id"], "job": self.view(job)}

    def cancel(self, project_id: str, job_id: str):
        return self.view(self.store.cancel(project_id, job_id))

    async def stream(self, project_id, content, request, snapshot_mode="full"):
        from .agent_errors import agent_error_event
        phase = None
        job = None
        try:
            if request.get("action") == "start":
                job, created = self.store.create(project_id, content, request)
                if not created:
                    yield self.event(job)
                    yield {"type": "done", "project_id": project_id, "changed": job["status"] == "applied",
                           "project": self.app.projects.get(project_id)}
                    return
                pins = self.store.pins(job)
            elif request.get("action") == "continue" and not content:
                job = self.store.get(request["job_id"])
                if job["project_id"] != project_id:
                    raise ValueError("任务不属于此项目")
                pins = request.get("pins")
                if any(p.get("admission_pins_hash") == digest(pins) for p in job["phases"]):
                    yield self.event(job)
                    yield {"type": "done", "project_id": project_id, "changed": job["status"] == "applied",
                           "project": self.app.projects.get(project_id)}
                    return
            else:
                raise ValueError("继续任务不能添加新用户要求；新反馈需要新任务")
            final = None
            while True:
                job, phase = self.store.open_phase(job["id"], pins)
                yield self.event(job)
                if not phase:
                    break
                context = {"job_id": job["id"], "source_id": job["source_id"],
                           "phase_id": phase["id"], "phase_number": phase["number"]}
                if phase["kind"] == "review":
                    before = self.app.db.get(project_id)
                    record = self.app.unified.store.get(phase["candidate_id"])
                    review_pins = recheck_offer(before, record)
                    if not review_pins:
                        raise ValueError("完整候选缺少可重放的评审准入凭据")
                    stream = stream_recheck(self.app.unified, project_id, review_pins, snapshot_mode,
                                            _job_phase=context)
                else:
                    stream = self.app.unified.stream(project_id, job["input"], snapshot_mode=snapshot_mode,
                                                     _job_phase=context)
                async with aclosing(stream) as events:
                    async for event in events:
                        if event["type"] == "done":
                            final = event
                        else:
                            yield event
                        if event["type"] == "candidate_changed":
                            yield self.event(job)
                job = self.store.close_phase(job["id"], phase["id"])
                phase = None
                yield self.event(job)
                if job["status"] != "paused":
                    break
                pins = self.store.pins(job)
            yield {**(final or {"type": "done", "project_id": project_id, "changed": False}),
                   "project": self.app.projects.get(project_id)}
        except (GeneratorExit, __import__("asyncio").CancelledError):
            if job:
                self.store.cancel(project_id, job["id"])
            raise
        except Exception as exc:
            if job and phase:
                job = self.store.close_phase(job["id"], phase["id"])
                phase = None
                yield self.event(job)
            yield agent_error_event(exc)
            yield {"type": "done", "project_id": project_id, "changed": False,
                   "project": self.app.projects.get(project_id)}
        finally:
            if job and phase:
                self.store.close_phase(job["id"], phase["id"])
