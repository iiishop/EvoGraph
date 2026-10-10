"""One explicit review of an unchanged complete candidate; never a generation retry.

Original generation/review evidence stays in place. Each review-only attempt owns
only its review audit and references the same plan, sources and compiler lineage.
"""
from copy import deepcopy

from ..domain.models import Project, now
from ..domain.plan_contracts import candidate_hash, planning_fingerprint
from ..domain.plan_harness import CURRENT_POLICY
from .plan_harness import seal_snapshot
from .plan_review import CHECKER_VERSION, SCOPED_BATCH_VERSION
from .plan_units import _hash, all_units_complete, schedule_findings

RECHECK_VERSION = "candidate-review-only/v1"
REVIEW_FIELDS = frozenset({
    "metrics", "model_inputs", "reviews", "batch_reviews", "review_inputs", "report",
    "harness_run", "harness_runs", "harness_snapshots", "validation_receipt", "harness_commit_replay",
    "turn_summary", "harness_snapshot_refs", "review_protocol",
})
REVIEW_BUDGET = {"max_calls": 1, "max_request_bytes": 163840,
                "max_review_request_bytes": 163840, "max_total_input_bytes": 393216,
                "max_output_bytes": 98304, "call_timeout_seconds": 180}


def review_projection(record):
    """Transient view only; never persist a second copy of the candidate plan."""
    result = deepcopy(record)
    attempts = result.pop("review_attempts", [])
    if attempts:
        for key in REVIEW_FIELDS:
            result.pop(key, None)
        result.update(deepcopy(attempts[-1]["audit"]))
        catalog = snapshot_catalog(record)
        result["harness_snapshots"] = [catalog[key] for key in result.get("harness_snapshot_refs", [])] + result.get("harness_snapshots", [])
        result["review_attempt"] = {k: deepcopy(v) for k, v in attempts[-1].items() if k != "audit"}
        result["review_attempt_history"] = [
            {k: deepcopy(a[k]) for k in ("id", "started_at", "closed_at", "status")}
            for a in attempts[:-1]]
    return result


def snapshot_catalog(record):
    return {snapshot["snapshot_id"]: deepcopy(snapshot)
            for audit in [record, *[a["audit"] for a in record.get("review_attempts", [])]]
            for snapshot in audit.get("harness_snapshots", [])}


def review_audit(record, durable, *, replacing_latest=True):
    audit = {k: deepcopy(record[k]) for k in REVIEW_FIELDS if k in record}
    # The latest audit is being replaced. A snapshot stored only there must
    # stay inline; it cannot become a reference to its own removed copy.
    prior = durable.get("review_attempts", [])
    if (replacing_latest and prior
            and ("review_protocol" in record, record.get("review_protocol")) != (
                "review_protocol" in prior[-1]["audit"], prior[-1]["audit"].get("review_protocol"))):
        raise ValueError("评审协议不能在已开始的轮次中改变")
    known = snapshot_catalog({**durable, "review_attempts": prior[:-1] if replacing_latest else prior})
    snapshots = audit.pop("harness_snapshots", [])
    audit["harness_snapshot_refs"] = list(dict.fromkeys(
        s["snapshot_id"] for s in snapshots if known.get(s["snapshot_id"]) == s))
    fresh = [s for s in snapshots if known.get(s["snapshot_id"]) != s]
    if fresh:
        audit["harness_snapshots"] = fresh
    return audit


def recheck_pins(before, record):
    return {"version": RECHECK_VERSION, "candidate_id": record["id"],
            "candidate_revision": record["revision"], "candidate_hash": record["candidate_hash"],
            "record_hash": _hash(record), "base_revision": before.revision,
            "base_hash": candidate_hash(before), "checker_version": CHECKER_VERSION,
            "policy_version": CURRENT_POLICY.version}


