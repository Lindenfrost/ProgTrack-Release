# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Module: Heritage Track pedigree-engine caching.

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, Optional

from .pedigree_engine import PedigreeEngine


class PedigreeEngineCache:
    """Caches PedigreeEngine instances with automatic invalidation.
    
    The cache key is computed from a hash of the animals and heritage entries data.
    When the underlying data changes, a new engine is built. Otherwise, the cached
    engine is returned for 3-5x performance improvement.
    """
    
    def __init__(self):
        self._cache: Optional[PedigreeEngine] = None
        self._cache_key: str = ""
        self._parentage_hash: int = 0
    
    def _compute_cache_key(
        self,
        animals: Dict[str, Dict],
        heritage_entries: Dict[str, Dict[str, Any]]
    ) -> str:
        """Compute a cache key from the data.
        
        Uses a fast hash of the animal names and their parentage data.
        """
        # Create a deterministic string representation
        key_parts = []
        
        # Add animal names and their parent fields
        for name in sorted(animals.keys()):
            record = animals[name]
            key_parts.append(name)
            # Include parentage fields in hash
            for field in ["eizellspenderin", "samenspender", "ziehmutter", "ziehvater", "species"]:
                value = record.get(field, "")
                key_parts.append(f"{field}={value}")
        
        # Include heritage entries
        for name in sorted(heritage_entries.keys()):
            entry = heritage_entries[name]
            key_parts.append(f"h:{name}")
            for field in ["egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father"]:
                value = entry.get(field, "")
                key_parts.append(f"{field}={value}")
        
        # Compute hash
        key_string = "|".join(key_parts)
        return hashlib.md5(key_string.encode()).hexdigest()
    
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
            heritage_entries: Heritage-only entries from heritage_animals.json
            
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
