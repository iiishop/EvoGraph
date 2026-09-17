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
| 行为修订 | 稳定的行为 key 加一份带版本的断言。改断言会生成新修订，旧修订留在历史里 |
| 基线 | 仓库快照：文件数、git commit，以及覆盖相对路径与文件内容的 sha256 指纹 |
| 证据 | 本地命令的执行结果，绑定到某个里程碑、该里程碑的行为修订，以及某一条基线 |
| 当前证据 | 基线 id 与指纹都与最新基线一致、且结果为 PASS 的证据。其余都是过期的 |

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
npm install
uv run python run.py
```

启动时会自己检查前端：`dist/` 缺失，或者 `frontend/` 下的源码比 `dist/` 新，就自动跑一次 `npm run build`（约 15 秒）再启动。改完前端不必再手动 build。构建失败不会挡住启动：会打印 npm 的输出并沿用现有的 `dist/`，只有 `dist/` 也不存在时才退出。

两种启动方式：

```bash
uv run python run.py            # 桌面窗口，用 pywebview 打开
uv run python run.py --browser  # 本地 HTTP 服务，http://127.0.0.1:8765
```

首次启动会自动建一个示例项目（认证工作台）：一份七个里程碑的注册登录计划，刻意一个都没执行，所以验收取值从 0/7 开始。它的用途是拿来点开看、拆开研究，不是拿来相信的。

其他参数：`--port`（默认 `8765`）、`--data-dir`（默认 `~/.evograph`，或环境变量 `EVOGRAPH_DATA_DIR`）、`--build`（启动前强制重建前端）、`--no-build`（跳过前端检查）。项目状态、事件与证据都存在该目录下的 `evograph.sqlite3`。

桌面模式说明：pywebview 需要一个 WebView 后端，它的安装文档要求 Linux 用户显式选一个，例如 `pip install "pywebview[gtk]"`；Windows 用 WebView2，macOS 用系统自带的 WebKit。浏览器模式和桌面窗口是同一个应用，只是走本地 HTTP，所以不需要这些。

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
