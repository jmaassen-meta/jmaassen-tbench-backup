# dual-index-migrator - deterministic offline migration
# step 3 - file-backed contract
# step 3 - deterministic
from pathlib import Path
from .shard import ShardStore
from . import encoding

class DualIndex:
    def __init__(self, base_dir, num_shards, fmt):
        self.base_dir = Path(base_dir)
        self.num_shards = int(num_shards)
        self.fmt = fmt
        if fmt not in ("single", "dual"):
            raise ValueError("fmt must be single or dual")
        self._shard_store = ShardStore(str(self.base_dir), self.num_shards)

    def write_single(self, username, uid):
        rec = encoding.encode_single(username, uid)
        self._shard_store.put(username, rec)

    def write_dual(self, username, ig_uid, threads_uid, link_state):
        rec = encoding.encode_dual(username, ig_uid, threads_uid, link_state)
        self._shard_store.put(username, rec)

    def read(self, username):
        rec = self._shard_store.get(username)
        if rec is None:
            return None
        return encoding.decode(rec)

# step - file-backed contract - deterministic
