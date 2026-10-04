import type { PlanCandidate, PlanRequirement, Project } from '../types';

export const candidateStatus: Record<PlanCandidate['status'], string> = {
  generating: '正在生成',
  reviewing: '正在评审',
  needs_resolution: '待解决',
  ready: '等待自动提交',
  applied: '已提交',
  stopped: '已停止',
  failed: '未完成',
  stale: '基于旧版本',
  discarded: '已放弃',
};

export function candidateApplication(candidate: PlanCandidate): 'applied' | 'noop' | 'unknown' {
  if (candidate.status !== 'applied' || !Number.isInteger(candidate.applied_revision))
    return 'unknown';
  if (candidate.applied_revision === candidate.base_revision) return 'noop';
  return candidate.applied_revision! > candidate.base_revision &&
    candidate.turn_summary?.changed !== false
    ? 'applied'
    : 'unknown';
}

export function candidateStatusLabel(candidate: PlanCandidate): string {
  if (candidate.status !== 'applied') return candidateStatus[candidate.status];
  const application = candidateApplication(candidate);
  return application === 'applied'
    ? '已自动应用'
    : application === 'noop'
      ? '本轮无变更'
      : '已处理';
}

export function planReviewDisclosure(candidate?: PlanCandidate | null, canonicalRevision?: number) {
  const application = candidate ? candidateApplication(candidate) : 'unknown';
  const matchesView = Boolean(
    candidate &&
    (canonicalRevision === undefined ||
      (candidate.status === 'applied' && candidate.applied_revision === canonicalRevision)),
  );
  const receipt =
    matchesView &&
    Boolean(candidate?.candidate_hash) &&
    candidate?.validation_receipt?.schema_version === 'planning-validation/v1' &&
    candidate.validation_receipt.candidate_hash === candidate.candidate_hash
      ? candidate.validation_receipt
      : undefined;
  const review = matchesView && application !== 'noop' ? candidate?.report?.semantic : undefined;
  const legacyReview =
    !receipt &&
    Boolean(
      review?.candidate_hash &&
      review.candidate_hash === candidate?.candidate_hash &&
      review.checks.length,
    );
  let structural = '未提供与此版本对应的独立结构检查记录；状态未知';
  if (receipt?.structural.status === 'not_run') structural = '本轮尚未进行结构检查';
  if (receipt?.structural.status === 'clear')
    structural = '结构检查未发现问题；不代表语义正确或实现已验证';
  if (receipt?.structural.status === 'issues')
    structural =
      receipt.structural.finding_count === null
        ? '结构检查记录存在问题'
        : `结构检查发现 ${receipt.structural.finding_count} 项问题`;
  let label = '评审记录未知';
  let semantic = '没有与此版本精确对应的模型评审记录；状态未知';
  if (receipt?.model.kind === 'model_opinion') {
    if (receipt.model.status === 'not_run') {
      const pending = candidate && ['generating', 'reviewing'].includes(candidate.status);
      label = pending ? '尚无模型评审结果' : '模型评审未运行';
      semantic = pending ? '本轮尚无模型评审结果，不能据此判断方案正确性' : '本轮未进行模型评审';
    }
    if (receipt.model.status === 'unavailable') {
      label = '模型评审未完成';
      semantic = '本轮模型评审不可用，不能据此判断方案正确性';
    }
    if (application !== 'noop' && ['no_issue_found', 'issues'].includes(receipt.model.status)) {
      label = '模型评审，可能遗漏问题';
      semantic =
        receipt.model.status === 'no_issue_found'
          ? `${application === 'applied' ? '已自动应用 · ' : ''}模型评审未发现问题，仍可能有错误`
          : '模型评审指出问题或尚无法判断；意见也可能有误';
    }
  } else if (legacyReview) {
    label = '旧模型意见，可能遗漏问题';
    semantic = review!.checks.every((check) => check.verdict === 'supported')
      ? `${application === 'applied' ? '已自动应用 · ' : ''}模型评审未发现问题，仍可能有错误（旧模型意见，未提供新版检查回执）`
      : '旧模型意见仍有矛盾或未决项；这不是实现验证结论';
  }
  if (application === 'noop' && receipt?.model.status !== 'not_run')
    semantic = '本轮没有应用规划变更，不能据此推断进行过模型评审';
  const savedOpinion = candidate?.report?.semantic;
  let legacyNotice = '';
  if (savedOpinion && !matchesView && canonicalRevision !== undefined)
    legacyNotice = '保存的候选模型意见不适用于当前正式版本';
  else if (savedOpinion && savedOpinion.candidate_hash !== candidate?.candidate_hash)
    legacyNotice = savedOpinion.candidate_hash
      ? '另有旧模型意见，指纹不匹配，不适用于当前候选'
      : '旧模型意见未记录版本指纹，是否适用于当前候选未知';
  const notRun =
    receipt?.implementation.scope === 'current_planning_turn' &&
    receipt.implementation.status === 'not_run';
  return {
    label,
    structural,
    semantic,
    legacyNotice,
    implementationTitle: notRun ? '未运行实现' : '实现执行证据',
    implementation: notRun
      ? `本轮未执行实现或测试${receipt.implementation.existing_record_count ? `；${receipt.implementation.existing_record_count} 条既有验收记录不代表本轮运行` : ''}`
      : '没有提供本轮执行证据；运行状态未知，不能据此认定代码正确',
    source:
      matchesView && candidate
        ? `候选 ${candidate.id} · 基于 R${candidate.base_revision}`
        : canonicalRevision === undefined
          ? '未提供候选来源'
          : `正式方案 R${canonicalRevision} · 尚无匹配的评审来源`,
    fingerprint: matchesView ? (candidate?.candidate_hash ?? '') : '',
  };
}

