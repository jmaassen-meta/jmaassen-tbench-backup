import pytest
from dual_index import encoding

def test_encode_single_valid():
    rec = encoding.encode_single("alice", 1001)
    assert rec == {"username": "alice", "uid": 1001, "format": "single"}

def test_encode_single_valid_with_dot_underscore():
    rec = encoding.encode_single("alice.bob_01", 42)
    assert rec["username"] == "alice.bob_01"
    assert rec["format"] == "single"

def test_encode_single_invalid_uppercase():
    with pytest.raises(ValueError):
        encoding.encode_single("Alice", 1)

def test_encode_single_invalid_too_short():
    with pytest.raises(ValueError):
        encoding.encode_single("ab", 1)

def test_encode_single_invalid_too_long():
    with pytest.raises(ValueError):
        encoding.encode_single("a" * 31, 1)

def test_encode_single_invalid_start_digit():
    with pytest.raises(ValueError):
        encoding.encode_single("1alice", 1)

def test_encode_single_invalid_char():
    with pytest.raises(ValueError):
        encoding.encode_single("ali#ce", 1)

def test_encode_single_invalid_uid_type():
    with pytest.raises(ValueError):
        encoding.encode_single("alice", "1001")
    with pytest.raises(ValueError):
        encoding.encode_single("alice", 1001.0)

def test_encode_dual_linked_valid():
    rec = encoding.encode_dual("bob", 100, 200, "linked")
    assert rec == {"username": "bob", "ig_uid": 100, "threads_uid": 200, "link_state": "linked", "format": "dual"}

def test_encode_dual_unlinked_valid_with_none():
    rec = encoding.encode_dual("bob", 100, None, "unlinked")
    assert rec["threads_uid"] is None
    assert rec["link_state"] == "unlinked"

def test_encode_dual_invalid_link_state():
    with pytest.raises(ValueError):
        encoding.encode_dual("bob", 100, 200, "pending")
    with pytest.raises(ValueError):
        encoding.encode_dual("bob", 100, 200, "LINKED")

def test_encode_dual_linked_requires_threads_uid():
    with pytest.raises(ValueError):
        encoding.encode_dual("bob", 100, None, "linked")

def test_encode_dual_unlinked_requires_none():
    with pytest.raises(ValueError):
        encoding.encode_dual("bob", 100, 200, "unlinked")

def test_encode_dual_invalid_username():
    with pytest.raises(ValueError):
        encoding.encode_dual("Bob", 100, 200, "linked")

def test_decode_single_valid():
    rec = {"username": "alice", "uid": 1001, "format": "single"}
    out = encoding.decode(rec)
    assert out == rec

def test_decode_dual_valid():
    rec = {"username": "bob", "ig_uid": 100, "threads_uid": 200, "link_state": "linked", "format": "dual"}
    out = encoding.decode(rec)
    assert out == rec

def test_decode_single_missing_key():
    with pytest.raises(ValueError):
        encoding.decode({"username": "alice", "format": "single"})

def test_decode_dual_missing_key():
    with pytest.raises(ValueError):
        encoding.decode({"username": "bob", "ig_uid": 100, "format": "dual"})

def test_decode_unknown_format():
    with pytest.raises(ValueError):
        encoding.decode({"username": "alice", "uid": 1, "format": "unknown"})

def test_decode_invalid_types():
    with pytest.raises(ValueError):
        encoding.decode({"username": "alice", "uid": "1001", "format": "single"})

def test_decode_invalid_username():
    with pytest.raises(ValueError):
        encoding.decode({"username": "Alice", "uid": 1, "format": "single"})
