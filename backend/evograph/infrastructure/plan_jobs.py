"""Durable bounded-job ownership. Existing call receipts are the only spend ledger."""
import json
from copy import deepcopy

from ..domain.models import now, uid
from ..domain.plan_contracts import candidate_hash
from ..domain.plan_harness import CURRENT_POLICY
from .database import ConflictError

JOB_VERSION = "bounded-planning-job/v1"
PHASE_BUDGET = {"max_calls": 5, "max_request_bytes": 163840,
                "max_review_request_bytes": 163840, "max_total_input_bytes": 393216,
                "max_output_bytes": 98304, "call_timeout_seconds": 180}


def digest(value):
    from ..application.plan_units import _hash
    return _hash(value)


def latest_record(connection, project_id):
    row = connection.execute("SELECT payload FROM plan_candidates WHERE project_id=? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (project_id,)).fetchone()
    record = json.loads(row[0]) if row else None
    return None if record and record["status"] == "discarded" else record


def phase_receipt(connection, project_id, phase_id):
    row = connection.execute("SELECT detail FROM events WHERE project_id=? AND kind='agent_turn_finished' "
        "AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END, '$.turn_id')=? "
        "ORDER BY rowid DESC LIMIT 1", (project_id, phase_id)).fetchone()
    return json.loads(row[0]) if row else None


def closed_unchanged_receipt(receipt, phase):
    outcome = (receipt or {}).get("candidate_outcome", {})
    return bool(receipt and receipt.get("turn_id") == phase["id"]
                and receipt.get("status") in {"completed", "failed", "stopped"}
                and outcome.get("id") == phase["candidate_id"]
                and outcome.get("canonical_unchanged") is True)


def start_pins(before, record):
    return {"base_revision": before.revision, "base_hash": candidate_hash(before),
            "candidate_id": record["id"] if record else None,
            "record_hash": digest(record), "policy_version": CURRENT_POLICY.version}


def phase_metrics(connection, job, replacement=None):
    """No independently incremented aggregate. Every call belongs to exactly one phase."""
    result = []
    for phase in job["phases"]:
        row = connection.execute("SELECT payload FROM plan_candidates WHERE id=?",
                                 (phase["candidate_id"],)).fetchone()
        record = json.loads(row[0]) if row else None
        if replacement and phase["id"] == replacement.get("metrics", {}).get("job_phase_id"):
            metrics = replacement["metrics"]
        elif phase["kind"] == "review":
            attempt = next((a for a in (record or {}).get("review_attempts", [])
                            if a["id"] == phase["id"]), None)
            metrics = attempt["audit"]["metrics"] if attempt else {}
        else:
            metrics = (record or {}).get("metrics", {})
        result.append(metrics)
    return result


def spend_of(metrics):
    calls = [c for m in metrics for c in m.get("calls", [])]
    return {"calls": len(calls), "input_bytes": sum(c["input_bytes"] for c in calls),
            "tokens": sum(m.get("tokens", 0) for m in metrics),
            "usage_complete": bool(calls) and all(c.get("usage_events") and c.get("normal_stream_end")
                and not c.get("error_type")
                and (not any(e.get("usage_counter", "").startswith("anthropic_") for e in c["usage_events"])
                     or {"anthropic_input", "anthropic_output"} <= {e.get("usage_counter") for e in c["usage_events"]})
                for c in calls)}


class PlanningJobStore:
    def __init__(self, db):
        self.db = db
        with db.connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS planning_jobs(id TEXT PRIMARY KEY, "
                      "project_id TEXT NOT NULL REFERENCES projects(id), payload TEXT NOT NULL)")

    def read(self, c, job_id):
        row = c.execute("SELECT payload FROM planning_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("规划任务不存在")
        return json.loads(row[0])

    def get(self, job_id):
        with self.db.connect() as c:
            return self.read(c, job_id)

    def put(self, c, job):
        job["write_version"] += 1
        c.execute("INSERT INTO planning_jobs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                  (job["id"], job["project_id"], json.dumps(job, ensure_ascii=False)))

    def create(self, project_id, content, request):
        from ..application.plan_complete_change import (
            COMPLETE_CHANGE_LIMITS,
            COMPLETE_CHANGE_VERSION,
        )
        mode = request.get("mode")
        limits = request.get("limits")
        if mode not in {None, COMPLETE_CHANGE_VERSION}:
            raise ValueError("unsupported planning job mode")
        if mode == COMPLETE_CHANGE_VERSION and limits != COMPLETE_CHANGE_LIMITS:
            raise ValueError("complete-change requires one phase, at most two calls and 393216 input bytes")
        if (not isinstance(limits, dict) or set(limits) != {"max_phases", "max_calls", "max_input_bytes"}
                or any(type(v) is not int or v <= 0 for v in limits.values())
                or limits["max_phases"] > 3 or limits["max_calls"] > 12
                or limits["max_calls"] > 5 * limits["max_phases"]
                or limits["max_input_bytes"] > 393216 * limits["max_phases"]):
            raise ValueError("必须明确授权任务总阶段、调用和输入字节上限")
        job_id = request.get("job_id")
        if not isinstance(job_id, str) or not 1 <= len(job_id) <= 100 or not content.strip():
            raise ValueError("任务需要稳定标识与明确的新用户要求")
        identity = digest({"project_id": project_id, "content": content, "request": request})
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute("SELECT payload FROM planning_jobs WHERE id=?", (job_id,)).fetchone()
            if existing:
                job = json.loads(existing[0])
                if job["request_hash"] != identity:
                    raise ConflictError("任务标识已用于不同授权")
                return job, False
            before = self.db.read_project(c, project_id)
            previous = latest_record(c, project_id)
            if (before.archived or not before.unified_planning or before.question
                    or request.get("pins") != start_pins(before, previous)):
                raise ConflictError("任务来源已变化，请刷新后授权")
            rows = c.execute("SELECT payload FROM planning_jobs WHERE project_id=?", (project_id,)).fetchall()
            if any(json.loads(r[0])["status"] in {"authorized", "running", "paused"} for r in rows):
                raise ConflictError("当前规划任务尚未结束，请先取消")
            job = {"version": JOB_VERSION, "id": job_id, "project_id": project_id,
                   "created_at": now(), "write_version": 0, "request_hash": identity,
                   "input": content, "source_id": uid(), "source_message_id": None,
                   "pins": request["pins"], "limits": deepcopy(limits), "phases": [],
                   "status": "authorized", "stop_reason": None, "cancelled": False,
                   "retained_checkpoints": ((previous or {}).get("generation_progress", {}).get("checkpoint_count", 0)
                       if previous and previous["status"] not in {"applied", "discarded"} else 0)}
            if mode is not None:
                job["mode"] = mode
            self.put(c, job)
            return job, True

    def latest(self, project_id):
        with self.db.connect() as c:
            row = c.execute("SELECT payload FROM planning_jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 1",
                            (project_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def pins(self, job):
        return {"job_id": job["id"], "write_version": job["write_version"], "job_hash": digest(job)}

    def _fresh(self, c, job):
        before = self.db.read_project(c, job["project_id"])
        if (before.archived or not before.unified_planning or before.question
                or before.revision != job["pins"]["base_revision"]
                or candidate_hash(before) != job["pins"]["base_hash"]
                or CURRENT_POLICY.version != job["pins"]["policy_version"]):
            raise ConflictError("任务正式版本或检查策略已变化")
        return before

    def open_phase(self, job_id, expected_pins):
        from ..application.plan_jobs import boundary_kind
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            job = self.read(c, job_id)
            if expected_pins != self.pins(job):
                raise ConflictError("任务已变化，请刷新后继续")
            if job["cancelled"] or job["status"] not in {"authorized", "paused"}:
                raise ConflictError("任务不能继续")
            before = self._fresh(c, job)
            record = latest_record(c, job["project_id"])
            kind = "generation"
            if job["phases"]:
                previous = job["phases"][-1]
                receipt = phase_receipt(c, job["project_id"], previous["id"])
                if (not closed_unchanged_receipt(receipt, previous)
                        or digest(receipt) != previous["receipt_hash"]
                        or not record or not previous.get("closed_at") or not previous.get("safe_boundary")
                        or record["id"] != previous["candidate_id"]
                        or digest(record) != previous["record_hash"]
                        or boundary_kind(record, previous) != previous["next_kind"]):
                    raise ConflictError("上一阶段凭据或候选已改变")
                kind = previous["next_kind"]
            elif start_pins(before, record) != job["pins"]:
                raise ConflictError("任务原始候选已改变")
            spend = spend_of(phase_metrics(c, job))
            reason = ("aggregate_phase_limit" if len(job["phases"]) >= job["limits"]["max_phases"] else
                      "aggregate_call_limit" if spend["calls"] >= job["limits"]["max_calls"] else
                      "aggregate_input_limit" if spend["input_bytes"] >= job["limits"]["max_input_bytes"] else None)
            if reason:
                job.update(status="stopped", stop_reason=reason)
                self.put(c, job)
                return job, None
            phase_id = job["source_id"] if not job["phases"] else uid()
            phase = {"id": phase_id, "number": len(job["phases"]) + 1,
                     "kind": kind, "candidate_id": record["id"] if kind == "review" else phase_id,
                     "previous_candidate_id": record["id"] if record else None,
                     "previous_record_hash": digest(record), "admission_pins_hash": digest(expected_pins),
                     "started_at": now(), "closed_at": None,
                     "starting_checkpoints": (record or {}).get("generation_progress", {}).get("checkpoint_count", 0)}
            job["phases"].append(phase)
            job.update(status="running", stop_reason=None)
            self.put(c, job)
            return job, phase

    def cancel(self, project_id, job_id):
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            job = self.read(c, job_id)
            if job["project_id"] != project_id:
                raise ValueError("任务不属于此项目")
            if job["status"] != "applied" and not job["cancelled"]:
                job.update(cancelled=True, status="cancelled", stop_reason="cancelled")
                self.put(c, job)
        return job

    def close_phase(self, job_id, phase_id):
        from ..application.plan_jobs import boundary_kind, stop_reason
        with self.db.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            job = self.read(c, job_id)
            phase = job["phases"][-1]
            if phase["id"] != phase_id or phase.get("closed_at"):
                return job
            row = c.execute("SELECT payload FROM plan_candidates WHERE id=?", (phase["candidate_id"],)).fetchone()
            record = json.loads(row[0]) if row else None
            closed = phase_receipt(c, job["project_id"], phase_id)
            next_kind = (boundary_kind(record, phase)
                         if record and closed_unchanged_receipt(closed, phase) else None)
            if job.get("mode") is not None:
                next_kind = None
            phase.update(closed_at=now(), record_hash=digest(record), receipt_hash=digest(closed),
                         safe_boundary=bool(next_kind), next_kind=next_kind)
            if record and record.get("source_message_id"):
                job["source_message_id"] = record["source_message_id"]
            if not job["cancelled"]:
                if record and record["status"] == "applied":
                    job.update(status="applied", stop_reason=None)
                elif next_kind:
                    job.update(status="paused", stop_reason="phase_budget_boundary")
                else:
                    job.update(status="stopped", stop_reason=stop_reason(record))
            self.put(c, job)
            return job


def guard_candidate_write(db, c, data, previous, *, commit=False, dispatch=False, question=False):
    """Final admission in the SAME writer transaction as persisted call receipts.

    Admitted but uncertain calls remain charged forever. No retry/recovery may
    erase them, and a closed phase cannot be reopened by stale writers.
    """
    context = data.get("planning_job")
    if not context:
        if previous and previous.get("planning_job"):
            raise ConflictError("不能移除任务归属")
        return
    store = PlanningJobStore.__new__(PlanningJobStore)
    store.db = db
    job = store.read(c, context["job_id"])
    phase = job["phases"][-1]
    if question and (job["cancelled"] or job["status"] != "running"
                     or (previous or {}).get("planning_job_question")):
        raise ConflictError("任务已取消或问题已保存，不能新增问题状态")
    metrics = data.get("metrics", {})
    from ..application.plan_complete_change import (
        COMPLETE_CHANGE_VERSION,
        guard_write,
        is_complete_change,
    )
    if (job.get("mode") == COMPLETE_CHANGE_VERSION) != is_complete_change(data):
        raise ConflictError("任务完整变更模式不能修改或移除")
    if is_complete_change(data):
        guard_write(data, previous, job, canonical=db.read_project(c, job["project_id"]),
                    dispatch=dispatch, commit=commit)
    if (phase["id"] != metrics.get("job_phase_id") or phase["candidate_id"] != data["id"]
            or context != {"job_id": job["id"], "source_id": job["source_id"]}
            or data["project_id"] != job["project_id"] or data["input"] != job["input"]
            or phase.get("closed_at")):
        raise ConflictError("阶段或候选归属已变化")
    question_stop = (previous or {}).get("planning_job_question")
    if data.get("planning_job_question") != question_stop:
        raise ConflictError("任务问题终态凭据不能修改或移除")
    if question_stop:
        audit_fields = {"metrics", "job_write_version", "status", "report", "validation_receipt"}
        if ({k: v for k, v in data.items() if k not in audit_fields}
                != {k: v for k, v in previous.items() if k not in audit_fields}
                or data["status"] not in {"needs_resolution", "stopped"}):
            raise ConflictError("任务问题终态只能保存审计，不能改写候选或调度")
        before = db.read_project(c, job["project_id"])
        if (dispatch or commit or not before.question
                or question_stop != {"phase_id": phase["id"], "question_id": before.question.id,
                    "base_revision": before.revision, "canonical_hash": candidate_hash(before)}
                or candidate_hash(before) != job["pins"]["base_hash"]
                or data["project"].get("question") != before.question.model_dump()):
            raise ConflictError("任务问题已保留，只能完成原阶段审计")
    else:
        before = store._fresh(c, job)
    if data["base_revision"] != before.revision:
        raise ConflictError("阶段基线已变化")
    sources = [s for s in data["project"]["plan_contract"]["sources"] if s["id"] == job["source_id"]]
    if (len(sources) != 1 or sources[0]["text"] != job["input"]
            or (job["source_message_id"] and sources[0].get("message_id") != job["source_message_id"])):
        raise ConflictError("任务原始意图或消息归属已变化")
    if previous is None:
        prior = latest_record(c, job["project_id"])
        if digest(prior) != phase["previous_record_hash"]:
            raise ConflictError("阶段来源候选已变化")
        if is_complete_change(data):
            from ..domain.models import Project
            from ..domain.plan_contracts import IntentSource, bootstrap_contract
            resume = bool(prior and prior["status"] not in {"applied", "discarded"})
            if data.get("resumes_candidate_id") != (prior["id"] if resume else None):
                raise ConflictError("完整变更入口候选身份已变化")
            expected = Project.model_validate(prior["project"]) if resume else before.model_copy(deep=True)
            expected.question = before.question
            bootstrap_contract(expected)
            expected.plan_contract.sources.append(IntentSource(id=job["source_id"], text=job["input"],
                reference_context=data.get("reference_context", {})))
            candidate = Project.model_validate(data["project"])
            if candidate.revision != expected.revision or candidate_hash(candidate) != candidate_hash(expected):
                raise ConflictError("完整变更入口不是当前固定候选，禁止覆盖原始记录")
    else:
        latest = latest_record(c, job["project_id"])
        if latest["id"] != data["id"]:
            raise ConflictError("任务候选已被更新")
    if not data.get("review_attempt"):
        if previous and data.get("job_write_version") != previous.get("job_write_version"):
            raise ConflictError("候选写入版本已改变，拒绝迟到写入")
        data["job_write_version"] = data.get("job_write_version", 0) + 1
    previous_metrics = (previous or {}).get("metrics", {})
    if data.get("review_attempt") and previous:
        from ..application.plan_recheck import review_projection
        previous_metrics = review_projection(previous).get("metrics", {})
    old_calls, calls = previous_metrics.get("calls", []), metrics.get("calls", [])
    if len(calls) < len(old_calls) or len(calls) > len(old_calls) + 1:
        raise ConflictError("调用凭据不可删除或跳号")
    new_dispatch = len(calls) > len(old_calls)
    for index, call in enumerate(calls):
        if call.get("number") != index + 1 or call.get("phase_call_id") != f"{phase['id']}:{index + 1}":
            raise ConflictError("调用身份不属于当前阶段")
        if index < len(old_calls):
            old = old_calls[index]
            if (old.get("termination_reason") or old.get("status") in {"interrupted", "cancelled_before_dispatch"}) and old != call:
                raise ConflictError("已结束调用凭据不能改变")
            for key in ("number", "phase_call_id", "request_sha256", "input_bytes", "purpose"):
                if call.get(key) != old.get(key):
                    raise ConflictError("已入账调用身份不能改变")
            if old.get("dispatched") and not call.get("dispatched"):
                raise ConflictError("不能退还已发送调用")
            new_dispatch |= bool(call.get("dispatched") and not old.get("dispatched"))
        if call["input_bytes"] > PHASE_BUDGET["max_request_bytes"]:
            raise ConflictError("单次输入超过预算")
    if metrics.get("provider_calls", 0) != len(calls) or metrics.get("input_bytes", 0) != sum(x["input_bytes"] for x in calls):
        raise ConflictError("调用统计与持久凭据不一致")
    if metrics.get("budget") != (dict(PHASE_BUDGET, max_calls=1) if phase["kind"] == "review" else PHASE_BUDGET):
        raise ConflictError("阶段预算不能修改")
    if dispatch and (not old_calls or old_calls[-1].get("status") != "admitted"
                     or not new_dispatch or len(calls) != len(old_calls)):
        raise ConflictError("调用已发送或归属不确定，不能重复发送")
    if question_stop and new_dispatch:
        raise ConflictError("任务已等待回答，不能追加或发送请求")
    if is_complete_change(data):
        from ..application.plan_batch_policy import experiment_policy
        expected = {"project_id": data["project_id"], **experiment_policy(COMPLETE_CHANGE_VERSION)}
        if data.get("planning_experiment") != expected or metrics.get("planning_experiment") != expected:
            raise ConflictError("完整变更策略不能修改")
        if len(calls) > 2 or any(call.get("purpose") != ["generation", "semantic_review"][index]
                                for index, call in enumerate(calls)):
            raise ConflictError("完整变更仅允许一次生成和一次评审")
        if new_dispatch and len(calls) == 2:
            from ..application.plan_complete_change import complete
            if not complete(data) or any(a.get("status") != "succeeded" for a in data.get("tool_attempts", [])):
                raise ConflictError("完整变更尚未闭合，不能调用评审")
    if new_dispatch or commit:
        if job["cancelled"] or job["status"] != "running":
            raise ConflictError("任务已取消或停止，未发送请求")
        if any(not x.get("normal_stream_end") or x.get("termination_reason") != "stream_end"
               or x.get("error_type")
               for x in (old_calls if len(calls) > len(old_calls) else old_calls[:-1])):
            raise ConflictError("前一调用结果不确定，不能继续")
    spend = spend_of(phase_metrics(c, job, data))
    if (len(calls) > metrics["budget"]["max_calls"]
            or sum(x["input_bytes"] for x in calls) > PHASE_BUDGET["max_total_input_bytes"]
            or spend["calls"] > job["limits"]["max_calls"]
            or spend["input_bytes"] > job["limits"]["max_input_bytes"]):
        from ..application.plan_budget import BudgetExceededError
        error = BudgetExceededError("任务累计授权预算已用尽，未发送额外请求")
        error.reason = ("aggregate_call_limit" if spend["calls"] > job["limits"]["max_calls"]
                        else "aggregate_input_limit")
        raise error
    store.put(c, job)
