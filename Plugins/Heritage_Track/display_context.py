# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track immutable display context.

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Dict, FrozenSet, Optional, Set, Tuple

from .pedigree_engine import PedigreeEngine

if TYPE_CHECKING:
    from .display_strategies import DisplaySetStrategy
    from .ghost_strategies import GhostNodeStrategy


Point = Tuple[float, float]


def _freeze_value(value: Any) -> Any:
    """Recursively freeze render data at the cache boundary.

    ``@dataclass(frozen=True)`` protects attribute assignment only.  Render
    results also contain nested dictionaries and sets, so they must be copied
    into immutable containers before they can safely be shared by painting,
    hit testing and export preparation.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


@dataclass(frozen=True)
class RenderCacheKey:
    """Stable identity of one user-owned Heritage render result."""

    user_id: str
    canonical_selection: Tuple[str, ...]
    selection_type: str
    display_mode: str

    @classmethod
    def create(
        cls,
        user_id: Any,
        selection: Any,
        selection_type: Any,
        display_mode: Any,
    ) -> "RenderCacheKey":
        if isinstance(selection, (str, bytes)):
            selection_values = (selection,)
        else:
            selection_values = selection or ()
        values = tuple(
            sorted(
                {str(value).strip() for value in selection_values if str(value).strip()},
                key=str.casefold,
            )
        )
        return cls(
            user_id=str(user_id or "anonymous").strip() or "anonymous",
            canonical_selection=values,
            selection_type=str(selection_type or "selected").strip() or "selected",
            display_mode=str(display_mode or "partner_normalized").strip() or "partner_normalized",
        )


@dataclass(frozen=True)
class FrozenRoutePlan:
    """Read-only route geometry copied from a completed ``RoutePlan``.

    The router still builds a mutable ``RoutePlan`` internally.  Once a frame
    is accepted, this value is the immutable representation used by the render
    cache; a mutable copy can be requested only for a view-pixel recalculation.
    """

    animal_positions: Mapping[str, Point]
    family_positions: Mapping[str, Point]
    family_members: Mapping[str, FrozenSet[str]]
    routes: Mapping[str, Mapping[str, Tuple[Point, ...]]]
    crossing_gaps: Mapping[Any, Tuple[Point, ...]] = field(default_factory=dict)
    unresolved: Tuple[str, ...] = ()
    line_crossing_gaps: Mapping[Any, Tuple[Point, ...]] = field(default_factory=dict)
    line_crossing_problems: Tuple[str, ...] = ()
    line_crossings_ready: bool = False
    geometry_revision: int = 0
    gap_geometry_revision: int = -1
    pixel_gap_revision: int = 0
    route_obstacle_hits: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # A frozen dataclass does not freeze nested dictionaries/lists.  The
        # render boundary must not retain mutable router aliases.
        for field_name in (
            "animal_positions",
            "family_positions",
            "family_members",
            "routes",
            "crossing_gaps",
            "line_crossing_gaps",
        ):
            object.__setattr__(self, field_name, _freeze_value(getattr(self, field_name)))
        for field_name in ("unresolved", "line_crossing_problems", "route_obstacle_hits"):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))

    @classmethod
    def from_route_plan(cls, plan: Any) -> "FrozenRoutePlan":
        """Copy and freeze a router result without retaining mutable aliases."""
        return cls(
            animal_positions={key: tuple(point) for key, point in plan.animal_positions.items()},
            family_positions={key: tuple(point) for key, point in plan.family_positions.items()},
            family_members={key: frozenset(values) for key, values in plan.family_members.items()},
            routes={
                family_id: {
                    endpoint: tuple(tuple(point) for point in path)
                    for endpoint, path in endpoints.items()
                }
                for family_id, endpoints in plan.routes.items()
            },
            crossing_gaps={
                key: tuple(tuple(point) for point in points)
                for key, points in plan.crossing_gaps.items()
            },
            unresolved=tuple(plan.unresolved),
            line_crossing_gaps={
                key: tuple(tuple(point) for point in points)
                for key, points in plan.line_crossing_gaps.items()
            },
            line_crossing_problems=tuple(plan.line_crossing_problems),
            line_crossings_ready=bool(plan.line_crossings_ready),
            geometry_revision=int(plan.geometry_revision),
            gap_geometry_revision=int(plan.gap_geometry_revision),
            pixel_gap_revision=int(plan.pixel_gap_revision),
            route_obstacle_hits=tuple(plan.route_obstacle_hits),
        )

    def route_segments(self, family_id: str, endpoint: str) -> list[tuple[Point, Point]]:
        from .pedigree_router import _path_segments

        return _path_segments(self.routes.get(family_id, {}).get(endpoint, ()))

    def draw_segments(
        self,
        family_id: str,
        endpoint: str,
        *,
        gap_radius: float = 0.10,
        gap_radius_pixels: Optional[float] = None,
        pixel_scale: Optional[Tuple[float, float]] = None,
    ) -> list[tuple[Point, Point]]:
        from .pedigree_router import _split_segment_at_gaps
        import math

        output: list[tuple[Point, Point]] = []
        for index, segment in enumerate(self.route_segments(family_id, endpoint)):
            gaps = self.crossing_gaps.get((family_id, endpoint, index), ())
            segment_gap_radius = gap_radius
            if gap_radius_pixels is not None and pixel_scale is not None:
                (x1, y1), (x2, y2) = segment
                data_length = math.hypot(x2 - x1, y2 - y1)
                pixel_length = math.hypot(
                    (x2 - x1) * max(1.0, float(pixel_scale[0])),
                    (y2 - y1) * max(1.0, float(pixel_scale[1])),
                )
                if pixel_length > 1e-7:
                    segment_gap_radius = max(0.0, float(gap_radius_pixels)) * data_length / pixel_length
            output.extend(_split_segment_at_gaps(segment, gaps, segment_gap_radius))
        return output

    def all_points(self) -> list[Point]:
        points = list(self.animal_positions.values()) + list(self.family_positions.values())
        for endpoint_routes in self.routes.values():
            for path in endpoint_routes.values():
                points.extend(path)
        return points

    def to_mutable(self) -> Any:
        """Return a detached mutable router plan for pixel-only gap updates."""
        from .pedigree_router import RoutePlan

        return RoutePlan(
            animal_positions={key: tuple(point) for key, point in self.animal_positions.items()},
            family_positions={key: tuple(point) for key, point in self.family_positions.items()},
            family_members={key: set(values) for key, values in self.family_members.items()},
            routes={
                family_id: {
                    endpoint: [tuple(point) for point in path]
                    for endpoint, path in endpoints.items()
                }
                for family_id, endpoints in self.routes.items()
            },
            crossing_gaps={key: [tuple(point) for point in points] for key, points in self.crossing_gaps.items()},
            unresolved=list(self.unresolved),
            line_crossing_gaps={key: [tuple(point) for point in points] for key, points in self.line_crossing_gaps.items()},
            line_crossing_problems=list(self.line_crossing_problems),
            line_crossings_ready=self.line_crossings_ready,
            geometry_revision=self.geometry_revision,
            gap_geometry_revision=self.gap_geometry_revision,
            pixel_gap_revision=self.pixel_gap_revision,
            route_obstacle_hits=list(self.route_obstacle_hits),
        )


@dataclass(frozen=True)
class RenderCacheEntry:
    """Complete immutable result accepted for one Heritage render cycle."""

    CACHE_SCHEMA_VERSION: ClassVar[str] = "heritage-render-cache.v1"

    cache_key: RenderCacheKey
    core_projection_revision: str
    pedigree_f_revision: str
    engine_resolution_revision: str
    logical_layout_revision: str
    dependencies: FrozenSet[str]
    record_index: Mapping[str, Mapping[str, Any]]
    canonical_selection: Tuple[str, ...]
    selection_type: str
    display_mode: str
    effective_parent_map: Mapping[str, Mapping[str, str]]
    display_nodes: FrozenSet[str]
    ghost_nodes: FrozenSet[str]
    levels: Mapping[str, int]
    family_nodes: Mapping[str, Mapping[str, Any]]
    family_members: Mapping[str, FrozenSet[str]]
    positions: Mapping[str, Point]
    locked_positions: Mapping[str, Point]
    route_plan: FrozenRoutePlan
    obstacles: Mapping[str, Any]
    bounds: Tuple[Tuple[float, float], Tuple[float, float]]
    f_values: Mapping[str, float] = field(default_factory=dict)
    f_status: Mapping[str, str] = field(default_factory=dict)
    diagnostics: Tuple[str, ...] = ()
    fatal_diagnostics: Tuple[str, ...] = ()
    schema_version: str = CACHE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_selection", tuple(self.canonical_selection))
        object.__setattr__(self, "dependencies", frozenset(self.dependencies))
        object.__setattr__(self, "display_nodes", frozenset(self.display_nodes))
        object.__setattr__(self, "ghost_nodes", frozenset(self.ghost_nodes))
        object.__setattr__(self, "record_index", _freeze_value(self.record_index))
        object.__setattr__(self, "effective_parent_map", _freeze_value(self.effective_parent_map))
        object.__setattr__(self, "levels", _freeze_value(self.levels))
        object.__setattr__(self, "family_nodes", _freeze_value(self.family_nodes))
        object.__setattr__(self, "family_members", _freeze_value(self.family_members))
        object.__setattr__(self, "positions", _freeze_value(self.positions))
        object.__setattr__(self, "locked_positions", _freeze_value(self.locked_positions))
        object.__setattr__(self, "obstacles", _freeze_value(self.obstacles))
        object.__setattr__(self, "bounds", tuple(tuple(point) for point in self.bounds))
        object.__setattr__(self, "f_values", _freeze_value(self.f_values))
        object.__setattr__(self, "f_status", _freeze_value(self.f_status))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "fatal_diagnostics", tuple(self.fatal_diagnostics))
        object.__setattr__(self, "schema_version", str(self.schema_version or self.CACHE_SCHEMA_VERSION))
        if not isinstance(self.route_plan, FrozenRoutePlan):
            object.__setattr__(self, "route_plan", FrozenRoutePlan.from_route_plan(self.route_plan))

    @property
    def revision_tuple(self) -> Tuple[str, str, str, str]:
        return (
            self.core_projection_revision,
            self.pedigree_f_revision,
            self.engine_resolution_revision,
            self.logical_layout_revision,
        )

    @property
    def dependency_ids(self) -> FrozenSet[str]:
        return self.dependencies

    @property
    def valid(self) -> bool:
        # Diagnostics may be non-fatal routing warnings.  Only explicit fatal
        # diagnostics reject an entry; callers can display the full summary.
        return not bool(self.fatal_diagnostics)

    def matches_revisions(self, revisions: Tuple[str, str, str, str]) -> bool:
        return self.revision_tuple == tuple(revisions)


class RenderCacheRegistry:
    """Small atomic cache with reverse dependency invalidation.

    Entries are immutable, so replacing one key is a single dictionary
    operation from the renderer's point of view.  Dependency ids are stable
    animal/IPID keys; invalidating one removes every entry that references it.
    """

    def __init__(self) -> None:
        self._entries: Dict[RenderCacheKey, RenderCacheEntry] = {}
        self._dependency_index: Dict[str, Set[RenderCacheKey]] = {}

    def put(self, entry: "RenderCacheEntry") -> None:
        old = self._entries.get(entry.cache_key)
        if old is not None:
            for dependency in old.dependencies:
                keys = self._dependency_index.get(dependency)
                if keys is not None:
                    keys.discard(entry.cache_key)
                    if not keys:
                        self._dependency_index.pop(dependency, None)
        self._entries[entry.cache_key] = entry
        for dependency in entry.dependencies:
            self._dependency_index.setdefault(dependency, set()).add(entry.cache_key)

    def get(self, key: RenderCacheKey) -> Optional["RenderCacheEntry"]:
        return self._entries.get(key)

    def get_valid(
        self,
        key: RenderCacheKey,
        *,
        revisions: Optional[Tuple[str, str, str, str]] = None,
        dependencies: Optional[Set[str]] = None,
    ) -> Optional["RenderCacheEntry"]:
        """Return an entry only when its binding tuple/dependencies still match."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if revisions is not None and not entry.matches_revisions(revisions):
            self.invalidate({key_value for key_value in entry.dependencies})
            return None
        if dependencies is not None and set(dependencies) != set(entry.dependencies):
            self.invalidate({key_value for key_value in entry.dependencies})
            return None
        return entry

    def invalidate(self, dependencies: Set[str]) -> int:
        affected: Set[RenderCacheKey] = set()
        for dependency in dependencies:
            affected.update(self._dependency_index.get(str(dependency), set()))
        for key in affected:
            entry = self._entries.pop(key, None)
            if entry is None:
                continue
            for dependency in entry.dependencies:
                keys = self._dependency_index.get(dependency)
                if keys is not None:
                    keys.discard(key)
                    if not keys:
                        self._dependency_index.pop(dependency, None)
        return len(affected)

    def clear(self) -> None:
        self._entries.clear()
        self._dependency_index.clear()

    def __len__(self) -> int:
        return len(self._entries)


