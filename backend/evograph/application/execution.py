import asyncio

from ..domain.models import Evidence
from ..domain.policies import readiness
from ..infrastructure.repository import snapshot
from ..infrastructure.verifier import run_verifier
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
        if m.status not in {"IN_PROGRESS", "REVALIDATION_REQUIRED"}:
            raise ValueError("只有在途或待重验证节点可以释放")
        m.status, m.pinned_baseline = "PLANNED", None
        m.lease_active = False
        return self.db.save(p, "execution_released", milestone_id)

    async def verify(self, project_id: str, milestone_id: str, command: list[str]):
        # Pin verification to the latest filesystem state, then reject drift during the run.
        self.refresh(project_id)
        p = self.db.get(project_id)
        m = p.milestone(milestone_id)
        if m.status not in {"IN_PROGRESS", "REVALIDATION_REQUIRED"}:
            raise ValueError("请先领取里程碑")
        if not p.baseline.complete:
            raise ValueError("扫描未覆盖完整仓库，不能创建有效验收证据")
        # Revalidation may verify an old prerequisite; resource conflicts still matter.
        state = readiness(p, milestone_id)
        if state["conflicts"] or any(not o.resolved for o in m.obligations):
            raise ValueError("请先解决资源冲突和调查义务")
        baseline = p.baseline
        result = await asyncio.to_thread(run_verifier, p.repository, command)
        after = await asyncio.to_thread(snapshot, p.repository, len(p.baselines) + 1)
        if (
            after.fingerprint != baseline.fingerprint
            or after.commit != baseline.commit
            or not after.complete
        ):
            result["result"] = "ERROR"
            result["output"] = (
                "验证过程中仓库发生变化或扫描不完整；结果不能支持当前基线。\n" + result["output"]
            )
        p.evidence.append(
            Evidence(
                milestone_id=milestone_id,
                behavior_revision_ids=m.behavior_revision_ids,
                baseline_id=baseline.id,
                fingerprint=baseline.fingerprint,
                architecture_revision=m.architecture_revision,
                command=command,
                **result,
            )
        )
        p.metrics["verification_seconds"] += result["duration"]
        m.status = "VERIFIED_COMPLETE" if result["result"] == "PASS" else "REVALIDATION_REQUIRED"
        m.lease_active = result["result"] != "PASS"
        m.pinned_baseline = baseline.id
        # CAS rejects any concurrent update. It never blesses a result against a newer plan.
        self.db.save(p, "verification_finished", f"{milestone_id}: {result['result']}")
        self.refresh(project_id)
        return result

    def positions(self, project_id: str, positions: dict):
        p = self.db.get(project_id)
        for mid, pos in positions.items():
            m = next((n for n in p.source_milestones if n.id == mid), None) or p.milestone(mid)
            m.position = {"x": float(pos["x"]), "y": float(pos["y"])}
        return self.db.save(p, "layout_updated")
