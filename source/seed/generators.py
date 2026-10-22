from __future__ import annotations
import copy
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from itertools import permutations
from pathlib import Path
from typing import Any
from Plugins.core.animal_identity import animal_identity_key
from Plugins.core.animal_relationships import resolve_animal_reference
from Plugins.core.animal_roles import SCHEMA_VERSION as ROLE_SCHEMA_VERSION, canonical_role_value, normalize_role_definition
from Plugins.core.experimental_limits import KNOWN_LIMIT_FIELDS, default_experimental_limits, synchronize_experimental_limits
from Plugins.core.identity_conventions import render_animal_id
from .config import *
from .common import *

def add_animal(core: dict[str, Any], key_map: dict[str, str], name: str,
               birth: str, sex: str, role: str, *, origin: str = "DPZ",
               parent_f: str = "", parent_m: str = "") -> str:
    species = "Callitrix jacchus"
    key = animal_identity_key(name, species, birth, origin)
    record = complete_record({
        "rolle": role,
        "id": f"cj_{birth[-2:]}_seed_{name.lower().replace(' ', '_')}",
        "sex": sex,
        "genotype": "WT/WT",
        "eizellspenderin": parent_f,
        "samenspender": parent_m,
        "project": "OTOF-" if role in {"sperm_donor", "egg_cell_donor", "surrogate"} else "",
        "in_experiment": role in {"sperm_donor", "egg_cell_donor", "surrogate"},
    }, name=name, species=species, birth=birth, origin=origin)
    record["ipid"] = key
    core["animals"][key] = record
    key_map[key] = key
    return key


def add_mouse_animal(
    core: dict[str, Any],
    key_map: dict[str, str],
    name: str,
    birth: str,
    sex: str,
    role: str,
    *,
    parent_f: str = "",
    parent_m: str = "",
    project: str = "",
    in_experiment: bool = False,
    partner: str = "",
) -> str:
    """Add one deterministic Mus musculus example record."""
    species = "Mus musculus"
    origin = "DPZ"
    key = animal_identity_key(name, species, birth, origin)
    record = complete_record({
        "rolle": role,
        # Assigned in one deterministic pass after the complete mouse cohort
        # is known, so archived and active mice share the same yearly counter.
        "id": "",
        "sex": sex,
        "genotype": "WT/WT",
        "eizellspenderin": parent_f,
        "samenspender": parent_m,
        "project": project,
        "in_experiment": bool(in_experiment),
        "partner_von": partner,
        "verpaart_mit": partner,
        # Mouse birth date is represented by the immutable identity field;
        # mouse event timelines start with scientific/procedure entries.
        "events": [],
    }, name=name, species=species, birth=birth, origin=origin)
    record["ipid"] = key
    core["animals"][key] = record
    key_map[key] = key
    return key


