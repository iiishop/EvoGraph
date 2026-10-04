"""Read-only checks of represented actions, never prose completeness or execution.

Providers are resolved from final active bindings on every call. Their existing
behavior revisions and the sealed plan hash version capabilities; no parallel
capability history or model-authored availability list exists.
"""

from .dependencies import ancestor_sets
from .plan_contracts import active_behaviors


def declared_typed_keys(project):
    """Only successfully stored active declarations establish typed obligations."""
    active = active_behaviors(project)
    return {binding.behavior_key for binding in project.plan_contract.bindings
            if binding.behavior_key in active and (binding.provides or binding.steps)}


def capability_providers(project):
    """Keep duplicate providers visible instead of silently overwriting a key."""
    behaviors = active_behaviors(project)
    providers = {}
    for index, binding in enumerate(project.plan_contract.bindings):
        behavior = behaviors.get(binding.behavior_key)
        if behavior is None:
            continue
        for capability_index, capability in enumerate(binding.provides):
            providers.setdefault(capability.key, []).append({
                "behavior_key": behavior.behavior_key, "behavior_revision_id": behavior.id,
                "owner": behavior.owner, "kind": capability.kind, "action": capability.action,
                "evidence_ref": f"/candidate/plan_contract/bindings/{index}/provides/{capability_index}",
            })
    return providers


def _consumers(project, capability_key):
    behaviors = active_behaviors(project)
    result = []
    for binding in project.plan_contract.bindings:
        behavior = behaviors.get(binding.behavior_key)
        if behavior is None:
            continue
        indices = [index for index, step in enumerate(binding.steps or [])
                   if step.kind != "inspect" and step.capability_key == capability_key]
        if indices:
            result.append({"behavior_key": behavior.behavior_key,
                           "behavior_revision_id": behavior.id, "owner": behavior.owner,
                           "step_indices": indices})
    return sorted(result, key=lambda row: row["behavior_key"])


def capability_changes(before, after):
    """Server-computed provider changes, including unchanged affected consumers."""
    def index(project):
        return {key: [{k: v for k, v in row.items() if k != "evidence_ref"} for row in rows]
                for key, rows in capability_providers(project).items()}

    old, new = index(before), index(after)
    changes = []
    for key in sorted(old.keys() | new.keys()):
        previous, current = old.get(key, []), new.get(key, [])
        if previous == current:
            continue
        changes.append({"key": key, "before_providers": previous, "after_providers": current,
                        "before_consumers": _consumers(before, key),
                        "after_consumers": _consumers(after, key)})
    return changes


def validate_capability_moves(before, after, reasons):
    """A reason records intent, not semantic authorization of a reassignment."""
    for change in capability_changes(before, after):
        previous, current = change["before_providers"], change["after_providers"]
        if (len(previous) == len(current) == 1
                and previous[0]["behavior_key"] != current[0]["behavior_key"]):
            receiver = current[0]["behavior_key"]
            if not reasons.get(receiver, "").strip():
                raise ValueError(f"capability_provider_move [{change['key']}]: receiving contract "
                                 f"{receiver} requires capability_move_reason")