@dataclass(frozen=True)
class DisplayContext:
    """Immutable context containing all data needed for rendering a pedigree plot.

    This class encapsulates the complete state needed to render a pedigree graph,
    making the rendering process side-effect free and enabling caching/reuse.
    """

    engine: PedigreeEngine
    display_nodes: Set[str]
    levels: Dict[str, int]
    ghost_nodes: Set[str] = field(default_factory=set)
    collapsed_families: Set[str] = field(default_factory=set)
    hidden_nodes: Set[str] = field(default_factory=set)
    family_nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    positions: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    locked_positions: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    # Selection metadata is part of the immutable context so every downstream
    # stage renders the same semantic scope and mode decision.
    canonical_selection: Tuple[str, ...] = field(default_factory=tuple)
    selection_type: str = "selected"
    display_mode: str = "focused"

    def __post_init__(self) -> None:
        # Keep the context itself and every nested collection immutable.  A
        # builder can still create a new context via ``copy_with``; render and
        # hit-test consumers cannot accidentally mutate a published frame.
        object.__setattr__(self, "display_nodes", frozenset(self.display_nodes))
        object.__setattr__(self, "levels", _freeze_value(self.levels))
        object.__setattr__(self, "ghost_nodes", frozenset(self.ghost_nodes))
        object.__setattr__(self, "collapsed_families", frozenset(self.collapsed_families))
        object.__setattr__(self, "hidden_nodes", frozenset(self.hidden_nodes))
        object.__setattr__(self, "family_nodes", _freeze_value(self.family_nodes))
        object.__setattr__(self, "positions", _freeze_value(self.positions))
        object.__setattr__(self, "locked_positions", _freeze_value(self.locked_positions))
        object.__setattr__(
            self,
            "canonical_selection",
            tuple(
                sorted(
                    {str(value).strip() for value in self.canonical_selection if str(value).strip()},
                    key=lambda value: (value.casefold(), value),
                )
            ),
        )
        object.__setattr__(self, "selection_type", str(self.selection_type or "selected").strip() or "selected")
        object.__setattr__(self, "display_mode", str(self.display_mode or "focused").strip() or "focused")

    def get_visible_nodes(self) -> Set[str]:
        """Return nodes that should be rendered (display nodes minus hidden)."""
        return self.display_nodes - self.hidden_nodes

    def get_render_nodes(self) -> Set[str]:
        """Return all nodes to render including family nodes."""
        return self.get_visible_nodes() | set(self.family_nodes.keys())

    def is_ghost(self, node: str) -> bool:
        """Check if a node is a ghost node."""
        return node in self.ghost_nodes

    def is_collapsed(self, family_id: str) -> bool:
        """Check if a family is collapsed."""
        return family_id in self.collapsed_families

    def get_node_level(self, node: str) -> int:
        """Get the computed level for a node, defaulting to 0."""
        return self.levels.get(node, 0)

    def get_max_level(self) -> int:
        """Get the maximum level across all nodes."""
        return max(self.levels.values(), default=0)

    def copy_with(
        self,
        display_nodes: Optional[Set[str]] = None,
        levels: Optional[Dict[str, int]] = None,
        ghost_nodes: Optional[Set[str]] = None,
        collapsed_families: Optional[Set[str]] = None,
        hidden_nodes: Optional[Set[str]] = None,
        family_nodes: Optional[Dict[str, Dict[str, Any]]] = None,
        positions: Optional[Dict[str, Tuple[float, float]]] = None,
        locked_positions: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> "DisplayContext":
        """Create a new context with specified fields replaced."""
        return DisplayContext(
            engine=self.engine,
            display_nodes=display_nodes if display_nodes is not None else self.display_nodes,
            levels=levels if levels is not None else self.levels,
            ghost_nodes=ghost_nodes if ghost_nodes is not None else self.ghost_nodes,
            collapsed_families=collapsed_families if collapsed_families is not None else self.collapsed_families,
            hidden_nodes=hidden_nodes if hidden_nodes is not None else self.hidden_nodes,
            family_nodes=family_nodes if family_nodes is not None else self.family_nodes,
            positions=positions if positions is not None else self.positions,
            locked_positions=locked_positions if locked_positions is not None else self.locked_positions,
            canonical_selection=self.canonical_selection,
            selection_type=self.selection_type,
            display_mode=self.display_mode,
        )


class DisplayContextBuilder:
    """Builds DisplayContext by orchestrating the pipeline stages.

    This builder separates the concerns of:
    1. Data layer - Building the pedigree engine
    2. Filter layer - Computing which nodes to display
    3. Layout layer - Computing levels and positions
    4. Ghost handling - Finding out-of-scope ghost nodes
    """

    def __init__(
        self,
        engine: PedigreeEngine,
        settings: Dict[str, Any],
        display_strategy: "DisplaySetStrategy",
        ghost_strategy: Optional["GhostNodeStrategy"] = None,
    ):
        self.engine = engine
        self.settings = settings
        self.display_strategy = display_strategy
        self.ghost_strategy = ghost_strategy
        self._max_generations: int = settings.get("max_generations", 999)
        self._exclude_archived: bool = settings.get("exclude_archived", False)

    def build(
        self,
        selected_animals: list[str],
        archived_animals: Optional[Set[str]] = None,
        *,
        display_mode: str = "focused",
        selection_type: str = "selected",
    ) -> DisplayContext:
        """Build a complete DisplayContext from the current state."""
        archived = archived_animals or set()

        # Step 1: Compute display set using strategy
        display_nodes = self.display_strategy.compute(
            self.engine, selected_animals, self._max_generations, self._exclude_archived, archived
        )

        # Step 2: Handle archived exclusion in all-animals mode
        is_no_selection = len(selected_animals) == 0
        if is_no_selection and self._exclude_archived:
            display_nodes = display_nodes - archived

        # Step 3: Find ghost nodes
        ghost_nodes: Set[str] = set()
        if self.ghost_strategy:
            ghost_nodes = self.ghost_strategy.find_ghosts(display_nodes, self.engine, archived)
            display_nodes = display_nodes | ghost_nodes

        # Step 4: Compute levels with modifications
        levels = self._compute_modified_levels(display_nodes, ghost_nodes)

        return DisplayContext(
            engine=self.engine,
            display_nodes=display_nodes,
            levels=levels,
            ghost_nodes=ghost_nodes,
            collapsed_families=set(),
            hidden_nodes=set(),
            family_nodes={},
            positions={},
            locked_positions={},
            canonical_selection=tuple(selected_animals),
            selection_type=selection_type,
            display_mode=display_mode,
        )

    def _compute_modified_levels(
        self, display_nodes: Set[str], ghost_nodes: Set[str]
    ) -> Dict[str, int]:
        """Compute levels with leaf promotion and pull-up passes."""
        # Get base levels from engine
        all_graph_nodes = self.engine.all_nodes
        all_graph_levels = self.engine.compute_levels(all_graph_nodes)
        pre_collapse_levels = self.engine.compute_levels(display_nodes)

        if not pre_collapse_levels:
            return {}

        _max_lvl = max(pre_collapse_levels.values(), default=0)

        # Leaf promotion: isolated nodes → max_level
        if _max_lvl > 0:
            for _node in list(display_nodes):
                if pre_collapse_levels.get(_node, 0) < _max_lvl:
                    _has_any_children = bool(self.engine.parent_to_children.get(_node, set()))
                    if not _has_any_children:
                        pre_collapse_levels[_node] = _max_lvl

        # Assign levels to ghost nodes from full-graph level dict
        for _ghost in ghost_nodes:
            if _ghost not in pre_collapse_levels:
                pre_collapse_levels[_ghost] = all_graph_levels.get(_ghost, 0)

        # Pull-up pass: level 0 parents pulled toward children
        for _pull_pass in range(_max_lvl + 2):
            _pull_changed = False
            for _node in list(display_nodes):
                if pre_collapse_levels.get(_node, 0) != 0:
                    continue
                _kids = self.engine.parent_to_children.get(_node, set()) & display_nodes
                if not _kids:
                    continue
                _kid_lvls = [pre_collapse_levels.get(k, 0) for k in _kids]
                _max_kid = max(_kid_lvls)
                _min_kid = min(_kid_lvls)
                if _max_kid <= 1:
                    continue
                _desired = _max_kid - 1
                if _desired >= _min_kid:
                    continue
                pre_collapse_levels[_node] = _desired
                _pull_changed = True
            if not _pull_changed:
                break

        return pre_collapse_levels
