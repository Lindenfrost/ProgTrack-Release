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
        self.animals = {
            'Child': {
                'name': 'Child', 'species': 'Callithrix jacchus',
                'sex': 'female', 'birth_date': '01.01.2020',
                'eizellspenderin': '', 'samenspender': '',
                'ziehmutter': '', 'ziehvater': '',
            },
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

    def _master_can(self, action):
        return action == 'heritage.edit_links'


def _record(name, *, sex='female', parents=()):
    mother = parents[0] if len(parents) > 0 else ''
    father = parents[1] if len(parents) > 1 else ''
    return {
        'name': name,
        'ipid': name,
        'species': 'Callithrix jacchus',
        'sex': sex,
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
                name: {'name': name, 'ipid': name, 'species': 'Callithrix jacchus'}
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


if __name__ == '__main__':
    unittest.main()