def typed_capability_report(project, before=None, *, obligation_keys=()):
    """Check declared typing only; clearing a surviving typed contract cannot opt out.

    The before snapshot is the publication base, not an invented project-wide
    migration requirement. Retired contracts do not retain applicability.
    """
    behaviors = active_behaviors(project)
    bindings = {binding.behavior_key: binding for binding in project.plan_contract.bindings}
    applicable = declared_typed_keys(project)
    applicable.update(set(obligation_keys) & behaviors.keys())
    if before is not None:
        applicable.update(declared_typed_keys(before) & behaviors.keys())
    uncovered = ["typed_acceptance:" + behavior.id for key, behavior in behaviors.items()
                 if key not in applicable]
    requirements = {r.id: r for r in project.plan_contract.requirements if r.active}
    providers = capability_providers(project)
    ancestors = ancestor_sets({m.id: m.dependencies
                              for m in [*project.source_milestones, *project.milestones]})
    source_ids = {m.id for m in project.source_milestones}
    findings, subjects, edges = [], [], []
    violations = []
    counts = {"invocations": 0, "inspections": 0, "untyped_behaviors": 0,
              "uncovered_behaviors": len(uncovered)}

    def issue(code, subject, message, *, unknown=False, refs=()):
        findings.append({"code": code, "subject": subject, "message": message,
                         "severity": "error", "refs": tuple(refs)})
        if not unknown:
            violations.append(code)

    for key, rows in providers.items():
        subjects.append("capability:" + key)
        if len(rows) != 1:
            issue("duplicate_capability_provider", key, "同一能力 key 必须只有一个活跃提供契约",
                  refs=[row["evidence_ref"] for row in rows])
        for row in rows:
            behavior = behaviors[row["behavior_key"]]
            if row["action"] not in behavior.statement:
                issue("capability_action_anchor", key, "能力 action 必须逐字出自提供契约的当前验收",
                      refs=(row["evidence_ref"],))
    for key, behavior in behaviors.items():
        if key not in applicable:
            continue
        subjects.append("typed_acceptance:" + behavior.id)
        binding = bindings.get(key)
        if binding is None or not binding.steps:
            counts["untyped_behaviors"] += 1
            issue("acceptance_steps_unknown", key,
                  "已声明类型化动作的活跃验收缺少步骤；清空不能撤销本次检查义务，请恢复对应 steps",
                  unknown=True)
            continue
        has_invoke = False
        binding_index = project.plan_contract.bindings.index(binding)
        for step_index, step in enumerate(binding.steps):
            subject = f"{key}:step:{step_index}"
            subjects.append(subject)
            ref = f"/candidate/plan_contract/bindings/{binding_index}/steps/{step_index}"
            if step.quote not in behavior.statement:
                issue("acceptance_step_anchor", subject, "步骤 quote 不在所属契约的当前验收中", refs=(ref,))
            if step.kind == "inspect":
                counts["inspections"] += 1
                requirement = requirements.get(step.requirement_id)
                if (step.requirement_id not in binding.requirement_ids or requirement is None
                        or requirement.kind not in {"constraint", "exclusion"}):
                    issue("invalid_inspection_requirement", subject,
                          "inspect 必须引用本契约关联的活跃 constraint/exclusion，不能代替产品操作",
                          refs=(ref,))
                continue
            has_invoke = True
            counts["invocations"] += 1
            rows = providers.get(step.capability_key, [])
            if not rows:
                uses_source = bool(source_ids & ancestors.get(behavior.owner, set()))
                issue("source_capability_unknown" if uses_source else "missing_capability_provider",
                      subject, ("本版不能类型化 SRC 提供的操作；请保留未验证状态，勿伪造计划包装提供者"
                                if uses_source else f"调用能力 {step.capability_key} 没有活跃提供契约"),
                      unknown=uses_source, refs=(ref,))
                continue
            if len(rows) != 1:
                continue  # Duplicate provider finding already identifies every declaration.
            provider = rows[0]
            if step.kind != "invoke_" + provider["kind"]:
                issue("capability_kind_mismatch", subject, "调用步骤类型与能力 command/query 类型不一致",
                      refs=(ref, provider["evidence_ref"]))
            owner = provider["owner"]
            edge = {"consumer": behavior.owner, "provider": owner,
                    "behavior_key": key, "behavior_revision_id": behavior.id,
                    "step_index": step_index, "capability_key": step.capability_key,
                    "provider_behavior_key": provider["behavior_key"],
                    "provider_behavior_revision_id": provider["behavior_revision_id"]}
            edges.append(edge)
            if owner not in ancestors.get(behavior.owner, set()) | {behavior.owner}:
                issue("capability_not_available", subject,
                      f"调用 {step.capability_key} 由 {owner}/{provider['behavior_key']} 提供，"
                      f"不在消费者 {behavior.owner} 自身或已声明前置闭包中",
                      refs=(ref, provider["evidence_ref"]))
        if (not has_invoke and any(requirements[rid].kind == "outcome"
                                   for rid in binding.requirement_ids if rid in requirements)):
            issue("outcome_invocation_unknown", key,
                  "关联产品 outcome 的验收仅含 inspect；至少一个真实操作必须表示为调用步骤",
                  unknown=True)
    return {"verdict": "block" if violations else "unknown" if findings
            else "pass" if applicable else "not_applicable", "findings": findings,
            "applicability_reason": "" if applicable else "no_declared_typed_actions",
            "covered_subjects": subjects, "uncovered_subjects": uncovered,
            "derived_edges": edges, "coverage": counts}
