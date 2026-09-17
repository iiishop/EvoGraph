"""Evidence-based investigation and bounded verification, without granting full acceptance."""

import hashlib
from dataclasses import asdict

from ..domain.models import LightCheck
from ..infrastructure.repository import root_path, snapshot
from ..infrastructure.verifier import run_verifier
from ..verification import candidates


class AssuranceService:
    def __init__(self, db, execution):
        self.db, self.execution = db, execution

    def investigate(self, ctx, milestone_id, obligation_id, paths, conclusion):
        p = self.db.get(ctx.project_id)
        if not p.baseline:
            raise ValueError("请先读取基线")
        root = root_path(p.repository)
        for name in paths:
            path = (root / name).resolve()
            if not path.is_relative_to(root) or name not in ctx.receipts:
                raise ValueError("只能引用本轮 read_repository_file 实际读取的文件")
            if hashlib.sha256(path.read_bytes()).hexdigest() != ctx.receipts[name]:
                raise ValueError("调查文件发生变化，请重新调查")
        current = snapshot(p.repository, 0)
        if current.fingerprint != p.baseline.fingerprint or not current.complete:
            raise ValueError("基线已变化或不完整，请刷新后重新调查")
        obligation = next(
            (o for o in p.milestone(milestone_id).obligations if o.id == obligation_id), None
        )
        if obligation is None:
            raise ValueError("调查项不存在")
        obligation.resolved = True
        obligation.investigator = "agent"
        obligation.fingerprint = p.baseline.fingerprint
        obligation.note = conclusion + "\n源码依据：" + ", ".join(paths)
        self.db.save(p, "agent_investigation", f"{milestone_id}/{obligation_id}: {obligation.note}")
        return {"node_ids": [milestone_id], "effect": "updated"}

    def catalog(self, project_id, milestone_id):
        p = self.db.get(project_id)
        return [asdict(c) for c in candidates(root_path(p.repository), p.milestone(milestone_id))]

    def run(self, ctx, milestone_id, candidate_id, rationale):
        if ctx.verification_milestone != milestone_id:
            raise ValueError("请点击该里程碑的「Agent 轻量验收」按钮授权本轮检查")
        self.execution.refresh(ctx.project_id)
        p = self.db.get(ctx.project_id)
        options = candidates(root_path(p.repository), p.milestone(milestone_id))
        selected = next((c for c in options if c.id == candidate_id), None)
        if selected is None:
            raise ValueError("检查项已失效，请重新发现检查工具")
        root = root_path(p.repository)
        if any(
            name not in ctx.receipts
            or hashlib.sha256((root / name).read_bytes()).hexdigest() != ctx.receipts[name]
            for name in selected.files
        ):
            raise ValueError("请先读取所选测试或脚本定义，确认它实际覆盖的范围")
        ctx.checks_run += 1
        result = run_verifier(p.repository, selected.command, timeout=60)
        after = snapshot(p.repository, 0)
        if (
            after.fingerprint != p.baseline.fingerprint
            or not after.complete
            or not p.baseline.complete
        ):
            result["result"] = "ERROR"
            result["output"] = (
                "检查期间源码变化或基线不完整，结果不适用于当前基线。\n" + result["output"]
            )
        check = LightCheck(
            milestone_id=milestone_id,
            baseline_id=p.baseline.id,
            fingerprint=p.baseline.fingerprint,
            kind=selected.id,
            rationale=rationale,
            command=selected.command,
            **result,
        )
        p.light_checks.append(check)
        self.db.save(p, "light_check_finished", f"{milestone_id}: {check.result}")
        return {
            "node_ids": [milestone_id],
            "effect": "updated",
            "report": check.model_dump(),
            "limitation": selected.limitation + "；不会改变正式验收状态",
        }

    def handoff(self, project_id: str, milestone_id: str):
        self.execution.refresh(project_id)
        p = self.db.get(project_id)
        m = p.milestone(milestone_id)
        return {
            "schema": "evograph.verification.v1",
            "project_id": p.id,
            "repository": p.repository,
            "baseline": p.baseline.model_dump(),
            "milestone": m.model_dump(),
            "behaviors": [b.model_dump() for b in p.behaviors if b.id in m.behavior_revision_ids],
            "architecture": p.architectures[-1].model_dump() if p.architectures else None,
            "checks": [c.model_dump() for c in p.light_checks if c.milestone_id == milestone_id],
        }

    def import_report(
        self,
        project_id: str,
        milestone_id: str,
        fingerprint: str,
        output: str,
        result: str,
        provider: str,
    ):
        """External agents can submit evidence through the shared command surface.

        External reports remain reviews: trust policy/full acceptance stays with the
        integrating agent, rather than treating an arbitrary JSON PASS as proof.
        """
        self.execution.refresh(project_id)
        p = self.db.get(project_id)
        p.milestone(milestone_id)
        if not p.baseline or fingerprint != p.baseline.fingerprint:
            raise ValueError("外部报告的基线不匹配")
        if result not in {"PASS", "FAIL", "ERROR"} or not output.strip() or not provider.strip():
            raise ValueError("请提供有效的外部验收报告")
        check = LightCheck(
            milestone_id=milestone_id,
            baseline_id=p.baseline.id,
            fingerprint=fingerprint,
            kind="external:" + provider[:100],
            result="REVIEW",
            rationale="外部 Agent 报告，待集成方验证证据",
            output=f"声明结果：{result}\n{output[:30000]}",
        )
        p.light_checks.append(check)
        self.db.save(p, "external_check_received", check.id)
        return check
