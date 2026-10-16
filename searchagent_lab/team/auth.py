"""Hashed, revocable capability tokens. Browser sessions remain server-side."""
from __future__ import annotations
import json
import secrets
import time
import uuid
from pathlib import Path
from ..core import ValidationError, dumps, now, ident
from .util import connect, sha, durable_write


class Auth:
    def __init__(self, data_dir):
        self.path=Path(data_dir).expanduser().resolve()/'.team'/'auth.sqlite3'
        c=connect(self.path)
        try:c.executescript('''CREATE TABLE IF NOT EXISTS tokens(
            id TEXT PRIMARY KEY, digest TEXT NOT NULL, owner TEXT NOT NULL,
            role TEXT NOT NULL, experiments TEXT NOT NULL, created_at TEXT NOT NULL,
            expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);''')
        finally:c.close()
        try:self.path.chmod(0o600)
        except OSError:pass

    def issue(self, owner, role, experiments=(), days=90):
        if role not in ('admin','viewer','uploader'):raise ValidationError('role must be admin/viewer/uploader')
        if not isinstance(owner,str) or not owner.strip() or len(owner)>180:raise ValidationError('Explicit owner label required')
        for e in experiments:ident(e)
        if role=='uploader' and not experiments:raise ValidationError('Upload credentials require at least one assigned experiment')
        if not 1<=days<=3650:raise ValidationError('Token expiry must be 1–3650 days')
        tid='key-'+uuid.uuid4().hex
        token=tid+'.'+secrets.token_urlsafe(32)
        c=connect(self.path)
        try:c.execute('INSERT INTO tokens VALUES (?,?,?,?,?,?,?,0)',(tid,sha(token.encode()),owner,role,dumps(sorted(set(experiments))),now(),time.time()+days*86400))
        finally:c.close()
        return token,{'id':tid,'owner':owner,'role':role,'experiments':list(experiments),'expires_in_days':days}

    def authenticate(self, token):
        if not isinstance(token,str) or not 40<=len(token)<=200:return None
        tid=token.split('.',1)[0]
        c=connect(self.path)
        try:r=c.execute('SELECT * FROM tokens WHERE id=?',(tid,)).fetchone()
        finally:c.close()
        if not r or r['revoked'] or r['expires_at']<=time.time() or not secrets.compare_digest(r['digest'],sha(token.encode())):return None
        return {k:r[k] for k in ('id','owner','role','expires_at')}|{'experiments':json.loads(r['experiments'])}

    def active(self, tid):
        c=connect(self.path)
        try:r=c.execute('SELECT * FROM tokens WHERE id=?',(tid,)).fetchone()
        finally:c.close()
        if not r or r['revoked'] or r['expires_at']<=time.time():return None
        return {k:r[k] for k in ('id','owner','role','expires_at')}|{'experiments':json.loads(r['experiments'])}

    def revoke(self, tid):
        c=connect(self.path)
        try:
            n=c.execute('UPDATE tokens SET revoked=1 WHERE id=?',(tid,)).rowcount
            if not n:raise ValidationError('Unknown token ID')
        finally:c.close()

    def list(self):
        c=connect(self.path)
        try:return [dict(r)|{'experiments':json.loads(r['experiments'])} for r in c.execute('SELECT id,owner,role,experiments,created_at,expires_at,revoked FROM tokens ORDER BY created_at')]
        finally:c.close()
