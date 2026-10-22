"""Retained Core-derived medical history, atomic with the source command (#321).

No widget, timer, render side effect, seed backfill or manual medical CRUD.
The internal Core facade supplies its actor-bound source capability snapshot;
automatic derived documentation grants no manual medical editing permission.
"""

from copy import deepcopy
from datetime import datetime

from .errors import ConflictError, ValidationError
from .json_codec import dumps, loads
from .repositories import _execute, _fetchone, _placeholder, _json_placeholder, deterministic_record_id


NAMESPACE = 'medical.core-events'
POLICY = 'medical_history_policy'
VERSION = 'clinical_source_revision'
CLINICAL_TYPES = frozenset(('surgery', 'oocyte_retrieval', 'embryo_transfer',
                            'pregnancy', 'birth', 'abortion', 'special_measurement'))
BUILTIN_TYPES = CLINICAL_TYPES | frozenset(('sperm_donation', 'pregnancy_verification',
                                          'pgf', 'fsh', 'progesterone', 'measurement'))


def clinical_date(value):
    text = str(value or '').strip()
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        try:
            return datetime.strptime(text, '%d.%m.%Y').date().isoformat()
        except ValueError as exc:
            raise ValidationError('Invalid clinical source date') from exc


def business_payload(event):
    return {key: deepcopy(value) for key, value in event.items() if key not in (POLICY, VERSION)}