def validate_recheck(before, record, pins):
    """Authoritative admission: callers cannot reset or reuse a spent stage budget."""
    if not record or pins != recheck_pins(before, record):
        raise ValueError("候选或评审版本已变化，请刷新后重新评审")
    candidate = Project.model_validate(record["project"])
    current = review_projection(record)
    progress = record.get("generation_progress", {})
    run = current.get("harness_run", {})
    snapshot_record = current
    # Closing the native stream before the first plugin checkpoint spends no
    # call and creates no new review evidence. The unchanged prior sealed run
    # remains a completeness/admission basis, never a new semantic certificate.
    if (not run and record.get("review_attempts") and record["review_attempts"][-1]["closed_at"]
            and current.get("metrics", {}).get("provider_calls") == 0):
        audits = [record, *[a["audit"] for a in record["review_attempts"][:-1]]]
        basis = next((a for a in reversed(audits) if a.get("harness_run")), {})
        run = basis.get("harness_run", {})
        # An unstarted v3 attempt cannot relabel the earlier run's v2 scope.
        snapshot_record = {k: v for k, v in current.items() if k != "review_protocol"}
        if "review_protocol" in basis:
            snapshot_record["review_protocol"] = basis["review_protocol"]
    if (before.archived or not before.unified_planning or before.question or candidate.question
            or record["project_id"] != before.id or candidate.id != before.id
            or record["base_revision"] != before.revision
            or record["revision"] != candidate.revision
            or record["candidate_hash"] != candidate_hash(candidate)
            or record["status"] not in {"needs_resolution", "failed", "stopped"}
            or not record.get("validation_requested") or progress.get("state") != "ready"
            or progress.get("ready_revision") != candidate.revision
            or progress.get("ready_planning_fingerprint") != planning_fingerprint(candidate)
            or (record.get("work_units") is not None and not all_units_complete(record))
            or schedule_findings(record, candidate)
            or run.get("status") not in {"completed", "cancelled"}
            or run.get("decision") == "apply"
            or run.get("snapshot_id") != seal_snapshot(before, candidate, snapshot_record).snapshot_id
            or any(c.get("status") in {"admitted", "streaming"}
                   for c in current.get("metrics", {}).get("calls", []))
            or (record.get("review_attempts") and not record["review_attempts"][-1]["closed_at"])):
        raise ValueError("仅能重新评审已结束且完整、未改变的待解决候选")
    return candidate


def recheck_offer(before, record):
    try:
        pins = recheck_pins(before, record)
        validate_recheck(before, record, pins)
        return pins
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def new_review_attempt(record, turn_id, pins):
    return {"id": turn_id, "version": RECHECK_VERSION, "started_at": now(), "closed_at": None,
            "status": "reviewing", "write_version": 0, "pins": deepcopy(pins),
            "previous_attempt_id": (record["review_attempts"][-1]["id"]
                                    if record.get("review_attempts") else record["turn_id"]),
            "audit": {"review_protocol": SCOPED_BATCH_VERSION,
                      "report": {"findings": []}, "reviews": [],
                      "metrics": {"provider_calls": 0, "tokens": 0, "elapsed_seconds": 0,
                                  "usage_reported": False, "budget": deepcopy(REVIEW_BUDGET),
                                  "review_attempt_id": turn_id, "request_sequence": ["semantic_review"]}}}


def protected_record(record):
    return {k: v for k, v in record.items() if k not in REVIEW_FIELDS | {
        "status", "review_attempts", "review_attempt", "review_attempt_history"}}


class ReviewAttemptStore:
    """Narrow staging adapter: the shared harness can save only this attempt's audit."""
    def __init__(self, store):
        self.store = store

    def save(self, record, *, dispatch=False):
        return self.store.save_review_attempt(record, dispatch=dispatch)


