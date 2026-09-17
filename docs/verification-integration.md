# 外部验收集成

EvoGraph 负责流程，不执行仓库测试。HTTP 的 POST /api/command 与桌面桥接共用命令注册表。

1. baseline.refresh 检查基线，milestone.start 校验前置条件并领取、锁定资源。
2. implementation.export 返回 prompt，用户复制给外部制作 Agent。
3. 制作结束调用 verification.export，刷新基线并生成一次性 request_id 与验收 prompt，进入 AWAITING_ACCEPTANCE。
4. 外部 Agent 按行为检查后，用户粘贴 JSON，通过 verification.import 导入。
5. 全部条目 PASS 才生成外部 Evidence、完成任务并释放资源；FAIL/ERROR 保留领取状态，返回制作阶段。

导入参数：
```json
{
  "project_id": "项目 ID",
  "milestone_id": "M01",
  "report": {
    "request_id": "提示词中的请求 ID",
    "provider": "外部 Agent 名称",
    "summary": "结论与覆盖限制",
    "checks": [{"behavior_id": "行为修订 ID", "result": "PASS", "method": "实际检查方式", "evidence": "真实结果"}]
  }
}
```

每条行为必须恰好覆盖一次。报告绑定基线、行为修订、架构版本及任务，重放、释放后的请求和过期契约均拒绝。
重新生成提示词会作废前一次请求。验收期间修改源码后必须重新生成提示词并验收。
报告是外部提供的证据声明，EvoGraph 校验归属、新鲜度、结构与覆盖，不证明报告真实性。

将其他完整验收 Agent 接入时，复用这两个 verification 命令即可，不需要修改节点组件或运行器。
旧 LightCheck 数据保留以兼容历史项目；本地轻量检查、视觉检查和正式命令执行入口已移除。
