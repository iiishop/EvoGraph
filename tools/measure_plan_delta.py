"""Offline request accounting for a strict shallow-delta fixture; never an AI quality claim.

PYTHONPATH=backend python tools/measure_plan_delta.py input.txt delta.json report.json
Uses in-memory fake response events; never reads settings, credentials or the network.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from evograph.agent_tools import tools
from evograph.application.api import Application
from evograph.application.plan_budget import compact_schema, encoded_size
from evograph.application.plan_contracts import VALIDATE_TOOL
from evograph.application.plan_ir import DELTA_TOOL, PlanDelta
from evograph.application.unified_planning import ALLOWED_TOOLS, GENERATOR


class NoSecrets:
    def get(self, name):
        raise AssertionError('Offline measurement must not read credentials')

    def set(self, name, value):
        raise AssertionError('Offline measurement must not write credentials')


def measure(content, delta):
    PlanDelta.model_validate(delta)
    with tempfile.TemporaryDirectory() as directory:
        app = Application(Path(directory), NoSecrets())
        project = app.projects.create('offline-flat-budget')
        app.unified.enable(project.id, True)
        generation = 0
        async def stream(messages, schemas):
            nonlocal generation
            if schemas[0]['function']['name'] == 'submit_plan_review':
                packet = json.loads(messages[-1]['content'])
                name, args = 'submit_plan_review', {
                    'candidate_hash': packet['candidate_hash'],
                    'summary': 'Offline transport fixture; semantic validity is not assessed',
                    'checks': [{'subject': s, 'verdict': 'unknown',
                                'reason': 'No semantic judgment is performed by this fixture',
                                'counterexample': 'No adversarial reasoning was run'}
                               for s in packet['required_subjects']],
                }
            elif generation == 0:
                generation += 1
                name, args = 'submit_plan_delta', delta
            else:
                yield {'type': 'text', 'text': 'Offline fixture does not repair semantic findings'}
                return
            yield {'type': 'tool_delta', 'index': 0, 'id': 'offline', 'name': name,
                   'arguments': json.dumps(args, ensure_ascii=False)}
        app.settings.stream = stream
        async def run():
            return [event async for event in app.agent.stream(project.id, content)]
        asyncio.run(run())
        candidate = app.unified.store.latest(project.id)
        registry = {n: t for n, t in tools().items() if n in ALLOWED_TOOLS}
        registry.update(submit_plan_delta=DELTA_TOOL, validate_candidate=VALIDATE_TOOL)
        return {
            'scope': 'Offline normal protocol replay with unknown semantic verdict; no model, network, credentials or quality claim',
            'input_text_bytes': len(content.encode()),
            'delta_bytes': encoded_size(delta),
            'schema_count': len(registry),
            'schema_bytes': encoded_size([compact_schema(t.schema()) for t in registry.values()]),
            'delta_tool_schema_bytes': encoded_size(compact_schema(DELTA_TOOL.schema())),
            'generator_system_bytes': len(GENERATOR.encode()),
            'metrics': candidate['metrics'], 'status': candidate['status'],
            'candidate_milestones': len(candidate['project']['milestones']),
            'canonical_milestones': len(app.db.get(project.id).milestones),
            'report': candidate['report'],
            'review_packet_bytes': [encoded_size(p) for p in candidate.get('review_inputs', [])],
            'compilations': candidate.get('compilations', []),
        }


if __name__ == '__main__':
    report = measure(Path(sys.argv[1]).read_text(), json.loads(Path(sys.argv[2]).read_text()))
    Path(sys.argv[3]).write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k not in {'report', 'metrics', 'compilations'}}, ensure_ascii=False, indent=2))
    print(json.dumps([{k: v for k, v in call.items() if k not in {'response_audit'}} for call in report['metrics']['calls']], ensure_ascii=False, indent=2))
