"""Short-TTL registry of proposed (confirm-required) actions.

Populated by the tool loop when an action needs confirmation; drained by the
/api/assistant/confirm route. Ids are unguessable (uuid4) and single-use.
"""
import threading
import time
import uuid


class PendingRegistry:
    def __init__(self, ttl=120):
        self._ttl = ttl
        self._items = {}
        self._lock = threading.Lock()

    def add(self, name, args):
        pid = uuid.uuid4().hex
        with self._lock:
            self._items[pid] = (name, args, time.time() + self._ttl)
        return pid

    def take(self, pid):
        with self._lock:
            item = self._items.pop(pid, None)
        if item is None:
            return None
        name, args, expires = item
        if time.time() > expires:
            return None
        return name, args
