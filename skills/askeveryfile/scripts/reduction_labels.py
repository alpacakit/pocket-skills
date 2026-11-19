"""Pure L2c subject contracts, prefix vocabulary and complete relation groups."""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from reduction_contract import reducer_input_value, canonical_sha256
from scan_artifacts import render_json

LABEL_BLOCK_SIZE = 128
LABEL_PROJECTION_VERSION = 3
KEY_ASCII_LIMIT = 32
KEY_PATTERN = rf'^[a-z0-9][a-z0-9._:-]{{0,{KEY_ASCII_LIMIT-1}}}$'
DEFINITION_PATTERN = r'^[^\u0000-\u001f\u007f-\u009f]+$'
DEFINITION_CHAR_LIMIT = 80
DEFINITION_BYTE_LIMIT = 240
LABEL_REASONING_RESERVE_TOKENS = 16384

@dataclass(frozen=True)
class SubjectPrefix:
    subjects: dict[str, dict[str, Any]] = field(default_factory=dict)
    results: tuple[dict[str, str], ...] = ()
    statistics: tuple[dict[str, Any], ...] = ()

    @property
    def chain_sha256(self) -> str:
        return canonical_sha256(list(self.results))

    def vocabulary(self) -> list[list[str]]:
        return [[k, v['definition']] for k, v in sorted(self.subjects.items())]


def position_index(packet: dict[str, Any]) -> dict[str, Any]:
    p = reducer_input_value(packet)
    n = len(p['o'])
    return {'v': LABEL_PROJECTION_VERSION, 'q': p['q'], 'c': p['c'], 'f': p['f'],
            'o': [[i, *r[:6], [[e[0], e[1]] for e in r[6]]] for i, r in enumerate(p['o'])],
            'u': [[n+i, *r] for i, r in enumerate(p['u'])]}


def label_input_value(packet: dict[str, Any], positions: list[int], vocabulary: list[list[str]]) -> dict[str, Any]:
    p = position_index(packet)
    wanted = set(positions)
    return {**p, 'o': [r for r in p['o'] if r[0] in wanted],
            'u': [r for r in p['u'] if r[0] in wanted], 'vocabulary': vocabulary}


def label_output_schema(count: int, total: int) -> dict[str, Any]:
    key = {'type': 'string', 'minLength': 1, 'maxLength': KEY_ASCII_LIMIT, 'pattern': KEY_PATTERN}
    definition = {'type': 'string', 'minLength': 1, 'maxLength': DEFINITION_CHAR_LIMIT, 'pattern': DEFINITION_PATTERN}
    return {'type': 'object', 'additionalProperties': False,
            'required': ['schema_version', 'new_subjects', 'assignments'], 'properties': {
                'schema_version': {'type': 'integer', 'const': 1},
                'new_subjects': {'type': 'array', 'maxItems': count, 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['key', 'definition'],
                    'properties': {'key': key, 'definition': definition}}},
                'assignments': {'type': 'array', 'maxItems': count, 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['key', 'positions'],
                    'properties': {'key': key, 'positions': {'type': 'array', 'minItems': 1,
                        'maxItems': count, 'items': {'type': 'integer', 'minimum': 0, 'maximum': max(total-1, 0)}}}}}}}


def label_shape_errors(value: Any, count: int, total: int) -> list[str]:
    if not isinstance(value, dict) or set(value) != {'schema_version', 'new_subjects', 'assignments'}:
        return ['label output fields mismatch']
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        return ['label output version mismatch']
    for name in ('new_subjects', 'assignments'):
        if not isinstance(value[name], list) or len(value[name]) > count:
            return ['label array count/shape mismatch']
    def key_ok(k: Any) -> bool:
        return isinstance(k, str) and re.fullmatch(KEY_PATTERN, k) is not None
    errors = []
    for r in value['new_subjects']:
        if not isinstance(r, dict) or set(r) != {'key', 'definition'} or not key_ok(r.get('key')):
            errors.append('label subject shape/key mismatch'); continue
        d = r['definition']
        if not isinstance(d, str) or not 1 <= len(d) <= DEFINITION_CHAR_LIMIT or re.fullmatch(DEFINITION_PATTERN, d) is None:
            errors.append('label definition shape mismatch'); continue
        try:
            d.encode('utf-8')
        except UnicodeError:
            errors.append('label definition is not UTF-8 encodable')
    for r in value['assignments']:
        if not isinstance(r, dict) or set(r) != {'key', 'positions'} or not key_ok(r.get('key')):
            errors.append('label assignment shape/key mismatch'); continue
        ps = r['positions']
        if (not isinstance(ps, list) or not 1 <= len(ps) <= count
                or any(type(p) is not int or not 0 <= p < total for p in ps)):
            errors.append('label assignment positions shape mismatch')
    return errors


