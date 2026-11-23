# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Shared connection attempts and the empty projection used before login."""

from copy import deepcopy
from threading import Lock, Thread

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from .backend.errors import BackendUnavailableError


class EmptySharedRecords:
    def get(self, namespace, record_id, *, default=None):
        return deepcopy(default)

    def get_with_revision(self, namespace, record_id, *, default=None):
        return self.get(namespace, record_id, default=default), 0

    def list(self, namespace):
        return {}

    def __getattr__(self, name):
        raise BackendUnavailableError("The Shared PostgreSQL database is unavailable.")


class EmptySharedBackend:
    core_configuration = None

    def __init__(self, paths):
        self.paths = paths
        self.records = EmptySharedRecords()

    def load_core_data(self, *, session=None):
        return {"animals": {}, "archived_animals": {}, "settings": {}}

    def __getattr__(self, name):
        raise BackendUnavailableError("The Shared PostgreSQL database is unavailable.")


def load_initial_projections(candidate, context, enabled_plugins):
    from Plugins.Cage__Track.cage_store import CageStore

    from .backend.shared_heritage import SharedHeritageStore
    from .backend.shared_housing import load_shared_housing
    from .backend.shared_projects import load_shared_project_catalog, load_shared_project_history

    with candidate.protected_request(context) as (_, actor, permissions):
        def allowed(*required):
            return "*" in permissions or all(permission in permissions for permission in required)

        projections = {
            "core": candidate.load_core_data(session=context) if allowed("core.view")
            else {"animals": {}, "archived_animals": {}, "settings": {}},
            "projects": {"version": 1, "projects": {}},
            "project_history": {"version": 1, "projects": {}},
            "heritage": (None, 0),
            "cages": None,
            "chat": [],
        }
        if "Projects_Track" in enabled_plugins and allowed("project.view"):
            projections["projects"] = load_shared_project_catalog(candidate, context)[0]
            projections["project_history"] = load_shared_project_history(candidate, context)
        if "Heritage_Track" in enabled_plugins and allowed("heritage.view"):
            projections["heritage"] = SharedHeritageStore(candidate, lambda: context).load_with_revision()
        if "Cage__Track" in enabled_plugins and allowed("cage.view", "core.view"):
            projections["cages"] = load_shared_housing(candidate, context, CageStore._default_data())
        if "Network_Track" in enabled_plugins and allowed("network.view"):
            projections["chat"] = candidate.load_shared_chat(context)
        return actor, projections


class AttemptSignals(QObject):
    completed = pyqtSignal(int, object, object)


class AttemptResult:
    def __init__(self, discard, cleanup):
        self.discard = discard
        self.cleanup = cleanup
        self._lock = Lock()
        self._result = None

    def store(self, value):
        with self._lock:
            if not self.discard.is_set():
                self._result = value
                return True
        if self.cleanup is not None:
            self.cleanup(value)
        return False

    def take_result(self):
        with self._lock:
            value, self._result = self._result, None
        return value

    def cancel(self):
        self.discard.set()
        value = self.take_result()
        if value is not None and self.cleanup is not None:
            Thread(target=self.cleanup, args=(value,), daemon=True).start()


class SharedAttempt(QRunnable):
    def __init__(self, generation, operation, discard, cleanup=None):
        super().__init__()
        self.generation = generation
        self.operation = operation
        self.result = AttemptResult(discard, cleanup)
        self.signals = AttemptSignals()

    def run(self):
        try:
            value = self.operation()
        except Exception as error:
            if not self.result.discard.is_set():
                self.signals.completed.emit(self.generation, None, error)
        else:
            if self.result.store(value):
                self.signals.completed.emit(self.generation, self.result, None)
