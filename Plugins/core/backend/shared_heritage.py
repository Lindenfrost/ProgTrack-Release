# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Authenticated Shared access to the existing Heritage graph record."""

from copy import deepcopy

from ..authorization import organizational_owner_id
from ..project_visibility import animal_visible_by_project_scope
from .errors import ConflictError, PermissionDeniedError, ValidationError


_PARENTS = ("egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father")
_IDENTITY = ("ipid", "name", "unit_id", "dummy_kind", "persistence_kind", "heritage_only")
_ROW_FIELDS = (*_PARENTS, *_IDENTITY, "sex", "species", "birth_date", "genotype")


class SharedHeritageStore:
    """Preserve the graph store interface while enforcing the backend scope."""

    def __init__(self, backend, session):
        self.backend = backend
        self.session = session

    def load(self, default=None):
        return self.load_with_revision(default)[0]

    def load_with_revision(self, default=None):
        session = self.session()
        if session is None:
            return deepcopy(default), 0
        with self.backend.protected_request(session, "heritage.view") as scope:
            graph, revision = self.backend.records.get_with_revision("heritage", "graph", default=None)
            if graph is None:
                return deepcopy(default), revision
            return self._projection(graph, *scope)[0], revision

    def _projection(self, graph, connection, account, permissions):
        if not isinstance(graph, dict):
            raise ValidationError("The Heritage graph is invalid.")
        auth = self.backend.authenticated_sessions
        project_scope = auth.animal_scope(connection, account, permissions)
        unit = auth.active_unit(connection, account)
        core = self.backend.animals.load_snapshot()
        core_rows = core["animals"] | core["archived_animals"]
        records = graph.get("animals", {})
        if not isinstance(records, dict) or any(not isinstance(row, dict) for row in records.values()):
            raise ValidationError("The Heritage animal register is invalid.")
        hidden = {
            ipid for ipid, row in core_rows.items()
            if not animal_visible_by_project_scope(row, *project_scope)
            or organizational_owner_id(row).casefold() not in ("", unit)
        }
        hidden.update(
            key for key, row in records.items()
            if str(row.get("unit_id") or "").casefold() not in ("", unit)
            or row.get("ipid") in hidden
        )
        projected = deepcopy(graph)
        projected["animals"] = {
            key: row for key, row in projected.get("animals", {}).items() if key not in hidden
        }
        for row in projected["animals"].values():
            for parent in _PARENTS:
                if row.get(parent) in hidden:
                    row[parent] = ""
        for name in ("derived_inbreeding_cache", "node_positions"):
            projected[name] = {key: item for key, item in projected.get(name, {}).items() if key not in hidden}
        user = account["username"]
        projected["position_cache"] = {
            user: {
                key: item for key, item in projected.get("position_cache", {}).get(user, {}).items()
                if not hidden.intersection(item.get("dependency_ids", ()))
            }
        }
        return projected, hidden, set(core_rows), unit

    def save(self, data, *, expected_revision=None):
        with self.backend.protected_request(self.session(), "heritage.view", write=True) as scope:
            connection, account, permissions = scope
            graph, revision = self.backend.records.get_with_revision("heritage", "graph", default={})
            if expected_revision is None or revision != expected_revision:
                raise ConflictError("The Heritage graph changed. Reload and try again.")
            projected, hidden, core, unit = self._projection(graph, *scope)

            def require(permission):
                if "*" not in permissions and permission not in permissions:
                    raise PermissionDeniedError("This account cannot change the requested Heritage data.")

            prior = projected["animals"]
            replacement = data.get("animals", {})
            animals = graph.setdefault("animals", {})
            if hidden.intersection(replacement):
                raise PermissionDeniedError("A Heritage animal is outside the current scope.")
            for key in prior.keys() | replacement.keys():
                old, new = prior.get(key), replacement.get(key)
                if old == new:
                    continue
                if key in core or (new or old or {}).get("ipid") in core:
                    if old is None or new is None or any((old.get(field) or "") != (new.get(field) or "") for field in _ROW_FIELDS):
                        raise PermissionDeniedError("Core identity is read-only in Heritage.")
                    continue
                if new is None:
                    require("heritage.delete_durable_dummy")
                    if not unit or str(old.get("unit_id") or "").casefold() != unit:
                        raise PermissionDeniedError("The Heritage dummy is outside the current Unit.")
                    del animals[key]
                else:
                    if old is None:
                        require("core.create_animals")
                    if old is None or any((old.get(field) or "") != (new.get(field) or "") for field in _ROW_FIELDS):
                        require("heritage.edit_links")
                        if not unit or str(new.get("unit_id") or "").casefold() != unit:
                            raise PermissionDeniedError("The Heritage dummy is outside the current Unit.")
                        if old is not None and str(old.get("unit_id") or "").casefold() != unit:
                            raise PermissionDeniedError("A Heritage dummy's Unit cannot change.")
                    for parent in _PARENTS:
                        if new.get(parent) in hidden:
                            raise PermissionDeniedError("A parent is outside the current Heritage scope.")
                        if old is not None and not new.get(parent) and animals[key].get(parent) in hidden:
                            new = {**new, parent: animals[key][parent]}
                    animals[key] = new

            for name in ("derived_inbreeding_cache", "node_positions"):
                values = data.get(name, {})
                if hidden.intersection(values):
                    raise PermissionDeniedError("A Heritage cache record is outside the current scope.")
                graph[name] = {key: item for key, item in graph.get(name, {}).items() if key in hidden} | values
            for name in ("version", "updated_at", "settings", "pedigree_revision", "pedigree_sequence"):
                if name in data:
                    graph[name] = data[name]
            if data.get("genotype_colors", {}) != projected.get("genotype_colors", {}):
                require("heritage.edit_genotype_colors")
                graph["genotype_colors"] = data["genotype_colors"]
            user = account["username"]
            positions = data.get("position_cache", {})
            if positions.keys() - {user}:
                raise PermissionDeniedError("Another user's Heritage layout cannot be changed.")
            user_positions = positions.get(user, {})
            if any(hidden.intersection(item.get("dependency_ids", ())) for item in user_positions.values()):
                raise PermissionDeniedError("A Heritage layout dependency is outside the current scope.")
            graph.setdefault("position_cache", {})[user] = user_positions
            revision = self.backend.records.put("heritage", "graph", graph, expected_revision=revision)
            self.backend.audit._append_in_transaction(
                connection, actor_login=user, category="heritage", action="update_graph",
                entity_type="heritage", entity_id="graph", payload={},
            )
            return revision
