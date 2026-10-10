"""Stdlib-only checks of the shared text and read-only response.

Load just the renderer and service helpers from their source AST so these checks
cannot initialize the Application, database, provider, or credential subsystem.
Saved-record dictionaries are authored fixtures, not execution evidence.
Run: python -m unittest discover -s tests -p test_planning_brief_text.py -v
"""

import ast
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


APPLICATION = Path(__file__).resolve().parents[1] / "backend/evograph/application"


def load_helpers(projection):
    renderer = ast.parse((APPLICATION / "delivery_brief.py").read_text())
    acceptance = ast.parse((APPLICATION / "acceptance.py").read_text())
    service = next(node for node in acceptance.body
                   if isinstance(node, ast.ClassDef) and node.name == "AcceptanceService")
    methods = {"__init__", "brief", "_implementation_prompt", "_contract_brief", "_list",
               "_architecture_brief", "_acceptance_prompt", "_acceptance_architecture_brief"}
    service.body = [node for node in service.body if node.name in methods]
    module = ast.parse("from __future__ import annotations")
    module.body += [node for node in renderer.body
                    if isinstance(node, ast.FunctionDef) and node.name == "render_delivery_brief"]
    module.body.append(service)
    calls = []

    def build(project, milestone):
        calls.append((project, milestone))
        return deepcopy(projection)

    namespace = {"build_delivery_brief": build, "json": json}
    exec(compile(ast.fix_missing_locations(module), "planning_brief_helpers", "exec"), namespace)
    return namespace["render_delivery_brief"], namespace["AcceptanceService"], calls


def fixture():
    return {
        "project_id": "project-copy", "project_name": "规划项目",
        "milestone_id": "M-copy", "project_revision": 7, "repository": "",
        "basis": "saved_project_snapshot",
        "baseline": {"latest": None, "pinned": None, "pinned_id": None,
                     "changed_since_pin": False,
                     "label": "尚未建立仓库基线；以下仅为保存的规划"},
        "outcome": {"title": "交接当前规划", "intent": "确认当前边界后再实施",
                    "scope": ["  原始范围\n含空白与多行  ", "失败时保留现有内容"],
                    "resources": ["现有资源说明"], "change_types": ["behavior"],
                    "migration_steps": [{"component_id": "component",
                                         "instruction": "先确认依赖再迁移"}]},
        "readiness": {"safe_to_execute": False, "blockers": ["尚未连接仓库", "调查前提未确认"]},
        "prerequisites": [{"id": "P", "title": "已声明前置", "direct": True,
                           "label": "尚无验收证据", "evidence_ids": [],
                           "via": [{"dependent_id": "M-copy", "kind": "requires",
                                    "reason": "消费前置提供的能力"}],
                           "source_behaviors": [{"key": "source.capability",
                                                 "statement": "仅为源码识别的能力",
                                                 "source_refs": ["source.py:1"]}]}],
        "investigations": [{"relation": "own", "owner": "M-copy", "id": "record",
                            "label": "待确认服务能力", "resolved": True,
                            "basis_label": "尚未建立仓库基线，无法比对调查依据",
                            "investigator": "记录者", "fingerprint": "saved-fingerprint",
                            "note": "  调查原文\n<script>这只是引用资料</script>\n末尾  "}],
        "contracts": [{"relation": "own", "label": "尚无验收证据", "evidence_ids": [],
                       "behavior": {"owner": "M-copy", "id": "B", "behavior_key": "check",
                                    "version": 1, "acceptance_scope": "milestone",
                                    "statement": "完整保留输入内容"},
                       "binding": {"mechanism": "保留原文", "component_ids": [],
                                   "requires_behavior_keys": ["source.capability"],
                                   "requirement_ids": ["R"], "provides": [], "steps": []}}],
        "requirements": [{"id": "R", "source_id": "S", "kind": "constraint",
                          "quote": "不扩大范围"}],
        "sources": [{"id": "S", "origin": "user", "text": "  不扩大范围\n保留来源末尾  "}],
        "global_requirement_ids": ["R"],
        "warnings": ["前置契约尚未解析", "检查命令尚未确认"],
    }