def label_closure(value: Any, positions: list[int], total: int, prefix: SubjectPrefix,
                  group_limit: int) -> list[str]:
    errors = label_shape_errors(value, len(positions), total)
    if errors:
        return errors
    new = [r['key'] for r in value['new_subjects']]
    assigned = [r['key'] for r in value['assignments']]
    if len(new) != len(set(new)) or set(new) & set(prefix.subjects):
        errors.append('label new keys duplicate or redeclare existing keys')
    if any(len(r['definition'].encode('utf-8')) > DEFINITION_BYTE_LIMIT for r in value['new_subjects']):
        errors.append('label definition exceeds 240 UTF-8 bytes')
    if len(assigned) != len(set(assigned)):
        errors.append('label assignment keys repeat')
    if set(assigned) - set(new) - set(prefix.subjects):
        errors.append('label references unknown key')
    if Counter(p for r in value['assignments'] for p in r['positions']) != Counter(positions):
        errors.append('label positions must exactly cover the block once')
    if any(len(prefix.subjects.get(r['key'], {}).get('positions', [])) + len(r['positions']) > group_limit
           for r in value['assignments']):
        errors.append('label cumulative subject group exceeds capacity; no splitting')
    return errors


def advance_prefix(prefix: SubjectPrefix, value: Any, positions: list[int], total: int,
                   group_limit: int, job_id: str, result_sha256: str) -> SubjectPrefix:
    errors = label_closure(value, positions, total, prefix, group_limit)
    if errors:
        raise ValueError('; '.join(errors))
    subjects = {k: {'definition': v['definition'], 'positions': list(v['positions'])}
                for k, v in prefix.subjects.items()}
    used = {r['key'] for r in value['assignments']}
    adopted = [r for r in value['new_subjects'] if r['key'] in used]
    for r in adopted:
        subjects[r['key']] = {'definition': r['definition'], 'positions': []}
    for r in value['assignments']:
        subjects[r['key']]['positions'].extend(sorted(r['positions']))
    stats = {'job_id': job_id, 'new_keys': len(adopted), 'reused_keys': len(used & set(prefix.subjects)),
             'declared_new_keys': len(value['new_subjects'])}
    return SubjectPrefix(subjects, (*prefix.results, {'job_id': job_id, 'sha256': result_sha256}),
                         (*prefix.statistics, stats))


def subject_groups(prefix: SubjectPrefix, total: int) -> list[list[int]]:
    groups = sorted([sorted(v['positions']) for v in prefix.subjects.values()], key=lambda ps: ps[0])
    if Counter(p for ps in groups for p in ps) != Counter(range(total)):
        raise ValueError('Label prefix does not exactly cover the whole packet')
    return groups


def worst_vocabulary(count: int) -> list[list[str]]:
    # Valid 80-codepoint, 240-byte strings can serialize to 266 bytes due to
    # escaped quotes: 53*4 + 26*1 + 1*2 decoded bytes; 26 extra escaped bytes.
    definition = chr(0x10ffff)*53 + '"'*26 + 'é'
    assert len(definition) == DEFINITION_CHAR_LIMIT and len(definition.encode('utf-8')) == DEFINITION_BYTE_LIMIT
    return [['k'+str(i).zfill(KEY_ASCII_LIMIT-1), definition] for i in range(count)]


def label_reserve(count: int, total: int) -> dict[str, Any]:
    # Keep a conservative 320 serialized-byte payload budget per definition.
    # One new declaration+singleton assignment per position bounds formatting;
    # moving positions to a shared assignment removes much larger object costs.
    worst = {'schema_version': 1,
             'new_subjects': [{'key': 'k'*KEY_ASCII_LIMIT, 'definition': chr(0x10ffff)*DEFINITION_CHAR_LIMIT} for _ in range(count)],
             'assignments': [{'key': 'k'*KEY_ASCII_LIMIT, 'positions': [max(total-1, 0)]} for _ in range(count)]}
    size = len(render_json(worst).encode('utf-8'))
    return {'kind': 'serialized_utf8_byte_upper_bound_plus_reasoning',
            'serialization': 'render_json; all new subjects; ASCII32; 320-byte definition payload; singleton assignments; maximum position width',
            'definition_utf8_byte_limit': DEFINITION_BYTE_LIMIT, 'definition_max_serialized_payload_bytes': 266,
            'definition_payload_budget_bytes': 320, 'max_serialized_utf8_bytes': size,
            'reasoning_reserve_tokens': LABEL_REASONING_RESERVE_TOKENS,
            'computed_tokens': size + LABEL_REASONING_RESERVE_TOKENS, 'backend_enforced_maximum': False}
