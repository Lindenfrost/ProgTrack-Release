# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Master Track permission definitions and resolution logic.

from __future__ import annotations

import json
import os
from typing import Dict, Iterable, List, Optional, Set

# Module-level cache for permission labels
_PERMISSION_LABELS: Optional[Dict[str, Dict[str, str]]] = None


def _load_permission_labels() -> Dict[str, Dict[str, str]]:
    """Load permission labels from JSON file. Cached after first load."""
    global _PERMISSION_LABELS
    if _PERMISSION_LABELS is not None:
        return _PERMISSION_LABELS

    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    labels_path = os.path.join(plugin_dir, "permissions_labels.json")

    try:
        with open(labels_path, "r", encoding="utf-8") as f:
            _PERMISSION_LABELS = json.load(f)
    except Exception:
        _PERMISSION_LABELS = {}

    return _PERMISSION_LABELS

# ---------------------------------------------------------------------------
# Role identifiers
# ---------------------------------------------------------------------------

ROLE_LORD = "lord"
ROLE_MASTER = "master"
ROLE_USER = "user"
ROLE_GUEST = "guest"

# AWO is a normal job, but it is valid only together with the Vet job.
JOB_ANIMAL_WELFARE = "animal_welfare_officer"
JOB_VET = "vet"

# ---------------------------------------------------------------------------
# Permission name constants (canonical keys used in all can() calls)
# ---------------------------------------------------------------------------

# core — Animal data and application baseline
PERM_CORE_VIEW = "core.view"
PERM_CORE_USE_FILTERS = "core.use_filters"
PERM_CORE_OPEN_READONLY = "core.open_readonly_dialogs"
PERM_CORE_EXPORT = "core.export"
PERM_CORE_IMPORT = "core.import"
PERM_CORE_CREATE_ANIMALS = "core.create_animals"
PERM_CORE_EDIT_ANIMAL_CORE = "core.edit_animal_core"  # open edit-animal dialog (master switch)
PERM_CORE_ARCHIVE_ANIMALS = "core.archive_animals"
PERM_CORE_DELETE_ANIMALS = "core.delete_animals"
# Fine-grained in-dialog field-group permissions
PERM_CORE_EDIT_ANIMAL_IDENTITY      = "core.edit_animal_identity"    # name/species/ID/origin/project/dates
PERM_CORE_EDIT_ANIMAL_IMMUTABLE     = "core.edit_animal_immutable"   # chip_nr and origin fields (set once or by privileged roles)
PERM_CORE_EDIT_ANIMAL_HOUSING       = "core.edit_animal_housing"     # address + parents (+ sex for experimental)
PERM_CORE_EDIT_ANIMAL_MEASUREMENTS  = "core.edit_animal_measurements"  # weight values + events
PERM_CORE_EDIT_ANIMAL_RESEARCH_DATA = "core.edit_animal_research_data"  # pdg/prog/sperm + max/recovery fields
PERM_CORE_EDIT_ANIMAL_ROLE          = "core.edit_animal_role"  # assign an animal to another role
PERM_CORE_STYLE_SETTINGS = "core.style_settings"  # access Settings - Style menu
PERM_CORE_MANAGE_ANIMAL_ROLES = "core.manage_animal_roles"
# core.project_assign moved to project namespace – see PERM_PROJECT_ASSIGN below

# master — User and permission administration
PERM_MASTER_VIEW_USERS = "master.view_users"
PERM_MASTER_CREATE_USERS = "master.create_users"
PERM_MASTER_EDIT_USERS = "master.edit_users"
PERM_MASTER_ASSIGN_PRIMARY_ROLE = "master.assign_primary_role"
PERM_MASTER_ASSIGN_JOBS = "master.assign_jobs"
# Retained as a migration marker only; it is intentionally not grantable.
PERM_MASTER_MANAGE_ROLE_BASELINES = "master.manage_role_baselines"
PERM_MASTER_MANAGE_JOB_BUNDLES = "master.manage_job_bundles"
PERM_MASTER_GRANT_DIRECT = "master.grant_direct_permissions"
PERM_MASTER_REVOKE_DIRECT = "master.revoke_direct_permissions"
PERM_MASTER_VIEW_AUDIT = "master.view_audit"