export function candidateNotice(candidate: PlanCandidate, canonicalRevision: number) {
  if (candidate.status === 'applied') {
    const application = candidateApplication(candidate);
    if (application === 'noop') return '本轮没有应用规划变更，可切回正式方案查看';
    return application === 'applied'
      ? '已自动应用，可切回正式方案查看；评审详情可展开'
      : '此候选已处理；应用与评审来源记录不完整';
  }
  if (candidate.status === 'stale' || candidate.base_revision < canonicalRevision)
    return '正式方案已发生变化，这份候选不能直接提交。可在下方继续说明调整要求';
  if (candidate.status === 'needs_resolution')
    return '评审有待解决项，正式方案未被替换。候选已保留，可在下方补充要求继续调整';
  if (candidate.status === 'failed' || candidate.status === 'stopped')
    return '本次候选尚未提交，正式方案未被替换。已生成的内容保留供查看';
  return '生成后自动检查需求、验收与架构的对应关系；评审接受后自动提交';
}

export function requirementSource(
  project: Pick<Project, 'messages' | 'plan_contract'>,
  requirement: PlanRequirement,
) {
  const anchor = project.plan_contract?.sources?.find((item) => item.id === requirement.source_id);
  const message = project.messages?.find((item) => item.id === requirement.source_id);
  return {
    label: requirement.origin === 'legacy' ? '既有方案来源' : '用户原始消息',
    content: anchor?.text ?? message?.content ?? '',
    available: Boolean(anchor || message),
  };
}

// Resolve only the exact behavior revision. A newer revision of the same key
// cannot quietly provide the acceptance statement for an older contract.
export function contractRows(
  project: Pick<Project, 'plan_contract' | 'behaviors' | 'milestones' | 'architectures'>,
) {
  const requirements = project.plan_contract?.requirements ?? [];
  const diagram = project.architectures.at(-1)?.diagram;
  return (project.plan_contract?.bindings ?? []).map((binding) => {
    const behavior = project.behaviors.find((item) => item.id === binding.behavior_revision_id);
    const owner = project.milestones.find((item) => item.id === behavior?.owner);
    return {
      binding,
      behavior,
      owner,
      requirements: binding.requirement_ids.map((id) => ({
        id,
        requirement: requirements.find((item) => item.id === id),
      })),
      components: binding.component_ids.map((id) => ({
        id,
        component: diagram?.nodes.find((item) => item.id === id),
      })),
    };
  });
}
