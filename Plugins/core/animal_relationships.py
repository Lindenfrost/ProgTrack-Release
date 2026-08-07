"""Symmetric, conflict-safe partner and breeding relationships."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from Plugins.core.animal_identity import animal_base_name
from Plugins.core.animal_roles import canonical_role_value


RELATIONSHIP_FIELDS = ("partner_von", "verpaart_mit")


def _role(record: Mapping[str, Any]) -> str:
    return canonical_role_value(record.get("rolle"))


def _reciprocal_field(
    field: str,
    subject_record: Mapping[str, Any],
    target_record: Mapping[str, Any],
) -> str:
    """Return the field appropriate for the other animal's role.

    A dedicated Partner stores ``partner_von``. Its breeding counterpart
    stores ``verpaart_mit``. Two breeding animals use ``verpaart_mit`` on
    both records. This keeps both meanings distinct while making the logical
    relationship reciprocal.
    """
    if _role(target_record) == "partner_animal":
        return "partner_von"
    if _role(subject_record) == "partner_animal":
        return "verpaart_mit"
    return "verpaart_mit" if field in RELATIONSHIP_FIELDS else field


@dataclass(frozen=True)
class RelationshipConflict(ValueError):
    subject: str
    target: str
    existing_partner: str
    field: str

    def __str__(self) -> str:
        return (
            f"{self.target} is already linked to {self.existing_partner} "
            f"through {self.field}."
        )


def resolve_animal_reference(
    records: Mapping[str, Mapping[str, Any]], reference: object
) -> str:
    """Resolve an IPID, generated ID, or unique legacy name to its IPID key."""
    raw = str(reference or "").strip()
    if not raw:
        return ""
    if raw in records:
        return raw
    folded = raw.casefold()
    by_id = [
        key for key, record in records.items()
        if str(record.get("id") or "").strip().casefold() == folded
    ]
    if len(by_id) == 1:
        return by_id[0]
    base = animal_base_name(raw).casefold()
    by_name = [
        key for key, record in records.items()
        if animal_base_name(key, record).casefold() == base
    ]
    return by_name[0] if len(by_name) == 1 else ""


def plan_symmetric_relationship(
    records: Mapping[str, Mapping[str, Any]],
    subject_key: str,
    subject_record: Mapping[str, Any],
    *,
    field: str,
    target_reference: object,
    can_edit: bool,
    previous_subject_key: str = "",
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Validate a relationship change and return atomic in-memory updates."""
    if field not in RELATIONSHIP_FIELDS:
        raise ValueError(f"Unsupported relationship field: {field!r}")
    previous_key = str(previous_subject_key or subject_key).strip()
    previous_record = records.get(previous_key, {})
    old_raw = previous_record.get(field) if isinstance(previous_record, Mapping) else ""
    old_target = resolve_animal_reference(records, old_raw)
    target = resolve_animal_reference(records, target_reference)
    raw_target = str(target_reference or "").strip()
    if raw_target and not target:
        raise ValueError(f"Unknown or ambiguous animal relationship: {raw_target}")
    if target in {subject_key, previous_key}:
        raise ValueError("An animal cannot be linked to itself")
    if target != old_target and not can_edit:
        raise PermissionError("Changing this animal relationship is not permitted")

    updated_subject = dict(subject_record)
    updated_subject[field] = target
    updates: dict[str, dict[str, Any]] = {}

    if old_target and old_target != target and old_target in records:
        old_partner = dict(records[old_target])
        for reciprocal_field in RELATIONSHIP_FIELDS:
            reciprocal = resolve_animal_reference(
                records, old_partner.get(reciprocal_field)
            )
            if reciprocal in {previous_key, subject_key}:
                old_partner[reciprocal_field] = ""
                updates[old_target] = old_partner

    if target:
        target_record = dict(updates.get(target, records[target]))
        reciprocal_field = _reciprocal_field(field, updated_subject, target_record)
        existing = resolve_animal_reference(
            records, target_record.get(reciprocal_field)
        )
        if existing and existing not in {previous_key, subject_key}:
            raise RelationshipConflict(
                subject_key, target, existing, reciprocal_field
            )
        target_record[reciprocal_field] = subject_key
        updates[target] = target_record

    return updated_subject, updates


def relationship_status_icon(
    records: Mapping[str, Mapping[str, Any]],
    subject_key: str,
    record: Mapping[str, Any],
) -> str:
    """Project one valid symmetric relationship to its semantic status icon."""
    subject = str(subject_key or "").strip()
    for field in RELATIONSHIP_FIELDS:
        target = resolve_animal_reference(records, record.get(field))
        if not target or target == subject or target not in records:
            continue
        target_record = records[target]
        reciprocal = any(
            resolve_animal_reference(records, target_record.get(other_field))
            == subject
            for other_field in RELATIONSHIP_FIELDS
        )
        if not reciprocal:
            continue
        roles = {
            canonical_role_value(record.get("rolle")),
            canonical_role_value(target_record.get("rolle")),
        }
        return "status.partner" if "partner_animal" in roles else "role.breeding"
    return ""
