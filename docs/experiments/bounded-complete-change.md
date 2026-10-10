# Bounded complete change v1 (offline implementation)

This is an explicit, reusable mode in the existing experimental planning-job
controls. It is not the cold-start policy, a larger ordinary unit, or a new
planning framework. Legacy/cold-start defaults, ordinary packing bounds,
provider/model settings, existing saved job schedules and records remain intact.

## Admission and workflow

`planning_job` start requests may opt into `mode: bounded-complete-change/v1`
with exactly `max_phases: 1`, `max_calls: 2`, `max_input_bytes: 393216`, plus the
existing current canonical/candidate start pins and fresh job identity/input.
The same job ID is idempotent; changed authorization is rejected. A stopped job
cannot continue, retry or fall back to ordinary generation. A genuinely new
request obtains new pins and a fresh retained-entry inventory, without erasing
old records or treating old pending proposals as immutable requirements.

An active nonempty plan is required. Both a canonical-only plan with no retained
candidate and the current retained staged plan are eligible; an empty plan is not. The policy pins its current candidate,
source, revision, fingerprint, protected accepted/draft entry and checkpoint
count. There is no router, manifest generation or unit-generation sequence.
Exactly one complete response may contain one PlanDelta or ask_user. The
existing whole-delta compiler stages all changes privately, verifies references,
history, capabilities and retained acceptance, then writes one atomic checkpoint. The writer transaction privately replays the
existing compiler/patch services and compares the complete result, so a matching
audit digest cannot admit an undeclared change. Equality alignment is restricted
to generated behavior IDs and creation timestamps of newly appended records;
old history and substantive fields remain exact. The admission policy seals
`result_verifier: whole-patch-replay/v1`; earlier complete-change admissions or
certificates without that identity fail closed, without rewriting old records
or changing legacy/default modes.
Multiple separate, coherent one-coding-session milestones remain appropriate.
Atomic planning is not a command to combine coding deliverables.

Only after a successful complete delta do the existing deterministic harness and
one full semantic review run. A hold, invalid output, timeout, provider error,
stale source/base/mode/job, cancellation, or cap stops the attempt. The existing
review/apply transaction replays a certificate sealed to the explicit mode and
admission. Existing modes retain their previous snapshot identity. Questions use
the ordinary durable question terminal receipt and ordinary answer route; they
spend one generation call and produce no compiler checkpoint. A separately
initiated answer is a new ordinary turn with its own calls; those calls are not
part of the original two-call allowance and cannot revive the stopped job.

## Explicit bounds

- Requests: 163,840 compact UTF-8 application-envelope bytes each, measured by
  the existing BudgetedSettings serializer immediately before dispatch
- Phase: existing outer 393,216 bytes / five-call ceiling stays unchanged; this
  mode separately enforces at most one generation plus one review
- Stream output: 98,304 bytes; timeout: 180 seconds per request
- Complete delta: 65,536 bytes, 32 submitted operations, six submitted slice
  rows, eight submitted contract rows, 64 field atoms, 256 fields, 256 references
- Counts apply to submitted identities, including unchanged restatements and
  removals; they do not count all saved project objects and do not infer semantic
  change. Nested capability consumption references are included

The previous remaining proposal in frozen snapshot22 has three slice rows, five
contract rows and 13 identities; the project has seven slices and eight active
contracts. These are distinct counts. This mode does not synthesize or execute
that historical manifest. Do not restate every saved object as a delta.

There is deliberately no fixed cold-start 65,536-byte context allowance here:
complete current facts use a lossless bounded projection, and exact production
request admission decides whether they fit the unchanged 163,840-byte envelope.
All active statements/mechanisms, dependency reasons, current architecture,
sources, retained originals, applicable findings and exact pending proposals are
included using existing exact deduplication/aliases. Oversize data is rejected,
not truncated. A saved-candidate review size is only a proxy; a future generated
candidate's actual review is always remeasured.

Metadata-only disposition proposals stop without claiming a substantive repair.
Their raw rejected attempt remains auditable. Legitimate withdrawal accompanied
by real plan changes continues through existing retained-disposition checks and
semantic review; matching source text alone never gives semantic permission.
Exact accepted delta replay makes no progress; changed replay is rejected.

## Offline verification and native fixture

`tests/test_bounded_complete_change.py` exercises atomic whole-delta apply,
first-invalid/uncertain stops, no-op/metadata-only stops, semantic hold, cancellation
and staleness at the checkpoint writer gate, policy/source/pin tampering, exact
replay, mode-bound snapshots, and the question/ordinary-answer route.

`tools/measure_complete_change.py --snapshot PATH --out PATH` restores exported
snapshot22 into new temporary databases and captures actual production requests
using local fake providers. Original candidate/job/message records and frozen
file hashes are checked. It also tests generation/review cap and cap+1 dispatch,
and a distinct synthetic complete delta plus scripted review. Fake passes show
workflow wiring, never real model quality or a repaired real project.

`tools/preview_complete_change.py --check --evidence-dir PATH` verifies the same
synthetic workflow without GUI. Without `--check` it opens the native app with a
fresh temporary, prominently synthetic profile, NoSecrets, blocked real adapters
and nonloopback network, and the exact production DesktopBridge. It refuses a
missing or stale frontend bundle. Native GUI operation and any later real run
remain gated separately; no real provider call is part of this implementation.
