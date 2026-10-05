# Architecture-generation quality evaluation

This is a manual, real-provider evaluation protocol. Unit tests, demo projects,
scripted provider responses and an empty `review_design.findings` list do **not**
establish architecture quality. Do not report a live before/after result unless
both runs actually completed with the configured model through the application.

## Run and evidence

1. Record the exact application commit, model ID, non-secret provider endpoint,
   date and whether the connection test succeeds. Never export credentials or
   credential-store contents. Use an already authorized provider; otherwise
   pause the live run and report the connection blocker.
2. Create a fresh project through the normal application. Leave the repository
   unset for these invented greenfield cases. Enter the prompts below verbatim;
   do not seed an architecture or coach the model with the scoring rubric.
3. If a necessary question appears, record it and the answer. Use facts in the
   scenario or an explicitly recorded, plausible choice. Preserve those answers
   for the matched rerun. Do not reward unnecessary questions.
4. After each turn, retain the architecture revision (nodes, groups, edges,
   rationale, risks, quality scenarios and retirements), milestone contracts,
   model reply, actual structural findings, and a screenshot of the rendered
   architecture. Record failed/partial/stopped turns too. The source of every
   output must be clear: live model, human assessment or deterministic test.
5. Assess the saved architecture and its rationale together. A missing edge in
   the picture may be explained by a declared abstraction; a plausible picture
   may conceal contradictory ownership. Cite component IDs, relation labels,
   decision text and milestone IDs for each judgement.
6. Rerun the same cases from fresh projects after a change, using the same model
   and recorded answers. Do not manually repair output before scoring it. Also
   run the held-out case without tuning specifically to its output. Prefer
   repeated paired runs when cost permits; one pair is an example, not a general
   claim of model improvement. Record model nondeterminism and run conditions.

## Case A: a small appointment application, then growth

### A1: initial goal

> 我想给一家只有 6 位技师的小型自行车维修店做预约工具。顾客可以在手机网页上选服务和时间、留下联系方式，店员能查看和调整预约。不能把同一位技师同时排给两单。现在每天大约 40 单，只有我一个开发者，想用一个小服务器部署，暂时不收线上付款。请设计适合这个阶段的软件架构和可逐步交付的路线图。

### A2: nontrivial requirement

> 现在要加预约提醒和取消候补：预约前一天发邮件提醒；有人取消时通知候补顾客，但不要因为重复执行就发两封邮件或重复占位。邮件服务有时会超时，发送失败不能让预约数据丢掉。仍然是一家店，维护人手没增加，请调整现有设计。

### A3: scale and boundary change

> 计划让 30 家互不相关的维修店共用这个产品，约 500 位技师。店员只能看自己店的数据，同一顾客可以在不同店预约；高峰可能同时有 200 个预约请求。还是两个人维护，希望能平滑上线，旧店的预约不能丢。请评估原架构哪里需要改，保留仍合适的部分，给出迁移路线。

Review pressures, not a required implementation: scheduling invariant ownership,
atomic booking, durable reminder/candidate state, timeout-versus-failure
ambiguity, duplicate delivery/reservation handling, tenant authorization and data
isolation, migration compatibility, and operational cost. A worker or queue may
be justified; a separate service is not required. A specific database, framework,
layer count or named pattern is not the answer key.

## Case B: distinct medium-scale domain

### B1: initial goal

> 我们有 3 个仓库，约 120 位员工，每天处理 8000 张电商订单。想做退货管理：客服登记退货申请，仓库扫码收货和质检，根据结果恢复库存或隔离商品，再向外部电商平台提交退款请求。平台通知有时重复或乱序，平台停机时仓库仍要工作。6 人开发团队，现有运维熟悉 PostgreSQL，希望先作为一个产品部署。请给出架构和交付路线。

### B2: extension and failure

> 再接入第二家电商平台，它的退款状态和原平台不一样，还会在超时后实际完成退款。一个退货单可能分多次收到货。不要重复退款或错误恢复库存，客服需要看见卡在哪一步并能安全重试。请演化现有设计，说明哪些边界保持不变。

Review pressures: return/receipt/inventory/refund ownership, partial receipt,
external side-effect reconciliation, idempotency boundaries, auditability,
operator recovery, provider variation and testability. A provider adapter or
explicit state model may be useful when justified; mandatory distributed
transactions, event sourcing, CQRS or microservices are not expected.

## Held-out case C: offline-first desktop utility

### C1: initial goal

> 我想做一个离线桌面工具，帮研究人员整理本机的图片和 PDF，用标签、备注和全文搜索找资料。一次大约管理 2 万个文件，不能改动用户的原文件，索引损坏后能重建。只有一位开发者，先支持一个操作系统，不需要账号、云同步或多人协作。请给出适合它的架构与实施路线。

### C2: a different change axis

> 有些 PDF 很大或损坏，导入时不能卡住界面。现在还要支持一种新文档格式，未来可能换 OCR 实现。用户取消导入或程序崩溃后应该能继续，不要重复生成记录。请调整设计，仍保持离线和单人维护。

Review pressures: original-file safety, recoverable derived index, bounded
background work, cancellation/resume, stable identity and format/OCR variation.
No web server, authentication service or cloud data layer is implied by this goal.

## Evidence-based rubric

Score each dimension separately; do not sum into a claim of correctness. Use
`not assessed` when a run is blocked or evidence is absent. Use `not applicable`
only with a scenario-specific reason. Every score needs evidence and a concrete
consequence or uncertainty.

