<h1 align="center">EvoGraph</h1>

<p align="center">
  <strong>只看证据的项目演化规划器。</strong>
</p>

<p align="center">
  把项目拆成一张里程碑图，每个里程碑带着可验证的行为断言，<br/>
  而一次验证只有在仓库指纹没变的前提下才算数。
</p>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img alt="Status: Alpha" src="https://img.shields.io/badge/status-alpha-f4b942" />
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" />
  <img alt="Vue 3" src="https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white" />
  <img alt="Runtime: local first" src="https://img.shields.io/badge/runtime-local--first-1756d1" />
  <img alt="Acceptance: evidence gated" src="https://img.shields.io/badge/acceptance-evidence--gated-0f7d65" />
  <img alt="GitHub stars" src="https://img.shields.io/github/stars/iiishop/EvoGraph?style=flat-square" />
</p>

---

## 为什么要做这个

产出一个"看起来能跑"的实现已经很便宜了，判断它到底成不成立依然很贵。

模型可以用一句话写下"已完成"，而这句话里没有任何东西能证明它是真的。把 diff 读得更仔细也不解决问题：agent 碰过的部分越多，你没读过的部分就越多。最后手里剩下的，是一个你相信的计划，和一堆你在猜的代码。

EvoGraph 把判断权从对话里拿出来。计划是一张里程碑图，每个里程碑拥有自己的可验证行为；验收契约是一个目标版本，列出必须成立的行为；而唯一能把一个行为标成完成的，是你在自己认可的仓库快照上亲手跑过的那条验证命令。

代码之后只要再动，证据就过期。当时通过的那个东西，已经不再是现在存在的那个东西，所以里程碑退回待重验证，而不是继续挂着绿色。

## 核心概念

| 概念 | 含义 |
|---|---|
| 目标版本 | 验收契约：目标陈述，加上必须成立的行为集合 |
| 里程碑 | 一个可独立验证的演化单元：范围、资源、变更类别、前置依赖边 |
| 行为修订 | 稳定的行为 key 加一份带版本的断言与验收范围。修改断言或范围都会生成新修订，旧修订留在历史里 |
| 基线 | 仓库快照：文件数、git commit，以及覆盖相对路径与文件内容的 sha256 指纹 |
| 证据 | 本地命令的执行结果，绑定到某个里程碑、该里程碑的行为修订，以及某一条基线 |
| 当前证据 | 基线 id 与指纹都与最新基线一致、且结果为 PASS 的证据。其余都是过期的 |

## 看清每轮实际改了什么

在统一输入框描述新目标、约束或方向变化，Agent 会判断需要调整哪些里程碑、依赖和架构。需要明确上下文时，可用 `#` 选择计划/SRC 里程碑或架构组件，用 `@` 选择项目资料或已连接仓库内的安全文件。选中后生成绑定项目和稳定 ID 的行内引用；普通 `#`/`@` 字符仍是文本，引用也不限定 Agent 只能修改这些对象。

发送前会按当前项目解析真实名称。对象已删除、类型不符或来自其他项目时，请求会明确被拦住，待回答问题不会因此被消耗。通过菜单选择的上传资料与行内资料引用合并计算，每轮最多六份，重复引用只计一次。仓库引用只接受当前安全文件目录中的路径，不会按任意路径或网址取内容。历史消息保留当时的名称与引用身份，旧纯文本记录仍可读取。

引用目录与源码读取共用路径规则，排除 `.npmrc`、`.netrc`、`.aws`、`.ssh` 等常见认证文件/目录、已知密钥格式和符号链接。这是保守的路径限制，不保证任意源码文本都不含敏感信息。

输入框上方的「本轮变更」可展开查看新增、更新和移除的里程碑、前置依赖变化、最终验收归属变化及架构变化。摘要对比本轮开始前与收尾后已保存的项目状态，统计目标提交后的净变化，不依赖模型的文字自述，也不把中途操作次数当作最终结果。摘要会保留在演化记录中，重新打开项目仍可查看。

完成、等待回答、已停止和失败会明确区分。停止或失败不会撤销已经保存的工具修改，部分变更仍会如实显示；中断后界面等待当前轮次完成收尾，再同步最终结果。旧记录保持可读，不会编造历史摘要。这些记录说明规划的变化，不代表代码已经实现或验收通过。

## 最终目标与本步验收

路线图可以包含过渡状态。例如，第一步先把库存写入 CSV，后一步迁移到 PostgreSQL 并停止 CSV 写入。这两条要求不应该同时成为最终目标。

每条行为支持 `acceptance_scope`：

