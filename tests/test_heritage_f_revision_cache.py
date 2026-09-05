"""Regression tests for issue #158 lineage-aware inbreeding state."""

from __future__ import annotations

import copy
import unittest

from Plugins.Heritage_Track.heritage_store import HeritageStore
from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackPlugin, HeritageTrackWidget
from Plugins.Heritage_Track.inbreeding import InbreedingCalculator
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine


class _MemoryRecords:
    def __init__(self, initial=None):
        self.values = copy.deepcopy(initial or {})
        self.put_count = 0

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def put(self, namespace, record_id, payload, **_kwargs):
        self.put_count += 1
        self.values[(namespace, record_id)] = copy.deepcopy(payload)
        return self.put_count


class _Backend:
    def __init__(self, graph=None):
        self.records = _MemoryRecords(
            {('heritage', 'graph'): graph} if graph is not None else None
        )


class _App:
    def __init__(self, graph=None):
        self.backend = _Backend(graph)
        self.messages = {}
        self.master_track = type(
            "_Master",
            (),
            {"current_unit_id": "unit-a", "current_username": "researcher"},
        )()
        self.animals = {
            'Mother': {
                'name': 'Mother', 'species': 'Callithrix jacchus',
                'sex': 'female', 'birth_date': '01.01.2010',
            },
            'Father': {
                'name': 'Father', 'species': 'Callithrix jacchus',
                'sex': 'male', 'birth_date': '01.01.2010',
            },
        }
        self.archived = {}
        if graph is None:
            self.backend.records.values[('heritage', 'graph')] = {
                'version': '1.0.0',
                'animals': {
                    'Child': {
                        'name': 'Child', 'species': 'Callithrix jacchus',
                        'sex': 'female', 'birth_date': '01.01.2020',
                        'heritage_only': True, 'dummy_kind': 'direct',
                        'persistence_kind': 'direct_dummy', 'unit_id': 'unit-a',
                    }
                },
            }

    def _master_can(self, action):
        return action in {'heritage.view', 'heritage.edit_links'}


def _record(name, *, sex='female', parents=()):
    mother = parents[0] if len(parents) > 0 else ''
    father = parents[1] if len(parents) > 1 else ''
    return {
        'name': name,
        'ipid': name,
        'species': 'Callithrix jacchus',
        'sex': sex,
        'heritage_only': True,
        'dummy_kind': 'direct',
        'persistence_kind': 'direct_dummy',
        'unit_id': 'unit-a',
        'eizellspenderin': mother,
        'samenspender': father,
    }


