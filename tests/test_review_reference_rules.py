"""Offline schema hints mirror existing reference gates, not semantic entailment."""
import itertools
import re

import pytest
from evograph.application.plan_budget import compact_schema
from evograph.application.plan_review import (
    OBLIGATION_REF_PATTERN,
    PACKET_POINTER_PATTERN,
    BatchSemanticReview,
    MaterialityEvidence,
    _pointer_tokens,
    normalize_batch_review,
    resolve_packet_pointer,
)
from test_review_materiality import batch_for, material_issue, observation
from test_review_materiality import context as context

# Pin the pre-alignment rule independently: extracting it must not broaden the
# obligation anchors, even if the runtime and schema change together later.
LEGACY_OBLIGATION_RULE = (
    r"/current_input|/candidate/(?:target/statement|target_draft|"
    r"behaviors/[0-9]+/statement|milestones/[0-9]+/(?:intent|scope/[0-9]+)|"
    r"source_milestones/[0-9]+/source_behaviors/[0-9]+/statement|"
    r"plan_contract/(?:sources/[0-9]+/(?:text|reference_context/(?:references|attachments)/"
    r".+/(?:text|excerpt|content)|evidence/[0-9]+/result/(?:text|excerpt|content))|"
    r"requirements/[0-9]+/quote|process_constraints/[0-9]+/quote))|"
    r"/reference_context/(?:references|attachments)/.+/(?:text|excerpt|content)"
)


def test_schema_advertises_shared_patterns_without_changing_field_bounds():
    schema = compact_schema(BatchSemanticReview.model_json_schema())
    material = schema['$defs']['MaterialityEvidence']['properties']
    obligation = material['obligation_ref']
    assert obligation == {'type': 'string', 'minLength': 1, 'maxLength': 240,
                          'pattern': OBLIGATION_REF_PATTERN}
    assert material['provider_ref'] == {
        'anyOf': [{'type': 'string', 'maxLength': 240, 'pattern': PACKET_POINTER_PATTERN},
                  {'type': 'null'}], 'default': None}
    assert 'provider_ref' not in schema['$defs']['MaterialityEvidence']['required']
    assert 'consumer_owner_id' not in schema['$defs']['MaterialityEvidence']['required']
    for field in [material['boundary_refs'],
                  schema['$defs']['BatchIssue']['properties']['evidence_refs'],
                  schema['$defs']['BatchObservation']['properties']['evidence_refs']]:
        assert field == {'type': 'array', 'minItems': 1, 'maxItems': 8,
                         'items': {'type': 'string', 'pattern': PACKET_POINTER_PATTERN}}


def test_pointer_pattern_preserves_actual_parser_syntax():
    # Empty tokens, trailing slashes, Unicode, control characters and a leading
    # zero in a token are syntactically valid. Resolution checks are separate.
    pointers = ['', '/', 'owner-id', None, 42, '/a/', '//', '/~00', '/00', '/中/文']
    alphabet = ('a', '/', '~', '0', '1', '2', '\n', '\x00')
    pointers += ['/' + ''.join(tokens) for n in range(5)
                 for tokens in itertools.product(alphabet, repeat=n)]
    for pointer in pointers:
        legacy = (isinstance(pointer, str) and pointer.startswith('/') and pointer != '/'
                  and not re.search(r'~(?![01])', pointer))
        advertised = isinstance(pointer, str) and bool(re.search(PACKET_POINTER_PATTERN, pointer))
        assert advertised == legacy, repr(pointer)
        if legacy:
            assert _pointer_tokens(pointer) == tuple(
                token.replace('~1', '/').replace('~0', '~') for token in pointer[1:].split('/'))
        else:
            with pytest.raises(ValueError):
                _pointer_tokens(pointer)


@pytest.mark.parametrize('pointer', [
    '/current_input', '/candidate/target/statement', '/candidate/target_draft',
    '/candidate/behaviors/0/statement', '/candidate/milestones/0/intent',
    '/candidate/milestones/0/scope/0',
    '/candidate/source_milestones/0/source_behaviors/0/statement',
    '/candidate/plan_contract/sources/0/text',
    '/candidate/plan_contract/sources/0/reference_context/references/0/text',
    '/candidate/plan_contract/sources/0/reference_context/attachments/a/b/excerpt',
    '/candidate/plan_contract/sources/0/evidence/0/result/content',
    '/candidate/plan_contract/requirements/0/quote',
    '/candidate/plan_contract/process_constraints/0/quote',
    '/reference_context/references/a~1b/content', '/reference_context/attachments/0/text',
    '/candidate/plan_contract/bindings/4/mechanism',
    '/candidate/plan_contract/bindings/6/mechanism',
    '/candidate/behaviors/0/id', '/before/behaviors/0/statement',
    '/candidate/milestones/0/status', '/candidate/architecture/summary',
    '/reference_context/attachments/a\nb/text', '/current_input\n',
    '/reference_context/attachments/0/text\n',
    '/reference_context/attachments/a~2/text', '/reference_context/attachments/a~/text',
])
def test_obligation_schema_and_runtime_preserve_exact_legacy_rules(pointer):
    legacy = (bool(re.fullmatch(LEGACY_OBLIGATION_RULE, pointer))
              and not re.search(r'~(?![01])', pointer))
    assert bool(re.fullmatch(OBLIGATION_REF_PATTERN, pointer)) == legacy
    assert bool(re.search(OBLIGATION_REF_PATTERN, pointer)) == legacy