- `target`（默认）：最终交付时仍需成立，纳入最终目标契约
- `milestone`：仅用于本步或过渡阶段，仍必须在该里程碑验收，但不纳入最终目标契约

增量编辑工具和全量计划草案都支持此字段。规划迁移时应明确标记，不能仅凭图中的先后顺序推断。节点详情会标明每条验收的范围，目标栏按最新已提交目标中的行为 ID 统计。只有本步验收、没有最终目标标准时，会明确提示，不能显示为已达成。

旧项目与旧草案缺少此字段时保持 `target` 语义。修改范围会创建新的行为修订，旧目标和历史证据继续保留。增量更新未提供既有活动行为的范围时，保留它当前的范围。

这次区分解决的是最终目标包含哪些要求。它不会跳过本步验收、使旧证据重新有效或自动解除历史前置约束：里程碑仍需验证全部行为，前置依赖仍要求当前基线证据，验收结果仍按里程碑汇总。它也不会自动发现所有语义冲突；最终目标达成不等于路线图中的每一步都已通过验收。

## 图会拦住什么

- 只允许前置依赖边。每条边都要写明理由和类型：implementation、migration 或 verification。
- 保存前先做结构检查。里程碑 id 重复、同一个行为 key 被两个里程碑占用、变更类别不存在、依赖缺少理由、依赖成环，全部拒绝。
- 调查义务由变更类别生成。general、api、data、auth 各带一份清单：api 会问谁在消费这个接口、契约是否还兼容；data 会问迁移与回滚；auth 会问凭据存储和失败路径。
- 勾选义务必须写依据。只勾不写会被拒绝——勾选框记录的是"我调查过了"，它替代不了调查。
- 一个资源只能有一个租约。两个声明了同一资源的在途里程碑不能同时跑；一个没声明资源的里程碑，会和所有在途租约冲突。
- 用重验证代替悄悄漂移。刷新一次基线，所有 IN_PROGRESS 或 VERIFIED_COMPLETE 的里程碑都会掉到 REVALIDATION_REQUIRED。

## Agent 能做什么，不能做什么

| 工具 | 作用 |
|---|---|
| create_milestone、update_milestone、remove_milestone | 一次编辑一个节点，id 与行为 key 保持不变 |
| add_dependency、remove_dependency | 增删前置依赖边，必须给理由 |
| set_target | 修改目标陈述 |
| read_project | 读取当前目标、里程碑、行为修订与基线 |
| inspect_repository | 有上限的文件清单，外加 README 与清单文件摘录 |
| read_repository_file | 读一个源码或测试文件：每轮最多六个，密钥文件与符号链接一律拒绝 |
| ask_user | 意图不足以支撑可靠修改时，停下来提问 |

每轮给 12 次模型往返、24 次工具调用、6 个源文件，用完即止；连续两轮没有有效进展也会结束本轮。

没有 shell 工具，也没有写文件的工具。Agent 改的是规划数据，读的是源码。它跑不了你的测试，也没法跟你说它跑过。仓库内容和被引用的文本一律按不可信数据处理，从不当作指令。

## 从架构按需展开局部结构

架构工作遵循本轮请求的范围。说明「只做 PR 路线图，不用架构图」或「架构以后再做」时，Agent 提示词要求继续路线图规划，不把架构作为前置条件；已有架构、设计图和组件映射会保留，跳过架构不等于要求删除。明确要求架构，或未限制范围且实际设计需要时，仍可正常维护架构。这是模型指令约束，不是关键词拦截，也不保证模型每次都遵循。

新里程碑确实不属于任何已有组件时，可将组件映射留空，作为「待关联」的评审提示，不必虚构组件或强行关联无关组件。增量编辑与全量计划替换中，空值或省略映射都会保留该里程碑已有的非空关联；未知组件 ID 仍会被拒绝。架构一致性调查义务和验收证据规则不因此放宽。

架构更新会替换完整的摘要、技术列表和图。省略决策、质量场景、风险或研究引用时，保留原值；显式传入列表会替换对应内容，空列表表示清空。退役条目仅描述本次版本移除的组件；当前或历史退役记录的里程碑缺失时，评审会给出提示，不擅自判断其已经完成、取消或仍待执行。

生成指导要求职责内聚、契约具体，复杂度要符合实际负载和维护约束，不强制分层、拆服务或套用设计模式。这些指令不证明模型设计质量；[真实模型评测流程](docs/architecture-quality-evaluation.md)将基于证据的架构判断与确定性完整性检查、脚本化测试分开记录。

设计评审还会在本轮收尾前，按活跃行为的实际验收范围预览最终目标成员数。把里程碑称作「可选」或放在叶子位置，不会排除其中的 target 行为；该预览仅展示真实计数供评审，不推断用户意图，也不自动免除要求。

