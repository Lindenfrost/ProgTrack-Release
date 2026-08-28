# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track kinship and inbreeding helpers.

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple


class InbreedingCalculator:
    """Computes phi, r, and F from a pedigree with memoization."""

    def __init__(self, genetic_parents: Dict[str, Tuple[Optional[str], Optional[str]]]):
        self.parents: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        self._malformed_nodes: Set[str] = set()
        referenced_parents: set[str] = set()

        for raw_child, raw_pair in (genetic_parents or {}).items():
            child = self._norm(raw_child)
            if not child:
                continue

            mother_raw = None
            father_raw = None
            if isinstance(raw_pair, (tuple, list)):
                if len(raw_pair) >= 1:
                    mother_raw = raw_pair[0]
                if len(raw_pair) >= 2:
                    father_raw = raw_pair[1]

            mother = self._norm(mother_raw) or None
            father = self._norm(father_raw) or None

            if mother == child:
                self._malformed_nodes.add(child)
            if father == child:
                self._malformed_nodes.add(child)

            self.parents[child] = (mother, father)
            if mother:
                referenced_parents.add(mother)
            if father:
                referenced_parents.add(father)

        for parent_name in referenced_parents:
            self.parents.setdefault(parent_name, (None, None))

        self._phi_cache: Dict[Tuple[str, str], float] = {}
        self._stack: set[Tuple[str, str]] = set()
        self._depth_cache: Dict[str, int] = {}
        self._depth_stack: set[str] = set()
        self._cycle_nodes: Set[str] = set(self._malformed_nodes)
        self._detect_cycle_nodes()

    @property
    def cycle_nodes(self) -> Set[str]:
        """Return nodes participating in a cyclic or self-parent pedigree."""
        return set(self._cycle_nodes)

    def _detect_cycle_nodes(self) -> None:
        state: Dict[str, int] = {}
        stack: List[str] = []

        def visit(node: str) -> None:
            if state.get(node, 0) == 2:
                return
            if state.get(node, 0) == 1:
                try:
                    start = stack.index(node)
                except ValueError:
                    start = 0
                self._cycle_nodes.update(stack[start:])
                return
            state[node] = 1
            stack.append(node)
            for parent in self.parents.get(node, (None, None)):
                if parent:
                    visit(parent)
            stack.pop()
            state[node] = 2

        for node in sorted(self.parents, key=str.casefold):
            visit(node)

    def _norm(self, name: Optional[str]) -> str:
        if name is None:
            return ""
        return str(name).strip()

    def _key(self, a: str, b: str) -> Tuple[str, str]:
        return (a, b) if a <= b else (b, a)

    def _depth(self, name: Optional[str]) -> int:
        node = self._norm(name)
        if not node:
            return 0

        cached = self._depth_cache.get(node)
        if cached is not None:
            return cached
        if node in self._depth_stack:
            # cycle guard in malformed pedigrees
            return 0

        self._depth_stack.add(node)
        try:
            mother, father = self.parents.get(node, (None, None))
            if mother or father:
                value = 1 + max(self._depth(mother), self._depth(father))
            else:
                value = 0
            self._depth_cache[node] = value
            return value
        finally:
            self._depth_stack.discard(node)

    def kinship_phi(self, a: Optional[str], b: Optional[str]) -> float:
        """Pairwise kinship coefficient phi(a, b)."""
        n1 = self._norm(a)
        n2 = self._norm(b)
        if not n1 or not n2:
            return 0.0

        # Recurse from the more descendant side first.
        # This avoids order-dependent underestimation for ancestor/descendant
        # pairs (e.g., parent-offspring where the parent is non-founder).
        if n1 != n2:
            d1 = self._depth(n1)
            d2 = self._depth(n2)
            if d2 > d1:
                n1, n2 = n2, n1
            elif d1 == d2:
                p1 = self.parents.get(n1, (None, None))
                p2 = self.parents.get(n2, (None, None))
                has_parents_1 = bool((p1[0] or "") or (p1[1] or ""))
                has_parents_2 = bool((p2[0] or "") or (p2[1] or ""))
                if has_parents_2 and not has_parents_1:
                    n1, n2 = n2, n1
                elif has_parents_1 == has_parents_2 and n2.lower() < n1.lower():
                    n1, n2 = n2, n1

        key = self._key(n1, n2)
        cached = self._phi_cache.get(key)
        if cached is not None:
            return cached

        if key in self._stack:
            # Cycle guard in malformed pedigrees.
            return 0.0

        self._stack.add(key)
        try:
            if n1 == n2:
                mother, father = self.parents.get(n1, (None, None))
                if mother and father:
                    value = 0.5 * (1.0 + self.kinship_phi(mother, father))
                else:
                    # founder assumption (non-inbred founder)
                    value = 0.5
            else:
                m1, f1 = self.parents.get(n1, (None, None))
                if m1 or f1:
                    value = 0.5 * (self.kinship_phi(m1, n2) + self.kinship_phi(f1, n2))
                else:
                    m2, f2 = self.parents.get(n2, (None, None))
                    if m2 or f2:
                        value = 0.5 * (self.kinship_phi(n1, m2) + self.kinship_phi(n1, f2))
                    else:
                        value = 0.0

            # Clamp defensive bounds.
            if value < 0.0:
                value = 0.0
            if value > 1.0:
                value = 1.0

            self._phi_cache[key] = value
            return value
        finally:
            self._stack.discard(key)

    def relationship_r(self, a: Optional[str], b: Optional[str]) -> float:
        """Relationship coefficient r(a, b) = 2 * phi(a, b)."""
        value = 2.0 * self.kinship_phi(a, b)
        if value < 0.0:
            return 0.0
        if value > 2.0:
            return 2.0
        return value

    def hypothetical_offspring_F(self, a: Optional[str], b: Optional[str]) -> float:
        """Hypothetical inbreeding of offspring from parents a and b.

        F_offspring(a,b) = phi(a,b)
        """
        return self.kinship_phi(a, b)

    def self_inbreeding_F(self, name: Optional[str]) -> float:
        """Inbreeding coefficient of animal *name*.

        F(X) = phi(mother_of_X, father_of_X).
        Returns 0.0 for founders (no parents known, or only one parent known).
        """
        n = self._norm(name)
        if not n:
            return 0.0
        mother, father = self.parents.get(n, (None, None))
        if not mother or not father:
            return 0.0
        return self.kinship_phi(mother, father)
