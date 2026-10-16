"""Versioned JSON + original bytes, not a database export.

Every changed run is a self-contained snapshot. The receiver deduplicates immutable
records. This release does NOT claim byte-level delta or resumable multipart upload.
"""
from __future__ import annotations
import json
import os
import platform
import re
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from .. import __version__
from ..core import ValidationError, NotFoundError, digest, dumps, now, ident, clone, Store as CoreStore
from ..inspection.repository import strict_json
from ..methods.schema import direct_refs, evidence_refs
from ..studio.sdk import StudioRecorder
from . import FORMAT
from .util import durable_write, json_bytes, sha, file_sha, connect, process_lock, sync_dir

MAX_ZIP = 128 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_MANIFEST = 4 * 1024 * 1024
MAX_RECORDS_BYTES = 96 * 1024 * 1024


def local_dir(data_dir) -> Path:
    return Path(data_dir).expanduser().resolve() / '.team'


def init_source(data_dir, operator: str, platform_name: str) -> dict:
    if not operator.strip() or not platform_name.strip():
        raise ValidationError('operator and platform must be explicit nonempty labels')
    if (local_dir(data_dir) / 'center.json').exists(): raise ValidationError('A center cannot also be an independent source workspace')
    p = local_dir(data_dir) / 'source.json'
    record = {'workspace_id': str(uuid.uuid4()), 'operator': operator,
              'platform': platform_name, 'created_at': now()}
    try: durable_write(p, json_bytes(record), exclusive=True)
    except FileExistsError:
        old = strict_json(p.read_text())
        if (old['operator'], old['platform']) != (operator, platform_name):
            raise ValidationError('Source identity already exists; do not clone .team/source.json across independent platforms')
        return old
    return record


def source(data_dir) -> dict:
    p = local_dir(data_dir) / 'source.json'
    if not p.is_file(): raise ValidationError('Run team-init first; each independent platform needs its own source identity')
    s = strict_json(p.read_text(encoding='utf-8'))
    uuid.UUID(s['workspace_id'])
    return s


