import hashlib
import json
import re
from pathlib import Path

_USERNAME_RE = re.compile(r'^[a-z][a-z0-9_.]{2,29}$')

class UserStore:
    def __init__(self, base_dir, num_shards):
        self.base_dir = Path(base_dir) / "users"
        self.num_shards = int(num_shards)
        if self.num_shards <= 0:
            raise ValueError("num_shards must be >0")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _shard_path(self, uid):
        # uid is int, hash it as string
        h = int(hashlib.md5(str(uid).encode("utf-8")).hexdigest(), 16)
        idx = h % self.num_shards
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

    def _validate_blob(self, uid, blob):
        if not isinstance(blob, dict):
            raise ValueError("blob must be dict")
        if "username" not in blob or "uid" not in blob or "universe" not in blob:
            raise ValueError("blob missing required keys")
        username = blob["username"]
        buid = blob["uid"]
        universe = blob["universe"]
        if not isinstance(uid, int) or isinstance(uid, bool):
            raise ValueError("uid must be int")
        if not isinstance(buid, int) or isinstance(buid, bool):
            raise ValueError("blob uid must be int")
        if buid != uid:
            raise ValueError("blob uid must equal key uid")
        if not isinstance(username, str) or not _USERNAME_RE.match(username):
            raise ValueError(f"invalid username in blob: {username}")
        if universe not in ("ig", "threads"):
            raise ValueError(f"invalid universe: {universe}")

    def put(self, uid, blob):
        self._validate_blob(uid, blob)
        path = self._shard_path(uid)
        data = self._load_shard(path)
        key = str(uid)
        # check immutability: if exists, universe must match
        if key in data:
            old = data[key]
            if old.get("universe") != blob.get("universe"):
                raise ValueError(f"universe is immutable for uid {uid}: {old.get('universe')} != {blob.get('universe')}")
        data[key] = blob
        self._save_shard(path, data)

    def get(self, uid):
        if not isinstance(uid, int) or isinstance(uid, bool):
            raise ValueError("uid must be int")
        path = self._shard_path(uid)
        data = self._load_shard(path)
        return data.get(str(uid))

    def shards(self):
        return self.num_shards
# step3 user_store — immutable universe is expected to be
