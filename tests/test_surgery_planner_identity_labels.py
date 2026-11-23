"""Focused tests for compact Surgery Planner animal identities."""

from Plugins.Surgery_Planner.surgery_planner import _animal_identity_display


def test_compact_label_uses_base_name_and_generated_id_from_projection() -> None:
    name, generated_id, ipid = _animal_identity_display(
        {
            "name": "Alice | Callithrix jacchus | 01.01.2020 | DPZ",
            "id": "cj_20_0001_F_Alice",
        }
    )
    assert name == "Alice"
    assert generated_id == "cj_20_0001_F_Alice"
    assert ipid == "Alice | Callithrix jacchus | 01.01.2020 | DPZ"


def test_explicit_display_name_wins_without_changing_ipid() -> None:
    name, generated_id, ipid = _animal_identity_display(
        {
            "name": "A | Callithrix jacchus | 01.01.2020 | DPZ",
            "display_name": "A",
            "id": "cj_20_0001_F_A",
        }
    )
    assert (name, generated_id) == ("A", "cj_20_0001_F_A")
    assert ipid.startswith("A | Callithrix jacchus")