class ClinicalMirrorTransaction:
    def __init__(self, adapter, connection, context, timestamp):
        self.adapter, self.connection = adapter, connection
        self.context, self.timestamp = context, timestamp
        self.states, self.dirty = {}, set()
        self.definitions = None

    def _read(self, namespace, key, *, lock=False):
        mark = _placeholder(self.adapter)
        row = _fetchone(self.connection,
                        f'SELECT payload_json FROM domain_records WHERE namespace={mark} AND record_id={mark}'
                        + (' FOR UPDATE' if lock and self.adapter.dialect != 'sqlite' else ''),
                        (namespace, key))
        if row is None:
            return None
        data = self.adapter.row_to_dict(row)['payload_json']
        return deepcopy(data) if isinstance(data, dict) else loads(data, {})

    def state(self, ipid):
        if ipid not in self.states:
            # Reserve then lock one affected animal's ledger, including the
            # absent-row case. No global population/medical-history scan.
            mark, jmark = _placeholder(self.adapter), _json_placeholder(self.adapter)
            _execute(self.connection,
                     f'INSERT INTO domain_records(namespace,record_id,payload_json,revision,created_at,updated_at) '
                     f'VALUES({mark},{mark},{jmark},1,{mark},{mark}) ON CONFLICT(namespace,record_id) DO NOTHING',
                     (NAMESPACE, ipid, dumps({'sources': {}, 'entries': []}), self.timestamp, self.timestamp))
            self.states[ipid] = self._read(NAMESPACE, ipid, lock=True)
        return self.states[ipid]

    def authorize(self, action):
        if self.context.get('permissions', {}).get(action) is not True:
            raise ValidationError('Clinical source operation is not authorized: ' + action)

    def policy(self, event_type):
        if event_type in BUILTIN_TYPES:
            return {'mirror': event_type in CLINICAL_TYPES, 'definition_id': 'builtin:' + event_type,
                    'definition_revision': 1, 'recorded_at': self.timestamp}
        if self.definitions is None:
            registry = self._read('configuration', 'role-block-presets') or {}
            self.definitions = {str(item.get('event_type', '')).casefold(): item
                                for item in registry.get('event_definitions', [])}
        definition = self.definitions.get(event_type, {})
        flag = definition.get('mirror_in_medical_history', False)
        if not isinstance(flag, bool):
            raise ValidationError('mirror_in_medical_history must be a boolean')
        return {'mirror': bool(flag and definition.get('active', True)),
                'definition_id': definition.get('stable_id') or definition.get('id', event_type),
                'definition_revision': definition.get('revision', 1),
                'label': definition.get('name') or event_type, 'recorded_at': self.timestamp}

    def _entry(self, ipid, source_id, source, revision, *, action='original', original_id=''):
        effective = source.get('datum') or source.get('date')
        return {'id': deterministic_record_id(NAMESPACE, ipid, source_id, action, revision),
                'date': clinical_date(effective) if action == 'original' else clinical_date(self.timestamp),
                'effective_date': clinical_date(effective), 'recorded_at': self.timestamp,
                'entry_type': 'core_event' if action == 'original' else 'core_' + action,
                'status_type': str(source.get('event_type', '')), 'issue_id': '',
                'condition_key': '', 'condition_label_snapshot': '',
                'note': '', 'signature': str(self.context.get('signature') or self.context.get('actor_login') or 'local-user'),
                'actor_login': str(self.context.get('actor_login') or ''),
                'actor_role': str(self.context.get('actor_role') or ''),
                'source_namespace': 'core.events', 'source_animal_ipid': ipid,
                'source_event_id': source_id, 'source_revision': revision,
                'original_entry_id': original_id, 'source_payload': deepcopy(source),
                'linked_document_ids': []}

    def event(self, ipid, payload, previous, *, new_animal=False):
        """Complete policy/revision on the canonical accepted event before SQL."""
        source_id = payload['event_id']
        if previous is not None and POLICY not in previous:
            # Existing historical rows never acquire a current policy on edit.
            if POLICY in payload or VERSION in payload:
                raise ValidationError('Historical events cannot acquire a new mirroring policy')
            return
        if previous is not None and payload.get(POLICY, previous[POLICY]) != previous[POLICY]:
            raise ConflictError('Recording-time clinical policy is immutable')
        if previous is not None and dumps(business_payload(previous)) == dumps(business_payload(payload)):
            payload[POLICY] = deepcopy(previous[POLICY])
            payload[VERSION] = previous[VERSION]
            return
        self.authorize('core.create_animals' if new_animal else 'core.edit_animal_measurements')
        if not new_animal:
            self.authorize('core.edit_animal_core')
        state = self.state(ipid)
        old = state['sources'].get(source_id)
        if old and old.get('deleted'):
            raise ConflictError('A deleted clinical source identity cannot be reused')
        if previous is not None:
            if not old or type(payload.get(VERSION)) is not int or payload.get(VERSION) != old['revision']:
                raise ConflictError('Stale clinical source revision')
            policy = old['policy']
            if payload.get(POLICY, policy) != policy:
                raise ConflictError('Recording-time clinical policy is immutable')
        else:
            if old:
                raise ConflictError('Clinical source identity already recorded')
            policy = self.policy(str(payload['event_type']).casefold())
        revision = old['revision'] + 1 if old else 1
        payload[POLICY], payload[VERSION] = deepcopy(policy), revision
        source = business_payload(payload)
        if policy['mirror']:
            entry = self._entry(ipid, source_id, source, revision,
                                action='correction' if old else 'original',
                                original_id=old.get('original_id', '') if old else '')
            if policy.get('label'):
                entry['condition_label_snapshot'] = policy['label']
            if old:
                entry['previous_source_payload'] = deepcopy(old['payload'])
            state['entries'].append(entry)
            original_id = old['original_id'] if old else entry['id']
        else:
            original_id = ''
        state['sources'][source_id] = dict(policy=deepcopy(policy), payload=source,
                                           revision=revision, original_id=original_id, deleted=False)
        self.dirty.add(ipid)

    def delete(self, ipid, previous, *, animal_deleted=False):
        if POLICY not in previous:
            return
        if animal_deleted:
            self.authorize('core.delete_animals')
        else:
            self.authorize('core.edit_animal_core')
            self.authorize('core.edit_animal_measurements')
        state = self.state(ipid)
        old = state['sources'].get(previous['event_id'])
        if old is None or old['revision'] != previous.get(VERSION):
            raise ConflictError('Stale clinical source deletion')
        if old.get('deleted'):
            return
        revision = old['revision'] + 1
        if old['original_id']:
            entry = self._entry(
                ipid, previous['event_id'], old['payload'], revision,
                action='deletion', original_id=old['original_id'])
            entry['condition_label_snapshot'] = old['policy'].get('label', '')
            state['entries'].append(entry)
        old.update(revision=revision, deleted=True)
        self.dirty.add(ipid)

    def lifecycle(self, ipid, record, previous):
        sources = []
        if previous is None and str(record.get('rolle', '')).casefold() in ('offspring', 'experimental_offspring'):
            self.authorize('core.create_animals')
            sources.append(dict(event_id='own-birth', event_type='own_birth', datum=record['birth_date']))
        previous_ids = {event.get('event_id') for event in (previous or {}).get('lifecycle_events', [])}
        for event in record.get('lifecycle_events', []):
            if event.get('event_type') == 'departure' and event.get('event_id') not in previous_ids and not record.get('death_date'):
                self.authorize('core.archive_animals')
                sources.append(dict(event, datum=event.get('date')))
        for source in sources:
            source.setdefault('recorded_role', str(record.get('rolle') or record.get('role_id') or ''))
            source.setdefault('project', str(record.get('project') or ''))
            if record.get('project_period_id'):
                source.setdefault('project_period_id', record['project_period_id'])
            state = self.state(ipid)
            source_id = 'lifecycle:' + source['event_id']
            if source_id not in state['sources']:
                entry = self._entry(ipid, source_id, source, 1)
                entry['source_namespace'] = 'core.lifecycle'
                state['entries'].append(entry)
                state['sources'][source_id] = {'original_id': entry['id'], 'revision': 1}
                self.dirty.add(ipid)

    def flush(self):
        mark, jmark = _placeholder(self.adapter), _json_placeholder(self.adapter)
        for ipid in sorted(self.dirty):
            _execute(self.connection,
                     f'UPDATE domain_records SET payload_json={jmark},revision=revision+1,updated_at={mark} '
                     f'WHERE namespace={mark} AND record_id={mark}',
                     (dumps(self.states[ipid]), self.timestamp, NAMESPACE, ipid))
