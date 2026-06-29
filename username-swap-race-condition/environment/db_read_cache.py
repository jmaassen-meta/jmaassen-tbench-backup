"""
Read Cache simulating distributed read shards.

Each client gets its own ReadCache instance per table.
Cache updates periodically from global state, simulating eventual consistency.
Tests can disable auto updates and manually control cache refresh order.
"""

import time
import threading
from typing import Dict, Optional, Any

from db.tables import (
    username_index_table,
    username_hold_table,
    user_blob_table,
)


class ReadCache:
    """Per-client per-table read cache."""
    
    # Global flag to disable auto updates for deterministic testing
    _auto_update_enabled = True
    
    def __init__(self, table_name: str, update_interval: float = 0.5):
        self.table_name = table_name
        self.update_interval = update_interval
        self._cache: Dict[Any, Any] = {}
        self._last_update = 0.0
        self._lock = threading.Lock()
        self._update_from_global()
    
    def _update_from_global(self):
        """Update local cache from global tables. Assumes lock is held."""
        if self.table_name == "username_index":
            self._cache = username_index_table.get_all()
        elif self.table_name == "username_hold":
            self._cache = username_hold_table.get_all()
        elif self.table_name == "user_blob":
            self._cache = user_blob_table.get_all()
        self._last_update = time.time()
    
    def get(self, key: Any) -> Optional[Any]:
        """Get value by key from cache (may be stale)."""
        with self._lock:
            # Update if auto updates enabled and stale, or if key not found
            if (ReadCache._auto_update_enabled and 
                time.time() - self._last_update > self.update_interval) or key not in self._cache:
                self._update_from_global()
            return self._cache.get(key)
    
    def get_all(self) -> Dict[Any, Any]:
        """Get all cached entries."""
        with self._lock:
            if (ReadCache._auto_update_enabled and 
                time.time() - self._last_update > self.update_interval):
                self._update_from_global()
            return dict(self._cache)
    
    @classmethod
    def disable_auto_update(cls):
        """Disable automatic cache updates. For deterministic testing."""
        cls._auto_update_enabled = False
    
    @classmethod
    def enable_auto_update(cls):
        """Enable automatic cache updates."""
        cls._auto_update_enabled = True


_thread_local = threading.local()

def get_client_cache(table_name: str, update_interval: float = 0.5) -> ReadCache:
    """Get or create a per-client per-table read cache."""
    if not hasattr(_thread_local, "caches"):
        _thread_local.caches = {}
    cache_key = f"{table_name}:{update_interval}"
    if cache_key not in _thread_local.caches:
        _thread_local.caches[cache_key] = ReadCache(table_name, update_interval)
    return _thread_local.caches[cache_key]