def _state(data_dir):
    c = connect(local_dir(data_dir) / 'collector.sqlite3')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS revisions(run_id TEXT PRIMARY KEY, state_hash TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS artifacts(run_id TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(run_id,key));
    ''')
    return c


def add_artifact(data_dir, run_id, key, path=None, uri=None, expected_sha=None, required=False, role='evidence'):
    ident(run_id); ident(key)
    if bool(path) == bool(uri): raise ValidationError('Provide exactly one --file or --uri')
    from ..store import Store
    with Store(data_dir) as store: store.run(run_id)
    d = {'key': key, 'required': bool(required), 'role': role}
    if path:
        p = Path(path).expanduser().resolve(strict=True)
        if not p.is_file(): raise ValidationError('Artifact must be a regular file')
        if p.stat().st_size > MAX_RECORDS_BYTES: raise ValidationError('Large weights should use a persistent external URI instead of this small-result transport')
        h = file_sha(p)
        if expected_sha and expected_sha != h: raise ValidationError('Artifact SHA256 mismatch')
        d.update(source_path=str(p), sha256=h, size=p.stat().st_size, name=p.name, storage='embedded')
    else:
        from urllib.parse import urlparse
        u = urlparse(uri)
        if u.scheme not in ('https', 's3', 'gs', 'az') or not u.netloc or u.username or u.password or u.query or u.fragment:
            raise ValidationError('Use a durable https/s3/gs/az URI without credentials, query tokens or fragment; access is not verified')
        if expected_sha and not re.fullmatch(r'[a-f0-9]{64}', expected_sha): raise ValidationError('sha256 must be lowercase hex')
        d.update(uri=uri, sha256=expected_sha, storage='external_unverified')
    c = _state(data_dir)
    try:
        with c:
            old = c.execute('SELECT data FROM artifacts WHERE run_id=? AND key=?',(run_id,key)).fetchone()
            if old and json.loads(old[0]) != d: raise ValidationError('Artifact key is immutable; register a new version/key')
            c.execute('INSERT OR IGNORE INTO artifacts VALUES (?,?,?)',(run_id,key,dumps(d)))
    finally: c.close()
    return d


def _rows(c, table, where='', args=()):
    return [json.loads(r[0]) for r in c.execute('SELECT data FROM '+table+(' WHERE '+where if where else ''),args)]


def collect(data_dir, run_id: str) -> tuple[dict, dict[str, bytes]]:
    """Take a bounded, coherent snapshot; only known referenced files are collected.

    Never crawls home directories, environment variables, credentials or arbitrary
    paths found in user JSON. Extra files are explicit team-add-artifact entries.
    """
    blobs = {}
    with StudioRecorder(data_dir) as r:
        c = r.store.conn
        # ATTACH + BEGIN IMMEDIATE reserves all participating DBs during the read.
        # This briefly blocks local logging, but never waits on network while locked.
        for schema, path in [('sx',r.repo.path),('mx',r.methods.path),('ix',r.inspections.path)]:
            c.execute('ATTACH DATABASE ? AS '+schema,(str(path),))
        with r.store.transaction():
            run = r.store.run(run_id)
            project = r.store._get('projects',run['project_id'])
            experiment = r.store._get('experiments',run['experiment_id'])
            prows = _rows(c,'sx.profiles','experiment_id=?',(run['experiment_id'],))
            if not prows:
                raise ValidationError('Register an explicit main/reproduction profile before team export; unclassified runs are not guessed')
            profile = prows[0]
            cps = _rows(c,'checkpoints','run_id=?',(run_id,))
            evs = _rows(c,'evaluations','checkpoint_id IN (SELECT id FROM checkpoints WHERE run_id=?)',(run_id,))
            ev_ids = {e['id'] for e in evs}
            trs = _rows(c,'trajectories','evaluation_id IN (SELECT e.id FROM evaluations e JOIN checkpoints p ON p.id=e.checkpoint_id WHERE p.run_id=?)',(run_id,))
            batches = _rows(c,'sx.training_batches','run_id=?',(run_id,))
            harness_checks = _rows(c,'sx.harness_checks','run_id=?',(run_id,))
            methods = {o['id']:o for o in [json.loads(row[0]) for row in c.execute('SELECT payload FROM mx.objects')]}
            chosen = {o['id'] for o in methods.values() if o.get('scope',{}).get('run_id') == run_id}
            # Batch references can reach otherwise unscoped method objects.
            def walk_refs(value):
                if isinstance(value,dict):
                    if any(k in value for k in ('object_id','trajectory')) or ('file' in value and 'record' in value): yield value
                    for child in value.values(): yield from walk_refs(child)
                elif isinstance(value,list):
                    for child in value: yield from walk_refs(child)
            for ref in walk_refs(batches):
                if ref.get('object_id'): chosen.add(ref['object_id'])
            pending = list(chosen)
            while pending:
                oid = pending.pop()
                if oid not in methods: raise ValidationError('Missing referenced method object: '+oid)
                obj = methods[oid]
                sc = obj.get('scope',{})
                if sc.get('run_id') and sc['run_id'] != run_id:
                    raise ValidationError('Cross-run method dependency '+oid+': export/normalize it as an explicit shareable artifact first; no broken link is silently exported')
                refs = [x[0] for x in direct_refs(obj)] + [x['object_id'] for x in evidence_refs(obj) if x.get('object_id')]
                for target in refs:
                    if target not in chosen: chosen.add(target); pending.append(target)
            objs = [methods[k] for k in sorted(chosen)]
            file_ids = set()
            tr_ids = {t['id'] for t in trs}
            for ref in list(walk_refs(batches)) + [x for o in objs for x in evidence_refs(o)]:
                if ref.get('file'): file_ids.add(ref['file'])
                if ref.get('trajectory') and ref['trajectory'] not in tr_ids:
                    raise ValidationError('Formal trajectory dependency is outside this run; use an explicitly archived inspection-file reference')
            ins = []
            for fid in sorted(file_ids):
                row = c.execute('SELECT * FROM ix.files WHERE id=?',(fid,)).fetchone()
                if row is None: raise ValidationError('Missing referenced inspection file: '+fid)
                name = 'inspection/'+fid+'.'+row['format']
                blobs[name] = bytes(row['content'])
                ins.append({k:row[k] for k in ('id','name','format','sha256') } | {'path':name})
            raw_methods=[]
            for row in c.execute('SELECT * FROM mx.imports'):
                if chosen.intersection(json.loads(row['object_ids'])):
                    path = 'method_sources/'+row['sha256']+'.bin'
                    blobs[path] = bytes(row['content'])
                    raw_methods.append({'name':row['name'],'sha256':row['sha256'],'path':path,
                                        'object_ids':sorted(chosen.intersection(json.loads(row['object_ids']))),
                                        'note':'Original source bytes; may contain other objects from the same source file'})
            metrics=[{'run_id':run_id,'step':x['step'],'metrics':json.loads(x['data'])}
                     for x in c.execute('SELECT step,data FROM metrics WHERE run_id=? ORDER BY step',(run_id,))]
            panels=[CoreStore.panel(r.store,p) for p in sorted({e['panel_id'] for e in evs})]
            notes=_rows(c,'experiment_notes','experiment_id=?',(experiment['id'],))
            # Saved report snapshots and manual-review sidecars are not folded into
            # scores. Their existence is declared rather than silently lost.
            counts = {'saved_reports':c.execute('SELECT count(*) FROM sx.saved_reports WHERE project_id=?',(project['id'],)).fetchone()[0],
                      'manual_annotations':c.execute('SELECT count(*) FROM annotations WHERE trajectory_id IN (SELECT id FROM trajectories WHERE evaluation_id IN (SELECT e.id FROM evaluations e JOIN checkpoints p ON e.checkpoint_id=p.id WHERE p.run_id=?))',(run_id,)).fetchone()[0]}
            payload={'project':project,'experiment':experiment,'profile':profile,'run':run,
                     'panels':panels,'checkpoints':cps,'metrics':metrics,'evaluations':evs,'trajectories':trs,
                     'batches':batches,'harness_checks':harness_checks,'methods':objs,'inspection_files':ins,'method_sources':raw_methods,
                     'experiment_notes':notes,'artifacts':[],
                     'coverage':{'kind':'all_currently_recorded_records_for_run',
                        'training_rollouts':'only batches/inspection files explicitly logged and linked; not every generated rollout is implied',
                        'weights':'only explicitly registered artifacts; no implicit checkpoint directory upload',
                        'not_transferred':counts,'cross_run_shard_merge':False}}
    c = _state(data_dir)
    try: artifacts=[json.loads(x[0]) for x in c.execute('SELECT data FROM artifacts WHERE run_id=? ORDER BY key',(run_id,))]
    finally: c.close()
    for a in artifacts:
        if a['storage']=='embedded':
            p=Path(a['source_path'])
            if not p.is_file():
                a['storage']='missing';a['error']='registered source file no longer exists'
            else:
                raw=p.read_bytes()
                if len(raw)!=a['size'] or sha(raw)!=a['sha256']: raise ValidationError('Registered artifact changed: '+a['key']+'; create a new artifact version')
                name='artifacts/'+a['sha256']+'.bin';blobs[name]=raw;a['path']=name
        payload['artifacts'].append(a)
    return payload,blobs


def export_run(data_dir, run_id, out=None) -> dict:
    ident(run_id); s=source(data_dir)
    with process_lock(local_dir(data_dir)/'export.lock'):
        payload,blobs=collect(data_dir,run_id)
        blobs['records.json']=json_bytes(payload)
        descriptors=[{'path':p,'sha256':sha(b),'size':len(b)} for p,b in sorted(blobs.items())]
        state_hash=digest({'files':descriptors,'source':s})
        c=_state(data_dir)
        try:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM revisions WHERE run_id=?',(run_id,)).fetchone()
            rev=old['revision'] if old and old['state_hash']==state_hash else (old['revision']+1 if old else 1)
            created=old['created_at'] if old and old['state_hash']==state_hash else now()
            c.execute('INSERT INTO revisions VALUES (?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET state_hash=excluded.state_hash,revision=excluded.revision,created_at=excluded.created_at',(run_id,state_hash,rev,created))
            c.commit()
        except BaseException: c.rollback();raise
        finally:c.close()
        m={'format':FORMAT,'producer_version':__version__,'workspace_id':s['workspace_id'],
           'run_id':run_id,'project_id':payload['project']['id'],'experiment_id':payload['experiment']['id'],
           'track':payload['profile']['track'],'revision':rev,'created_at':created,'source':s,
           'state_sha256':state_hash,'files':descriptors}
        m['package_id']=digest(m)
        dest=Path(out).expanduser().resolve() if out else local_dir(data_dir)/'packages'/(m['package_id']+'.zip')
        dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():
            existing=read_package(dest)[0]
            if existing['package_id']!=m['package_id']:raise ValidationError('Output exists with different content')
        else:
            fd,temp=tempfile.mkstemp(prefix='.pack-',dir=dest.parent);os.close(fd)
            try:
                with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                    z.writestr('manifest.json',json_bytes(m))
                    for p,b in sorted(blobs.items()):z.writestr(p,b)
                if Path(temp).stat().st_size>MAX_ZIP or sum(len(b) for b in blobs.values())>MAX_EXPANDED:
                    raise ValidationError('Result package exceeds transport limits; use external artifacts / smaller recorded rollout samples; no records were dropped')
                with open(temp,'rb') as f:os.fsync(f.fileno())
                os.link(temp,dest);sync_dir(dest.parent)
            finally:Path(temp).unlink(missing_ok=True)
        return {'path':str(dest),'package_id':m['package_id'],'revision':rev,'run_id':run_id,
                'size':dest.stat().st_size,'state_sha256':state_hash}


def read_package(path) -> tuple[dict,dict,dict[str,bytes]]:
    path=Path(path)
    if not 0<path.stat().st_size<=MAX_ZIP:raise ValidationError('Package must be 1 byte–128 MiB')
    try:
        with zipfile.ZipFile(path) as z:
            infos=z.infolist()
            if not 2<=len(infos)<=MAX_MEMBERS:raise ValidationError('Invalid archive member count')
            names=set();total=0
            for i in infos:
                parts=PurePosixPath(i.filename).parts
                if (i.filename in names or not parts or '..' in parts or '.' in parts or
                    i.filename.startswith('/') or '\\' in i.filename or ':' in i.filename or
                    i.is_dir() or str(PurePosixPath(i.filename))!=i.filename or
                    any(ord(x)<32 for x in i.filename) or
                    stat.S_ISLNK(i.external_attr>>16) or i.flag_bits&1):
                    raise ValidationError('Unsafe, duplicate, encrypted or non-file ZIP member')
                if i.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):raise ValidationError('Unsupported ZIP compression')
                names.add(i.filename);total+=i.file_size
                if i.file_size>MAX_RECORDS_BYTES or total>MAX_EXPANDED:raise ValidationError('Expanded archive exceeds safety limits')
            if 'manifest.json' not in names or z.getinfo('manifest.json').file_size>MAX_MANIFEST:raise ValidationError('Missing/oversize manifest')
            m=strict_json(z.read('manifest.json').decode('utf-8'))
            if not isinstance(m,dict) or m.get('format')!=FORMAT:raise ValidationError('Unsupported result package format')
            mid=m.get('package_id');clean={k:v for k,v in m.items() if k!='package_id'}
            if mid!=digest(clean):raise ValidationError('Manifest content hash mismatch')
            uuid.UUID(m['workspace_id']);ident(m['run_id']);ident(m['project_id']);ident(m['experiment_id'])
            if not isinstance(m.get('source'),dict) or any(not isinstance(m['source'].get(k),str) or not m['source'][k].strip() or len(m['source'][k])>512 for k in ('workspace_id','operator','platform','created_at')):raise ValidationError('Invalid source provenance')
            if type(m.get('revision')) is not int or m['revision']<1:raise ValidationError('Invalid revision')
            if m.get('track') not in ('main','reproduction'):raise ValidationError('Explicit main/reproduction track required')
            if not isinstance(m.get('files'),list):raise ValidationError('manifest.files must be an array')
            expected={};blobs={}
            for desc in m['files']:
                if not isinstance(desc,dict) or set(desc)!={'path','size','sha256'}:raise ValidationError('Invalid file descriptor')
                p=desc['path']
                if not isinstance(p,str) or p in expected or p=='manifest.json':raise ValidationError('Duplicate file descriptor')
                if type(desc['size']) is not int or not 0<=desc['size']<=MAX_RECORDS_BYTES:raise ValidationError('Invalid declared size')
                if not isinstance(desc['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',desc['sha256']):raise ValidationError('Invalid declared SHA256')
                expected[p]=desc
            if names!=set(expected)|{'manifest.json'} or 'records.json' not in expected:raise ValidationError('Manifest file inventory is incomplete')
            for p,d in expected.items():
                if z.getinfo(p).file_size!=d['size']:raise ValidationError('ZIP member size mismatch: '+p)
                b=z.read(p)
                if len(b)!=d['size'] or sha(b)!=d['sha256']:raise ValidationError('File checksum mismatch: '+p)
                blobs[p]=b
            if m['state_sha256']!=digest({'files':m['files'],'source':m['source']}):raise ValidationError('State hash mismatch')
            payload=strict_json(blobs['records.json'].decode('utf-8'))
            if not isinstance(payload,dict):raise ValidationError('records.json must be an object')
            return m,payload,blobs
    except (zipfile.BadZipFile,UnicodeError,KeyError,RuntimeError,ValueError) as exc:
        if isinstance(exc,ValidationError):raise
        raise ValidationError('Invalid result package: '+str(exc)[:240]) from exc