def add_mouse_colony(core: dict[str, Any], key_map: dict[str, str]) -> dict[str, Any]:
    """Extend the seed with a connected, species-appropriate mouse colony.

    Existing Mus musculus sample records remain as disposable examples but are
    normalized to breeding animals. The new Tolkien-family cohort provides
    ancestry, partners, the Ringbearer experiment, and a deliberately single
    Bilbo housing exception for Cage Track.
    """
    for record in {**core.get("animals", {}), **core.get("archived_animals", {})}.values():
        if str(record.get("species") or "").strip() == "Mus musculus":
            record["rolle"] = "breeding_animal"
            record["role_id"] = "breeding_animal"
            record["project"] = ""
            record["in_experiment"] = False

    # Rebuild the named Hobbit cohort as one canonical family graph.  The
    # initial mouse fixture incorrectly made Frodo a child of Bilbo.  Remove
    # the old disposable rows first so changed sample birth dates (notably
    # Belladonna) cannot leave stale IPIDs in the generated domain records.
    canonical_names = set(MOUSE_SPEC["canonical_names"])
    for section in ("animals", "archived_animals"):
        stale = [
            ipid for ipid, record in core.get(section, {}).items()
            if str(record.get("species") or "").strip() == "Mus musculus"
            and str(record.get("name") or "").strip() in canonical_names
        ]
        for ipid in stale:
            core[section].pop(ipid, None)
            key_map.pop(ipid, None)

    def add_mouse_pair(
        male: str, male_birth: str, female: str, female_birth: str,
    ) -> tuple[str, str]:
        male_key = add_mouse_animal(
            core, key_map, male, male_birth, "Male", "breeding_animal"
        )
        female_key = add_mouse_animal(
            core, key_map, female, female_birth, "Female", "breeding_animal"
        )
        core["animals"][male_key]["verpaart_mit"] = female_key
        core["animals"][female_key]["partner_von"] = male_key
        return male_key, female_key

    # Canonical named parents and couples.  Bilbo's parents are Bungo and
    # Belladonna; Frodo's parents are Drogo and Primula; Rosie is Sam's mate,
    # while Bell is Sam's mother.  Diamond is Pippin's mate, not his child.
    parent_pairs = {key: add_mouse_pair(*spec) for key, spec in MOUSE_SPEC["parent_pairs"].items()}

    def parent_keys(pair_name: str) -> tuple[str, str]:
        father, mother = parent_pairs[pair_name]
        return father, mother

    child_keys: dict[str, str] = {}
    child_specs = MOUSE_SPEC["children"]
    for name, birth, sex, pair_name, role, project, in_experiment in child_specs:
        father, mother = parent_keys(pair_name)
        child_keys[name] = add_mouse_animal(
            core, key_map, name, birth, sex, role,
            parent_f=mother, parent_m=father,
            project=project, in_experiment=in_experiment,
        )
        surgery_date = RINGBEARER_SURGERY_DATES.get(name)
        if surgery_date:
            core["animals"][child_keys[name]]["events"].append({
                "event_type": "surgery",
                "datum": surgery_date,
            })

    # Elanor is Sam and Rosie's daughter.  Rosie is retained as the canonical
    # partner from the Gamgee family, rather than being Hamfast's partner.
    rosie_key = add_mouse_animal(
        core, key_map, "Rosie", "03.12.2021", "Female", "breeding_animal"
    )
    sam_key = child_keys["Sam"]
    core["animals"][sam_key]["verpaart_mit"] = rosie_key
    core["animals"][rosie_key]["partner_von"] = sam_key
    child_keys["Elanor"] = add_mouse_animal(
        core, key_map, "Elanor", "10.06.2024", "Female", "breeding_animal",
        parent_f=rosie_key, parent_m=sam_key,
    )
    child_keys["Diamond"] = add_mouse_animal(
        core, key_map, "Diamond", "10.07.2024", "Female", "breeding_animal",
        partner=child_keys["Pippin"],
    )
    core["animals"][child_keys["Pippin"]]["verpaart_mit"] = child_keys["Diamond"]
    core["animals"][child_keys["Diamond"]]["partner_von"] = child_keys["Pippin"]

    # Italian names provide ordinary non-canonical partner/background examples.
    # They are intentionally not inserted into the Hobbit parentage graph.
    partner_specs = MOUSE_SPEC["partners"]
    partner_keys: dict[str, str] = {}
    for name, birth, sex, paired_name in partner_specs:
        paired_key = child_keys[paired_name]
        partner_key = add_mouse_animal(
            core, key_map, name, birth, sex, "breeding_animal",
        )
        partner_keys[name] = partner_key
        if sex == "Male":
            core["animals"][paired_key]["partner_von"] = partner_key
            core["animals"][partner_key]["partner_von"] = paired_key
        else:
            core["animals"][paired_key]["verpaart_mit"] = partner_key
            core["animals"][partner_key]["partner_von"] = paired_key

    # Experimental Ringbearer animals are deliberately housed and paired in
    # same-sex study pairs. Their ordinary breeding partners remain in the
    # breeding colony; this relationship never creates offspring.
    for left_name, right_name in MOUSE_SPEC["study_pairs"]:
        left_key, right_key = child_keys[left_name], child_keys[right_name]
        for key, other in ((left_key, right_key), (right_key, left_key)):
            core["animals"][key]["partner_von"] = other
            core["animals"][key]["verpaart_mit"] = other
    for external_name in ("Giulia", "Rosie", "Chiara", "Diamond"):
        external = next(
            (record for record in core["animals"].values()
             if record.get("name") == external_name
             and record.get("species") == "Mus musculus"),
            None,
        )
        if external is not None:
            external["partner_von"] = ""
            external["verpaart_mit"] = ""

    # Keep the remaining Italian examples as unpaired breeding animals so the
    # colony still exercises ordinary male/female records without inventing
    # extra Hobbit ancestry.
    for name, birth, sex in MOUSE_SPEC["background"]:
        add_mouse_animal(core, key_map, name, birth, sex, "breeding_animal")

    return {"experimental": [child_keys[name] for name in ("Frodo", "Sam", "Merry", "Pippin")],
            "bilbo": next(key for key in core["animals"] if key.startswith("Bilbo | Mus musculus |"))}