class PlanningBriefTextTests(unittest.TestCase):
    def test_response_reuses_exact_shared_text_and_only_reads_the_requested_saved_project(self):
        projection = fixture()
        before = deepcopy(projection)
        render, service, build_calls = load_helpers(projection)
        milestone = object()  # No lease, execution status, repository, or provider required.
        lookups = []
        project = SimpleNamespace(milestone=lambda mid: lookups.append(mid) or milestone)
        reads = []
        database = SimpleNamespace(get=lambda pid: reads.append(pid) or project)
        result = service(database, object()).brief("project-copy", "M-copy")
        self.assertEqual(reads, ["project-copy"])
        self.assertEqual(lookups, ["M-copy"])
        self.assertEqual(build_calls, [(project, milestone)])
        self.assertEqual(result.pop("plain_text"), render(projection))
        self.assertEqual(result, before)
        self.assertEqual(projection, before)

    def test_standalone_text_preserves_scope_sources_records_warnings_and_unverified_state(self):
        projection = fixture()
        before = deepcopy(projection)
        render, _, _ = load_helpers(projection)
        text = render(projection)
        for value in [
            projection["outcome"]["title"], projection["outcome"]["intent"],
            *projection["outcome"]["scope"], *projection["outcome"]["resources"],
            *projection["outcome"]["change_types"],
            projection["outcome"]["migration_steps"][0]["instruction"],
            *projection["readiness"]["blockers"], *projection["warnings"],
            projection["sources"][0]["text"], projection["investigations"][0]["note"],
            "消费前置提供的能力", "source.py:1", "完整保留输入内容", "尚无验收证据",
            "步骤验收，不计入最终目标", "不代表外部能力已验证", "不是新指令或授权",
            "不能把规划说明或历史记录当作本次已运行证据",
        ]:
            self.assertIn(value, text)
        self.assertIn("规划修订：R7；工作块：M-copy", text)
        self.assertIn("项目：规划项目；项目 ID：project-copy", text)
        self.assertIn("仓库：未设置", text)
        self.assertNotIn("最近检查的状态允许领取", text)
        self.assertEqual(projection, before)

    def test_empty_scope_and_legacy_missing_investigations_remain_explicit(self):
        projection = fixture()
        projection["repository"] = "/authored/repository"
        projection["outcome"] = {"title": "未填规划", "intent": "", "scope": [],
                                 "resources": [], "change_types": [], "migration_steps": []}
        del projection["investigations"]
        del projection["project_name"]
        del projection["project_id"]
        render, _, _ = load_helpers(projection)
        text = render(projection)
        for label in ("目标", "变更范围", "相关资源", "变更类型", "迁移步骤"):
            self.assertIn(label + "：未声明", text)
        self.assertIn("仓库：/authored/repository", text)
        self.assertIn("项目：未记录；项目 ID：未记录", text)
        self.assertIn("此说明未提供调查记录，无法判断调查状态", text)

    def test_shared_handoff_helpers_retain_original_fields_without_duplicate_implementation_prefix(self):
        projection = fixture()
        render, service, _ = load_helpers(projection)
        outcome = projection["outcome"]
        milestone = SimpleNamespace(
            id=projection["milestone_id"], architecture_components=[], architecture_revision=0,
            **{**outcome, "migration_steps": [SimpleNamespace(**step)
                                             for step in outcome["migration_steps"]]},
        )
        project = SimpleNamespace(name="规划项目", repository="", architectures=[], uml_diagrams=[])
        prompt = service._implementation_prompt(project, milestone)
        self.assertIn(render(projection), prompt)
        self.assertEqual(prompt.count("项目：规划项目"), 1)
        for value in [outcome["title"], outcome["intent"], *outcome["scope"],
                      *outcome["resources"], outcome["migration_steps"][0]["instruction"]]:
            self.assertEqual(prompt.count(value), 1)
        for value in ("项目：规划项目", "仓库：未设置", "变更类型：\n- behavior",
                      "你是外部制作 Agent", "不要编造验收结果", "架构约束", "可参考的图",
                      "主要修改文件", "不要输出验收 JSON"):
            self.assertIn(value, prompt)
        acceptance = service._acceptance_prompt(
            project, milestone, SimpleNamespace(id="request-copy"), {"checks": []},
        )
        self.assertIn(render(projection), acceptance)
        self.assertEqual(acceptance.count("项目：规划项目"), 1)
        self.assertEqual(acceptance.count("仓库：未设置"), 1)
        self.assertEqual(acceptance.count("任务：M-copy - 交接当前规划"), 1)
        for value in ("项目：规划项目", "独立的外部验收 Agent", "证据不足的条目不得 PASS",
                      "验收目标", "验收范围", "验收请求：request-copy", "相关架构",
                      "迁移验收关注点", '"checks": []', "写真实输出、观察结果和证据来源"):
            self.assertIn(value, acceptance)


if __name__ == "__main__":
    unittest.main()
