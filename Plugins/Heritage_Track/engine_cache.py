# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track pedigree-engine caching.

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, Optional

from .pedigree_engine import PedigreeEngine


class PedigreeEngineCache:
    """Caches PedigreeEngine instances with automatic invalidation.
    
    The cache key is computed from the complete effective identity and parentage
    inputs used by the resolver.  Rendering metadata and measurements are kept out
    of the key so ordinary plot refreshes do not cause unnecessary engine rebuilds.
    """

    RESOLUTION_POLICY_VERSION = "heritage-engine-resolution.v2"
    
    def __init__(self):
        self._cache: Optional[PedigreeEngine] = None
        self._cache_key: str = ""
        self._parentage_hash: int = 0
    
    def _compute_cache_key(
        self,
        animals: Dict[str, Dict],
        heritage_entries: Dict[str, Dict[str, Any]]
    ) -> str:
        """Compute a deterministic revision for effective graph resolution.

        The resolver distinguishes same-name animals by stable identity, normalized
        display metadata, species and birth information.  Parent references are
        included in both Core and Heritage records.  Deliberately excluding event
        rows and visual/layout settings avoids rebuilding the pedigree engine for
        changes that cannot affect graph topology or disambiguation.
        """
        core_fields = (
            "ipid",
            "name",
            "_base_name",
            "display_name",
            "species",
            "sex",
            "rolle",
            "role_id",
            "birth_date",
            "origin",
            "id",
            "eizellspenderin",
            "samenspender",
            "ziehmutter",
            "ziehvater",
        )
        heritage_fields = (
            "ipid",
            "name",
            "_base_name",
            "display_name",
            "species",
            "sex",
            "birth_date",
            "origin",
            "heritage_only",
            "egg_donor",
            "sperm_donor",
            "surrogate_mother",
            "surrogate_father",
        )

        def selected(records: Dict[str, Dict[str, Any]], fields) -> list:
            result = []
            for key in sorted(records, key=lambda value: str(value).casefold()):
                record = records.get(key)
                if not isinstance(record, dict):
                    record = {}
                result.append(
                    {
                        "key": str(key),
                        **{field: record.get(field, "") for field in fields},
                    }
                )
            return result

        payload = {
            "policy": self.RESOLUTION_POLICY_VERSION,
            "core": selected(animals, core_fields),
            "heritage": selected(heritage_entries, heritage_fields),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    
    def get_engine(
        self,
        animals: Dict[str, Dict],
        parent_lookup: Callable[[str, Optional[Dict[str, Any]]], Dict[str, str]],
        heritage_entries: Dict[str, Dict[str, Any]]
    ) -> PedigreeEngine:
        """Get a PedigreeEngine, either from cache or newly built.
        
        Args:
            animals: Dictionary of animal records from ProgTrack
            parent_lookup: Function to look up parentage for an animal
            heritage_entries: Heritage-only entries from the backend graph
                (the former heritage_animals.json store is archived).
            
        Returns:
            PedigreeEngine instance (cached or newly built)
        """
        # Compute cache key from current data
        key = self._compute_cache_key(animals, heritage_entries)
        
        # Return cached engine if data hasn't changed
        if self._cache is not None and self._cache_key == key:
            return self._cache
        
        # Build new engine
        engine = PedigreeEngine(animals, parent_lookup, heritage_entries)
        engine.build()
        # Expose the exact resolver revision to the complete immutable render
        # cache entry; genetic F revisions remain a separate concern.
        engine.resolution_revision = key
        
        # Cache the engine
        self._cache = engine
        self._cache_key = key
        
        return engine
    
    def invalidate(self) -> None:
        """Manually invalidate the cache."""
        self._cache = None
        self._cache_key = ""
    
    def is_cached(self, animals: Dict[str, Dict], heritage_entries: Dict[str, Dict[str, Any]]) -> bool:
        """Check if the current data would hit the cache."""
        if self._cache is None:
            return False
        key = self._compute_cache_key(animals, heritage_entries)
        return self._cache_key == key