class HeritageFRevisionTest(unittest.TestCase):
    def test_malformed_state_propagates_to_descendants_and_unresolved_parents(self):
        parent_map = {
            'Child': ('Missing', 'Father'),
            'Grandchild': ('Child', None),
            'CycleA': ('CycleB', None),
            'CycleB': ('CycleA', None),
        }
        malformed = HeritageTrackWidget._find_malformed_f_nodes(
            parent_map, {'Child', 'Grandchild', 'Father', 'CycleA', 'CycleB'}
        )
        self.assertEqual(malformed, {'Child', 'Grandchild', 'CycleA', 'CycleB'})
        self.assertEqual(InbreedingCalculator(parent_map).cycle_nodes, {'CycleA', 'CycleB'})

    def test_cache_requires_structured_lineage_metadata_and_reuses_matching_lineage(self):
        graph = {
            'animals': {
                name: {
                    'name': name, 'ipid': name, 'species': 'Callithrix jacchus',
                    'sex': 'female', 'inbreeding_f': 0.9,
                    'heritage_only': True, 'dummy_kind': 'direct',
                    'persistence_kind': 'direct_dummy', 'unit_id': 'unit-a',
                }
                for name in ('Child', 'Mother', 'Father')
            }
        }
        app = _App(graph)
        plugin = HeritageTrackPlugin(app)
        plugin.schedule_store_flush = lambda: None
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget.plugin = plugin
        widget._render_store_animals = plugin.store.get_all_entries()
        widget._get_node_record = lambda node: app.animals.get(node, {})

        def make_engine(mother='Mother'):
            animals = {
                'Child': _record('Child', parents=(mother, 'Father')),
                'Mother': _record('Mother'),
                'Father': _record('Father', sex='male'),
            }
            engine = PedigreeEngine(
                animals,
                lambda name, record: {
                    'egg_donor': record.get('eizellspenderin', ''),
                    'sperm_donor': record.get('samenspender', ''),
                },
            )
            engine.build()
            return engine

        first_values, first_status = widget._compute_inbreeding_state(
            make_engine(), {'Child'}, show_f=True
        )
        self.assertEqual(first_status['Child'], 'calculated')
        self.assertEqual(first_values['Child'], 0.0)
        self.assertIsNotNone(plugin.store.get_inbreeding_cache('Child'))

        _values, second_status = widget._compute_inbreeding_state(
            make_engine(), {'Child'}, show_f=True
        )
        self.assertEqual(second_status['Child'], 'cached')

        _values, changed_status = widget._compute_inbreeding_state(
            make_engine(mother='Father'), {'Child'}, show_f=True
        )
        self.assertEqual(changed_status['Child'], 'calculated')

    def test_genetic_revision_changes_once_and_surrogate_only_edit_does_not(self):
        app = _App()
        plugin = HeritageTrackPlugin(app)
        self.assertTrue(plugin.set_parentage(
            actor='researcher', animal_id='Child',
            values={'egg_donor': 'Mother', 'sperm_donor': 'Father'},
        ))
        first = app.backend.records.values[('heritage', 'graph')]
        first_revision = first['pedigree_revision']
        self.assertEqual(first['pedigree_sequence'], 1)
        self.assertEqual(
            first['animals']['Child']['genetic_parentage_revision'], first_revision
        )

        self.assertTrue(plugin.set_parentage(
            actor='researcher', animal_id='Child',
            values={
                'egg_donor': 'Mother', 'sperm_donor': 'Father',
                'surrogate_mother': 'Mother',
            },
        ))
        second = app.backend.records.values[('heritage', 'graph')]
        self.assertEqual(second['pedigree_revision'], first_revision)
        self.assertEqual(second['pedigree_sequence'], 1)

    def test_unrelated_lineage_keeps_matching_cache_after_other_line_changes(self):
        graph = {
            'animals': {
                name: {
                    'name': name, 'ipid': name, 'species': 'Callithrix jacchus',
                    'heritage_only': True, 'dummy_kind': 'direct',
                    'persistence_kind': 'direct_dummy', 'unit_id': 'unit-a',
                }
                for name in ('A', 'B', 'C', 'D')
            }
        }
        app = _App(graph)
        plugin = HeritageTrackPlugin(app)
        plugin.schedule_store_flush = lambda: None
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget.plugin = plugin
        widget._render_store_animals = plugin.store.get_all_entries()
        widget._get_node_record = lambda _node: {}

        def make_engine(a_mother):
            animals = {
                'A': _record('A', parents=(a_mother, 'B')),
                'B': _record('B', sex='male'),
                'C': _record('C', parents=('D', 'B')),
                'D': _record('D'),
            }
            engine = PedigreeEngine(
                animals,
                lambda _name, record: {
                    'egg_donor': record.get('eizellspenderin', ''),
                    'sperm_donor': record.get('samenspender', ''),
                },
            )
            engine.build()
            return engine

        _values, initial_status = widget._compute_inbreeding_state(
            make_engine('B'), {'A', 'C'}, show_f=True
        )
        self.assertEqual(initial_status['A'], 'calculated')
        self.assertEqual(initial_status['C'], 'calculated')

        _values, changed_status = widget._compute_inbreeding_state(
            make_engine('D'), {'A', 'C'}, show_f=True
        )
        self.assertEqual(changed_status['A'], 'calculated')
        self.assertEqual(changed_status['C'], 'cached')

    def test_core_f_cache_uses_stable_ipid_namespace_without_shadow_animal(self):
        graph = {
            'animals': {
                'Dummy': _record('Dummy'),
            }
        }
        app = _App(graph)
        store = HeritageStore('', app.backend)
        metadata = {
            'value': 0.125,
            'pedigree_revision': 'rev-1',
            'lineage_fingerprint': 'fp-core',
            'status': 'valid',
        }
        self.assertTrue(store.set_inbreeding_cache_batch(
            {'Core Animal': metadata},
            persist=False,
            cache_keys={'Core Animal': 'core-ipid-1'},
        ))
        self.assertNotIn('Core Animal', store.load()['animals'])
        self.assertEqual(
            store.get_inbreeding_cache('Core Animal', cache_key='core-ipid-1'),
            metadata,
        )
        store.flush_pending()
        persisted = app.backend.records.values[('heritage', 'graph')]
        self.assertNotIn('Core Animal', persisted['animals'])
        self.assertEqual(
            persisted['derived_inbreeding_cache']['core-ipid-1'], metadata
        )

    def test_unrelated_global_revision_does_not_rewrite_matching_cache_metadata(self):
        graph = {
            'pedigree_revision': 'rev-1',
            'animals': {
                'Child': _record('Child', parents=('Mother', 'Father')),
                'Mother': _record('Mother'),
                'Father': _record('Father', sex='male'),
            },
        }
        app = _App(graph)
        plugin = HeritageTrackPlugin(app)
        plugin.schedule_store_flush = lambda: None
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget.plugin = plugin
        widget._render_store_animals = plugin.store.get_all_entries()
        widget._get_node_record = lambda _node: {}

        def make_engine():
            engine = PedigreeEngine(
                graph['animals'],
                lambda _name, record: {
                    'egg_donor': record.get('eizellspenderin', ''),
                    'sperm_donor': record.get('samenspender', ''),
                },
            )
            engine.build()
            return engine

        widget._compute_inbreeding_state(make_engine(), {'Child'}, show_f=True)
        plugin.store.flush_pending()
        committed = copy.deepcopy(app.backend.records.values[('heritage', 'graph')])
        committed['pedigree_revision'] = 'rev-2'
        app.backend.records.values[('heritage', 'graph')] = committed
        # A matching lineage remains a cache hit and does not create another
        # derived write merely because the unrelated global token advanced.
        _values, status = widget._compute_inbreeding_state(
            make_engine(), {'Child'}, show_f=True
        )
        self.assertEqual(status['Child'], 'cached')
        self.assertFalse(plugin.store.has_pending_changes())

    def test_malformed_diagnostic_is_conditional_and_preserves_selected_detail(self):
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget.messages = {}
        widget._malformed_f_nodes = {'Child'}
        widget._get_node_birth_date_text = lambda _node: '01.01.2020'
        widget._get_node_public_id = lambda _node, _record=None: 'CJ-0001'

        for mode, expected in (
            ('nothing', 'F: unavailable (cyclic pedigree)'),
            ('birth_date', '01.01.2020\nF: unavailable (cyclic pedigree)'),
            ('animal_id', 'CJ-0001\nF: unavailable (cyclic pedigree)'),
            ('inbreeding_f', 'F: unavailable (cyclic pedigree)'),
        ):
            with self.subTest(mode=mode):
                widget.settings = {'animal_label_detail': mode}
                self.assertEqual(
                    widget._get_node_detail_text(
                        'Child', {}, 0.25, inbreeding_unavailable=True
                    ),
                    expected,
                )

        widget._malformed_f_nodes = set()
        widget.settings = {'animal_label_detail': 'nothing'}
        self.assertEqual(widget._get_node_detail_text('Child', {}, 0.25), '')
        widget.settings = {'animal_label_detail': 'birth_date'}
        self.assertEqual(widget._get_node_detail_text('Child', {}, 0.25), '01.01.2020')


if __name__ == '__main__':
    unittest.main()