def add_shire_colony(core: dict[str, Any], key_map: dict[str, str]) -> dict[str, Any]:
    """Author a bounded five-generation pedigree with paternal family names.

    Each planned pair has one or two litters of 10--12 young. Selection is
    TOTAL per pair, not per litter: two in generations 1--3, one in generation
    4, and only Sam in generation 5 (the preserved Sam/Rosie/Elanor outcross).
    Existing biological dates and Ringbearer procedures remain unchanged.
    Litter/selection provenance is seed metadata, never a Heritage Core shadow
    or an invented embryo-transfer workflow.
    """
    animals = core["animals"]
    existing = {
        record["name"]: ipid for ipid, record in animals.items()
        if record.get("species") == "Mus musculus"
    }
    generations: dict[str, int] = {}
    litters: list[dict[str, Any]] = []
    pairings: list[dict[str, Any]] = []
    hobbit_males = SHIRE_SPEC["hobbit_males"]
    portuguese_females = SHIRE_SPEC["portuguese_females"]
    surnames_by_ipid = {
        existing[name]: surname
        for name, surname in SHIRE_SPEC["external_surnames"].items()
    }
    serial = 0
    moniker_counts: dict[str, int] = {}
    used_mouse_names = {
        str(record.get("name") or "").strip().casefold()
        for record in {**core.get("animals", {}), **core.get("archived_animals", {})}.values()
        if str(record.get("species") or "").strip() == "Mus musculus"
    }

    def new_child(sex: str, born: date, father: str, mother: str) -> str:
        nonlocal serial
        serial += 1
        names = hobbit_males if sex == "Male" else portuguese_females
        surname = surnames_by_ipid[father]
        base_name = f"{names[(serial - 1) % len(names)]} {surname}"
        base_key = base_name.casefold()
        # Distribute descriptors across different base names while repeated
        # given-name/surname combinations walk the same deterministic pool.
        start = int(hashlib.sha256(base_key.encode("ascii")).hexdigest()[:8], 16)
        used = moniker_counts.get(base_key, 0)
        while used < len(SHIRE_NAME_MONIKERS):
            moniker = SHIRE_NAME_MONIKERS[(start + used) % len(SHIRE_NAME_MONIKERS)]
            name = f"{base_name} {moniker}"
            used += 1
            if name.casefold() not in used_mouse_names:
                break
        else:
            raise ValueError(f"No unused descriptive moniker remains for {base_name}")
        moniker_counts[base_key] = used
        used_mouse_names.add(name.casefold())
        child = add_mouse_animal(
            core, key_map, name, born.strftime("%d.%m.%Y"), sex,
            "breeding_animal", parent_m=father, parent_f=mother, project="Shire",
        )
        surnames_by_ipid[child] = surname
        return child

    @lru_cache(maxsize=None)
    def kinship(left: str, right: str) -> float:
        """Pedigree coancestry; unrelated external founders have coefficient 0."""
        if left == right:
            record = animals[left]
            father, mother = record.get("samenspender"), record.get("eizellspenderin")
            return (1.0 + (kinship(father, mother) if father and mother else 0.0)) / 2.0
        if (generations.get(left, 0), left) < (generations.get(right, 0), right):
            return kinship(right, left)
        record = animals[left]
        father, mother = record.get("samenspender"), record.get("eizellspenderin")
        return (kinship(father, right) + kinship(mother, right)) / 2.0 if father and mother else 0.0

    def best_assignment(costs: list[list[float]]) -> tuple[int, ...]:
        # At most eight pairs: exhaustively minimize worst coancestry, then
        # total coancestry; stable index order breaks ties deterministically.
        return min(permutations(range(len(costs))), key=lambda perm: (
            max(costs[index][mate] for index, mate in enumerate(perm)),
            sum(costs[index][mate] for index, mate in enumerate(perm)), perm,
        ))

    def select_pairs(males: list[str], females: list[str]) -> list[tuple[str, str]]:
        costs = [[kinship(male, female) for female in females] for male in males]
        assignment = best_assignment(costs)
        return [(male, females[assignment[index]]) for index, male in enumerate(males)]

    founders = [(existing["Bungo"], existing["Belladonna"])]
    for index, (male, female) in enumerate(SHIRE_SPEC["additional_founder_pairs"]):
        birthday = date(2019, 1, 15) + timedelta(days=index * 2)
        founders.append((
            add_mouse_animal(core, key_map, male, birthday.strftime("%d.%m.%Y"), "Male", "breeding_animal", project="Shire"),
            add_mouse_animal(core, key_map, female, (birthday + timedelta(days=1)).strftime("%d.%m.%Y"), "Female", "breeding_animal", project="Shire"),
        ))
    for pair in founders:
        generations.update({ipid: 0 for ipid in pair})
    if len(SHIRE_SPEC["founder_surnames"]) != len(founders):
        raise ValueError("Shire requires explicit paternal and maternal founder surnames")
    for pair, surnames in zip(founders, SHIRE_SPEC["founder_surnames"], strict=True):
        father, mother = pair
        surnames_by_ipid[father], surnames_by_ipid[mother] = surnames

    def add_litter(generation: int, pair_index: int, litter_index: int,
                   father: str, mother: str, born: date,
                   named_child: str | None = None) -> list[str]:
        size = 10 + (generation + pair_index + litter_index) % 3
        children = [named_child] if named_child else []
        if named_child:
            record = animals[named_child]
            record["samenspender"], record["eizellspenderin"] = father, mother
            surnames_by_ipid[named_child] = surnames_by_ipid[father]
            if parse_record_date(record["birth_date"]) != born:
                raise ValueError("Shire must not change an existing animal's identity")
        while len(children) < size:
            sex = "Male" if len(children) % 2 == 0 else "Female"
            children.append(new_child(sex, born, father, mother))
        generations.update({ipid: generation for ipid in children})
        litters.append({
            "litter_id": f"SHIRE-G{generation}-P{pair_index + 1:02d}-L{litter_index + 1}",
            "generation": generation, "father": father, "mother": mother,
            "birth_date": born.isoformat(), "offspring": children,
        })
        return children

    current_pairs = founders
    canonical_pairs = SHIRE_SPEC["canonical_pairs"]
    for generation in range(1, 6):
        selected_males: list[str] = []
        selected_females: list[str] = []
        named_selections: dict[int, str] = {}
        if generation == 4:
            # Select one child from each of the eight families. Determine
            # prospective mates before naming them so the established named
            # parent couples also receive the least-related available match.
            males, females = current_pairs[::2], current_pairs[1::2]
            costs = [[sum(kinship(a, b) for a in male for b in female) / 4.0
                      for female in females] for male in males]
            assignment = best_assignment(costs)
            for index, (male_name, female_name) in enumerate(canonical_pairs):
                named_selections[index * 2] = existing[male_name]
                named_selections[assignment[index] * 2 + 1] = existing[female_name]
        for pair_index, (father, mother) in enumerate(current_pairs):
            named = named_selections.get(pair_index)
            if generation == 5:
                named = existing[("Frodo", "Sam", "Merry", "Pippin")[pair_index]]
            born = (
                parse_record_date(animals[named]["birth_date"]) if named
                else date.fromisoformat(SHIRE_SPEC["generation_starts"][str(generation)])
            )
            children = add_litter(generation, pair_index, 0, father, mother, born, named)
            if generation <= 3:
                selected = [next(ipid for ipid in children if animals[ipid]["sex"] == sex)
                            for sex in ("Male", "Female")]
            elif generation == 4:
                selected = [named]
            else:
                selected = [named] if animals[named]["name"] == "Sam" else []
            if generation == 1 and pair_index == 0:
                add_litter(1, 0, 1, father, mother, date(2021, 1, 10), existing["Bilbo"])
            elif generation == 5 and pair_index < 2:
                add_litter(5, pair_index, 1, father, mother, born + timedelta(days=35))
            pairings.append({
                "offspring_generation": generation, "father": father, "mother": mother,
                "selected_for_breeding": selected, "coancestry": kinship(father, mother),
            })
            animals[father]["verpaart_mit"] = mother
            animals[mother]["verpaart_mit"] = father
            for ipid in selected:
                (selected_males if animals[ipid]["sex"] == "Male" else selected_females).append(ipid)
        if generation < 4:
            current_pairs = select_pairs(selected_males, selected_females)
        elif generation == 4:
            current_pairs = [(existing[male], existing[female]) for male, female in canonical_pairs]
    # Keep Sam's daughter, with a complete 10-animal litter and an unrelated
    # existing Rosie as the external spouse. This is explicitly generation 6,
    # not a claim that Sam is a leaf or a sixth planned selection round.
    sam, rosie = existing["Sam"], existing["Rosie"]
    add_litter(6, 0, 0, sam, rosie, date(2024, 6, 10), existing["Elanor"])
    pairings.append({"offspring_generation": 6, "father": sam, "mother": rosie,
                     "selected_for_breeding": [], "coancestry": kinship(sam, rosie)})
    all_mice = {ipid: record for section in ("animals", "archived_animals")
                for ipid, record in core.get(section, {}).items()
                if record.get("species") == "Mus musculus"}
    if len(all_mice) > SHIRE_MOUSE_LIMIT:
        raise ValueError(f"Shire exceeds the complete seed mouse cap: {len(all_mice)}")
    def surname_for(ipid: str) -> str:
        father = all_mice[ipid].get("samenspender")
        if father:
            surnames_by_ipid[ipid] = surname_for(father)
        if ipid not in surnames_by_ipid:
            raise ValueError(f"Shire has no authored founder surname: {ipid}")
        return surnames_by_ipid[ipid]

    for ipid in all_mice:
        surname_for(ipid)
    for ipid, record in all_mice.items():
        if record.get("project") != "Ringbearer":
            record["project"] = "Shire"
            record["in_experiment"] = False
        else:
            record["project_history"] = [{
                "project": "Shire", "entry_date": record["birth_date"],
                "leave_date": "22.07.2026", "status": "former",
                "in_experiment": False,
            }]
    result = {
        "schema_version": 1, "fictional": True, "mouse_limit": SHIRE_MOUSE_LIMIT,
        "founder_pairs": [list(pair) for pair in founders],
        "generation_by_ipid": generations, "litters": litters, "pairings": pairings,
        "terminal_ringbearers": [existing[name] for name in ("Frodo", "Sam", "Merry", "Pippin")],
        "exception": "Sam remains Elanor's father; his unrelated Rosie outcross adds generation 6.",
        "external_spouses": [rosie],
        "surname_by_ipid": surnames_by_ipid,
        "named_animals": {record["name"]: ipid for ipid, record in all_mice.items()
                          if " " not in record["name"]},
        "medical_witnesses": [
            next(litter for litter in litters if litter["litter_id"] == litter_id)["offspring"][index]
            for litter_id, index in SHIRE_SPEC["medical_witnesses"]
        ],
        "naming": "All mice have surnames following the biological father's line for sons and daughters, including the established named animals. Hobbit and ASCII Portuguese given names use varied, unique descriptive monikers, never numeric offspring suffixes. Spouses retain their own paternal surname.",
        "pairing_policy": "Minimize worst and then total pedigree coancestry among available selected breeders; no claim of zero inbreeding in a closed colony.",
    }
    # Short authoring labels keep the recipes readable, but are never shipped
    # as mouse names. Rewrite immutable IPIDs and every relationship/scenario
    # reference together, before public IDs and downstream domains are built.
    renamed = {}
    for ipid in result["named_animals"].values():
        record = all_mice[ipid]
        name = f"{record['name']} {surnames_by_ipid[ipid]}"
        new_ipid = animal_identity_key(
            name, record["species"], record["birth_date"], record["origin"]
        )
        if new_ipid in all_mice or new_ipid in renamed.values():
            raise ValueError(f"Shire surname creates a duplicate mouse identity: {new_ipid}")
        renamed[ipid] = new_ipid
        record.update(name=name, _base_name=name, display_name=name, ipid=new_ipid)
    updated = rewrite_references(core, renamed)
    core.clear()
    core.update(updated)
    key_map.update({old: renamed.get(new, new) for old, new in list(key_map.items())})
    key_map.update(renamed)
    return rewrite_references(result, renamed)


