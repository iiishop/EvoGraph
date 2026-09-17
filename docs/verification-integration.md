# 外部验收 Agent 集成 v1

HTTP 和 pywebview 共用命令入口。HTTP 本地服务使用 `POST /api/command`，请求体为 `{ "action": "...", "params": {...} }`。桌面端使用 `pywebview.api.command(action, params)`。

## 导出任务

`verification.export` 参数：`project_id`、`milestone_id`。节点详情也提供导出 JSON 按钮。

返回 `schema=evograph.verification.v1`、仓库绝对路径、完整 baseline（id/fingerprint/commit）、里程碑、行为契约、最新架构、已执行轻量检查。集成方应在独立工作流运行完整验收，报告使用该 fingerprint。

## 导入报告

`verification.import` 参数：

```json
{
  "project_id": "实际项目 ID",
  "milestone_id": "M01",
  "fingerprint": "导出任务中的基线指纹",
  "provider": "external-agent-name",
  "result": "PASS",
  "output": "测试覆盖范围、命令、产物路径、失败与限制"
}
```

导入会刷新基线并拒绝过期 fingerprint。报告存为 `REVIEW`，显示外部声明的 PASS/FAIL/ERROR；不会信任任意 JSON 的 PASS 而自动完成里程碑。后续融合时可在 AssuranceService 中接入外部 Agent 的证据真实性与覆盖判定，复用版本/基线约束。

## 新增本地检查能力

在 `backend/evograph/verification` 添加一个模块：

```python
from . import Candidate, adapter

@adapter("my-check")
def discover(root, milestone):
    # 从真实仓库发现检查，不运行文件；files 必须列出 Agent 需先读的定义。
    return [Candidate("my-check:smoke", "Smoke", ["tool", "test"], ["test.config"], "覆盖限制")]
```

运行器自动发现并提供给 `discover_checks` / `run_light_check`，无需改设置表单、工具分派或图组件。现有 Python 适配器选择测试文件，Node 适配器发现显式 test:unit/test:smoke/typecheck/lint/test 脚本，过滤明显持续运行的 watch 模式；超时统一终止进程树。

视觉能力当前是上传截图 + 模型审阅接口，不能证明截图新鲜度或真实交互结果。完整浏览器视觉验收可由外部 Agent 提供，再用上述接口回传证据。