# network — Network_Track messaging
PERM_NETWORK_VIEW = "network.view"
PERM_NETWORK_CREATE_ENTRY = "network.create_entry"
PERM_NETWORK_EDIT_ENTRY = "network.edit_entry"

# heritage — Heritage_Track pedigree graph
PERM_HERITAGE_VIEW = "heritage.view"
PERM_HERITAGE_EDIT_LINKS = "heritage.edit_links"
PERM_HERITAGE_EXPORT = "heritage.export"

# medi_track — Medical status and history
PERM_MEDI_VIEW = "medi_track.view"
PERM_MEDI_FILTER_USE = "medi_track.filter_use"
PERM_MEDI_UPLOAD_DOCUMENT = "medi_track.upload_document"
PERM_MEDI_DELETE_DOCUMENT = "medi_track.delete_document"
PERM_MEDI_STATUS_ENABLE = "medi_track.status_enable"
PERM_MEDI_STATUS_MANAGE = "medi_track.status_manage"
PERM_MEDI_ADD_DOCS = "medi_track.add_docs"

# cage — Cage_Track housing
PERM_CAGE_VIEW = "cage.view"
PERM_CAGE_RECORD_INSPECTION = "cage.record_inspection"
PERM_CAGE_ASSIGN_LOCATIONS = "cage.assign_locations"
PERM_CAGE_MANAGE_ROOMS_BUILDINGS = "cage.manage_rooms_buildings"
PERM_CAGE_EDIT = "cage.edit"
PERM_CAGE_EXPORT_PDF = "cage.export_pdf"

# project — Project_Track scope
PERM_PROJECT_VIEW = "project.view"
PERM_PROJECT_VIEW_ALL = "project.view_all"
PERM_PROJECT_ASSIGN = "project.project_assign"
PERM_PROJECT_CREATE = "project.create"
PERM_PROJECT_MANAGE_SEVERITY = "project.manage_severity"
# Retained as a migration marker only; project.manage is authoritative.
PERM_PROJECT_MANAGE_SPECIES_SCOPE = "project.manage_species_scope"
PERM_PROJECT_SET_IN_EXPERIMENT   = "project.set_in_experiment"
PERM_PROJECT_UNSET_IN_EXPERIMENT = "project.unset_in_experiment"
PERM_PROJECT_MANAGE              = "project.manage"
PERM_PROJECT_ARCHIVE             = "project.archive_project"
PERM_PROJECT_UPLOAD_DOCUMENT     = "project.upload_document"
PERM_PROJECT_DELETE_DOCUMENT     = "project.delete_document"
PERM_PROJECT_UPLOAD_SOP          = "project.upload_sop"
PERM_PROJECT_DELETE_SOP          = "project.delete_sop"

# reports — Animal Reports
PERM_REPORTS_VIEW = "reports.view"
PERM_REPORTS_WRITE = "reports.write"

# plots — Plots tab viewing
PERM_PLOTS_VIEW = "plots.view"

# pdg_converter — PdG to progesterone conversion tool
PERM_PDG_CONVERTER_USE = "pdg_converter.use"

# op_scheduler — OP-Scheduler plugin (surgical planning)
PERM_OP_SCHEDULER_VIEW = "op_scheduler.view"
PERM_OP_SCHEDULER_USE = "op_scheduler.use"

# embryo_track — Embryo Track plugin
PERM_EMBRYO_TRACK_VIEW = "embryo_track.view"

# sample_track — Sample Track plugin
PERM_SAMPLE_TRACK_USE = "sample_track.use"

# flow_track — Flow Track plugin
PERM_FLOW_TRACK_USE = "flow_track.use"
PERM_FLOW_TRACK_OPEN = "flow_track.open"
PERM_FLOW_TRACK_EDIT = "flow_track.edit"
PERM_FLOW_TRACK_CREATE = "flow_track.create"
PERM_FLOW_TRACK_DELETE = "flow_track.delete"

