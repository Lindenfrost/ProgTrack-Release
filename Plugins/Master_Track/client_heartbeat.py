# Part of: ProgTrack 0.3.0
# Required ProgTrack version: see plugin manifest.
"""One background heartbeat for session authority and owned edit leases."""

from math import ceil
from time import monotonic

from PyQt6.QtCore import QEvent, QObject, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal

from Plugins.core.backend.errors import SessionRevokedError
from Plugins.core.backend.session_timing import heartbeat_seconds


def heartbeat_interval_ms() -> int:
    return heartbeat_seconds() * 1_000


class _HeartbeatRequest(QRunnable):
    def __init__(self, backend, context, tokens, renew, completed, configuration_token=None):
        super().__init__()
        self.backend = backend
        self.context = context
        self.tokens = tokens
        self.renew = renew
        self.completed = completed
        self.configuration_token = configuration_token

    def run(self):
        try:
            if self.renew:
                result = self.backend.session_heartbeat(self.context, self.tokens)
            else:
                result = self.backend.session_status(self.context)
            configuration = getattr(self.backend, "core_configuration", None)
            snapshot = None
            if configuration is not None and self.configuration_token is not None:
                generation, revisions = self.configuration_token
                try:
                    changed = configuration.snapshot_for_session(self.context, known_revisions=revisions)
                    if changed is not None:
                        snapshot = generation, changed
                except SessionRevokedError:
                    raise
                except Exception:
                    # Preserve the last projections if this independent read fails.
                    pass
                result = (*result, snapshot)
            error = None
        except Exception as exc:
            result, error = None, exc
        try:
            self.completed.emit(self.backend, self.context, self.tokens, result, error)
        except RuntimeError:
            # The window and its signal can be destroyed during shutdown.
            pass


class ClientHeartbeat(QObject):
    _completed = pyqtSignal(object, object, object, object, object)
    _revoked = pyqtSignal(object, object)

    def __init__(self, master):
        super().__init__(master.app)
        self.master = master
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self._completed.connect(self._finish)
        self._revoked.connect(self._handle_revocation, Qt.ConnectionType.QueuedConnection)
        master.app.installEventFilter(self)
        self.pending = False
        self.requested = False
        self.running = False
        self.heartbeat_interval = heartbeat_interval_ms() / 1000
        self.renew_at = 0.0
        self._server_sessions = None

    def start(self):
        self._server_sessions = self.master.app.backend.authenticated_sessions
        self._server_sessions.on_revoked = self._revoked.emit
        self.running = True
        self.renew_at = 0.0
        self.refresh()

    def stop(self):
        if self._server_sessions is not None:
            self._server_sessions.on_revoked = None
            self._server_sessions = None
        self.running = False
        self.requested = False
        self.timer.stop()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.WindowActivate and self.running:
            self.refresh()
        return super().eventFilter(watched, event)

    def refresh(self):
        context = self.master.session_context
        if context is None or not self.running:
            return
        if self.pending:
            self.requested = True
            return
        app = self.master.app
        now = monotonic()
        renew = now >= self.renew_at
        if renew:
            self.renew_at = now + self.heartbeat_interval
        self.timer.start(max(1, ceil((self.renew_at - now) * 1000)))
        lease_tokens = getattr(app, "_current_edit_lease_tokens", lambda: ())() if renew else ()
        self.pending = True
        QThreadPool.globalInstance().start(
            _HeartbeatRequest(
                app.backend, context, lease_tokens, renew, self._completed,
                getattr(app, "_core_configuration_refresh_token", lambda: None)(),
            )
        )

    def _finish(self, backend, context, tokens, result, error):
        self.pending = False
        retry = self.requested
        self.requested = False
        app = self.master.app
        if backend is not app.backend or context != self.master.session_context:
            if self.running:
                self.refresh()
            return
        if isinstance(error, SessionRevokedError):
            self._handle_revocation(context, error)
        elif error is None:
            update = getattr(app, "_apply_shared_lease_heartbeat", None)
            if callable(update):
                update(tokens, *result[:2])
            if len(result) > 2 and result[2] is not None:
                generation, snapshot = result[2]
                publish = getattr(app, "_publish_core_configuration", None)
                if callable(publish) and generation == getattr(app, "_core_configuration_generation", None):
                    publish(snapshot)
        if retry and self.running:
            self.refresh()

    def _handle_revocation(self, context, error):
        if self.running and context == self.master.session_context:
            self.master.handle_session_revoked(error.reason)