def normalize_mouse_public_ids(core: dict[str, Any]) -> None:
    """Apply the configured public-ID shape to every mouse in the seed.

    The mouse scenario was initially authored with ad-hoc ``mm_2021_name``
    values.  Public IDs follow the same convention as the Callitrix examples:
    species token, two-digit birth year, four-digit birth-year sequence, sex,
    and an ID-safe animal name.  Archived mice are included in the sequence so
    the complete fictional dataset remains deterministic and collision-free.
    """
    all_animals = {
        **core.get("animals", {}),
        **core.get("archived_animals", {}),
    }
    mice = [
        (ipid, record)
        for ipid, record in all_animals.items()
        if str(record.get("species") or "").strip() == "Mus musculus"
    ]
    counters: dict[str, int] = {}
    for _ipid, record in sorted(
        mice,
        key=lambda item: (
            parse_record_date(item[1].get("birth_date")) or date.max,
            str(item[1].get("name") or "").casefold(),
            str(item[1].get("sex") or "").casefold(),
            str(item[0]),
        ),
    ):
        birth = parse_record_date(record.get("birth_date"))
        if birth is None:
            continue
        year = f"{birth.year % 100:02d}"
        counters[year] = counters.get(year, 0) + 1
        name_token = re.sub(
            r"[^A-Za-z0-9]+", "_", str(record.get("name") or "")
        ).strip("_") or "Animal"
        sex = str(record.get("sex") or "").strip().casefold()
        sex_token = (
            "M" if sex in {"male", "m"}
            else "F" if sex in {"female", "f"}
            else "U"
        )
        record["id"] = f"mm_{year}_{counters[year]:04d}_{sex_token}_{name_token}"


