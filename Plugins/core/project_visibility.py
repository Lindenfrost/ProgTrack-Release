# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Module: shared project-association visibility helpers.

from __future__ import annotations

from typing import Any, Iterable, Mapping

from Plugins.core.animal_identity import animal_identity_label


ANIMAL_WELFARE_ROLE = "animal_welfare_officer"
UNRESTRICTED_PROJECT_ROLES = {"lord", "master"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _collect_logins(value: Any, result: set[str]) -> None:
    if isinstance(value, str):
        login = value.strip()
        if login:
            result.add(login.lower())
    elif isinstance(value, Mapping):
        for nested in value.values():
            _collect_logins(nested, result)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for nested in value:
            _collect_logins(nested, result)


def associated_usernames(project_record: Mapping[str, Any]) -> set[str]:
    """Return normalized usernames associated with one Project Track record."""
    usernames: set[str] = set()
    for key in ("assoc_users", "summary", "iacuc"):
        section = project_record.get(key, {})
        if isinstance(section, Mapping):
            _collect_logins(section, usernames)
    return usernames


def is_unrestricted_project_role(role: str | None) -> bool:
    return _clean(role).lower() in UNRESTRICTED_PROJECT_ROLES


def visible_projects_for_user(
    project_records: Mapping[str, Mapping[str, Any]],
    username: str | None,
    role: str | None,
    can_view_all_projects: bool = False,
) -> tuple[bool, set[str]]:
    """Return (unrestricted, visible project names) for the current user."""
    if can_view_all_projects or is_unrestricted_project_role(role):
        return True, {name for name in project_records if _clean(name)}
    login = _clean(username).lower()
    if not login:
        return False, set()
    visible = {
        name
        for name, record in project_records.items()
        if _clean(name) and isinstance(record, Mapping)
        and login in associated_usernames(record)
    }
    return False, visible


def animal_visible_by_project_scope(
    animal_record: Mapping[str, Any],
    unrestricted: bool,
    visible_projects: set[str],
) -> bool:
    if unrestricted:
        return True
    project = _clean(animal_record.get("project"))
    return bool(project and project in visible_projects)


def animal_matches_name_filter(
    animal_key: str,
    animal_record: Mapping[str, Any],
    filter_text: str | None,
) -> bool:
    needle = _clean(filter_text).casefold()
    if not needle:
        return True
    candidates = {
        _clean(animal_key),
        _clean(animal_identity_label(animal_key, animal_record)),
        _clean(animal_record.get("name")),
        _clean(animal_record.get("_base_name")),
        _clean(animal_record.get("display_name")),
        _clean(animal_record.get("birth_date")),
        _clean(animal_record.get("species")),
    }
    return any(needle in candidate.casefold() for candidate in candidates if candidate)


def diff_project_associated_users(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> set[str]:
    return associated_usernames(before) ^ associated_usernames(after)
