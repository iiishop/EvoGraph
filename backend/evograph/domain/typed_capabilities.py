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


def declared_consumption_keys(project):
    """Behavior/key pairs retain declared use through checkpoints and retirement.

    Explicit empty modeled use also establishes a declaration. Keeping the pair
    distinguishes retired owners while a restored capability retains its history.
    """
    return {(row["behavior_key"], key) for key, rows in capability_providers(project).items()
            for row in rows if row["consumes"] is not None}


def capability_providers(project):
    """Keep duplicate providers visible instead of silently overwriting a key."""
    behaviors = active_behaviors(project)
    providers = {}
    for index, binding in enumerate(project.plan_contract.bindings):
        behavior = behaviors.get(binding.behavior_key)
        if behavior is None or binding.behavior_revision_id != behavior.id:
            continue
        for capability_index, capability in enumerate(binding.provides):
            providers.setdefault(capability.key, []).append({
                "behavior_key": behavior.behavior_key, "behavior_revision_id": behavior.id,
                "owner": behavior.owner, "kind": capability.kind, "action": capability.action,
                "consumes": None if capability.consumes is None else list(capability.consumes),
                "evidence_ref": f"/candidate/plan_contract/bindings/{index}/provides/{capability_index}",
            })
    return providers


def consumption_indices(binding, capability_key):
    return [[i, j] for i, capability in enumerate(binding.provides)
            for j, consumed in enumerate(capability.consumes or []) if consumed == capability_key]


def _consumers(project, capability_key):
    behaviors = active_behaviors(project)
    result = []
    for binding in project.plan_contract.bindings:
        behavior = behaviors.get(binding.behavior_key)
        if behavior is None or binding.behavior_revision_id != behavior.id:
            continue
        indices = [index for index, step in enumerate(binding.steps or [])
                   if step.kind != "inspect" and step.capability_key == capability_key]
        consumption = consumption_indices(binding, capability_key)
        if indices or consumption:
            result.append({"behavior_key": behavior.behavior_key,
                           "behavior_revision_id": behavior.id, "owner": behavior.owner,
                           "step_indices": indices, "consumption_indices": consumption})
    return sorted(result, key=lambda row: row["behavior_key"])


def capability_changes(before, after):
    """Server-computed provider or consumer changes, including affected consumers."""
    def index(project):
        return {key: [{k: v for k, v in row.items() if k != "evidence_ref"} for row in rows]
                for key, rows in capability_providers(project).items()}

    old, new = index(before), index(after)
    changes = []
    for key in sorted(old.keys() | new.keys()):
        previous, current = old.get(key, []), new.get(key, [])
        old_consumers, new_consumers = _consumers(before, key), _consumers(after, key)
        if previous == current and old_consumers == new_consumers:
            continue
        changes.append({"key": key, "before_providers": previous, "after_providers": current,
                        "before_consumers": old_consumers, "after_consumers": new_consumers})
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


