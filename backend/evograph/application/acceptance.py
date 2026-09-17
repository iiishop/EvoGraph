"""External-agent handoff contracts. This service never executes repository code."""

import json

from ..domain.models import AcceptanceReport, AcceptanceRequest, Evidence
from ..domain.policies import readiness


class AcceptanceService:
    def __init__(self, db, execution):
        self.db, self.execution = db, execution

    def implementation(self, project_id: str, milestone_id: str):
        p = self.db.get(project_id)
        m = p.milestone(milestone_id)
        if not m.lease_active:
            raise ValueError("请先检查前置条件并领取任务")
        contract = self._context(p, m)
        return {
            "prompt": "你是外部制作 Agent。仅实现以下里程碑，遵守范围、架构和迁移步骤。"
            "完成后报告变更文件与已知限制；正式验收由另一次外部验收完成。\n"
            + json.dumps(contract, ensure_ascii=False, indent=2)
        }

    @staticmethod
    def _context(p, m):
        return {
            "project": p.name,
            "repository": p.repository,
            "milestone": m.model_dump(),
            "baseline": p.baseline.model_dump() if p.baseline else None,
            "behaviors": [b.model_dump() for b in p.behaviors if b.id in m.behavior_revision_ids],
            "architecture": p.architectures[-1].model_dump() if p.architectures else None,
            "uml": [
                d.model_dump()
                for d in {d.id: d for d in p.uml_diagrams}.values()
                if d.kind == "class" or m.id in d.milestone_ids
            ],
        }

    def prepare(self, project_id: str, milestone_id: str):
        p = self.execution.refresh(project_id)
        m = p.milestone(milestone_id)
        if not m.lease_active or m.status == "VERIFIED_COMPLETE":
            raise ValueError("请先领取任务，完成制作后再准备验收")
        if not p.baseline or not p.baseline.complete or not m.behavior_revision_ids:
            raise ValueError("需要完整基线和明确的行为验收契约")
        if readiness(p, m.id)["conflicts"]:
            raise ValueError("请先解决资源冲突")
        for old in p.acceptance_requests:
            if old.milestone_id == m.id:
                old.consumed = True
        request = AcceptanceRequest(
            milestone_id=m.id,
            baseline_id=p.baseline.id,
            fingerprint=p.baseline.fingerprint,
            behavior_revision_ids=m.behavior_revision_ids,
            architecture_revision=m.architecture_revision,
        )
        p.acceptance_requests.append(request)
        m.status = "AWAITING_ACCEPTANCE"
        m.pinned_baseline = p.baseline.id
        self.db.save(p, "acceptance_prepared", m.id)
        template = {
            "request_id": request.id,
            "provider": "填写外部 Agent 名称",
            "summary": "填写整体结论与未覆盖限制",
            "checks": [
                {
                    "behavior_id": bid,
                    "result": "ERROR",
                    "method": "填写实际检查方式或运行命令",
                    "evidence": "填写真实输出、观察和依据",
                }
                for bid in m.behavior_revision_ids
            ],
        }
        return {
            "request_id": request.id,
            "prompt": "你是独立的外部验收 Agent。依据以下契约逐项检查实际仓库，选择有代表性的"
            "自动化或人工/视觉检查。不要修改产品代码；若必须修复，返回 FAIL，修复后由用户"
            "重新生成验收提示词。未检查、受阻或缺少证据的条目不得 PASS。"
            "最终仅返回符合模板的 JSON，供用户粘贴到 EvoGraph；不要将报告写入被验收仓库。"
            "EvoGraph 会检查仓库是否变化，并且只有所有行为 PASS 才完成任务、释放资源。\n\n"
            + json.dumps(self._context(p, m), ensure_ascii=False, indent=2)
            + "\n\n报告模板：\n"
            + json.dumps(template, ensure_ascii=False, indent=2),
        }

    def import_report(self, project_id: str, milestone_id: str, report: AcceptanceReport):
        p = self.execution.refresh(project_id)
        m = p.milestone(milestone_id)
        request = next((r for r in p.acceptance_requests if r.id == report.request_id), None)
        if not request or request.milestone_id != m.id:
            raise ValueError("报告不属于此任务的验收请求")
        if request.consumed:
            raise ValueError("此验收请求已处理或已作废，请重新生成验收提示词")
        if not m.lease_active or m.status != "AWAITING_ACCEPTANCE":
            raise ValueError("任务不在等待验收状态，请重新生成验收提示词")
        if (
            not p.baseline.complete
            or request.baseline_id != p.baseline.id
            or request.fingerprint != p.baseline.fingerprint
            or request.behavior_revision_ids != m.behavior_revision_ids
            or request.architecture_revision != m.architecture_revision
        ):
            raise ValueError("基线或验收契约已变化，请重新生成验收提示词并重新验收")
        ids = [c.behavior_id for c in report.checks]
        if len(ids) != len(set(ids)) or set(ids) != set(request.behavior_revision_ids):
            raise ValueError("报告必须逐项覆盖全部行为，不得重复或添加其他行为")
        if (
            not report.provider.strip()
            or not report.summary.strip()
            or any(not c.method.strip() or not c.evidence.strip() for c in report.checks)
        ):
            raise ValueError("请填写真实验收方式、证据、来源与结论")
        passed = all(c.result == "PASS" for c in report.checks)
        evidence = Evidence(
            milestone_id=m.id,
            behavior_revision_ids=m.behavior_revision_ids,
            baseline_id=p.baseline.id,
            fingerprint=p.baseline.fingerprint,
            architecture_revision=m.architecture_revision,
            command=[],
            result="PASS" if passed else "FAIL",
            duration=0,
            output=report.model_dump_json(indent=2),
            provider=report.provider,
            request_id=request.id,
        )
        p.evidence.append(evidence)
        request.consumed = True
        m.status = "VERIFIED_COMPLETE" if passed else "IN_PROGRESS"
        m.lease_active = not passed
        self.db.save(p, "external_acceptance_imported", f"{m.id}: {evidence.result}")
        return {"result": evidence.result, "released": passed, "evidence_id": evidence.id}