架构图是全局模块总览。选择 1–3 个组件，必要时继续缩小到其关联文件，即可在同一工作区查看局部类结构。提取只读取选中的源码路径；导入和架构范围外的邻居保留为边界节点，不递归展开。不再生成全项目类图。

Python AST 及 TypeScript/Vue、C++ 的 tree-sitter 适配器提取声明与签名，不执行源码。每次限制 12 个文件、24 个类、160 个成员，超限需要缩小范围。静态声明不代表运行行为已经验证；缺少源码映射时不会猜测类。源码结构与目标设计分别标记 SRC / DESIGN，现有架构版本、证据、迁移和旧图归档继续保留。

## 架构

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  Workspace: Vue 3 + Vue Flow, built to dist/ and served locally          │
└──────────────┬───────────────────────────────────────────────────────────┘
               │  desktop: pywebview js_api bridge
               │  browser: POST /api/command, /api/agent/stream (NDJSON)
┌──────────────▼───────────────────────────────────────────────────────────┐
│  Application: one command surface shared by both transports              │
│  projects · settings · planning · graph · execution · agent              │
└────┬───────────────┬─────────────────┬───────────────────┬───────────────┘
     │               │                 │                   │
     ▼               ▼                 ▼                   ▼
┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐
│ SQLite   │  │ Baseline     │  │ Verifier    │  │ Provider          │
│ projects │  │ scanner      │  │ runs the    │  │ OpenAI-compatible │
│ events   │  │ sha256 over  │  │ command you │  │ Anthropic         │
│ evidence │  │ repo files   │  │ approve     │  │ Messages          │
└──────────┘  └──────────────┘  └─────────────┘  └───────────────────┘
```

## 一个里程碑是怎么变成已验证的

1. 建项目，指向一个本地仓库路径。
2. 和规划助手讨论目标。返回的是一份结构化 JSON 草案：里程碑、各自的范围、行为断言、资源、带类型的依赖。结构由机器检查，语义是否充分仍然要你自己判断。
3. 应用草案。里程碑进入图里，同时记录一个目标版本，写明必须成立的行为。
4. 领取一个里程碑。先做就绪检查：前置在当前基线上的证据、已解决的调查义务、完整的基线、没有资源冲突。被拦下的尝试会记进事件，不会放行。
5. 跑你自己的命令。PASS 会作为证据存下来，绑定基线指纹和该里程碑的行为修订，里程碑变为 VERIFIED_COMPLETE。

在第 4 步和第 5 步之间改动仓库，或者在第 5 步执行期间改动，结果都不被接受。

## 快速开始

环境要求：

- Python 3.11 或更新
- Node.js 与 npm，用来构建前端
- 推荐用 uv；仓库自带 `uv.lock`

```bash
git clone https://github.com/iiishop/EvoGraph.git
cd EvoGraph
npm ci
uv run evograph
```

启动时会自己检查前端：`dist/` 缺失，或者 `frontend/` 下的源码比 `dist/` 新，就自动跑一次 `npm run build`（约 15 秒）再启动。改完前端不必再手动 build。构建失败不会挡住启动：会打印 npm 的输出并沿用现有的 `dist/`，只有 `dist/` 也不存在时才退出。

uv 只管理 Python 依赖。拉取包含前端依赖变更的代码后，请在仓库根目录运行 `npm ci`，再启动程序。这会按照已提交的锁文件替换 `node_modules/`，不会改动 EvoGraph 已保存的项目。启动器会在构建前检查 npm 依赖是否缺失或过期，并给出安装命令，不会自动安装。

如果构建仍然失败，可以在同一目录直接运行 `npm run build` 查看原因。启动器也会打印完整的合并输出，避免 npm 配置警告遮住真正的 TypeScript 错误。Windows PowerShell 同样适用，依次运行即可：

```powershell
npm ci
npm run build
uv run evograph
```

两种启动方式：

```bash
uv run evograph            # 自动识别系统，打开桌面窗口
uv run evograph --browser  # 本地 HTTP 服务，http://127.0.0.1:8765
```

Linux、Windows、macOS 统一使用 `uv run evograph`。uv 会自动安装对应平台的 Python 依赖：Linux 默认 Qt/PySide6，Windows 和 macOS 使用 pywebview 原生后端，无需额外运行 `uv sync` 或添加 `--extra linux`、`--gui qt`。旧的 `--extra linux` 命令仍然兼容。图形会话、系统库、可写数据目录和安全密钥存储要求见 [Linux 安装说明](docs/linux.md)。


首次启动会自动建一个示例项目（认证工作台）：一份七个里程碑的注册登录计划，刻意一个都没执行，所以验收取值从 0/7 开始。它的用途是拿来点开看、拆开研究，不是拿来相信的。

其他参数：`--port`（默认 `8765`）、`--data-dir`（默认 `~/.evograph`，或环境变量 `EVOGRAPH_DATA_DIR`）、`--build`（启动前强制重建前端）、`--no-build`（跳过前端检查）。项目状态、事件与证据都存在该目录下的 `evograph.sqlite3`。

桌面模式说明：Windows 需要 WebView2 运行时，macOS 使用系统 WebKit，Linux 仍需要真实图形会话和 Qt 原生系统库。启动器不会安装系统包、修改安全设置或配置密钥存储。手动配置 GTK 后可以用 `--gui gtk`；`--gui qt` 也仍然可用。浏览器模式走本地 HTTP，不启动桌面后端。

### 本地 API

两种传输共用同一套命令面，窗口能做的事脚本都能做：

```bash
curl -X POST http://127.0.0.1:8765/api/command \
  -H 'content-type: application/json' \
  -d '{"action":"projects.list","params":{}}'
