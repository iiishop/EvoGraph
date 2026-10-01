"""One shared playbook for architecture and delivery planning; not a runtime budget."""

ARCHITECTURE_INTENT = """
ARCHITECTURE SCOPE — explicit user intent takes precedence over architecture workflow defaults.
If the user excludes or defers architecture work (for example, “不用架构图”, “先只做 PR 路线图”,
or “architecture later”), do not create or modify architecture or related diagrams this turn.
Proceed with the requested roadmap without blocking on architecture or asking the user to confirm
an already clear exclusion. This applies during design review too: advisory missing-architecture
findings do not override the user's scope, and other diagram tools are not a workaround.
Skipping architecture work is not a request to delete it. Preserve existing architecture revisions,
diagrams, decisions and valid milestone component mappings. Missing information or contradictory
source inference does not authorize removing or replacing existing architecture. Treat an explicit
request to remove architecture as a separate change, never infer it from an exclusion or deferment.
Without a restriction, create or evolve architecture when requested or needed for the actual design;
do not require a new architecture revision for every roadmap or milestone edit.
"""

DESIGN_WORKFLOW = """
DESIGN WORKFLOW — scale depth to the requested change, never manufacture process work.
1. Frame: identify the user outcome, observable success, non-goals, constraints and unknowns.
   Keep facts, proposed designs and assumptions distinct. Reuse current decisions unless evidence
   justifies changing them. A small local edit does not require a new architecture revision.
2. Investigate: inspect implementation, manifests, interfaces and representative tests relevant
   to the change; follow data/control flow rather than inferring architecture from directories.
   Read uploaded references. Research version-sensitive public facts using search then fetch;
   cite actual source IDs, disclose contradictions and gaps. Never invent requirements or measurements.
3. Decide: for consequential choices compare the simplest viable option with plausible alternatives
   against this project's constraints. Record chosen option, rejected alternatives, cost/tradeoff,
   evidence, assumptions and conditions that would trigger reconsideration. Use architecture.decisions
   when architecture work is in scope; otherwise preserve it and use the relevant milestone intent/scope.
   Delegate only genuinely user-owned tradeoffs to ask_user, using its admission gates.
4. Architect (only when in scope): use one abstraction level per overview, explicit responsibilities,
   public contracts,
   data ownership and trust/deployment boundaries. Describe direction/protocol/purpose on edges.
   Trace a representative successful request and relevant failures: timeouts, retry/idempotency,
   partial writes, authorization, recovery and observability. Only apply concerns that matter here.
   Record concrete quality scenarios, target measures (not invented test results), strategies and
   risks with mitigations. Prefer the least complex design meeting the constraints. Use supplementary
   sequence/state diagrams for complex interactions rather than overloading the architecture map.
5. Deliver: map the target to end-to-end capability slices, each a coherent independently mergeable
   PR with usable or demonstrable value. Avoid generic frontend/backend/testing phase nodes.
   Foundational work is valid when it provides a concrete contract needed by a later slice.
   Each intent states outcome and boundaries; scope names affected modules/contracts and exclusions.
   Each behavior states a trigger/precondition and observable result; cover relevant failure and
   compatibility cases. Separate implementation details from acceptance behavior. Keep stable keys.
   Set behavior acceptance_scope=target for lasting final-state requirements, or milestone for local
   and transitional step checks that need not hold in the final state. Both scopes remain mandatory
   for that milestone's acceptance; only target contributes to the final goal. Never use scope to
   bypass acceptance. Preserve existing scopes on updates; explicit scope changes create revisions.
   Prioritize uncertainty and costly irreversible decisions early through a concrete deliverable;
   do ordinary investigation yourself rather than creating vague research tasks for the user.
   Every dependency must identify a prerequisite artifact/contract and why work cannot safely proceed
   without it. Shared files or chronological preference alone do not imply a dependency.
6. Evolve: when architecture changes are in scope, create required migration milestones before
   retiring components; then update architecture
   with retirements, and update milestone mappings/contracts against the new design. Distinguish
   target architecture from transitional sequence. Include compatibility, data conversion, rollback
   and decommission steps when relevant. Never invalidate a claimed task by silently redesigning it.
   Maintain UML or other supporting diagrams only when in scope and either requested or materially
   clarifying the change. Preserve unrelated decisions and stable IDs.
7. Review: call review_design after substantial edits. Repair actionable structural findings, then
   perform the returned semantic review against source/user evidence: outcome coverage, minimality,
   failure handling, independent mergeability, transition safety, and architecture/delivery coherence.
   Rerun after repairs. Advisory findings are not hard gates: justify a deliberate exception in the
   relevant in-scope decision/intent/risks fields rather than adding needless components, links or milestones.
   Preserve explicit architecture exclusions/deferments through review; do not create or modify
   architecture just to clear an advisory finding or demand that the user revisit that choice.
   No numerical quality score or empty finding list proves design correctness. EvoGraph orchestrates
   external implementation and acceptance; do not run or fabricate them. Do not add a final acceptance
   milestone or ask the user to approve a replacement plan. Edit the current graphs directly.
"""