@pytest.mark.parametrize('prefix', [
    '/reference_context/attachments/',
    '/candidate/plan_contract/sources/0/reference_context/references/',
])
@pytest.mark.parametrize('separator', ['\r', '\u2028', '\u2029', '\n'])
def test_obligation_wildcards_preserve_python_not_lf_semantics(prefix, separator):
    pointer = prefix + 'a' + separator + 'b/text'
    legacy = bool(re.fullmatch(LEGACY_OBLIGATION_RULE, pointer))
    assert legacy == (separator != '\n')
    assert bool(re.search(OBLIGATION_REF_PATTERN, pointer)) == legacy
    # ECMAScript . also excludes CR/U+2028/U+2029; an explicit not-LF class
    # keeps the advertised wildcard identical to the legacy Python rule.
    assert '.+' not in OBLIGATION_REF_PATTERN
    assert OBLIGATION_REF_PATTERN.count(r'[^\n]+') == 2


def test_mechanisms_remain_boundary_evidence_but_never_obligation_anchors(context):
    project, _, _, packet = context
    issue = material_issue()
    assert normalize_batch_review(project, packet, batch_for(packet, issues=[issue]))
    for index in (0, 1):
        pointer = f'/candidate/plan_contract/bindings/{index}/mechanism'
        issue['materiality'].update(obligation_ref=pointer,
                                   obligation_excerpt=resolve_packet_pointer(packet, pointer))
        assert not re.search(OBLIGATION_REF_PATTERN, pointer)
        with pytest.raises(ValueError, match='准确的来源或契约义务文本字段'):
            normalize_batch_review(project, packet, batch_for(packet, issues=[issue]))


def test_advisory_metadata_does_not_change_model_parse_or_list_item_bounds(context):
    issue = material_issue()
    long_pointer = '/' + 'x' * 240
    issue['materiality']['boundary_refs'] = [long_pointer]
    issue['evidence_refs'] = [long_pointer]
    row = observation()
    row['evidence_refs'] = [long_pointer]
    batch = batch_for(context[3], issues=[issue], observations=[row])
    assert batch.issues[0].materiality.boundary_refs == [long_pointer]
    assert batch.observations[0].evidence_refs == [long_pointer]
    with pytest.raises(ValueError, match='具体包内记录'):
        normalize_batch_review(context[0], context[3], batch)
    for field in ('provider_ref', 'obligation_ref'):
        with pytest.raises(ValueError, match='240'):
            MaterialityEvidence.model_validate({**material_issue()['materiality'], field: long_pointer})
    # Invalid hints still parse for auditing; no automatic normalization occurs.
    malformed = MaterialityEvidence.model_validate({**material_issue()['materiality'],
        'provider_ref': 'slice-5-reminder-email', 'obligation_ref': '/not-an-obligation'})
    assert malformed.provider_ref == 'slice-5-reminder-email'
    assert malformed.obligation_ref == '/not-an-obligation'


@pytest.mark.parametrize('change,message', [
    ({'provider_ref': 'M2'}, '消费者'),
    ({'boundary_refs': ['/candidate/behaviors/0']}, '消费者'),
    ({'consumer_owner_id': None}, '消费者'),
    ({'consumer_owner_id': 'M2'}, '消费者'),
    ({'provider_ref': None}, '消费者'),
    ({'provider_ref': '/candidate/target', 'boundary_refs': ['/candidate/target']}, '未命名'),
    ({'provider_ref': '/candidate/milestones/90',
      'boundary_refs': ['/candidate/milestones/90']}, '具体包内记录'),
    ({'provider_ref': '/candidate/milestones/0',
      'boundary_refs': ['/candidate/milestones/0']}, '自身或声明祖先'),
])
def test_unavailable_prerequisite_still_requires_owner_membership_and_closure(context, change, message):
    issue = material_issue()
    issue['materiality'].update(obligation_ref='/current_input',
        affected_owner_ids=['M1'], gap_kind='unavailable_prerequisite', consumer_owner_id='M1',
        provider_ref='/candidate/milestones/1', boundary_refs=['/candidate/milestones/1'])
    assert normalize_batch_review(context[0], context[3], batch_for(context[3], issues=[issue]))
    issue['materiality'].update(change)
    with pytest.raises(ValueError, match=message):
        normalize_batch_review(context[0], context[3], batch_for(context[3], issues=[issue]))


def test_non_prerequisite_optional_and_null_semantics_are_unchanged(context):
    issue = material_issue()
    assert normalize_batch_review(context[0], context[3], batch_for(context[3], issues=[issue]))
    issue['materiality'].update(consumer_owner_id=None, provider_ref=None)
    assert normalize_batch_review(context[0], context[3], batch_for(context[3], issues=[issue]))
    for change in ({'consumer_owner_id': 'M1'}, {'provider_ref': '/candidate/milestones/1'}):
        issue['materiality'].update(consumer_owner_id=None, provider_ref=None)
        issue['materiality'].update(change)
        with pytest.raises(ValueError, match='只有不可用前置'):
            normalize_batch_review(context[0], context[3], batch_for(context[3], issues=[issue]))
