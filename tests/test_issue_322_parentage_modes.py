"""Focused #322 date, project identity, transaction and real-dialog witnesses."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import pytest
from Plugins.core.parent_eligibility import parent_eligible
from Plugins.core.project_periods import project_at_date
from Plugins.core.backend import ProgTrackBackend
from Plugins.core.backend.project_identity import create_project
from Plugins.core.backend.errors import ValidationError, ConflictError
from tests.test_current_backend_runtime import runtime_paths


def animal(name, sex='Male', birth='01.01.2010', **extra):
    return dict(name=name, sex=sex, birth_date=birth, species='Mus musculus',
                origin='Institute', rolle='breeding_animal', **extra)


@pytest.mark.parametrize('slot,sex,death,expected', [
    ('sperm_donor','Male','',True), ('sperm_donor','Male','01.01.2019',False),
    ('sperm_donor','Male','02.01.2019',True), ('sperm_donor','Male','31.12.2018',False),
    ('sperm_donor','Male','01.01.2020',True), ('sperm_donor','Male','02.01.2020',True),
    ('egg_donor','Female','31.12.2019',False), ('egg_donor','Female','01.01.2020',True),
    ('egg_donor','Female','',True), ('egg_donor','Female','invalid',False),
    ('sperm_donor','Male','invalid',False), ('egg_donor','Male','',False),
])
def test_natural_boundaries(slot, sex, death, expected):
    target=animal('child',birth='01.01.2020')
    parent=animal('parent',sex,death_date=death,project='Other',archived=True)
    assert parent_eligible(target,parent,slot,'natural') is expected


def test_leap_birth_and_invalid_births():
    target=animal('child',birth='29.02.2020')
    assert not parent_eligible(target,animal('p',death_date='28.02.2019'),'sperm_donor','natural')
    assert parent_eligible(target,animal('p',death_date='01.03.2019'),'sperm_donor','natural')
    for birth in ('', 'invalid','29.02.2020','01.03.2020'):
        assert not parent_eligible(target,animal('p',birth=birth),'sperm_donor','natural')


def test_et_uses_birth_period_identity_and_any_father_period_without_mutation():
    target=animal('child',birth='01.01.2020',project='Current',project_id='B',project_entry_date='01.01.2022',
                  project_history=[dict(project='Old title',project_id='A',entry_date='01.01.2019',leave_date='01.01.2022')])
    parent=animal('father',project_id='B',death_date='01.01.2011',
                  project_history=[dict(project='Renamed',project_id='A',entry_date='01.01.2025')])
    before=deepcopy((target,parent))
    assert project_at_date(target,'01.01.2020') == 'A'
    assert parent_eligible(target,parent,'sperm_donor','embryo_transfer')
    assert (target,parent) == before
    parent['project_history']=[]
    assert not parent_eligible(target,parent,'sperm_donor','embryo_transfer')
    target['project_history'][0].pop('project_id')
    assert not parent_eligible(target,parent,'sperm_donor','embryo_transfer')


@pytest.fixture
def backend(tmp_path):
    value=ProgTrackBackend(runtime_paths(tmp_path,application_root=tmp_path),acquire_process_lock=False,bootstrap_seed=False)
    yield value
    value.close()


def test_atomic_allocator_concurrency_tombstone_sequence_and_missing_ids(backend):
    with ThreadPoolExecutor(max_workers=4) as pool:
        created=list(pool.map(lambda n:create_project(backend,f'P{n}','tester'),range(8)))
    assert len({p['project_id'] for p in created}) == 8
    assert sorted(p['identity_sequence'] for p in created) == list(range(1,9))
    catalog,rev=backend.records.get_with_revision('projects','catalog')
    removed=catalog['projects'].pop('P0')
    catalog.setdefault('project_tombstones',{})[removed['project_id']]=removed
    backend.records.put('projects','catalog',catalog,expected_revision=rev)
    again=create_project(backend,'P0','tester')
    assert again['identity_sequence'] == 9
    assert again['project_id'] != removed['project_id']
    assert again['identity_creator'] == 'tester'
    assert 'tester' in again['project_id']
    with pytest.raises(ConflictError):
        create_project(backend,'P0','tester')
    backend.records.put('projects','catalog',{'projects':{'Bad':{}}})
    with pytest.raises(ValidationError):
        backend.save_core_data({'animals':{'child':animal('child',project='Bad')}})
    assert not backend.load_core_data()['animals']


def test_implicit_creation_commits_with_animal_and_rolls_back_on_failure(backend):
    child=animal('child',project='Implicit',birth='01.01.2020')
    backend.save_core_data({'animals':{'child':child}},clinical_context={'actor_login':'tester'})
    record=backend.load_core_data()['animals']['child']
    catalog=backend.records.get('projects','catalog')
    assert record['project_id'] == catalog['projects']['Implicit']['project_id']
    assert record['project_entry_date'] == '01.01.2020'
    assert record.get('project_id') and 'project_id' not in child
    failing=animal('bad',project='Must rollback',birth='invalid')
    with pytest.raises((ValueError,ValidationError)):
        backend.save_core_data({'animals':{'bad':failing}},clinical_context={'actor_login':'tester'})
    assert 'Must rollback' not in backend.records.get('projects','catalog')['projects']
    assert 'child' in backend.load_core_data()['animals']


def test_backend_rejects_new_invalid_parent_but_preserves_committed_links(backend):
    create_project(backend,'Birth project','tester')
    data={'animals':{'child':animal('child',birth='01.01.2020',parentage_mode='natural',samenspender='father'),
                     'father':animal('father',death_date='01.01.2019')}}
    with pytest.raises(ValidationError):
        backend.save_core_data(data)
    data['animals']['father']['death_date']='02.01.2019'
    backend.save_core_data(data)
    saved=backend.load_core_data()
    token=backend.animals.parentage_revision_for_snapshot(saved)
    saved['animals']['father']['death_date']='01.01.2011'
    backend.save_core_data(saved)
    assert backend.load_core_data()['animals']['child']['samenspender']=='father'
    with pytest.raises(ConflictError):
        backend.save_core_data(saved,expected_parentage_revision=token)


def test_seed_identity_references_and_import_roundtrip(tmp_path):
    from source.seed.compiler import build_bundle
    bundle=build_bundle()
    catalog=bundle.domains['projects','catalog']
    ids={r['project_id'] for r in catalog['projects'].values()}
    assert len(ids)==len(catalog['projects'])
    assert catalog['next_project_sequence']>max(r['identity_sequence'] for r in catalog['projects'].values())
    for section in ('animals','archived_animals'):
        for record in bundle.core[section].values():
            if record.get('project'):
                assert record['project_id']==catalog['projects'][record['project']]['project_id']
            for period in record.get('project_history',[]):
                assert period['project_id'] in ids
    backend=ProgTrackBackend(
        runtime_paths(tmp_path),
        acquire_process_lock=False,
        choose_demo_seed=lambda: True,
    )
    try:
        assert len(backend.load_core_data()['animals'])==len(bundle.core['animals'])
        assert backend.records.get('projects','catalog')['next_project_sequence']==catalog['next_project_sequence']
    finally:
        backend.close()


@pytest.fixture
def dialogs():
    from tests import test_phase023_core_repair_regressions as support
    helper=support.CoreRepairTests('runTest')
    helper.setUpClass()
    helper.setUp()
    yield helper, support.MAIN
    helper.tearDown()
    helper.doCleanups()


def test_real_picker_mode_switch_cross_project_and_commit(dialogs):
    helper,main=dialogs
    app=helper.host()
    app._load_species_options=lambda:['Callithrix jacchus']
    app._normalize_species_value=lambda value:str(value or '').strip()
    app._project_visibility_scope=lambda **kwargs:(False,{'Visible'})
    child=helper.record('offspring')
    child.update(project='Visible',project_id='A',project_entry_date='01.01.2019')
    mother=helper.record('donor');mother.update(name='Mother',project='Other',birth_date='01.01.2010')
    father=helper.record('sperm_donor');father.update(name='Father',sex='Male',project='Other',birth_date='01.01.2010',
        project_history=[dict(project_id='A',entry_date='01.01.2025')],death_date='01.01.2011')
    app.animals={'child':child,'mother':mother,'father':father}
    group,fields=app._core_parentage_group('child',child)
    group.setParent(app)
    form=main.QFormLayout()
    section=app._add_parent_mode_selector(form,group,fields,'natural')
    section.setParent(app)
    assert fields['egg_donor'].findData('mother')>0
    assert fields['sperm_donor'].findData('father')<0
    buttons=section.findChildren(main.QRadioButton)
    next(b for b in buttons if b.text()=='Embryo transfer').click()
    assert fields['sperm_donor'].findData('father')>0
    fields['sperm_donor'].setCurrentIndex(fields['sperm_donor'].findData('father'))
    candidate=deepcopy(child)
    app._apply_core_parentage_if_changed('child',candidate,child,fields)
    assert candidate['parentage_mode']=='embryo_transfer'
    assert candidate['samenspender']=='father'


def test_every_real_editor_reopens_persisted_mode_and_cancel_preserves_parentage(dialogs):
    helper,main=dialogs
    for method,role in helper.weight_editor_cases():
        app=helper.host()
        app._role_dialog_blocks=lambda *_a,**_k:['weight','parenting']
        record=helper.record(role);record['parentage_mode']='embryo_transfer'
        main.synchronize_experimental_limits(record, role)
        app.animals['Subject']=record
        before=deepcopy(record)
        kwargs = {'role_value': role} if method in ('_dlg_basic_animal_role','_dlg_versuchstier') else ({'default_role': role} if method == '_dlg_female_animal' else {})
        dialog=helper.open_dialog(app,method,**kwargs)
        checked=[b.text() for b in dialog.findChildren(main.QRadioButton) if b.isChecked()]
        assert 'Embryo transfer' in checked,method
        dialog.reject()
        assert {key: app.animals['Subject'].get(key) for key in ('parentage_mode','eizellspenderin','samenspender','ziehmutter','ziehvater','project','project_id','project_history')} == {key: before.get(key) for key in ('parentage_mode','eizellspenderin','samenspender','ziehmutter','ziehvater','project','project_id','project_history')}


def test_project_tab_create_archive_restore_delete_recreate_and_conflict(dialogs):
    from unittest.mock import patch
    from Plugins.Projects_Track.project_track_tab import ProjectTrackTab
    from Plugins.Projects_Track.ProjectsTrack_plugin import HistoryStore
    from PyQt6.QtWidgets import QInputDialog, QMessageBox
    helper,main=dialogs
    app=helper._307_sqlite_host(population=0)
    history=HistoryStore('',app.backend)
    tab=ProjectTrackTab(app,app.messages,history)
    helper.hosts.append(tab)
    with patch.object(QInputDialog,'getText',return_value=('Test Project',True)), patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):
        tab._on_new_project()
        first=app.backend.records.get('projects','catalog')['projects']['Test Project']['project_id']
        tab._show_archived_chk.setChecked(True)
        tab._current_project='Test Project'
        tab._on_archive()
        assert history.is_archived('Test Project')
        tab._on_restore_project()
        assert not history.is_archived('Test Project')
        tab._current_project='Test Project'
        tab._on_archive()
        tab._current_project='Test Project'
        tab._on_delete_project()
        assert first in app.backend.records.get('projects','catalog')['project_tombstones']
        tab._on_new_project()
        second=app.backend.records.get('projects','catalog')['projects']['Test Project']['project_id']
        assert first!=second
    create_project(app.backend,'Concurrent project','tester')
    with patch.object(QMessageBox,'warning') as warning:
        assert tab._save_data() is False
        warning.assert_called_once()
    assert 'Concurrent project' in app.backend.records.get('projects','catalog')['projects']
