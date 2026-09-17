from ..domain.policies import readiness
from ..infrastructure.repository import snapshot
from .source_index import populate


class ExecutionService:
    def __init__(self, db):
        self.db = db

    def refresh(self, project_id: str):
        p = self.db.get(project_id)
        baseline = snapshot(p.repository, len(p.baselines) + 1)
        if (
            p.baseline
            and p.baseline.fingerprint == baseline.fingerprint
            and p.baseline.commit == baseline.commit
            and p.baseline.complete == baseline.complete
        ):
            if p.source_fingerprint != baseline.fingerprint:
                populate(p)
                return self.db.save(p, "source_indexed", p.source_summary)
            return p
        p.baselines.append(baseline)
        populate(p)
        for m in p.milestones:
            for obligation in m.obligations:
                if obligation.fingerprint and obligation.fingerprint != baseline.fingerprint:
                    obligation.resolved = False
            if m.status in {"IN_PROGRESS", "VERIFIED_COMPLETE"}:
                m.status = "REVALIDATION_REQUIRED"
        return self.db.save(
            p,
            "baseline_updated",
            f"B{baseline.number} · {baseline.file_count} files · complete={baseline.complete}",
        )

    def resolve_obligation(
        self, project_id: str, milestone_id: str, obligation_id: str, resolved: bool, note: str
    ):
        p = self.db.get(project_id)
        obligation = next(
            (o for o in p.milestone(milestone_id).obligations if o.id == obligation_id), None
        )
        if obligation is None:
            raise ValueError("调查义务不存在")
        if resolved and not note.strip():
            raise ValueError("请填写调查依据；勾选本身不能替代证据")
        obligation.resolved, obligation.note = resolved, note[:4000]
        return self.db.save(p, "obligation_reviewed", f"{milestone_id}/{obligation_id}: {note}")

    def start(self, project_id: str, milestone_id: str):
        self.refresh(project_id)
        p = self.db.get(project_id)
        m = p.milestone(milestone_id)
        if m.status == "VERIFIED_COMPLETE":
            raise ValueError("该节点已完成当前基线验收")
        state = readiness(p, milestone_id)
        if not state["safe_to_execute"]:
            p.metrics["blocked_attempts"] += 1
            self.db.save(p, "execution_blocked", "; ".join(state["blockers"]))
            raise ValueError("; ".join(state["blockers"]))
        m.pinned_baseline = p.baseline.id
        m.lease_active = True
        m.status = "IN_PROGRESS"
        return self.db.save(p, "execution_started", milestone_id)

    def release(self, project_id: str, milestone_id: str):
        p = self.db.get(project_id)
        m = p.milestone(milestone_id)
        if m.status not in {"IN_PROGRESS", "REVALIDATION_REQUIRED", "AWAITING_ACCEPTANCE"}:
            raise ValueError("只有在途或待重验证节点可以释放")
        m.status, m.pinned_baseline = "PLANNED", None
        m.lease_active = False
        for request in p.acceptance_requests:
            if request.milestone_id == m.id:
                request.consumed = True
        return self.db.save(p, "execution_released", milestone_id)

    def positions(self, project_id: str, positions: dict):
        p = self.db.get(project_id)
        for mid, pos in positions.items():
            m = next((n for n in p.source_milestones if n.id == mid), None) or p.milestone(mid)
            m.position = {"x": float(pos["x"]), "y": float(pos["y"])}
        return self.db.save(p, "layout_updated")
