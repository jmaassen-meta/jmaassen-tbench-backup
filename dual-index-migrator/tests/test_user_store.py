import tempfile
import pytest
from dual_index.user_store import UserStore

def test_put_get_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        s = UserStore(tmp, 4)
        s.put(100, {"username": "alice", "uid": 100, "universe": "ig"})
        assert s.get(100) == {"username": "alice", "uid": 100, "universe": "ig"}

def test_put_invalid_universe():
    with tempfile.TemporaryDirectory() as tmp:
        s = UserStore(tmp, 4)
        with pytest.raises(ValueError):
            s.put(100, {"username": "alice", "uid": 100, "universe": "bad"})

def test_put_universe_immutable():
    with tempfile.TemporaryDirectory() as tmp:
        s = UserStore(tmp, 4)
        s.put(100, {"username": "alice", "uid": 100, "universe": "ig"})
        with pytest.raises(ValueError):
            s.put(100, {"username": "alice", "uid": 100, "universe": "threads"})
        # original still ig
        assert s.get(100)["universe"] == "ig"

def test_put_uid_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        s = UserStore(tmp, 4)
        with pytest.raises(ValueError):
            s.put(100, {"username": "alice", "uid": 101, "universe": "ig"})

def test_put_invalid_username():
    with tempfile.TemporaryDirectory() as tmp:
        s = UserStore(tmp, 4)
        with pytest.raises(ValueError):
            s.put(100, {"username": "Alice", "uid": 100, "universe": "ig"})

def test_get_absent_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        s = UserStore(tmp, 4)
        assert s.get(999) is None

def test_shards():
    with tempfile.TemporaryDirectory() as tmp:
        s = UserStore(tmp, 8)
        assert s.shards() == 8