# Master Track plugin control
PERM_TOGGLE_MASTER_TRACK = "toggle_master_track"
INTERNAL_PERMISSIONS: Set[str] = {PERM_TOGGLE_MASTER_TRACK}

# Permissions that ROLE_MASTER explicitly lacks (everything else is granted)
_MASTER_EXCLUDED: Set[str] = {PERM_TOGGLE_MASTER_TRACK}

# ---------------------------------------------------------------------------
# All known permission names (for UI enumeration)
# ---------------------------------------------------------------------------

ALL_PERMISSIONS: List[str] = [
    PERM_CORE_VIEW, PERM_CORE_USE_FILTERS, PERM_CORE_OPEN_READONLY,
    PERM_CORE_EXPORT, PERM_CORE_IMPORT,
    PERM_CORE_CREATE_ANIMALS,
    PERM_CORE_EDIT_ANIMAL_CORE,
    PERM_CORE_ARCHIVE_ANIMALS, PERM_CORE_DELETE_ANIMALS,
    PERM_CORE_EDIT_ANIMAL_IDENTITY, PERM_CORE_EDIT_ANIMAL_IMMUTABLE, PERM_CORE_EDIT_ANIMAL_HOUSING,
    PERM_CORE_EDIT_ANIMAL_MEASUREMENTS, PERM_CORE_EDIT_ANIMAL_RESEARCH_DATA,
    PERM_CORE_EDIT_ANIMAL_ROLE,
    PERM_CORE_STYLE_SETTINGS, PERM_CORE_MANAGE_ANIMAL_ROLES,
    PERM_MASTER_VIEW_USERS, PERM_MASTER_CREATE_USERS, PERM_MASTER_EDIT_USERS,
    PERM_MASTER_ASSIGN_PRIMARY_ROLE, PERM_MASTER_ASSIGN_JOBS,
    PERM_MASTER_MANAGE_JOB_BUNDLES,
    PERM_MASTER_GRANT_DIRECT, PERM_MASTER_REVOKE_DIRECT, PERM_MASTER_VIEW_AUDIT,
    PERM_NETWORK_VIEW, PERM_NETWORK_CREATE_ENTRY, PERM_NETWORK_EDIT_ENTRY,
    PERM_HERITAGE_VIEW, PERM_HERITAGE_EDIT_LINKS, PERM_HERITAGE_EXPORT,
    PERM_MEDI_VIEW, PERM_MEDI_FILTER_USE,
    PERM_MEDI_UPLOAD_DOCUMENT, PERM_MEDI_DELETE_DOCUMENT,
    PERM_MEDI_STATUS_ENABLE, PERM_MEDI_STATUS_MANAGE,
    PERM_MEDI_ADD_DOCS,
    PERM_CAGE_VIEW, PERM_CAGE_RECORD_INSPECTION,
    PERM_CAGE_ASSIGN_LOCATIONS, PERM_CAGE_MANAGE_ROOMS_BUILDINGS, PERM_CAGE_EDIT,
    PERM_CAGE_EXPORT_PDF,
    PERM_PROJECT_VIEW, PERM_PROJECT_VIEW_ALL, PERM_PROJECT_ASSIGN, PERM_PROJECT_CREATE,
    PERM_PROJECT_MANAGE_SEVERITY,
    PERM_PROJECT_SET_IN_EXPERIMENT, PERM_PROJECT_UNSET_IN_EXPERIMENT,
    PERM_PROJECT_MANAGE, PERM_PROJECT_ARCHIVE,
    PERM_PROJECT_UPLOAD_DOCUMENT, PERM_PROJECT_DELETE_DOCUMENT,
    PERM_PROJECT_UPLOAD_SOP, PERM_PROJECT_DELETE_SOP,
    PERM_REPORTS_VIEW, PERM_REPORTS_WRITE,
    PERM_PLOTS_VIEW,
    PERM_PDG_CONVERTER_USE,
    PERM_OP_SCHEDULER_VIEW, PERM_OP_SCHEDULER_USE,
    PERM_EMBRYO_TRACK_VIEW,
    PERM_SAMPLE_TRACK_USE,
    PERM_FLOW_TRACK_USE, PERM_FLOW_TRACK_OPEN,
    PERM_FLOW_TRACK_EDIT, PERM_FLOW_TRACK_CREATE, PERM_FLOW_TRACK_DELETE,
]

