"""One transactional project identity allocator in the existing catalog."""
from copy import deepcopy
from urllib.parse import quote
from .repositories import _execute, _fetchone, _placeholder, _json_placeholder, now_text
from .json_codec import dumps, loads
from .errors import ValidationError, ConflictError


def identity(username, timestamp, sequence):
    if not str(username).strip():
        raise ValidationError('A signed-in username is required to create a project.')
    return 'project:' + ':'.join((quote(username, safe=''), quote(timestamp, safe=''), str(sequence)))


def server_timestamp(adapter, connection):
    sql = "SELECT CURRENT_TIMESTAMP AS stamp" if adapter.dialect == 'sqlite' else "SELECT CURRENT_TIMESTAMP AT TIME ZONE 'UTC' AS stamp"
    return str(adapter.row_to_dict(_fetchone(connection, sql))['stamp']) + 'Z'


def allocate(catalog, name, username, timestamp, fields=None):
    projects = catalog.setdefault('projects', {})
    if name in projects:
        if not projects[name].get('project_id'):
            raise ValidationError('Project identity is incomplete. Use a current demo database.')
        return projects[name]
    sequence = int(catalog.get('next_project_sequence', 1))
    record = dict(fields or {})
    record.update(project_id=identity(username, timestamp, sequence),
                  identity_creator=username, identity_created_at=timestamp, identity_sequence=sequence)
    projects[name] = record
    catalog['next_project_sequence'] = sequence + 1
    return record


def read_catalog(adapter, connection):
    # Serialize even concurrent first creation when the catalog row is absent.
    if adapter.dialect != 'sqlite':
        _execute(connection, "SELECT pg_advisory_xact_lock(3220322)")
    mark = _placeholder(adapter)
    row = _fetchone(connection, 'SELECT payload_json,revision FROM domain_records WHERE namespace='+mark+' AND record_id='+mark + (' FOR UPDATE' if adapter.dialect != 'sqlite' else ''),
                    ('projects', 'catalog'))
    if row is None:
        return {'version': 1, 'projects': {}, 'next_project_sequence': 1}, 0
    row = adapter.row_to_dict(row)
    raw = row['payload_json']
    return deepcopy(raw if isinstance(raw, dict) else loads(raw)), int(row['revision'])


def write_catalog(adapter, connection, catalog, revision):
    mark, jm, stamp = _placeholder(adapter), _json_placeholder(adapter), now_text()
    if revision:
        _execute(connection, f'UPDATE domain_records SET payload_json={jm},revision=revision+1,updated_at={mark} WHERE namespace={mark} AND record_id={mark}',
                 (dumps(catalog), stamp, 'projects', 'catalog'))
    else:
        _execute(connection, f'INSERT INTO domain_records(namespace,record_id,payload_json,revision,created_at,updated_at) VALUES({mark},{mark},{jm},1,{mark},{mark})',
                 ('projects','catalog',dumps(catalog),stamp,stamp))


def create_project(backend, name, username, fields=None):
    with backend.adapter.transaction(write=True) as connection:
        catalog, revision = read_catalog(backend.adapter, connection)
        if name in catalog['projects']:
            raise ConflictError('A project with this name already exists.')
        timestamp = server_timestamp(backend.adapter, connection)
        record = allocate(catalog, name, username, timestamp, fields)
        write_catalog(backend.adapter, connection, catalog, revision)
    return record


def bind_assignment(record, catalog, *, old=None, username='', timestamp=''):
    """Bind a new/changed assignment; never backfill existing name-only data."""
    name = str(record.get('project') or '').strip()
    if name:
        if old and old.get('project') == name and not old.get('project_id'):
            raise ValidationError('Project identity is incomplete. Use a current demo database.')
        stable_id = record.get('project_id')
        unchanged = bool(old and old.get('project') == name)
        if stable_id and (unchanged or not old):
            project = next((p for p in catalog.get('projects', {}).values() if p.get('project_id') == stable_id), None)
            if project is None:
                project = catalog.get('project_tombstones', {}).get(stable_id)
            if project is None:
                raise ValidationError('Project identity is incomplete. Use a current demo database.')
        else:
            project = allocate(catalog, name, username, timestamp)
        if record.get('project_id') and (not old or old.get('project') == name) and record['project_id'] != project['project_id']:
            raise ValidationError('Project identity does not match the assignment.')
        record['project_id'] = project['project_id']
    else:
        record.pop('project_id', None)
    for period in record.get('project_history', []):
        if not period.get('project_id'):
            raise ValidationError('Project history identity is incomplete. Use a current demo database.')