def normalize_mature_offspring_roles(core: dict[str, Any]) -> list[str]:
    """Promote non-Callitrix offspring older than one year at Eldarion's birth.

    The fictional seed uses one fixed reference date so role categories remain
    reproducible.  Callitrix offspring are intentionally excluded because the
    family/reproduction examples use that role for their own scenarios.
    """
    cutoff = ELDARION_BIRTH_DATE.replace(year=ELDARION_BIRTH_DATE.year - 1)
    changed: list[str] = []
    all_animals = {
        **core.get("animals", {}),
        **core.get("archived_animals", {}),
    }
    for ipid, record in all_animals.items():
        species = str(record.get("species") or "").strip().lower()
        if species.startswith(("callitrix", "callithrix")):
            continue
        if str(record.get("rolle") or "").strip().lower() != "offspring":
            continue
        birth = parse_record_date(record.get("birth_date"))
        if birth is not None and birth < cutoff:
            record["rolle"] = "breeding_animal"
            if "role_id" in record:
                record["role_id"] = "breeding_animal"
            changed.append(str(ipid))
    return changed


def researcher_record_dates(animal: dict[str, Any]) -> list[date]:
    """Dates from scientific/clinical fields that researchers can populate."""
    found: list[date] = []
    for field in ("daten", "pdg", "gewicht", "sperm", "events"):
        values = animal.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict):
                parsed = parse_record_date(
                    value.get("datum") or value.get("date")
                )
            else:
                parsed = parse_record_date(value)
            if parsed is not None:
                found.append(parsed)
    return found


