"""Stable storage and identity primitives shared by WeiLan runtime planes."""

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def state_root():
    explicit = os.environ.get("WEILAN_METHOD_HOME")
    if explicit:
        return Path(explicit)
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "method-state"
    return Path.home() / ".weilan-method"


def canonical_workspace(value):
    expanded = os.path.expandvars(os.path.expanduser(value))
    return str(Path(expanded).resolve())


def normalized_workspace(value):
    return os.path.normcase(os.path.normpath(canonical_workspace(value)))


def workspace_key(value):
    normalized = normalized_workspace(value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def normalize_scope(value):
    scope = (value or "workspace").strip()
    if not scope:
        raise ValueError("scope cannot be empty")
    if len(scope) > 128:
        raise ValueError("scope cannot exceed 128 characters")
    return scope


def scope_key(value):
    return hashlib.sha256(normalize_scope(value).casefold().encode("utf-8")).hexdigest()[:12]


def normalize_branch(value):
    branch = (value or "main").strip()
    if not branch:
        raise ValueError("branch cannot be empty")
    if len(branch) > 128:
        raise ValueError("branch cannot exceed 128 characters")
    return branch


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_event_file_atomic(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def exclusive_file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