# ---------------------------------------------------------------------------
# Default job bundle definitions (may be overridden by backend configuration)
# ---------------------------------------------------------------------------

DEFAULT_JOB_BUNDLES: Dict[str, Set[str]] = {
    "vet": {
        PERM_CORE_EDIT_ANIMAL_CORE,
        PERM_CORE_EDIT_ANIMAL_MEASUREMENTS,
        PERM_MEDI_VIEW, PERM_MEDI_FILTER_USE,
        PERM_MEDI_UPLOAD_DOCUMENT,
        PERM_MEDI_STATUS_ENABLE, PERM_MEDI_STATUS_MANAGE,
        PERM_MEDI_ADD_DOCS,
        PERM_REPORTS_VIEW,
    },
    JOB_ANIMAL_WELFARE: {
        PERM_MEDI_FILTER_USE,
        PERM_MEDI_DELETE_DOCUMENT,
    },
    "keeper": {
        PERM_CORE_CREATE_ANIMALS,
        PERM_CORE_EDIT_ANIMAL_CORE,
        PERM_CORE_EDIT_ANIMAL_HOUSING,
        PERM_CORE_EDIT_ANIMAL_MEASUREMENTS,
        PERM_CAGE_ASSIGN_LOCATIONS,
        PERM_CAGE_VIEW, PERM_CAGE_RECORD_INSPECTION,
        PERM_CAGE_MANAGE_ROOMS_BUILDINGS,
        PERM_CAGE_EXPORT_PDF,
        PERM_MEDI_VIEW, PERM_MEDI_FILTER_USE,
        PERM_REPORTS_VIEW,
    },
    "manager": {
        PERM_CORE_EXPORT, PERM_CORE_IMPORT,
        PERM_CORE_CREATE_ANIMALS,
        PERM_CORE_EDIT_ANIMAL_CORE,
        PERM_CORE_EDIT_ANIMAL_IDENTITY,
        PERM_CORE_EDIT_ANIMAL_IMMUTABLE,
        PERM_CORE_EDIT_ANIMAL_HOUSING,
        PERM_CORE_EDIT_ANIMAL_ROLE,
        PERM_CORE_MANAGE_ANIMAL_ROLES,
        PERM_CORE_ARCHIVE_ANIMALS,
        PERM_CORE_DELETE_ANIMALS,
        PERM_CAGE_ASSIGN_LOCATIONS,
        PERM_CAGE_VIEW, PERM_CAGE_RECORD_INSPECTION, PERM_CAGE_EDIT,
        PERM_CAGE_MANAGE_ROOMS_BUILDINGS,
        PERM_CAGE_EXPORT_PDF,
        PERM_PROJECT_VIEW_ALL, PERM_PROJECT_ASSIGN, PERM_PROJECT_CREATE,
        PERM_PROJECT_MANAGE_SEVERITY,
        PERM_PROJECT_SET_IN_EXPERIMENT, PERM_PROJECT_UNSET_IN_EXPERIMENT,
        PERM_PROJECT_MANAGE, PERM_PROJECT_ARCHIVE,
        PERM_PROJECT_UPLOAD_DOCUMENT, PERM_PROJECT_DELETE_DOCUMENT,
        PERM_PROJECT_UPLOAD_SOP, PERM_PROJECT_DELETE_SOP,
        PERM_MEDI_VIEW, PERM_MEDI_DELETE_DOCUMENT, PERM_MEDI_ADD_DOCS,
        PERM_HERITAGE_EXPORT,
        PERM_REPORTS_VIEW,
    },
    "researcher": {
        PERM_CORE_EXPORT, PERM_CORE_IMPORT,
        PERM_CORE_EDIT_ANIMAL_CORE,
        PERM_CORE_EDIT_ANIMAL_MEASUREMENTS,
        PERM_CORE_EDIT_ANIMAL_RESEARCH_DATA,
        PERM_HERITAGE_VIEW, PERM_HERITAGE_EXPORT,
        PERM_MEDI_VIEW,
        PERM_CAGE_VIEW,
        PERM_PROJECT_VIEW,
        PERM_PROJECT_SET_IN_EXPERIMENT,
        PERM_REPORTS_VIEW, PERM_REPORTS_WRITE,
        PERM_PLOTS_VIEW,
        PERM_PDG_CONVERTER_USE,
        PERM_OP_SCHEDULER_VIEW, PERM_OP_SCHEDULER_USE,
        PERM_EMBRYO_TRACK_VIEW,
        PERM_SAMPLE_TRACK_USE,
        PERM_FLOW_TRACK_USE, PERM_FLOW_TRACK_OPEN,
        PERM_FLOW_TRACK_EDIT, PERM_FLOW_TRACK_CREATE, PERM_FLOW_TRACK_DELETE,
    },
}