def _stable_weight(value: float, ipid: str, stamp: date) -> float:
    digest = hashlib.sha256(f"{ipid}:{stamp.isoformat()}".encode()).digest()
    percent = (digest[0] % 7 - 3) / 100.0
    return round(max(0.1, value * (1.0 + percent)), 1)


def extend_weights_through_latest_data(
    ipid: str, animal: dict[str, Any]
) -> None:
    dates = researcher_record_dates(animal)
    if not dates:
        return
    latest = max(dates)
    parsed_weights = sorted(
        (
            (parse_record_date(value.get("datum")), float(value.get("wert")))
            for value in animal.get("gewicht", [])
            if isinstance(value, dict)
            and parse_record_date(value.get("datum")) is not None
            and isinstance(value.get("wert"), (int, float))
        ),
        key=lambda item: item[0],
    )
    if not parsed_weights:
        animal["gewicht"] = dated_weights(
            animal["birth_date"], animal["species"], animal.get("sex", "Unknown")
        )
        parsed_weights = [
            (parse_record_date(item["datum"]), float(item["wert"]))
            for item in animal["gewicht"]
        ]
    last_date, last_value = parsed_weights[-1]
    if last_date >= latest:
        return
    cursor = last_date
    additions: list[dict[str, Any]] = []
    while cursor + timedelta(days=180) < latest:
        cursor += timedelta(days=180)
        last_value = _stable_weight(last_value, ipid, cursor)
        additions.append({"datum": cursor.isoformat(), "wert": last_value})
    if cursor < latest:
        last_value = _stable_weight(last_value, ipid, latest)
        additions.append({"datum": latest.isoformat(), "wert": last_value})
    animal.setdefault("gewicht", []).extend(additions)


