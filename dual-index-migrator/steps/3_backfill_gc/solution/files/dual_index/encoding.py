# dual-index-migrator - deterministic offline migration v4.3
# step 3 - file-backed contract
# step 3 - deterministic
import re

_USERNAME_RE = re.compile(r'^[a-z][a-z0-9_.]{2,29}$')
_ALLOWED_LINK = {"linked", "unlinked"}

def _validate_username(username):
    if not isinstance(username, str):
        raise ValueError("username must be str")
    if not _USERNAME_RE.match(username):
        raise ValueError(f"invalid username: {username}")

def encode_single(username, uid):
    _validate_username(username)
    if not isinstance(uid, int) or isinstance(uid, bool):
        raise ValueError("uid must be int")
    return {"username": username, "uid": uid, "format": "single"}

def encode_dual(username, ig_uid, threads_uid, link_state):
    _validate_username(username)
    if not isinstance(ig_uid, int) or isinstance(ig_uid, bool):
        raise ValueError("ig_uid must be int")
    if link_state not in _ALLOWED_LINK:
        raise ValueError(f"invalid link_state: {link_state}")
    if link_state == "linked":
        if not isinstance(threads_uid, int) or isinstance(threads_uid, bool):
            raise ValueError("threads_uid must be int when linked")
    else:  # unlinked
        if threads_uid is not None:
            raise ValueError("threads_uid must be None when unlinked")
    return {
        "username": username,
        "ig_uid": ig_uid,
        "threads_uid": threads_uid,
        "link_state": link_state,
        "format": "dual",
    }

def decode(record):
    if not isinstance(record, dict):
        raise ValueError("record must be dict")
    fmt = record.get("format")
    if fmt == "single":
        required = {"username", "uid", "format"}
        if not required.issubset(record.keys()):
            raise ValueError("missing keys for single")
        username = record["username"]
        uid = record["uid"]
        _validate_username(username)
        if not isinstance(uid, int) or isinstance(uid, bool):
            raise ValueError("uid must be int")
        return {"username": username, "uid": uid, "format": "single"}
    elif fmt == "dual":
        required = {"username", "ig_uid", "threads_uid", "link_state", "format"}
        if not required.issubset(record.keys()):
            raise ValueError("missing keys for dual")
        username = record["username"]
        ig_uid = record["ig_uid"]
        threads_uid = record["threads_uid"]
        link_state = record["link_state"]
        _validate_username(username)
        if not isinstance(ig_uid, int) or isinstance(ig_uid, bool):
            raise ValueError("ig_uid must be int")
        if link_state not in _ALLOWED_LINK:
            raise ValueError("invalid link_state")
        if link_state == "linked":
            if not isinstance(threads_uid, int) or isinstance(threads_uid, bool):
                raise ValueError("threads_uid must be int when linked")
        else:
            if threads_uid is not None:
                raise ValueError("threads_uid must be None when unlinked")
        return {
            "username": username,
            "ig_uid": ig_uid,
            "threads_uid": threads_uid,
            "link_state": link_state,
            "format": "dual",
        }
    else:
        raise ValueError(f"unknown format: {fmt}")

# step - file-backed contract - deterministic
