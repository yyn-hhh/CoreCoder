"""File-level thread locks to protect concurrent tool access.

Problem: write_file / edit_file are executed in parallel via ThreadPoolExecutor
(see agent.py::_exec_tools_parallel). Two workers hitting the same file can
cause lost updates or torn Read-Modify-Write cycles.

Solution: one threading.Lock per resolved absolute path.  A guard lock
protects creation of the per-file locks.  Also provides a ThreadSafeSet
used as the shared _changed_files registry.
"""

import threading
from collections import defaultdict

# ---------------------------------------------------------------------------

# Per-file locks

# ---------------------------------------------------------------------------

_file_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()

def get_file_lock(resolved_path: str) -> threading.Lock:
    """Return a threading.Lock for *resolved_path*, creating one if needed."""
    with _locks_guard: 
        return _file_locks[resolved_path] 

# ---------------------------------------------------------------------------

# Thread-safe set (used for _changed_files)

# ---------------------------------------------------------------------------

class ThreadSafeSet:
    """A thin wrapper around set that serialises add() / __contains__().

    Drop-in replacement for a module-level set so concurrent tool.execute()
    calls can't corrupt it.
    """

    def __init__(self):
        self._set: set[str] = set()
        self._lock = threading.Lock()

    def add(self, item: str) -> None:
        with self._lock:
            self._set.add(item)

    def __contains__(self, item: str) -> bool:
        with self._lock:
            return item in self._set

    def __iter__(self):
        with self._lock:
            return iter(list(self._set))

    def __len__(self) -> int:
        with self._lock:
            return len(self._set)

    def clear(self) -> None:
        with self._lock:
            self._set.clear()

# ---------------------------------------------------------------------------

# Shared singleton used by edit.py and write.py

# ---------------------------------------------------------------------------

_changed_files = ThreadSafeSet()