```

```json
{"ok":true,"data":[{"id":"a2cfffd56b104e2c","name":"认证工作台","is_demo":true,"milestone_count":7,"acceptance":{"passed":0,"total":7,"achieved":false}}]}
```

HTTP 传输只绑回环地址，只接受 `127.0.0.1` 和 `localhost` 的 Host，来源走白名单校验，请求体上限 1 MB。

## 配置模型服务

侧边栏打开"设置"，进入"模型服务"。

| 适配器 | 字段 | 密钥 |
|---|---|---|
| OpenAI Compatible | base_url、model | API Key |
| Anthropic Messages | base_url（默认 `https://api.anthropic.com`）、model | API Key |

密钥通过 keyring 写进系统凭据库，服务名 `EvoGraph`，并按适配器与 base_url 做作用域隔离，所以改地址不会复用旧密钥。密钥不进 SQLite，也不进日志。

规划、草案生成和 Agent 循环都要求 Provider 支持流式工具调用，两个内置适配器都支持。不配 Provider 也能用项目状态，所以示例项目在配置之前就能看。

## 验证是怎么跑的

- 命令是一个参数数组，工作目录设为仓库根，`shell=False`。最多 64 个元素，程序名不能为空。
- 退出码 0 记 PASS，非 0 记 FAIL，系统错误或超时记 ERROR。默认超时 120 秒，超时会杀掉整个进程组。
- 合并输出只保留最后 30000 个字符。
- 跑之前和跑之后各算一次仓库指纹。如果期间指纹或 git commit 变了，结果降级为 ERROR：它已经不能描述某一个固定状态了。
- 只有扫描完整的仓库才接受结果。快照上限为 10000 个文件、单文件 10 MB、合计 200 MB，超过任一条基线即为不完整，验证会被拒绝。

## 目录结构

```text
backend/evograph/
  domain/            models and deterministic policies: obligations, readiness, acceptance
  application/       use cases: projects, planning, graph editor, execution, agent runtime
  agent_tools/       the tools the model is allowed to call
  infrastructure/    SQLite store, baseline scanner, local verifier
  providers/         OpenAI-compatible and Anthropic adapters
  transport/         pywebview bridge and FastAPI app
frontend/src/        Vue 3 workspace: sidebar, milestone graph, inspector, evidence panel, agent dock
tests/               pytest suite
run.py               run from source without an install
```

## 测试

```bash
uv run --extra test pytest
```

测试覆盖命令面、计划与草案校验、Provider 流式协议、Agent 流式循环，以及项目与运行时策略。

## 当前状态

版本 0.1.0，alpha。图、基线、证据与就绪判定这套模型已经实现并有测试覆盖；桌面窗口和浏览器传输都能从源码直接跑起来。目前没有打包产物、没有安装程序、没有 CI，界面语言是中文。

## Star History

<a href="https://www.star-history.com/?type=date&repos=iiishop%2FEvoGraph">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=iiishop/EvoGraph&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=iiishop/EvoGraph&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=iiishop/EvoGraph&type=date&legend=top-left" />
 </picture>
</a>

单次生成提案的 24 个里程碑上限不再限制项目长期增长。增量编辑支持最多 256 个活跃交付里程碑，每个里程碑最多 24 条直接前置依赖。这个明确的工作集上限用于约束全图校验、传递祖先集合和未虚拟化画布布局的开销，并不代表无限扩展；达到上限后仍可修改或移除现有节点，新增前请将路线图拆分到独立项目。
