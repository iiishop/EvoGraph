"""Transcribe a recorded candidate into the current shallow protocol offline.

Usage: PYTHONPATH=backend python tools/measure_unified_budget.py candidate.json report.json
The semantic verdict is intentionally unknown. This measures protocol bytes, not design quality.
Original evidence files and historical byte reports are never modified by this helper.
"""
import json
import sys
from pathlib import Path

from evograph.domain.models import Project
from measure_plan_delta import measure


def delta_from_record(record):
    project = Project.model_validate(record['project'])
    by_id = {b.id: b for b in project.behaviors}
    bindings = {b.behavior_key: b for b in project.plan_contract.bindings}
    delta = {
        'target': project.target_draft or project.targets[-1].statement,
        'requirements': [r.model_dump(include={'id', 'quote', 'kind'})
                         for r in project.plan_contract.requirements if r.active],
        'slices': [], 'contracts': [],
    }
    for node in project.milestones:
        delta['slices'].append(node.model_dump(include={
            'id', 'title', 'intent', 'scope', 'dependencies', 'dependency_reasons',
        }))
        for bid in node.behavior_revision_ids:
            behavior = by_id[bid]
            delta['contracts'].append({
                'key': behavior.behavior_key, 'owner': node.id,
                'statement': behavior.statement, 'acceptance_scope': behavior.acceptance_scope,
                **bindings[behavior.behavior_key].model_dump(exclude={'behavior_key', 'behavior_revision_id'}),
            })
    if project.architectures:
        architecture = project.architectures[-1]
        delta.update({
            'architecture_summary': architecture.summary,
            'technologies': [t.model_dump() for t in architecture.technologies],
            'decisions': architecture.decisions, 'risks': architecture.risks,
            'quality_scenarios': [q.model_dump() for q in architecture.quality_scenarios],
            'components': [n.model_dump(include={'id', 'label', 'description', 'role', 'source_refs'})
                           for n in architecture.diagram.nodes],
            'relations': [r.model_dump() for r in architecture.diagram.edges],
            'architecture_groups': [g.model_dump() for g in architecture.diagram.groups],
            'architecture_milestone_ids': architecture.diagram.milestone_ids,
        })
    return delta


if __name__ == '__main__':
    record = json.loads(Path(sys.argv[1]).read_text())
    report = measure(record['input'], delta_from_record(record))
    report['scope'] += '; recorded content transcribed, not a new generated plan'
    Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k not in {'metrics', 'report', 'compilations'}}, ensure_ascii=False, indent=2))