def typed_capability_report(project, before=None, *, obligation_keys=(),
                            consumption_obligation_keys=()):
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
    consumption_obligations = {tuple(key) for key in consumption_obligation_keys}
    if before is not None:
        consumption_obligations.update(declared_consumption_keys(before))
    ancestors = ancestor_sets({m.id: m.dependencies
                              for m in [*project.source_milestones, *project.milestones]})
    source_ids = {m.id for m in project.source_milestones}
    findings, subjects, edges, retired = [], [], [], []
    violations = []
    counts = {"invocations": 0, "inspections": 0, "untyped_behaviors": 0,
              "uncovered_behaviors": len(uncovered), "consumption_edges": 0,
              "unmodeled_consumption": 0, "declared_consumption": 0}

    def issue(code, subject, message, *, unknown=False, refs=()):
        findings.append({"code": code, "subject": subject, "message": message,
                         "severity": "error", "refs": tuple(refs)})
        if not unknown:
            violations.append(code)

    def missing_provider(key, behavior, subject, ref):
        uses_source = bool(source_ids & ancestors.get(behavior.owner, set()))
        issue("source_capability_unknown" if uses_source else "missing_capability_provider",
              subject, ("本版不能类型化 SRC 提供的操作；请保留未验证状态，勿伪造计划包装提供者"
                        if uses_source else f"调用能力 {key} 没有活跃提供契约"),
              unknown=uses_source, refs=(ref,))

    def available(provider, behavior, key, subject, ref):
        if provider["owner"] not in ancestors.get(behavior.owner, set()) | {behavior.owner}:
            issue("capability_not_available", subject,
                  f"调用 {key} 由 {provider['owner']}/{provider['behavior_key']} 提供，"
                  f"不在消费者 {behavior.owner} 自身或已声明前置闭包中",
                  refs=(ref, provider["evidence_ref"]))

    for index, binding in enumerate(project.plan_contract.bindings):
        behavior = behaviors.get(binding.behavior_key)
        if (behavior is not None and binding.behavior_revision_id != behavior.id
                and (binding.provides or binding.steps)):
            issue("stale_capability_binding", binding.behavior_key,
                  "能力声明必须绑定当前活跃验收版本", refs=(f"/candidate/plan_contract/bindings/{index}",))

    for behavior_key, key in sorted(consumption_obligations):
        if behavior_key in behaviors and key not in providers:
            # Reference closure still checks all surviving consumers below. A
            # removed declaration is not fulfillment, nor a permanent retirement
            # ban: the existing change audit and semantic review own its intent.
            retired.append({"behavior_key": behavior_key, "capability_key": key})

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
            subject = f"{row['behavior_key']}:capability:{key}"
            if row["consumes"] is None:
                counts["unmodeled_consumption"] += 1
                uncovered.append("capability_consumption:" + key)
                if any(needed == key for _, needed in consumption_obligations):
                    issue("capability_consumption_unknown", subject,
                          "已声明的运行时能力使用被省略；恢复 consumes 或显式声明建模范围内的空列表",
                          unknown=True, refs=(row["evidence_ref"],))
                continue
            counts["declared_consumption"] += 1
            subjects.append("capability_consumption:" + key)
            for index, consumed in enumerate(row["consumes"]):
                counts["consumption_edges"] += 1
                ref = f"{row['evidence_ref']}/consumes/{index}"
                targets = providers.get(consumed, [])
                if not targets:
                    missing_provider(consumed, behavior, subject, ref)
                    continue
                if len(targets) != 1:
                    issue("ambiguous_consumed_capability", subject,
                          f"运行时使用 {consumed} 无法解析唯一活跃提供者",
                          refs=(ref, *(target["evidence_ref"] for target in targets)))
                    continue
                target = targets[0]
                if row["kind"] == "query" and target["kind"] == "command":
                    issue("capability_effect_mismatch", subject,
                          f"query 能力 {key} 不能声明运行时调用 command 能力 {consumed}",
                          refs=(ref, target["evidence_ref"]))
                edges.append({"edge_kind": "mechanism_consumption",
                              "consumer": behavior.owner, "provider": target["owner"],
                              "behavior_key": behavior.behavior_key,
                              "behavior_revision_id": behavior.id, "consumer_capability_key": key,
                              "capability_key": consumed,
                              "provider_behavior_key": target["behavior_key"],
                              "provider_behavior_revision_id": target["behavior_revision_id"],
                              "consumer_ref": ref, "provider_ref": target["evidence_ref"]})
                available(target, behavior, consumed, subject, ref)
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
                missing_provider(step.capability_key, behavior, subject, ref)
                continue
            if len(rows) != 1:
                continue  # Duplicate provider finding already identifies every declaration.
            provider = rows[0]
            if step.kind != "invoke_" + provider["kind"]:
                issue("capability_kind_mismatch", subject, "调用步骤类型与能力 command/query 类型不一致",
                      refs=(ref, provider["evidence_ref"]))
            owner = provider["owner"]
            edge = {"edge_kind": "acceptance_invocation",
                    "consumer_ref": ref, "provider_ref": provider["evidence_ref"],
                    "consumer": behavior.owner, "provider": owner,
                    "behavior_key": key, "behavior_revision_id": behavior.id,
                    "step_index": step_index, "capability_key": step.capability_key,
                    "provider_behavior_key": provider["behavior_key"],
                    "provider_behavior_revision_id": provider["behavior_revision_id"]}
            edges.append(edge)
            available(provider, behavior, step.capability_key, subject, ref)
        if (not has_invoke and any(requirements[rid].kind == "outcome"
                                   for rid in binding.requirement_ids if rid in requirements)):
            issue("outcome_invocation_unknown", key,
                  "关联产品 outcome 的验收仅含 inspect；至少一个真实操作必须表示为调用步骤",
                  unknown=True)
    return {"verdict": "block" if violations else "unknown" if findings
            else "pass" if applicable else "not_applicable", "findings": findings,
            "applicability_reason": "" if applicable else "no_declared_typed_actions",
            "covered_subjects": subjects, "uncovered_subjects": uncovered,
            "derived_edges": edges, "coverage": counts,
            "removed_consumption_declarations": retired}
