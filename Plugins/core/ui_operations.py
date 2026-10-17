# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
"""Bounded GUI invalidation after the originating dialog, never modal polling.

Only replaceable view work belongs here. Commits, audits and clinical/history
mutations must finish synchronously under the actor who requested them.
"""

import logging

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication


class AfterDialogUpdates(QObject):
    def __init__(self, owner, identity):
        super().__init__(owner)
        self._identity = identity
        self._pending = {}
        self._origins = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush_ready)

    def schedule(self, key, callback):
        origin = QApplication.activeModalWidget()
        # No global modal barrier: only this request's originating dialog
        # matters. An unrelated modal opened later cannot hold it hostage.
        origin_id = id(origin) if origin is not None else None
        if origin is not None and origin_id not in self._origins:
            self._origins[origin_id] = origin
            origin.finished.connect(lambda _result, oid=origin_id: self._finish(oid))
            origin.destroyed.connect(lambda _obj=None, oid=origin_id: self._finish(oid))
        self._pending[key] = (origin_id, self._identity(), callback)
        if origin is None:
            self._timer.start(0)

    def _finish(self, origin_id):
        self._origins.pop(origin_id, None)
        # Mark ready, then publish outside finished/Save signal dispatch.
        for key, (oid, identity, callback) in list(self._pending.items()):
            if oid == origin_id:
                self._pending[key] = (None, identity, callback)
        try:
            self._timer.start(0)
        except RuntimeError:
            # A child dialog can be destroyed during its owner's teardown.
            self._pending.clear()

    def _flush_ready(self):
        for key, (origin_id, identity, callback) in list(self._pending.items()):
            if origin_id is not None:
                continue
            if self._pending.get(key) != (origin_id, identity, callback):
                # A preceding refresh can queue a newer version of this key.
                continue
            self._pending.pop(key, None)
            if identity == self._identity():
                try:
                    callback()
                except Exception:
                    logging.exception("Post-dialog view refresh failed: %s", key)

    def cancel(self):
        self._timer.stop()
        self._pending.clear()
        # Keep each existing dialog's single connection until it finishes;
        # repeated requests/cancellation must not grow signal connections.
