import type {
  PlanCandidate,
  PlanHarnessDisclosure,
  PlanRequirement,
  PlanSemanticIssue,
  Project,
} from '../types';

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
  if (candidate.status === 'needs_resolution' && candidate.generation_progress?.state === 'staged')
    return '待继续生成';
  if (candidate.status !== 'applied') return candidateStatus[candidate.status];
  const application = candidateApplication(candidate);
  return application === 'applied'
    ? '已自动应用'
    : application === 'noop'
      ? '本轮无变更'
      : '已处理';
}

const harnessCheckNames: Record<string, string> = {
  source_completeness: '来源完整性',
  graph_identity: '图结构与标识',
  contract_source_history: '需求来源与历史',
  design_consistency: '架构结构与绑定',
  declared_availability: '前置声明可用性',
  typed_capability_flow: '验收调用可用性',
  semantic_review: '语义评审',
};

const harnessExecutionLabels: Record<string, string> = {
  prerequisite_skipped: '已跳过 · 前置检查未满足',
  disabled_by_policy: '已跳过 · 当前策略禁用',
  unavailable: '不可用 · 未完成',
  timeout: '超时 · 未完成',
  budget_exhausted: '预算耗尽 · 未完成',
  invalid_output: '输出无效 · 未形成结论',
  error: '运行出错 · 未完成',
  cancelled: '已取消 · 未完成',
};

function harnessCheckDisclosure(check: PlanHarnessDisclosure['checks'][number]) {
  let status = harnessExecutionLabels[check.execution] ?? '执行状态未知 · 无法确认完成';
  if (check.execution === 'completed') {
    const verdicts = {
      pass:
        check.kind === 'model_opinion'
          ? '模型未发现问题'
          : check.id === 'typed_capability_flow'
            ? '已声明动作检查通过；未声明范围未覆盖'
            : '程序检查通过',
      block: check.kind === 'model_opinion' ? '模型指出阻断项' : '发现阻断项',
      unknown: '尚无法判断',
      not_applicable: '未覆盖 · 此可选检查不适用',
    };
    status = `已完成 · ${check.verdict ? verdicts[check.verdict] : '结论未知'}`;
  }
  return {
    ...check,
    message:
      check.message === 'no_declared_typed_actions'
        ? '当前没有已声明的动作可供此可选检查覆盖；不代表该范围已通过'
        : check.message,
    name: harnessCheckNames[check.id] ?? check.id,
    kindLabel: check.kind === 'model_opinion' ? '模型意见' : '程序检查',
    status,
  };
}

function historicalModelOpinion(candidate: PlanCandidate) {
  const review = candidate.report?.semantic;
  const inherited = candidate.inherited_semantic_findings;
  if (review?.candidate_hash && review.candidate_hash === candidate.candidate_hash) return;
  if (!review && inherited?.applicability !== 'historical_needs_recheck') return;
  const batch = candidate.report?.semantic_batch;
  // Structured batch IDs avoid repeating one issue for every affected review subject.
  // Legacy normalized rows have no issue IDs; group identical opinions without parsing model prose.
  const rows: PlanSemanticIssue[] = review
    ? batch && batch.candidate_hash === review.candidate_hash
      ? batch.issues
      : review.checks
          .filter((check) => check.verdict === 'contradicted' || check.verdict === 'unknown')
          .map((check) => ({
            id: '',
            subjects: [check.subject],
            verdict: check.verdict as PlanSemanticIssue['verdict'],
            reason: check.reason,
            counterexample: check.counterexample,
          }))
    : inherited!.issues;
  const unique = new Map<string, PlanSemanticIssue>();
  for (const issue of rows) {
    if (issue.verdict !== 'contradicted' && issue.verdict !== 'unknown') continue;
    const key = issue.id || JSON.stringify([issue.verdict, issue.reason, issue.counterexample]);
    const previous = unique.get(key);
    unique.set(
      key,
      previous
        ? { ...previous, subjects: [...new Set([...previous.subjects, ...issue.subjects])] }
        : { ...issue, subjects: [...new Set(issue.subjects)] },
    );
  }
  if (!unique.size) return;
  return {
    sourceCandidateId: review ? candidate.id : inherited!.source_candidate_id,
    fingerprint: review ? (review.candidate_hash ?? '') : inherited!.candidate_hash,
    issues: [...unique.entries()].map(([key, issue]) => ({ ...issue, key })),
  };
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
  const harness =
    receipt?.harness?.schema_version === 'planning-harness-disclosure/v1'
      ? {
          ...receipt.harness,
          checks: receipt.harness.checks.map(harnessCheckDisclosure),
          decisionLabel:
            receipt.harness.decision === 'apply'
              ? '当次检查策略允许自动应用'
              : '当次检查策略暂不允许应用',
        }
      : undefined;
  const review = matchesView && application !== 'noop' ? candidate?.report?.semantic : undefined;
  const legacyReview =
    !receipt &&
    Boolean(
      review?.candidate_hash &&
      review.candidate_hash === candidate?.candidate_hash &&
      review.checks.length,
    );
  const history =
    candidate &&
    canonicalRevision === undefined &&
    application !== 'noop' &&
    !['no_issue_found', 'issues'].includes(receipt?.model.status ?? '')
      ? historicalModelOpinion(candidate)
      : undefined;
  let structural = '未提供与此版本对应的独立结构检查记录；状态未知';
  if (receipt?.structural.status === 'not_run') structural = '当前候选尚未进行结构检查';
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
      label = history ? '当前候选待复核' : pending ? '当前候选尚无模型结论' : '当前候选尚未评审';
      semantic = history
        ? '当前候选尚无模型评审结论；此前意见需结合当前版本重新核对'
        : pending
          ? '当前候选尚无模型评审结果，不能据此判断方案正确性'
          : '当前候选尚未进行模型评审';
    }
    if (receipt.model.status === 'unavailable') {
      label = '模型评审未完成';
      semantic = '当前候选模型评审未完成，不能据此判断方案正确性';
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
  const previousPolicy = Boolean(
    harness &&
    candidate?.current_harness_policy_version &&
    harness.policy_version !== candidate.current_harness_policy_version,
  );
  let legacyNotice = previousPolicy
    ? `这些检查来自旧策略 ${harness!.policy_version}；当前策略 ${candidate!.current_harness_policy_version} 尚未检查此版本`
    : '';
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
    label: harness ? (previousPolicy ? '历史检查记录 · 策略已更新' : '程序检查 · 模型意见') : label,
    harness,
    history,
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
  if (candidate.status === 'needs_resolution') {
    if (candidate.generation_progress?.state === 'staged')
      return candidate.generation_progress.checkpoint_count > 0
        ? '规划进度已保留，尚待完成剩余生成；正式方案未被替换'
        : '请求已保留，尚未保存规划改动，可继续生成；正式方案未被替换';
    return '候选仍有待解决项，正式方案未被替换。候选已保留，可在下方补充要求继续调整';
  }
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
