import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Plugins.Heritage_Track.heritage_store import HeritageStore


class HeritageStoreSplitPersistenceTest(unittest.TestCase):
    def test_position_and_animal_batches_write_only_their_own_section(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = HeritageStore(temp_dir)
            store.load()

            with patch.object(store, "_atomic_write", wraps=store._atomic_write) as write:
                store.set_node_positions_batch({"A": (1.0, 2.0), "B": (3.0, 4.0)})
            self.assertEqual([Path(call.args[0]).name for call in write.call_args_list], ["heritage_settings.json"])

            with patch.object(store, "_atomic_write", wraps=store._atomic_write) as write:
                store.set_manual_sex_batch({"A": "male", "B": "female"})
                store.set_heritage_only_batch(["A", "B"], True)
            self.assertEqual(
                [Path(call.args[0]).name for call in write.call_args_list],
                ["heritage_animals.json", "heritage_animals.json"],
            )

            reloaded = HeritageStore(temp_dir)
            self.assertEqual(reloaded.get_node_positions()["A"], (1.0, 2.0))
            self.assertEqual(reloaded.get_manual_sex("A"), "male")
            self.assertTrue(reloaded.is_heritage_only("B"))


if __name__ == "__main__":
    unittest.main()