# Runtime-overridable job bundles (loaded from backend configuration by plugin.py)
JOB_BUNDLES: Dict[str, Set[str]] = {k: set(v) for k, v in DEFAULT_JOB_BUNDLES.items()}

# ---------------------------------------------------------------------------
# Role baselines
# ---------------------------------------------------------------------------

_USER_BASELINE: Set[str] = {
    PERM_CORE_VIEW, PERM_CORE_USE_FILTERS, PERM_CORE_OPEN_READONLY,
    PERM_CORE_STYLE_SETTINGS,
    PERM_REPORTS_VIEW,
    PERM_MEDI_VIEW,
    PERM_NETWORK_VIEW, PERM_NETWORK_CREATE_ENTRY, PERM_NETWORK_EDIT_ENTRY,
    PERM_HERITAGE_VIEW,
    PERM_CAGE_VIEW,
    PERM_PROJECT_VIEW,
    PERM_PLOTS_VIEW,
    PERM_FLOW_TRACK_OPEN,
}

_GUEST_BASELINE: Set[str] = {
    PERM_CORE_VIEW, PERM_CORE_USE_FILTERS, PERM_CORE_OPEN_READONLY,
    PERM_REPORTS_VIEW,
    PERM_MEDI_VIEW,
    PERM_NETWORK_VIEW,
    PERM_HERITAGE_VIEW,
    PERM_CAGE_VIEW,
    PERM_PROJECT_VIEW,
    PERM_PLOTS_VIEW,
}

ROLE_BASELINES: Dict[str, Set[str]] = {
    ROLE_LORD: {"*"},      # wildcard — all permissions always granted
    ROLE_MASTER: set(),    # resolved dynamically — see resolve_effective_permissions
    ROLE_USER: _USER_BASELINE,
    ROLE_GUEST: _GUEST_BASELINE,
}

# ---------------------------------------------------------------------------
# Effective-permission resolution
# ---------------------------------------------------------------------------