- **0 — material defect:** a required outcome is impossible, unsafe, contradictory
  or materially overbuilt in the presented design
- **1 — important gap:** plausible outline, but missing ownership, contract,
  failure handling or rationale prevents a confident implementation
- **2 — fit with caveats:** coherent and appropriate; bounded gaps/assumptions are
  explicit and can be resolved without replacing the design
- **3 — well supported:** coherent minimal design with concrete contracts,
  relevant failure/change reasoning and tradeoffs grounded in this scenario

Dimensions:

1. **Outcome and scope coverage.** Trace the main journey and meaningful failure
   path. Identify absent responsibilities and unrelated subsystems. Avoid counting
   feature words as proof of coverage.
2. **Cohesion and ownership.** Locate each invariant, authoritative write and
   decision. Look for catch-all managers, duplicate ownership or arbitrary
   per-screen/per-CRUD splits.
3. **Coupling and contracts.** Explain edge direction/purpose, public interfaces,
   trust boundaries and dependency direction. Trace whether a change to storage
   or external provider unnecessarily propagates across business rules. A cycle
   alone is not proof of bad coupling.
4. **Extensibility and maintainability.** Apply the actual next prompt. Compare
   changed versus preserved components and decisions. Verify useful seams and
   test boundaries; reject speculative interfaces with no current pressure.
5. **Scale and operational fit.** Relate modules, layers, deployables and shared
   infrastructure to workload, team and failure isolation. More layers or fewer
   components do not automatically mean better design.
6. **Pattern and technology rationale.** For added complexity, find the pressure,
   simplest viable alternative, tradeoff and reconsideration trigger. No named
   pattern is needed when straightforward code fits. Distinguish proposed targets
   from measurements and source facts from invented implementation.
7. **Evolution safety.** Check stable IDs/unrelated decisions, data conversion,
   compatibility, rollout, recovery and retirement ownership when relevant.
   Confirm the roadmap has concrete deliverables rather than generic layer phases.

## Deterministic checks stay separate

Record whether IDs/references resolve, groups do not duplicate membership, missing
retirement owners are surfaced without inventing completion/cancellation, omitted rationale remains intact,
explicit clearing works, and revisions/history remain immutable. These are
mechanically testable integrity properties. They do not prove cohesion, appropriate
layering, future extensibility, runtime safety or useful model judgement.

The normal review tool reports structural diagnostics plus semantic prompts.
Neither a model's self-assessment nor this rubric is production acceptance; actual
implementation still needs external verification against the user's contracts.

Scheduled generation also receives exact read-only mechanisms for one hop of
explicit prerequisites: `slice.dependencies` and `contract.requires_behavior_keys`
declared in the current unit's manifest `uses`. For a referenced saved slice,
only its active owned contracts are selected. Contract keys and revision IDs join
the existing acceptance directory; an identical same-revision mechanism already
in a full current-unit object or completed-contract context is referenced rather
than repeated. Selection IDs, coverage and unavailable or mismatched references
remain explicit, including prerequisites not saved yet. This adds no gate,
transitive traversal, owner text, history or inferred references. Exact text is
never truncated; the existing whole-request budget still decides admission.
These facts improve visibility, not proof that generated contracts are compatible.

During an agent turn, the existing review tool and automatic review also receive
a read-only `change_context` comparing the turn-start and current saved milestone
contracts. It includes eligible prerequisites across both snapshots, including
detached ancestors, and affected downstream consumers. Planned behaviors retain
their actual active revision IDs, owners and acceptance scopes; SRC observations
remain separate source inferences. Changed or removed contracts and edge reasons
are labeled as before-state facts, not current requirements.

This preview is limited to 16 node summaries, 32 changes per kind, 128 contract
or field details per kind, and 32 KiB of UTF-8 JSON. Summaries and stable active
revision references are admitted before details. Existing before summaries use
exact overrides plus explicitly absent fields, avoiding repeated unchanged
values. Added/deleted nodes retain explicit absence. Whole behavior records keep
their actual statements, owners and acceptance scopes; SRC observations stay
separate. One contract is offered per included node before further changed
acceptance/statements and node fields, then remaining unchanged details.

Coverage reports omitted nodes, contract counts and exact omitted field names;
summaries alone do not claim complete semantic context. A detail can be omitted
even when its node summary is present. Field-presence flags distinguish absence
from a saved null value. Use `read_project` for full current state; it cannot
recover omitted before-state records. Large summaries can be omitted even before
the node limit is reached. Changed edge reasons are prioritized; unchanged edge
reasons may be among omitted prerequisite-field details. Malformed duplicate SRC
keys preserve multiplicity but do not provide unique occurrence identity/order.
Repeated review of the same saved revision references
the already-emitted context rather than duplicating it. No extra model round,
semantic verdict, persisted review cache or acceptance result is introduced.
These limits and factual coverage are regression-testable; improved semantic
judgement still requires the paired real-provider evaluation described above.

The same existing review also previews dependency finalization on copied nodes
using the actual transitive-reduction algorithm. For affected dependents it shows
saved direct prerequisites, projected final direct prerequisites, and the direct
shortcuts that would be removed while preserving prerequisite reachability.
This is a conditional projection, not a saved edit: later graph edits or a failed
finalization can change the outcome. The preview is limited to 32 dependents and
8 KiB of UTF-8 JSON, with omitted-row counts; invalid graphs report an unavailable
preview rather than inventing a canonical result. Repeated review of the same
revision reuses the previous preview. The final saved turn receipt remains
authoritative; model narration is neither rewritten nor mechanically guaranteed.
