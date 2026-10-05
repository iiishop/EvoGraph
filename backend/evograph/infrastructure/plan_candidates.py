"""Durable, isolated planning candidates and one CAS publication transaction."""
import json
from copy import deepcopy

from ..domain.models import Project, now, uid
from ..domain.plan_contracts import (
    PLANNING_FIELDS,
    candidate_hash,
)
from .database import ConflictError


class CandidateStore:
    def __init__(self, db):
        self.db = db
        with db.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS plan_candidates(
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS candidates_project ON plan_candidates(project_id, created_at);
            """)

    def _writable_candidate(self, connection, candidate_id, *, required=True):
        """Called under the writer lock so terminal records cannot be resurrected."""
        row = connection.execute(
            "SELECT payload FROM plan_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        saved = json.loads(row[0]) if row else None
        if saved and saved["status"] in {"applied", "discarded"}:
            raise ConflictError("候选已应用或放弃，不能覆盖其最终状态")
        if saved and connection.execute(
                "SELECT 1 FROM plan_candidates WHERE "
                "json_extract(payload, '$.repair_phase.pins.candidate_id')=? LIMIT 1",
                (candidate_id,)).fetchone():
            raise ConflictError("候选已成为新修复轮次的固定来源，不能覆盖既有审计")
        if saved and connection.execute(
                "SELECT 1 FROM plan_candidates WHERE "
                "json_extract(payload, '$.retained_acceptance.pins.staged_candidate_id')=? LIMIT 1",
                (candidate_id,)).fetchone():
            raise ConflictError("候选已成为验收请求入口的固定来源，不能覆盖历史")
        if required and saved is None:
            raise ValueError("候选规划不存在")
        return saved

    def save(self, record, *, dispatch=False):
        data = deepcopy(record)
        data["candidate_hash"] = candidate_hash(Project.model_validate(data["project"]))
        data.setdefault("project_id", data["project"]["id"])
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._writable_candidate(connection, data["id"], required=False)
            if previous and previous.get("review_attempts"):
                raise ConflictError("候选已有独立评审轮次，旧写入不能覆盖评审记录")
            self._check_repair_phase(connection, data, previous)
            self._check_prior_schedule_owner(connection, data, previous)
            from .plan_jobs import guard_candidate_write
            guard_candidate_write(self.db, connection, data, previous, dispatch=dispatch)
            self._check_retained_acceptance(connection, data, previous)
            connection.execute("INSERT INTO plan_candidates VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (
                    data["id"], data["project"]["id"], json.dumps(data, ensure_ascii=False), data["created_at"],
                ))
        return data

    def _check_retained_acceptance(self, connection, data, previous, *, commit=False):
        from ..application.retained_acceptance import (
            capture_entry,
            entry_for,
            projection,
            resolutions,
            validate_metadata_scope,
        )
        from ..domain.plan_harness import content_hash

        before = self.db.read_project(connection, data["project_id"])
        candidate = Project.model_validate(data["project"])

        def read(identity):
            row = connection.execute("SELECT payload FROM plan_candidates WHERE id=?", (identity,)).fetchone()
            if not row:
                raise ConflictError("retained acceptance pinned predecessor missing")
            return json.loads(row[0])

        if previous is None:
            predecessor = read(data["resumes_candidate_id"]) if data.get("resumes_candidate_id") else None
            same_request = bool(predecessor and data.get("planning_job")
                                and data["planning_job"] == predecessor.get("planning_job")
                                and data.get("input") == predecessor.get("input"))
            if same_request and predecessor.get("retained_acceptance"):
                expected = deepcopy(predecessor["retained_acceptance"])
                inherited = predecessor.get("compilations", [])
                if data.get("compilations") not in (None, inherited):
                    raise ConflictError("retained acceptance continuation compiler history changed")
                data["compilations"] = deepcopy(inherited)
            else:
                entry_record = predecessor
                # Legacy job continuation is bootstrapped from its existing exact
                # job-entry pin, never the last weakened checkpoint or model IDs.
                if same_request and predecessor.get("planning_job"):
                    job_id = predecessor["planning_job"]["job_id"]
                    row = connection.execute("SELECT payload FROM planning_jobs WHERE id=?", (job_id,)).fetchone()
                    if not row:
                        raise ConflictError("retained acceptance legacy job pin missing")
                    job = json.loads(row[0])
                    entry_record = read(job["pins"]["candidate_id"]) if job["pins"]["candidate_id"] else None
                    if (content_hash(entry_record) != job["pins"]["record_hash"]
                            or job["project_id"] != before.id or job["input"] != data["input"]
                            or job["pins"]["base_hash"] != candidate_hash(before)):
                        raise ConflictError("retained acceptance legacy entry pin changed")
                    data["compilations"] = deepcopy(predecessor.get("compilations", []))
                source_id = data.get("planning_job", {}).get("source_id", data.get("turn_id", data["id"]))
                expected = capture_entry(before, entry_record, source_id=source_id,
                                         audit_start=0 if same_request else len(data.get("compilations", [])))
            if data.get("retained_acceptance") not in (None, expected):
                raise ConflictError("retained acceptance admission is server-owned")
            data["retained_acceptance"] = expected
        elif previous.get("retained_acceptance") != data.get("retained_acceptance"):
            raise ConflictError("retained acceptance fixed entry cannot change or disappear")
        if "retained_acceptance" not in data:
            if commit:
                raise ConflictError("retained acceptance legacy scope requires a new admitted request")
            return
        entry = entry_for(before, data)
        resolutions(entry, data, candidate)
        pin = entry["pins"]
        original = read(pin["staged_candidate_id"]) if pin["staged_candidate_id"] else None
        if original is not None and content_hash(original) != pin["staged_record_hash"]:
            raise ConflictError("retained acceptance pinned entry record changed")
        expected = capture_entry(before, original, source_id=pin["source_id"], audit_start=entry["audit_start"])
        # Question transitions may advance only canonical revision, not planning hash.
        expected["pins"]["canonical_revision"] = pin["canonical_revision"]
        expected["entry_hash"] = content_hash({k: v for k, v in expected.items() if k != "entry_hash"})
        if expected != entry:
            raise ConflictError("retained acceptance entry inventory differs from pinned records")
        if previous:
            old_audits, audits = previous.get("compilations", []), data.get("compilations", [])
            if audits[:len(old_audits)] != old_audits or len(audits) < len(old_audits):
                raise ConflictError("retained acceptance compiler history cannot be changed or deleted")
            if len(audits) > len(old_audits) + 1:
                raise ConflictError("retained acceptance accepts one atomic compiler checkpoint")
            old_project = Project.model_validate(previous["project"])
            if len(audits) > len(old_audits):
                audit = audits[-1]
                validate_metadata_scope(previous, old_project, audit.get("ir", {}))
                if any(row.get("acceptance_changes") for row in audit.get("ir", {}).get("contracts", [])) or audit.get("ir", {}).get("removal_acceptance_changes"):
                    if (audit.get("base_candidate_hash") != candidate_hash(old_project)
                            or audit.get("result_candidate_hash") != candidate_hash(candidate)):
                        raise ConflictError("retained acceptance compiler checkpoint is stale")
                    if previous.get("work_units"):
                        from ..application.plan_units import advance_unit_checkpoint
                        # Recompute the assigned checkpoint against durable state
                        # under this writer lock, not only the earlier handler.
                        replayed = advance_unit_checkpoint(
                            {**deepcopy(previous), "compilations": deepcopy(audits)},
                            old_project, candidate, audit)
                        if any(replayed.get(key) != data.get(key) for key in ("work_units", "unit_request")):
                            raise ConflictError("retained acceptance unit checkpoint changed before atomic save")
            changed = any(getattr(old_project, name) != getattr(candidate, name)
                          for name in ("behaviors", "milestones")) or old_project.plan_contract.bindings != candidate.plan_contract.bindings
            if changed or len(audits) != len(old_audits) or commit:
                projection(before, candidate, data)

    def _check_prior_schedule_owner(self, connection, data, previous):
        """Bind router-only predecessor context to saved job identity under the writer lock."""
        from ..application.plan_phase import predecessor_schedule_owner
        from .plan_jobs import PlanningJobStore, digest, latest_record

        owner = data.get("prior_work_units_owner")
        if previous:
            if any(data.get(key) != previous.get(key) for key in (
                    "prior_work_units_owner", "resumes_candidate_id", "prior_work_units")) and (
                    owner is not None or previous.get("prior_work_units_owner") is not None):
                raise ConflictError("前序任务调度归属不能修改或移除")
            return
        if owner is None and not data.get("resumes_candidate_id"):
            return
        predecessor = latest_record(connection, data["project_id"])
        expected = (predecessor_schedule_owner(predecessor)
                    if predecessor and predecessor["id"] == data.get("resumes_candidate_id") else None)
        if owner != expected:
            raise ConflictError("前序任务调度归属或来源凭据已改变")
        if owner is None:
            return
        store = PlanningJobStore.__new__(PlanningJobStore)
        store.db = self.db
        job = store.read(connection, owner["job_id"])
        # Historical ownership does not re-admit the old job. Question/answer
        # transitions may advance revision while leaving its pinned plan intact.
        before = self.db.read_project(connection, data["project_id"])
        source = [s for s in predecessor["project"]["plan_contract"]["sources"]
                  if s["id"] == owner["source_id"]]
        phase = next((p for p in job["phases"] if p["kind"] == "generation"
                      and p["id"] == predecessor["turn_id"]
                      and p["candidate_id"] == predecessor["id"]), None)
        if (job["project_id"] != data["project_id"] or job["source_id"] != owner["source_id"]
                or candidate_hash(before) != job["pins"]["base_hash"]
                or before.revision < job["pins"]["base_revision"]
                or predecessor["base_revision"] != before.revision
                or data["base_revision"] != before.revision
                or predecessor.get("candidate_hash") != candidate_hash(Project.model_validate(predecessor["project"]))
                or data.get("prior_work_units") != predecessor["work_units"]
                or predecessor["work_units"].get("project_id") != data["project_id"]
                or predecessor["work_units"].get("source_id") != owner["source_id"]
                or len(source) != 1 or source[0]["text"] != job["input"]
                or predecessor["input"] != job["input"]
                or (job["source_message_id"] and source[0].get("message_id") != job["source_message_id"])
                or not phase or not phase.get("closed_at")
                or digest(predecessor) != owner["record_hash"]):
            raise ConflictError("前序任务调度没有匹配的已保存来源与关闭阶段")

    def _repair_phase_admission(self, connection, before, predecessor):
        from ..application.plan_repair_phase import admit_repair_phase, closed_turn_ids
        from ..application.plan_units import _hash
        latest = connection.execute("SELECT payload FROM plan_candidates WHERE project_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (before.id,)).fetchone()
        if (not latest or _hash(json.loads(latest[0])) != _hash(predecessor)
                or self.db.read_project(connection, before.id) != before):
            raise ConflictError("候选或正式规划已改变，请刷新后开始新修复轮次")
        receipts = []
        for turn in closed_turn_ids(predecessor):
            row = connection.execute(
                "SELECT detail FROM events WHERE project_id=? AND kind='agent_turn_finished' "
                "AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END, '$.turn_id')=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (before.id, turn)).fetchone()
            if not row:
                raise ConflictError("前一轮尚无已结束记录，不能开始新修复轮次")
            receipts.append(json.loads(row[0]))
        return admit_repair_phase(before, predecessor, receipts)

    def repair_phase_admission(self, before, predecessor):
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._repair_phase_admission(connection, before, predecessor)

    def repair_phase_offer(self, before, predecessor):
        if not predecessor.get("planning_experiment"):
            return None
        try:
            return self.repair_phase_admission(before, predecessor)["pins"]
        except (ValueError, ConflictError, KeyError, TypeError, AttributeError):
            return None

    def _check_repair_phase(self, connection, data, previous):
        from ..application.plan_repair_phase import validate_repair_start
        from ..application.plan_units import _hash
        admission = data.get("repair_phase")
        if previous and previous.get("repair_phase") != admission:
            raise ConflictError("新修复轮次的来源凭据不能修改或移除")
        if not admission:
            return
        row = connection.execute("SELECT payload FROM plan_candidates WHERE id=?",
            (admission["pins"]["candidate_id"],)).fetchone()
        predecessor = json.loads(row[0]) if row else None
        if not predecessor or _hash(predecessor) != admission["pins"]["record_hash"]:
            raise ConflictError("新修复轮次的原候选审计已改变")
        sources = predecessor["project"]["plan_contract"]["sources"]
        if (data.get("resumes_candidate_id") != predecessor["id"]
                or data.get("prior_work_units") != predecessor["work_units"]
                or data["project"]["plan_contract"]["sources"][:len(sources)] != sources
                or data.get("planning_experiment") or data["metrics"].get("planning_experiment")):
            raise ConflictError("新修复轮次必须保留既有来源与完整检查点，不能重开实验")
        if previous is None:
            before = self.db.read_project(connection, data["project_id"])
            if admission != self._repair_phase_admission(connection, before, predecessor):
                raise ConflictError("新修复轮次的来源凭据已改变")
            validate_repair_start(data, predecessor)

    def begin_review_attempt(self, project_id, pins, turn_id, *, job_phase=None):
        from ..application.plan_recheck import (
            new_review_attempt,
            review_projection,
            validate_recheck,
        )
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM plan_candidates WHERE project_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (project_id,)).fetchone()
            record = json.loads(row[0]) if row else None
            before = self.db.read_project(connection, project_id)
            validate_recheck(before, record, pins)
            if record.get("planning_job") and not job_phase:
                raise ConflictError("任务候选需要新的明确授权或有界任务评审阶段")
            prior_turn = record["review_attempts"][-1]["id"] if record.get("review_attempts") else record["turn_id"]
            receipt = connection.execute(
                "SELECT 1 FROM events WHERE project_id=? AND kind='agent_turn_finished' "
                "AND json_extract(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END, '$.turn_id')=? LIMIT 1",
                (project_id, prior_turn)).fetchone()
            if not receipt:
                raise ConflictError("前一轮尚无已结束记录，不能启动重新评审")
            attempt = new_review_attempt(record, turn_id, pins)
            if job_phase:
                attempt["audit"]["metrics"]["job_phase_id"] = job_phase["phase_id"]
            record.setdefault("review_attempts", []).append(attempt)
            record["status"] = "reviewing"
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(record, ensure_ascii=False), record["id"]))
        return before, review_projection(record)

    def save_review_attempt(self, record, *, close=False, dispatch=False):
        from ..application.plan_recheck import protected_record, review_audit, review_projection
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = self._writable_candidate(connection, record["id"])
            latest = connection.execute("SELECT id FROM plan_candidates WHERE project_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (saved["project_id"],)).fetchone()
            attempt = saved.get("review_attempts", [{}])[-1]
            current = self.db.read_project(connection, saved["project_id"])
            pins = attempt.get("pins", {})
            if (current.revision != pins.get("base_revision") or current.archived
                    or not current.unified_planning or current.question
                    or candidate_hash(current) != pins.get("base_hash")):
                raise ConflictError("正式规划已改变，评审未覆盖当前状态")
            if (latest[0] != record["id"] or attempt.get("closed_at")
                    or record.get("review_attempt") != {k: v for k, v in attempt.items() if k != "audit"}
                    or protected_record(saved) != protected_record(record)):
                raise ConflictError("评审轮次或候选已改变，迟到结果未覆盖当前状态")
            from .plan_jobs import guard_candidate_write
            guard_candidate_write(self.db, connection, record, saved, dispatch=dispatch)
            attempt["audit"] = review_audit(record, saved)
            attempt["status"] = saved["status"] = record["status"]
            attempt["write_version"] += 1
            if close:
                attempt["closed_at"] = now()
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(saved, ensure_ascii=False), saved["id"]))
        return review_projection(saved)

    def get(self, candidate_id):
        with self.db.connect() as connection:
            row = connection.execute("SELECT payload FROM plan_candidates WHERE id=?", (candidate_id,)).fetchone()
        if not row:
            raise ValueError("候选规划不存在")
        return json.loads(row[0])

    def latest(self, project_id):
        with self.db.connect() as connection:
            row = connection.execute("SELECT payload FROM plan_candidates WHERE project_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (project_id,)).fetchone()
        if not row:
            return None
        record = json.loads(row[0])
        return None if record["status"] == "discarded" else record

    def event(self, project_id, kind, detail):
        with self.db.connect() as connection:
            connection.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                uid(), project_id, kind, json.dumps(detail, ensure_ascii=False), now(),
            ))

    def pause_question(self, record, question, *, status="needs_resolution"):
        """Question identity and resumed base advance together, never rebase a changed plan."""
        data = deepcopy(record)
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = self._writable_candidate(connection, data["id"])
            if prior.get("review_attempts"):
                raise ConflictError("独立评审候选不能由旧生成轮次覆盖")
            current = self.db.read_project(connection, record["project"]["id"])
            if current.revision != record["base_revision"]:
                raise ConflictError("正式规划已改变，候选问题未覆盖当前状态")
            if data.get("planning_job"):
                from .plan_jobs import guard_candidate_write

                # Validate source, budget and call ownership before the existing
                # question CAS changes canonical revision. This grants no call.
                guard_candidate_write(self.db, connection, data, prior, question=question is not None)
            self._check_retained_acceptance(connection, data, prior)
            current.question = question
            current.revision += 1
            current.updated_at = now()
            data.update(base_revision=current.revision, status=status)
            if data.get("planning_job") and question is not None:
                data["planning_job_question"] = {
                    "phase_id": data["turn_id"], "question_id": question.id,
                    "base_revision": current.revision, "canonical_hash": candidate_hash(current),
                }
            self.db.write_project(connection, current, expected_revision=record["base_revision"])
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(data, ensure_ascii=False), data["id"],
            ))
            connection.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                uid(), current.id, "agent_question" if question else "agent_answer", question.prompt if question else "已回答候选问题", now(),
            ))
        return data

    def discard(self, project_id, candidate_id):
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM plan_candidates WHERE project_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (project_id,)).fetchone()
            record = json.loads(row[0]) if row else None
            if not record or record["id"] != candidate_id:
                raise ConflictError("此候选已不是当前版本，请刷新后操作")
            if record["status"] == "applied":
                raise ValueError("已应用的方案不能通过放弃候选撤销")
            current = self.db.read_project(connection, project_id)
            question = record["project"].get("question")
            if current.question and question and question["id"] == current.question.id:
                old_revision = current.revision
                current.question = None
                current.revision += 1
                current.updated_at = now()
                self.db.write_project(connection, current, expected_revision=old_revision)
            record["status"] = "discarded"
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(record, ensure_ascii=False), candidate_id,
            ))
        return record

    def noop(self, record, before, turn_id, pending):
        from ..application.plan_validation import build_validation_receipt
        from ..application.turn_summary import build_turn_summary
        data = deepcopy(record)
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = self._writable_candidate(connection, data["id"])
            if prior.get("review_attempts"):
                raise ConflictError("独立评审候选不能由旧生成轮次覆盖")
            current = self.db.read_project(connection, before.id)
            if current.revision != data["base_revision"]:
                raise ConflictError("正式规划已改变，候选保留")
            self._check_retained_acceptance(connection, data, prior)
            summary = build_turn_summary(before, current, turn_id, "completed")
            data.update(status="needs_resolution" if pending else "applied", turn_summary=summary)
            data["candidate_hash"] = candidate_hash(Project.model_validate(data["project"]))
            data["validation_receipt"] = build_validation_receipt(Project.model_validate(data["project"]))
            if not pending:
                data["applied_revision"] = current.revision
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(data, ensure_ascii=False), data["id"],
            ))
            connection.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                uid(), current.id, "agent_turn_finished", json.dumps(summary, ensure_ascii=False), now(),
            ))
        return data, summary

    def commit(self, record, before, turn_id):
        from ..application.plan_harness import replay_harness, seal_snapshot
        from ..application.plan_review import validate_batch_certificate
        from ..application.plan_validation import build_validation_receipt
        from ..application.turn_summary import build_turn_summary

        # Re-read the durable exact candidate inside the transaction. There is no
        # check-then-save window between validation receipt and candidate identity.
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = self._writable_candidate(connection, record["id"])
            durable = deepcopy(saved)
            if saved.get("review_attempts"):
                from ..application.plan_recheck import review_projection
                saved = review_projection(saved)
                latest = connection.execute("SELECT id FROM plan_candidates WHERE project_id=? "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1", (before.id,)).fetchone()
                if (latest[0] != saved["id"] or saved.get("review_attempt") != record.get("review_attempt")
                        or saved["review_attempt"].get("closed_at")):
                    raise ConflictError("评审轮次已改变，迟到结果未应用")
            if not saved or saved["status"] != "ready" or saved["candidate_hash"] != record["candidate_hash"]:
                raise ValueError("候选已变化，需要重新校验")
            if saved.get("work_units") is not None:
                from ..application.plan_units import all_units_complete
                if not all_units_complete(saved):
                    raise ValueError("调度单元尚未全部完成，不能提交部分候选")
            staged = Project.model_validate(saved["project"])
            if candidate_hash(staged) != saved["candidate_hash"]:
                raise ValueError("候选内容指纹不一致")
            row = connection.execute("SELECT payload FROM projects WHERE id=?", (before.id,)).fetchone()
            current = self.db.decode_project(connection, row[0]) if row else None
            if not current or current.revision != saved["base_revision"]:
                raise ConflictError("正式规划已改变；候选保留，但不能覆盖较新的状态")
            if current.archived or not current.unified_planning or (durable.get("review_attempts") and current.question):
                raise ConflictError("项目状态已改变，候选未应用")
            if candidate_hash(current) != candidate_hash(before):
                raise ConflictError("评审前快照与当前正式规划不一致，候选需要重新校验")
            from .plan_jobs import guard_candidate_write
            guard_candidate_write(self.db, connection, saved, durable, commit=True)
            self._check_retained_acceptance(connection, saved, durable, commit=True)
            snapshot = seal_snapshot(before, staged, saved)

            def replay_model(sealed, certificate):
                # This synchronous callback only replays the exact durable model
                # certificate. No provider or application handles enter plugins.
                if not saved.get("batch_reviews") or certificate != saved["batch_reviews"][-1]:
                    raise ValueError("插件评审证书与保存的原始记录不一致")
                return validate_batch_certificate(
                    sealed.before_project(), sealed.candidate_project(), saved)

            run = replay_harness(snapshot, saved.get("harness_run"), replay_model)
            if run.decision != "apply":
                raise ValueError("当前检查策略仍有未解决项，候选未应用")
            saved["harness_commit_replay"] = {
                "kind": "synchronous_certificate_replay", "run": run.model_dump(mode="json")}

            saved["validation_receipt"] = build_validation_receipt(staged, harness_run=run)
            result = current.model_copy(deep=True)
            for field in PLANNING_FIELDS:
                setattr(result, field, deepcopy(getattr(staged, field)))
            result.question = None
            # Candidate costs have not yet been applied. Count generation plus
            # each explicitly admitted review once, in this same atomic commit.
            costs = [durable["metrics"], *[a["audit"]["metrics"] for a in durable.get("review_attempts", [])]]
            if saved.get("planning_job"):
                from .plan_jobs import PlanningJobStore, phase_metrics
                jobs = PlanningJobStore.__new__(PlanningJobStore)
                jobs.db = self.db
                job = jobs.read(connection, saved["planning_job"]["job_id"])
                costs = phase_metrics(connection, job)
            result.metrics["model_tokens"] += sum(m["tokens"] for m in costs)
            result.metrics["planning_seconds"] += sum(m["elapsed_seconds"] for m in costs)
            result.revision += 1
            result.updated_at = now()
            outcome = build_turn_summary(before, result, turn_id, "completed")
            saved.update(status="applied", applied_revision=result.revision, turn_summary=outcome)
            if durable.get("review_attempts"):
                from ..application.plan_recheck import review_audit
                attempt = durable["review_attempts"][-1]
                attempt["audit"] = review_audit(saved, durable)
                attempt.update(status="applied", closed_at=now(), write_version=attempt["write_version"] + 1)
                durable.update(status="applied", applied_revision=result.revision)
                stored = durable
            else:
                stored = saved
            self.db.write_project(connection, result, expected_revision=current.revision)
            connection.execute("UPDATE plan_candidates SET payload=? WHERE id=?", (
                json.dumps(stored, ensure_ascii=False), saved["id"],
            ))
            if saved.get("planning_job"):
                # Publication and job terminal state share the same transaction;
                # a concurrent cancel cannot relabel an already applied plan.
                job.update(status="applied", stop_reason=None)
                jobs.put(connection, job)
            connection.execute("INSERT INTO events VALUES(?,?,?,?,?)", (
                uid(), result.id, "agent_turn_finished", json.dumps(outcome, ensure_ascii=False), now(),
            ))
        if durable.get("review_attempts"):
            saved = review_projection(stored)
        return result, saved, outcome