def complete_scientific_histories(core: dict[str, Any]) -> None:
    all_animals = {**core["animals"], **core["archived_animals"]}
    for ipid, animal in sorted(all_animals.items()):
        sequence = int(hashlib.sha256(ipid.encode()).hexdigest()[:8], 16)
        events = animal.setdefault("events", [])
        existing_event_keys = {
            (
                str(event.get("event_type") or ""),
                str(event.get("datum") or event.get("date") or ""),
                str(event.get("sample_id") or ""),
            )
            for event in events if isinstance(event, dict)
        }

        # Every sperm sample is one collection event; measurements from the
        # sample share the same sample ID.
        sperm = animal.setdefault("sperm", [])
        if animal.get("rolle") == "sperm_donor" and not sperm:
            collection = date(2025, 6, 15) + timedelta(days=sequence % 120)
            sperm.append({
                "datum": collection.isoformat(),
                "sample_id": f"SP-SEED-{hashlib.sha256(ipid.encode()).hexdigest()[:12].upper()}",
                "motility": float(75 + sequence % 18),
                "progressive": float(55 + sequence % 24),
                "count": float(180_000_000 + (sequence % 9) * 55_000_000),
            })
        for sample_index, sample in enumerate(sperm):
            if not isinstance(sample, dict):
                continue
            stamp = str(sample.get("datum") or "")
            if not parse_record_date(stamp):
                continue
            sample_id = str(sample.get("sample_id") or "").strip()
            if not sample_id:
                sample_id = f"SP-{hashlib.sha256((ipid + stamp).encode()).hexdigest()[:12].upper()}"
                sample["sample_id"] = sample_id
            key = ("sperm_donation", stamp, sample_id)
            if key not in existing_event_keys:
                events.append({
                    "event_type": "sperm_donation",
                    "datum": stamp,
                    "sample_id": sample_id,
                })
                existing_event_keys.add(key)

        events.sort(key=lambda event: (
            str(event.get("datum") or event.get("date") or ""),
            str(event.get("event_type") or ""),
        ))
        extend_weights_through_latest_data(ipid, animal)