async def stream_recheck(service, project_id, pins, snapshot_mode="full", *, _job_phase=None):
    """Use the ordinary turn transport/cancellation with no router or source write."""
    import asyncio
    import time

    from ..domain.models import uid
    from ..infrastructure.database import ConflictError
    from .agent_errors import agent_error_event
    from .plan_stage import StagedDatabase
    from .plan_validation import build_validation_receipt
    from .turn_summary import build_turn_summary

    turn_id = _job_phase["phase_id"] if _job_phase else uid()
    app, store = service.app, service.store
    lock = app.operation_lock(project_id)
    if not lock.acquire(blocking=False):
        yield {"type": "error", "message": "此项目的 Agent 或其他操作仍在运行"}
        yield {"type": "done", "changed": False, "turn_id": turn_id}
        return
    before, stage, summary = None, None, None
    terminal, committed = "failed", False
    started = time.monotonic()
    app.agent.active_turns[project_id] = turn_id
    try:
        if snapshot_mode not in {"full", "compact-v1"}:
            raise ValueError("未知的 Agent 快照格式")
        before, record = store.begin_review_attempt(project_id, pins, turn_id, job_phase=_job_phase)
        stage = StagedDatabase(app.db, ReviewAttemptStore(store), record, record["id"])
        metrics = stage.metrics_ref = record["metrics"]
        yield {"type": "started", "turn_id": turn_id, "project_id": project_id,
               "snapshot_mode": snapshot_mode, "project": app.projects.get(project_id)}
        yield service.candidate_event(stage, turn_id, "重新评审完整候选")
        run = await service.run_review(stage, before, metrics)
        stage.record["metrics"] = {**metrics, "elapsed_seconds": time.monotonic() - started}
        if run.decision == "apply":
            stage.record["status"] = "ready"
            stage.record = stage.store.save(stage.record)
            _, stage.record, summary = store.commit(stage.record, before, turn_id)
            committed, terminal = True, "completed"
        else:
            stage.record["status"] = "needs_resolution"
    except (asyncio.CancelledError, GeneratorExit) as exc:
        terminal = "stopped"
        if stage:
            stage.record["status"] = "stopped"
            partial = getattr(exc, "harness_run", None)
            if partial:
                stage.record["harness_run"] = partial.model_dump(mode="json")
                stage.record["validation_receipt"] = build_validation_receipt(stage.project, harness_run=partial)
        raise
    except Exception as exc:
        if stage:
            stage.record["status"] = "stale" if isinstance(exc, ConflictError) else "needs_resolution"
            stage.record["report"]["findings"].append({"code": "review_attempt_failed",
                "subject": "candidate", "message": agent_error_event(exc)["message"]})
        yield agent_error_event(exc)
    finally:
        try:
            if stage and not committed:
                stage.record["metrics"] = {**metrics, "elapsed_seconds": time.monotonic() - started}
                try:
                    stage.record = store.save_review_attempt(stage.record, close=True)
                except ConflictError:
                    # Never replace a discard, newer plan, or newer review. Keep
                    # late output as attempt-attributed audit, without plan copies.
                    durable = store.get(stage.record["id"])
                    store.event(project_id, "candidate_terminal_audit", {
                        "candidate_id": durable["id"], "turn_id": turn_id,
                        "terminal_status": durable["status"], "partial_observations": True,
                        **review_audit(stage.record, durable, replacing_latest=False)})
                    stage.record = review_projection(durable)
                    terminal = "stopped" if durable["status"] == "discarded" else "failed"
            if not committed:
                current = app.db.get(project_id)
                summary = build_turn_summary(before or current, current, turn_id, terminal)
                summary["candidate_outcome"] = {
                    "id": stage.record["id"] if stage else None,
                    "status": stage.record["status"] if stage else "not_admitted",
                    "canonical_unchanged": bool(before and current.revision == before.revision
                                                and candidate_hash(current) == candidate_hash(before)),
                    "note": "本次重新评审未应用方案；候选内容与既有评审记录保留"}
                store.event(project_id, "agent_turn_finished", summary)
        finally:
            app.agent.active_turns.pop(project_id, None)
            lock.release()
    if stage:
        yield service.candidate_event(stage, turn_id, "规划已原子应用" if committed else "评审结束，候选保留")
    yield {"type": "done", "turn_id": turn_id, "project_id": project_id,
           "changed": committed, "summary": summary, "project": app.projects.get(project_id)}
