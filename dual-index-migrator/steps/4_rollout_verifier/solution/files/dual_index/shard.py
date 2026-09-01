# dual-index-migrator - deterministic offline migration
# step 4 - file-backed contract
# step 4 - deterministic
import hashlib
import json
from pathlib import Path

class ShardStore:
    def __init__(self, base_dir, num_shards):
        self.base_dir = Path(base_dir)
        self.num_shards = int(num_shards)
        if self.num_shards <= 0:
            raise ValueError("num_shards must be >0")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _shard_index(self, username):
        # stable hash via md5
        h = int(hashlib.md5(username.encode("utf-8")).hexdigest(), 16)
        return h % self.num_shards

    def _shard_path(self, username):
        idx = self._shard_index(username)
        return self.base_dir / f"shard_{idx}.json"

    def _load_shard(self, path):
        if not path.exists():
            return {}
        try:
            with open(path, "r") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return {}
                return data
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_shard(self, path, data):
        with open(path, "w") as f:
            json.dump(data, f, sort_keys=True)

    def put(self, username, record):
        if not isinstance(username, str):
            raise ValueError("username must be str")
        if not isinstance(record, dict):
            raise ValueError("record must be dict")
        path = self._shard_path(username)
        data = self._load_shard(path)
        data[username] = record
        self._save_shard(path, data)

    def get(self, username):
        if not isinstance(username, str):
            raise ValueError("username must be str")
        path = self._shard_path(username)
        data = self._load_shard(path)
        return data.get(username)

    def shards(self):
        return self.num_shards
