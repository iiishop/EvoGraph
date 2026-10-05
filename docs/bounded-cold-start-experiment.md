# Bounded empty-plan drafting prototype

Status: offline prototype for independent review. Default off. No commit, publication, deployment, settings change, provider call, or QA54 schedule migration is part of this change.

## Application activation, separately gated

The only activation surface is an optional application-layer argument on the existing `UnifiedPlanningService.stream` method. It is not accepted by the HTTP/UI request schema, project settings, provider configuration or model tool arguments. A reviewed test launcher can later bind this exact argument to the existing native UI application's call for one specified project and stage. That launcher wiring is not implemented or activated here.

```python
# Cold-start stage: only a genuinely new empty project/candidate.
app.unified.stream(project_id, content, experiment={
    "project_id": project_id,
    "version": "bounded-empty-plan/v1",
})

# Only after that complete stage was independently reviewed with actionable issues:
app.unified.stream(project_id, repair_content, experiment={
    "project_id": project_id,
    "version": "bounded-single-unit-repair/v1",
})
```

A mismatched project, unsupported version, unexpected option, new cold-start attempt on a pending candidate, unspecified stage on a retained experimental candidate, or repair without the prior completed cold-start review is rejected. A completed or failed repair stage cannot be automatically selected again. `experiment=None` preserves all non-experimental behavior.

Each explicit stage has at most three dispatched requests in this exact order: one manifest router, one generator, one independent semantic review. The budget wrapper checks the request schemas/purpose before dispatch, preventing paid routing/generation retries and a fourth request even if higher-level control flow attempts them. Any failed tool response stops the stage; incomplete/text-only generation cannot count as completion. A held review ends the stage. There is no automatic second stage. The repair stage retains ordinary segmented scheduling and must admit exactly one unit; multiple units stop before generation.

The unchanged product turn budget remains 5 calls, 163,840 B per request (including review), 393,216 B cumulative input, 98,304 B output and 180 seconds per request. The experiment is an additional lower three-call cap. The exact stage policy and limits are persisted in candidate and metrics provenance. No provider/model/request controls are increased.

## Admission and integrity

Cold-start policy `bounded-empty-plan/v1` uses schedule version `plan-units/bounded-empty-v1`; existing v1/v2 schedules and default scheduler constants are not upgraded or repacked. Existing candidates require their original default flow, not this mode.

Admission requires empty planning payload other than intent sources, no baseline/execution/source-analysis residue, no prior candidate/schedule or compilation/checkpoint evidence, and a new canonical manifest. New-only requirements, slices, contracts, components and architecture bootstrap must be present. Target is optional under the existing compiler contract. Every requirement/slice needs its covering/owned contract; each contract declares an owner and requirements. References are unique, use declared canonical fields, resolve inside this cohort, and relation endpoints match their identity. Capability definitions are unique. Existing structural scheduler checks are reused, excluding only its default packing-size hold, which this separate policy replaces.

Provisional cohort bounds:

- 32 canonical operations, 6 delivery slices, 8 contracts
- 64 field atoms, 256 canonical fields, 256 references
- 32,768 B serialized canonical manifest
- 49,152 B serialized actual assigned unit
- 65,536 B actual projected unit context
- 65,536 B compact accepted PlanDelta

All sizes use UTF-8 JSON bytes. Actual request and streamed response remain guarded by the existing global byte/finish/timeout admission. Routing estimates are never admission evidence. Actual delta fields/references are recounted before compilation; actual graph references must stay inside admitted identities, including declared capabilities. Every nested runtime `consumes` entry also counts toward the 256-reference limit and must target an admitted capability; these consumption edges are not new manifest definitions. Legal references may be added within that closed cohort, retaining the compiler's existing reference-hint semantics rather than treating `uses` as authorization. The architecture field atoms remain exact.

The schedule saves the versioned policy, all bounds and an exact empty planning-base snapshot/fingerprint. Request pins bind that admission along with the existing project/source/revision/candidate/manifest/unit hashes. Pinning and closure reconstruct the expected unit and bounds, refusing changed `depends_on`, size/count fields or holds. Atomic checkpoints bind the policy hash and are reconciled against the actual persisted compiler IR, delta hash and submitted fields. Compiler, finalization, harness and independent review are unchanged; no old semantic certificate is reused.

One response does not merge the plan's delivery milestones. Delivery cohesion, scope, prerequisites, acceptance ownership and architecture quality still need independent semantic judgment and the real experiment. This prototype does not establish successful joint generation, calls saved, latency, higher quality or implementation readiness.

## Offline reproduction

Run with an existing Python environment providing project test dependencies:

```bash
PYTHONPATH=backend python tools/replay_bounded_cold_start.py \
  /path/to/qa54-real-use/04-initial-reviewed-result report.json
pytest -q tests/test_bounded_cold_start.py \
  tests/test_architecture_record_packing.py tests/test_architecture_unit_bootstrap.py \
  tests/test_schedule_admission_flow.py tests/test_plan_continuation_context.py \
  tests/test_plan_repair_context.py tests/test_plan_budget.py
```

The frozen replay only reads `candidates.json`, `candidate-project.json` and `project.json`, reconstructs accepted sparse IR, invokes the actual new scheduler/compiler/checkpoint in private memory, and runs deterministic checks. It does not instantiate application settings, read credentials, dispatch any request or modify a database. It retains three separate historical sources and exact quotations, including later-turn input; it does not pretend that later evidence existed at the original first user turn. Frozen files are SHA-256 hashed and rechecked byte-for-byte afterward.

Measured QA54 reconstruction: 23,566 B accepted IR, 23 canonical operations, 26 field atoms, four default units versus one new-policy unit, one atomic checkpoint, 17,767 B projected unit context and 38,801 B projected generator request. Projection excludes no required source text; it is not an actual provider request. Deterministic plugins pass or are not applicable; design consistency still emits missing decisions, quality scenarios and risk review findings. Semantic review is not run by that replay.

Focused tests cover default/saved schedule immutability, project/stage scope, closed-manifest rejection, count and UTF-8 output boundaries, policy/unit/pin tampering, exact replay, compiler-audit closure, first-failure stop, pre-dispatch sequence/call limits, and a mocked three-call path ending held. Mocked streams are tests, not real model evidence.

## Independent-review corrections, prototype revision 2

The original patch `8d9512ba…` remains preserved with its changes-requested review. The revised artifact is `bounded-cold-start-v2.patch`; the application opt-in remains unreleased and default off.

- Empty-base admission now explicitly rejects light checks, acceptance-request history and UML designs, in addition to the earlier planning/execution residue checks. All excluded residue fields are stored in admission evidence and revalidated on replay, rather than reconstructed with silent empty defaults.
- The atomic save binds an accepted planning fingerprint and exact before/after source audit metadata. Closure recomputes the accepted historical candidate hash from the actual current planning payload while restoring only the captured source activity/message metadata. This allows legitimate tool-completion audit updates without trusting two matching copied hash strings.
- The checkpoint source, project and revisions must match admission, the originating schedule/turn and the actual accepted candidate. Closure reconstructs the empty before snapshot, re-runs the pure compiler, derives the completed audit from actual before/after planning, and requires the full audit and its content hash to match. These checks also run inside the atomic save before persistence.
- Regressions cover all three omitted residue fields, each original identity-tamper reproducer, rehashed forged compiler identities, matching-but-false checkpoint/compiler result hashes, source-activity completion compatibility and rollback on forged audit identity.
