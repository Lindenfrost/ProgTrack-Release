# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track scope provider abstraction.

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set


@dataclass(frozen=True)
class ScopeFilter:
    """Immutable scope filter specification."""

    project: Optional[str] = None
    species: Optional[str] = None

    @property
    def is_active(self) -> bool:
        """Check if any filter is active."""
        return self.project is not None or self.species is not None

    def matches(self, record: Dict[str, Any]) -> bool:
        """Check if a record matches this scope filter."""
        if self.project is not None and record.get("project") != self.project:
            return False
        if self.species is not None and record.get("species") != self.species:
            return False
        return True


class ScopeProvider(ABC):
    """Abstract base for scope resolution.

    This abstraction allows Heritage_Track to work with or without
    Projects_Track being available.
    """

    @abstractmethod
    def get_scope(self) -> ScopeFilter:
        """Return the current scope filter."""
        pass

    @abstractmethod
    def get_scoped_animals(self, animals: Dict[str, Dict[str, Any]], archived: Dict[str, Dict[str, Any]]) -> Set[str]:
        """Return set of animal names matching the current scope."""
        pass


class NullScopeProvider(ScopeProvider):
    """No-op scope provider when no filtering is needed."""

    def get_scope(self) -> ScopeFilter:
        return ScopeFilter()

    def get_scoped_animals(self, animals: Dict[str, Dict[str, Any]], archived: Dict[str, Dict[str, Any]]) -> Set[str]:
        # Return all animals
        result: Set[str] = set()
        result.update(animals.keys())
        result.update(archived.keys())
        return result


class ProjectsTrackScopeProvider(ScopeProvider):
    """Scope provider that reads from Projects_Track plugin."""

    def __init__(self, app: Any):
        self.app = app
        self._pt = getattr(app, "projects_plugin", None)

    def get_scope(self) -> ScopeFilter:
        """Extract scope from Projects_Track."""
        if self._pt is None:
            return ScopeFilter()

        current_project = getattr(self._pt, "current_project", "All")
        active_species = getattr(self._pt, "active_species", None)

        project = None if current_project == "All" else current_project
        species = active_species if active_species else None

        return ScopeFilter(project=project, species=species)

    def get_scoped_animals(self, animals: Dict[str, Dict[str, Any]], archived: Dict[str, Dict[str, Any]]) -> Set[str]:
        """Get animals matching the current scope from Projects_Track.

        Note: animals and archived parameters are ignored - this provider reads
        directly from self.app to ensure fresh data.
        """
        scope = self.get_scope()

        if not scope.is_active:
            # No filtering active - return empty to signal "show nothing" in all-animals mode
            # This matches the original behavior where no filter = empty display set
            return set()

        # Get fresh data from app
        app_animals = getattr(self.app, "animals", {}) or {}
        app_archived = getattr(self.app, "archived", {}) or {}

        result: Set[str] = set()

        # Include both active and archived animals matching filter
        for src in (app_animals, app_archived):
            for name, rec in src.items():
                if not isinstance(rec, dict):
                    continue
                if scope.matches(rec):
                    result.add(name)

        return result


class ExplicitScopeProvider(ScopeProvider):
    """Scope provider with explicitly set values (for testing or manual control)."""

    def __init__(self, project: Optional[str] = None, species: Optional[str] = None):
        self._scope = ScopeFilter(project=project, species=species)
        self._animals: Dict[str, Dict[str, Any]] = {}
        self._archived: Dict[str, Dict[str, Any]] = {}

    def set_scope(self, project: Optional[str] = None, species: Optional[str] = None) -> None:
        self._scope = ScopeFilter(project=project, species=species)

    def set_data(
        self, animals: Dict[str, Dict[str, Any]], archived: Dict[str, Dict[str, Any]]
    ) -> None:
        self._animals = animals
        self._archived = archived

    def get_scope(self) -> ScopeFilter:
        return self._scope

    def get_scoped_animals(self, animals: Dict[str, Dict[str, Any]], archived: Dict[str, Dict[str, Any]]) -> Set[str]:
        if not self._scope.is_active:
            return set()

        result: Set[str] = set()
        for src in (animals, archived):
            for name, rec in src.items():
                if not isinstance(rec, dict):
                    continue
                if self._scope.matches(rec):
                    result.add(name)
        return result