def sanitize_assigned_jobs(jobs: Iterable[str]) -> list[str]:
    """Return a deterministic job list with the Vet→AWO invariant enforced.

    Unknown/custom jobs remain visible and are resolved through JOB_BUNDLES when
    present.  AWO is removed fail-closed if Vet is not assigned.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in jobs or []:
        value = str(raw or "").strip()
        if value and value not in seen:
            cleaned.append(value)
            seen.add(value)
    if JOB_ANIMAL_WELFARE in seen and JOB_VET not in seen:
        cleaned = [job for job in cleaned if job != JOB_ANIMAL_WELFARE]
    return cleaned


def resolve_effective_permissions(
    role: Optional[str],
    jobs: Iterable[str],
    granted: Iterable[str],
    revoked: Iterable[str],
) -> Set[str]:
    """Return the effective permission set for the given user state.

    Formula: (role_baseline ∪ ⋃job_permissions ∪ granted) − revoked

    Exceptions:
    * lord → always {"*"} regardless of jobs/granted/revoked.
    * guest → fixed to guest baseline; granted/revoked ignored.
    """
    jobs = sanitize_assigned_jobs(jobs)
    if role == ROLE_LORD:
        return {"*"}
    if role == ROLE_MASTER:
        # All known permissions except the internal toggle. Master is an
        # administrative account and receives the complete grantable catalog.
        return {p for p in ALL_PERMISSIONS if p not in _MASTER_EXCLUDED}
    if role == ROLE_GUEST:
        return set(_GUEST_BASELINE)

    baseline = set(ROLE_BASELINES.get(role or ROLE_GUEST, _GUEST_BASELINE))
    job_perms: Set[str] = set()
    for job in jobs:
        job_perms |= {
            permission for permission in JOB_BUNDLES.get(job, set())
            if permission in ALL_PERMISSIONS
        }

    effective = baseline | job_perms | {
        permission for permission in granted if permission in ALL_PERMISSIONS
    }
    effective -= INTERNAL_PERMISSIONS
    effective -= {permission for permission in revoked if permission in ALL_PERMISSIONS}
    return effective


def can(
    role: Optional[str],
    jobs: Iterable[str],
    granted: Iterable[str],
    revoked: Iterable[str],
    permission_name: str,
) -> bool:
    """Pure permission check given full user state.

    Returns True if the effective permission set contains *permission_name*.
    Lord wildcard ("*") always returns True for any permission.
    """
    if role == ROLE_LORD:
        return True
    if permission_name in INTERNAL_PERMISSIONS:
        return False
    if role == ROLE_MASTER:
        return (
            permission_name in ALL_PERMISSIONS
            and permission_name not in _MASTER_EXCLUDED
        )
    effective = resolve_effective_permissions(role, jobs, granted, revoked)
    return "*" in effective or permission_name in effective


def can_manage_health_status(
    role: Optional[str],
    jobs: Iterable[str],
) -> bool:
    """Clinical sick/abnormal mutation is Vet-only, except Lord/Master."""
    if role in {ROLE_LORD, ROLE_MASTER}:
        return True
    return JOB_VET in {str(job).strip().casefold() for job in jobs}


def get_permission_label(permission_name: str, lang: Optional[str] = None) -> str:
    """Return a human-readable label for a permission key.

    Args:
        permission_name: The permission key (e.g., "core.view")
        lang: Language code ('en', 'de', 'it', 'ru'). If None, defaults to English.

    Returns:
        Translated label, or the permission name itself if not found.
    """
    labels_dict = _load_permission_labels()

    # Default to English if no language specified or not found
    lang_code = lang if lang in labels_dict else "en"

    # Get labels for requested language
    lang_labels = labels_dict.get(lang_code, {})

    # Return translated label, fallback to permission name
    return lang_labels.get(permission_name, permission_name)


def get_permission_tooltip(permission_name: str, lang: Optional[str] = None) -> str:
    """Return a localized effect description with the canonical permission key."""
    return f"{get_permission_label(permission_name, lang)} [{permission_name}]"


def get_permission_namespace_label(namespace: str, lang: Optional[str] = None) -> str:
    """Return a localized namespace heading, with a stable English fallback."""
    labels_dict = _load_permission_labels()
    lang_code = lang if lang in labels_dict else "en"
    lang_labels = labels_dict.get(lang_code, {})
    return lang_labels.get(
        f"__namespace.{namespace}",
        namespace.replace("_", " ").title(),
    )


def get_permission_namespace(permission_name: str) -> str:
    """Return the namespace portion of a permission key (e.g. 'core', 'cage')."""
    return permission_name.split(".")[0] if "." in permission_name else permission_name
