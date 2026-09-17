"""Evidence-based prerequisite investigation. Execution belongs to external agents."""

import hashlib

from ..infrastructure.repository import root_path, snapshot


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
