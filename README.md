# EvoGraph

本地项目演化工作台：Vue 3 + Vue Flow 前端，Python + pywebview 桌面外壳，SQLite 保存项目、修订、证据和上传资料。

## 启动

需要 Python 3.11+、Node.js 20+。Windows 桌面模式需要系统 WebView2。

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[test]"
npm install
npm run build
.venv/Scripts/python.exe run.py
```

已有环境只需更新依赖、构建前端并重启应用。也可以使用 `uv sync --extra test` 管理 Python 环境。

浏览器开发模式：

```powershell
.venv/Scripts/python.exe run.py --browser --data-dir .evograph/dev
```

打开 http://127.0.0.1:8765。前端热更新另行运行 `npm run dev`。默认正式数据库位于用户目录 `.evograph/evograph.sqlite3`；`--data-dir` 可隔离测试数据。

## 使用

1. 创建项目，连接仓库，建立基线。自动生成带 **SRC** 标识的源码现状节点和静态架构视图；SRC 表示发现了源码，不代表验收通过。重复刷新不会重复创建节点或项目。
2. 在设置中配置支持**流式工具调用**的 Provider。支持 OpenAI Compatible 和 Anthropic Messages；图片理解还需要模型支持视觉输入。密钥使用操作系统密钥库。
3. 上传需求文档、参考图片，在输入框选择本轮要引用的资料；选中的资料会随发送提供给已配置的模型。支持 TXT/MD/JSON/CSV/DOCX/PDF、PNG/JPEG/WebP。单文件 8 MB，每项目 40 份，每轮最多 6 份。PDF 不含 OCR，扫描 PDF 可改传图片；DOCX/PDF 当前提取文字，不提取嵌入图。提取文本最多保留 60,000 字符，首次上下文取 20,000，Agent 可分段读取剩余文本。
4. 先描述架构、技术约束，再规划里程碑。架构页保存组件图、技术选型理由、决策和历史版本。修改架构会要求现有里程碑重新审查，旧架构证据不计入当前验收。
5. 在底部直接描述修改。每个完整工具调用会立即更新当前图；信息不足时问题显示在同一个输入框上方，继续在原输入框回答。停止时保留已经完成的修改。
6. 里程碑对应具体 PR 交付物；最终目标显示为独立标识，不是“最终验收 PR”。真实测试设施或接入工作的 PR 可以存在，但须有明确交付范围。
7. Agent 可绘制状态机、工作流、UI 结构图，并关联上传图片与里程碑。资料页和节点详情都可查看。当前 UI 图是结构示意，非像素级设计工具；不执行模型生成的 HTML/脚本。
8. 自动布局采用 ELK 分层与贝塞尔曲线连线。颜色说明依赖类型，箭头均为“前置 → 后继”；悬停查看理由。拖动会退出自动镜头跟随，右下角可恢复；更新边框和闪烁仍保留。
9. 节点详情中点击 **Agent 调查**，Agent 读取源码后记录依据；基线变化会重新打开其调查项。点击 **Agent 轻量验收**，授权 Agent 持续调查并执行仓库中实际发现的相关检查，单项 60 秒超时。Agent 必须先读测试或脚本定义，再选择实际发现的检查，说明覆盖范围。结果单独记录，不把简单检查误认为正式完成。高级手动正式验收仍可展开使用。
10. 设置页可启用 Tavily（搜索和正文提取）或 SearXNG（搜索，服务端需开启 JSON 格式）。密钥只保存在系统凭据库。网页来源记录时间、链接和摘录，架构通过来源 ID 引用；不会静默切换搜索服务。
11. 支持视觉的模型可在设置中启用截图审阅，然后在输入区选择真实截图并要求检查。当前是上传截图的视觉审阅，不会自动启动浏览器、截屏或执行视觉交互测试。
12. 删除项目会从列表移出，可通过提示中的撤销恢复，不删除仓库文件。

## 验证与扩展

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check backend tests
npm run build
npm run format:check
npm test
```

结构与扩展方式见 [工程设计](docs/architecture.md)。测试 Provider 在 `tests/fixtures/streaming_provider.py`，仅供隔离数据目录的协议回归，不是默认模型或产品降级路径。

外部验收 Agent 接口见 [验收集成协议](docs/verification-integration.md)。源码视图目前是有界的目录组件与静态导入分析（最多 1,200 个源码文件、30 个组件、100 条边），不声称还原动态调用、运行时部署或所有路径别名。设计架构可在此基础上由 Agent 进一步维护。

本版是规划、架构维护与证据管理工作台，不包含自动编码、自动创建 PR 或自动合并。结构校验不能证明依赖的业务必要性；这些仍需设计审查。仓库基线采用有上限的文件扫描，扫描不完整时不认定验收通过。
