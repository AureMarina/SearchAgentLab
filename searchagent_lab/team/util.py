from __future__ import annotations
import contextlib
import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path
from ..core import ValidationError, dumps


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def durable_write(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if exclusive:
            # Atomic create-if-absent, including between independent collectors.
            os.link(name, path)
            os.unlink(name)
        else:
            os.replace(name, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(name): os.unlink(name)


def sync_dir(path: Path) -> None:
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


@contextlib.contextmanager
def process_lock(path: Path):
    """Local-machine advisory lock. Do not place the workspace on NFS/SMB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open('a+b')
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValidationError('Workspace is in use by another team writer; use the running center upload API, or stop it before offline import') from exc
        yield
    finally:
        f.close()


def connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=20, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=20000')
    c.execute('PRAGMA synchronous=FULL')
    return c


def json_bytes(value) -> bytes:
    return (dumps(value) + '\n').encode('utf-8')
