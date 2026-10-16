"""Immutable method evidence in a separate local sidecar.

No training, model calls, skill retrieval or reward recomputation. Imports are
atomic; relationships must be explicit; a lineage edge is not causal evidence.
"""
from __future__ import annotations
import copy
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from ..core import ValidationError, NotFoundError
from ..read_cache import cached_read
from ..inspection.repository import encode, digest, now, strict_json, MAX_FILE_BYTES
from ..inspection.adapters import normalize_record, resolve, text
from ..inspection.diagnostics import diagnose
from .schema import SCHEMA, MAX_OBJECTS, validate_object, direct_refs, evidence_refs, identifier
from .skills import looks_like_bank, adapt_bank, member, compare_banks

class MethodRepository:
    def __init__(self,data_dir,store=None,inspections=None):
        self.path=Path(data_dir).resolve()/'method_evidence.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.store=store;self.inspections=inspections;self.lock=threading.RLock()
        self.conn=sqlite3.connect(self.path,timeout=20,check_same_thread=False,isolation_level=None)
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS objects(
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT NOT NULL,
          experiment_id TEXT NOT NULL, run_id TEXT NOT NULL, hash TEXT NOT NULL,
          content_hash TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS objects_scope ON objects(project_id,experiment_id,run_id,kind);
        CREATE TABLE IF NOT EXISTS imports(
          id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,
          scope TEXT NOT NULL, object_ids TEXT NOT NULL, content BLOB NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews(
          id INTEGER PRIMARY KEY AUTOINCREMENT, object_id TEXT NOT NULL,
          source_hash TEXT NOT NULL, pointer TEXT NOT NULL, status TEXT NOT NULL,
          note TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL);
        ''')
        from .indexes import migrate
        migrate(self)
    def close(self):
        with self.lock:self.conn.close()
    def __enter__(self):return self
    def __exit__(self,*a):self.close()
    def _scope(self,scope):
        if not isinstance(scope,dict):raise ValidationError('scope must be an object')
        scope={k:v for k,v in scope.items() if v is not None and v!=''}
        if set(scope)-{'project_id','experiment_id','run_id'}:raise ValidationError('Unknown scope field')
        for k,v in scope.items():identifier(v,k)
        if not scope:return {}
        if self.store is None:raise ValidationError('Scoped import requires an existing research Store')
        if scope.get('run_id'):
            run=self.store.run(scope['run_id'])
            for k in ('project_id','experiment_id'):
                if scope.get(k) and scope[k]!=run[k]:raise ValidationError('Run does not belong to selected '+k)
                scope[k]=run[k]
        if scope.get('experiment_id'):
            ex=self.store.experiment(scope['experiment_id'])
            if scope.get('project_id') and scope['project_id']!=ex['project_id']:raise ValidationError('Experiment outside selected project')
            scope['project_id']=ex['project_id']
        if scope.get('project_id') and scope['project_id'] not in {p['id'] for p in self.store.projects()}:
            raise ValidationError('Research project is not registered')
        return scope
    def object(self,id):
        with self.lock:r=self.conn.execute('SELECT * FROM objects WHERE id=?',(id,)).fetchone()
        if r is None:raise NotFoundError('Method object not found: '+str(id)[:180])
        o=json.loads(r['payload'])
        return {**o,'hash':r['hash'],'content_hash':r['content_hash'],'imported_at':r['created_at']}
    def _raw_object(self,id,pending=None):
        if pending is not None and id in pending:return pending[id]
        o=self.object(id)
        return {k:v for k,v in o.items() if k not in ('hash','content_hash','imported_at')}
    @staticmethod
    def _scope_compatible(a,b):
        ap=a.get('scope',{}).get('project_id');bp=b.get('scope',{}).get('project_id')
        if ap and bp and ap!=bp and b.get('shareable') is not True:
            raise ValidationError('Cross-project reference requires explicitly shareable source artifact')
    @staticmethod
    def _same_scope(a,b):
        """An evidence session belongs to one run, including the unbound case."""
        return all(a.get('scope',{}).get(k,'')==b.get('scope',{}).get(k,'')
                   for k in ('project_id','experiment_id','run_id'))
    def resolve_ref(self,ref,pending=None,owner=None):
        if not isinstance(ref,dict):raise ValidationError('Evidence reference must be an object')
        choices=sum(k in ref for k in ('object_id','file','trajectory'))
        if choices!=1:raise ValidationError('Evidence reference needs exactly one object_id, file+record, or trajectory')
        p=ref.get('pointer','')
        if not isinstance(p,str):raise ValidationError('pointer must be string')
        if 'object_id' in ref:
            obj=self._raw_object(ref['object_id'],pending)
            if owner:self._scope_compatible(owner,obj)
            raw=obj['data'];h=digest(raw);target='method:'+obj['id']
        elif 'file' in ref:
            if self.inspections is None:raise ValidationError('Inspection repository required for file reference')
            if type(ref.get('record')) is not int:raise ValidationError('record must be integer')
            doc=self.inspections.record(ref['file'],ref['record']);raw=doc['raw'];h=doc['source_hash'];target=doc['target']
        else:
            if self.store is None or self.inspections is None:raise ValidationError('Formal trajectory reference requires store')
            t=self.store.trajectory(ref['trajectory']);doc=self.inspections.evaluation_record(t)
            raw=doc['raw'];h=doc['source_hash'];target=doc['target']
            if owner and owner.get('scope',{}).get('project_id'):
                ev=self.store.evaluation(t['evaluation_id'])
                if owner['scope']['project_id']!=ev['project_id']:
                    raise ValidationError('Formal trajectory belongs to another project')
        if ref.get('sha256') is not None and ref['sha256']!=h:raise ValidationError('Evidence hash mismatch')
        try:value=resolve(raw,p)
        except (KeyError,IndexError,ValueError,TypeError) as e:raise ValidationError('Evidence pointer not found: '+p[:200]) from e
        return {'ref':{**ref,'pointer':p,'sha256':h},'target':target,'value':value,'verified':True}
    def _validate_links(self,o,pending):
        for id,kinds,path in direct_refs(o):
            target=self._raw_object(identifier(id,'reference'),pending);self._scope_compatible(o,target)
            if kinds and target['kind'] not in kinds:raise ValidationError(path+' references wrong object kind')
        for ref in evidence_refs(o):self.resolve_ref(ref,pending,o)
        from .skill_tracking import validate_skill_links
        validate_skill_links(self,o,pending)
        from .opd_tracking import validate_opd_links
        validate_opd_links(self,o,pending)
        from .entropy_tracking import validate_entropy_links
        validate_entropy_links(self,o,pending)
        from .selfplay_tracking import validate_selfplay_links
        validate_selfplay_links(self,o,pending)
        d=o['data'];k=o['kind']
        if k in ('proposal','solver_attempt'):
            cycle=self._raw_object(d['cycle_id'],pending)
            ids={p['id'] for p in cycle['data'].get('participants',[])}
            for key in ('actor_id','solver_actor_id'):
                if key in d and d[key] not in ids:raise ValidationError(key+' missing from cycle participants')
        if k=='question_review' and d.get('supersedes'):
            previous=self._raw_object(d['supersedes'],pending)
            if previous['data']['question_id']!=d['question_id']:raise ValidationError('Cannot supersede another question review')
            visited={o['id']}
            while previous:
                if previous['id'] in visited:raise ValidationError('Cyclic review supersedes chain')
                visited.add(previous['id'])
                parent=previous['data'].get('supersedes')
                previous=self._raw_object(parent,pending) if parent else None
        if k in ('opd_teacher','opd_supervision'):
            failure=self._raw_object(d['failure_id'],pending)
            if not self._same_scope(o,failure):
                raise ValidationError('OPD failure and its teacher/supervision must belong to the same run scope')
            if k=='opd_teacher':
                if d.get('mode')=='continuation':
                    budget=failure['data'].get('attempt_budget')
                    if budget is not None and d['attempt_index']>budget:
                        raise ValidationError('Teacher attempt_index exceeds the recorded failure attempt_budget')
                    try:existing=self.related(d['failure_id'])
                    except NotFoundError:existing=[]
                    existing += list(pending.values())
                    for other in existing:
                        od=other.get('data',{})
                        if other['id']!=o['id'] and other['kind']=='opd_teacher' and od.get('failure_id')==d['failure_id'] and od.get('mode')=='continuation' and od.get('attempt_index')==d['attempt_index']:
                            raise ValidationError('Duplicate continuation attempt_index for one failure state')
                if d.get('verification',{}).get('status')=='passed' and d.get('status')!='completed':
                    raise ValidationError('A passed teacher verification requires completed execution')
            else:
                for tid in d['teacher_ids']:
                    teacher=self._raw_object(tid,pending)
                    if not self._same_scope(o,teacher) or teacher['data']['failure_id']!=d['failure_id']:
                        raise ValidationError('Supervision teacher must refer to this failure in the same run')
        if k=='skill_archive':
            previous=None
            for i,(bid,snapshot) in enumerate(zip(d['bank_ids'],d['snapshots'])):
                bank=self._raw_object(bid,pending)['data'];view=adapt_bank(bank)
                expected={g:sum(m['group']==g for m in view['members']) for g in ('general_skills','query_type_skills','task_specific_skills','common_mistakes')}
                expected.update(skill_count=view['skill_count'],mistake_count=view['mistake_count'])
                if snapshot.get('counts')!=expected:raise ValidationError('Archive snapshot counts differ from bank contents')
                if snapshot.get('content_hash')!=digest(bank):raise ValidationError('Archive snapshot content hash mismatch')
                if snapshot['phase']=='training' and previous is not None:
                    diff=compare_banks(previous,bank)
                    if snapshot.get('changes')!=diff.get('counts'):raise ValidationError('Archive changes differ from actual saved snapshots')
                if snapshot['phase']=='evaluation':
                    match=snapshot.get('byte_identical_training_index')
                    if match is not None:
                        if type(match) is not int or not 0<=match<i:raise ValidationError('Invalid frozen-bank training match')
                        other=d['snapshots'][match]
                        if other['phase']=='evaluation' or other.get('content_hash')!=snapshot['content_hash'] or other.get('file_sha256')!=snapshot.get('file_sha256'):
                            raise ValidationError('Frozen-bank equality metadata mismatch')
                else:previous=bank
        if k=='skill_retrieval':
            bank=self._raw_object(d['bank_id'],pending)['data']
            from .skill_tracking import bank_member
            for v in d.get('candidates',[])+d.get('selected',[])+d.get('injections',[]):
                key=v if isinstance(v,str) else v.get('member') if isinstance(v,dict) else None
                m=bank_member(self,bank,key,pending)
                if isinstance(v,dict) and v.get('content_hash') and v['content_hash']!=m['content_hash']:
                    raise ValidationError('Skill member content hash does not match frozen bank')
        if k=='evaluation_binding':
            if self.store is None:raise ValidationError('evaluation_binding requires registered evaluation')
            ev=self.store.evaluation(d['evaluation_id'])
            scope=o.get('scope',{})
            if not scope.get('run_id'):raise ValidationError('evaluation_binding requires explicit run scope')
            for field,expected in [('project_id',ev['project_id']),('experiment_id',ev['experiment_id']),('run_id',ev['checkpoint']['run_id'])]:
                if scope.get(field) and scope[field]!=expected:raise ValidationError('Evaluation outside selected '+field)
            synthetic_bank=bool(d.get('bank_id') and self._raw_object(d['bank_id'],pending).get('demo'))
            if (o.get('demo',False) or synthetic_bank) and not (ev['run'].get('demo') or ev['panel'].get('demo')):
                raise ValidationError('Synthetic method binding cannot attach to real evaluation')
            current=[x for x in self.objects(kind='evaluation_binding') if x['data']['evaluation_id']==d['evaluation_id'] and x['id']!=o['id']]
            current += [x for x in pending.values() if x['kind']=='evaluation_binding' and x['data']['evaluation_id']==d['evaluation_id'] and x['id']!=o['id']]
            if current:raise ValidationError('Evaluation already has a frozen method-state binding; use a distinct reevaluation ID')
        if k=='training_update' and d.get('output_checkpoint_id') and self.store:
            try:
                cp=self.store._get('checkpoints',d['output_checkpoint_id'])
                if o.get('scope',{}).get('run_id') and cp['run_id']!=o['scope']['run_id']:
                    raise ValidationError('Output checkpoint outside training run')
            except NotFoundError:
                # Require accurate absence, not a fabricated checkpoint.
                if d.get('checkpoint_ref_status')!='external_unverified':
                    raise ValidationError('Checkpoint not registered; use checkpoint_ref_status=external_unverified for a metadata-only reference')
    def import_bundle(self,bundle,raw=None,name='method_bundle.json',scope=None):
        if not isinstance(bundle,dict) or bundle.get('method_schema')!=SCHEMA:
            raise ValidationError('Expected method_schema='+SCHEMA)
        if set(bundle)-{'method_schema','scope','demo','objects','description'}:
            raise ValidationError('Unknown method bundle top-level fields')
        rows=bundle.get('objects')
        if not isinstance(rows,list) or not 1<=len(rows)<=MAX_OBJECTS:raise ValidationError('objects must have 1–10,000 entries')
        declared_scope=self._scope(bundle.get('scope',{}))
        supplied_scope=self._scope(scope or {})
        for key,value in supplied_scope.items():
            if key in declared_scope and declared_scope[key]!=value:
                raise ValidationError('Declared bundle scope conflicts with selected import scope')
        default_scope=self._scope({**declared_scope,**supplied_scope})
        pending={}
        for obj in rows:
            if not isinstance(obj,dict):raise ValidationError('Each object must be an object')
            o=copy.deepcopy(obj)
            local=self._scope(o.get('scope',default_scope))
            for key,value in default_scope.items():
                if local.get(key)!=value:raise ValidationError('Object scope conflicts with explicit import scope')
            o['scope']=local
            if 'demo' not in o and 'demo' in bundle:o['demo']=bundle['demo']
            validate_object(o)
            if o['id'] in pending:raise ValidationError('Duplicate method object ID in bundle')
            pending[o['id']]=o
        def cycle_check(id,stack,done):
            if id in stack:raise ValidationError('Cyclic derived_from lineage')
            if id in done:return
            if len(stack)>120:raise ValidationError('Lineage depth exceeds 120')
            o=self._raw_object(id,pending)
            for parent in o.get('derived_from',[]):cycle_check(parent,stack|{id},done)
            done.add(id)
        content=raw if raw is not None else encode(bundle).encode()
        if not isinstance(content,bytes) or len(content)>MAX_FILE_BYTES:raise ValidationError('File exceeds 50 MiB')
        sh=hashlib.sha256(content).hexdigest();import_id='imp-'+digest([sh,default_scope,sorted(pending)])[:24]
        label=Path(str(name).replace('\\','/')).name[:180]
        if not label or any(ord(c)<32 for c in label):raise ValidationError('Invalid source filename')
        with self.lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                done=set()
                for o in pending.values():
                    self._validate_links(o,pending);cycle_check(o['id'],set(),done)
                    r=self.conn.execute('SELECT hash FROM objects WHERE id=?',(o['id'],)).fetchone()
                    if r and r['hash']!=digest(o):raise ValidationError('Immutable method object ID already has different content: '+o['id'])
                for o in pending.values():
                    sc=o['scope'];self.conn.execute('INSERT OR IGNORE INTO objects VALUES (?,?,?,?,?,?,?,?,?)',
                        (o['id'],o['kind'],sc.get('project_id',''),sc.get('experiment_id',''),sc.get('run_id',''),digest(o),digest(o['data']),encode(o),now()))
                from .indexes import index_object
                for o in pending.values():
                    row=self.conn.execute('SELECT hash,content_hash,created_at FROM objects WHERE id=?',(o['id'],)).fetchone()
                    index_object(self,o,row)
                self.conn.executemany('INSERT OR IGNORE INTO method_import_objects VALUES (?,?)',[(import_id,oid) for oid in pending])
                self.conn.execute('INSERT OR IGNORE INTO imports VALUES (?,?,?,?,?,?,?)',
                    (import_id,label,sh,encode(default_scope),encode(list(pending)),content,now()))
                self.conn.commit()
            except BaseException:self.conn.rollback();raise
        return {'import_id':import_id,'sha256':sh,'object_ids':list(pending),'scope':default_scope,
            'count':len(pending),'no_training_or_evaluation_executed':True}
    def import_bytes(self,raw,name,scope=None,snapshot_id=None):
        if str(name).lower().endswith('.zip'):
            if snapshot_id:raise ValidationError('ZIP archive identity is content-addressed; do not supply snapshot_id')
            from .skill_archive import import_archive
            return import_archive(self,raw,name,scope)
        if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_FILE_BYTES:raise ValidationError('Expected 1 byte–50 MiB')
        try:s=raw.decode('utf-8-sig')
        except UnicodeDecodeError as e:raise ValidationError('UTF-8 JSON/JSONL required') from e
        if str(name).lower().endswith(('.jsonl','.ndjson')):
            objs=[];defs=[]
            for line,record in enumerate(s.splitlines(),1):
                if not record.strip():continue
                try:e=strict_json(record)
                except (ValueError,RecursionError) as exc:raise ValidationError('Method JSONL line '+str(line)+': '+str(exc)[:250]) from exc
                if not isinstance(e,dict) or e.get('method_schema')!=SCHEMA or not isinstance(e.get('object'),dict):
                    raise ValidationError('Method JSONL line '+str(line)+' requires method_schema and object. Plain traces belong in the trajectory inspector.')
                if set(e)-{'method_schema','object'}:raise ValidationError('Unexpected method JSONL fields')
                objs.append(e['object'])
            return self.import_bundle({'method_schema':SCHEMA,'objects':objs},raw,name,scope)
        try:obj=strict_json(s)
        except (ValueError,RecursionError) as e:raise ValidationError('Invalid method JSON: '+str(e)[:300]) from e
        if looks_like_bank(obj):
            sc=self._scope(scope or {});auto='bank-'+digest([digest(obj),sc])[:28]
            oid=snapshot_id or auto;identifier(oid)
            result=self.import_bundle({'method_schema':SCHEMA,'scope':sc,'objects':[{'id':oid,'kind':'skill_bank','title':Path(name).name,'version':oid,
                'data':obj,'usage':'unknown','demo':isinstance(obj.get('metadata'),dict) and obj['metadata'].get('demo') is True}]},raw,name,sc)
            result.update({'kind':'skill_bank','snapshot_id':oid,'content_hash':digest(obj),'skills':adapt_bank(obj)['skill_count']})
            return result
        return self.import_bundle(obj,raw,name,scope)
    @staticmethod
    def _where(scope=None,kind='',prefix=''):
        where=[];args=[]
        for key in ('project_id','experiment_id','run_id'):
            if key in (scope or {}):where.append(prefix+key+'=?');args.append(scope[key] or '')
        if kind:where.append(prefix+'kind=?');args.append(kind)
        return (' WHERE '+' AND '.join(where) if where else ''),args
    def objects(self,scope=None,kind='',query=''):
        where,args=self._where(scope,kind)
        with self.lock:rows=self.conn.execute('SELECT payload,hash,content_hash,created_at FROM objects'+where+' ORDER BY rowid',args).fetchall()
        items=[{**json.loads(r['payload']),'hash':r['hash'],'content_hash':r['content_hash'],'imported_at':r['created_at']} for r in rows]
        if query:
            if len(query)>500:raise ValidationError('Query exceeds 500 characters')
            items=[o for o in items if query.casefold() in encode(o).casefold()]
        return items
    def catalog(self,scope=None,kind='',offset=0,limit=None):
        selected={'project_id':''} if scope is None else scope
        where,args=self._where(selected,kind,'o.')
        if type(offset) is not int or offset<0:raise ValidationError('offset must be nonnegative integer')
        if limit is not None and (type(limit) is not int or not 1<=limit<=200):raise ValidationError('limit must be 1–200')
        with self.lock:
            counts={r[0]:r[1] for r in self.conn.execute('SELECT o.kind,count(*) FROM objects o'+where+' GROUP BY o.kind',args)}
            sql='SELECT s.summary FROM objects o JOIN method_summaries s ON s.id=o.id'+where+' ORDER BY o.rowid'
            rows=self.conn.execute(sql+(' LIMIT ? OFFSET ?' if limit is not None else ''),args+([limit,offset] if limit is not None else [])).fetchall()
            all_count=self.conn.execute('SELECT count(*) FROM objects').fetchone()[0]
        summaries=[json.loads(r[0]) for r in rows]
        return {'scope':selected,'objects':summaries,'counts':counts,'all_scope_count':all_count,
                'total':sum(counts.values()),'offset':offset,'limit':limit,
                'has_more':limit is not None and offset+len(rows)<sum(counts.values()),'no_causal_claim':True}
    def export(self,id):
        self.object(id);ids=set()
        def visit(x):
            if x in ids:return
            if len(ids)>=MAX_OBJECTS:raise ValidationError('Export too large')
            ids.add(x);o=self._raw_object(x)
            for target,_,_ in direct_refs(o):visit(target)
            for ref in evidence_refs(o):
                if 'object_id' in ref:visit(ref['object_id'])
        visit(id)
        objs=[self._raw_object(x) for x in sorted(ids)]
        return {'method_schema':SCHEMA,'objects':objs,'description':'Immutable evidence export. External file/trajectory refs require the original workspace; no remote artifacts copied.'}
    def original(self,import_id):
        with self.lock:r=self.conn.execute('SELECT * FROM imports WHERE id=?',(import_id,)).fetchone()
        if not r:raise NotFoundError('Method import not found')
        return dict(r)
    def imports_for(self,id):
        with self.lock:rs=self.conn.execute('SELECT i.id,i.name,i.sha256,i.created_at FROM imports i JOIN method_import_objects x ON x.import_id=i.id WHERE x.object_id=? ORDER BY i.rowid',(id,)).fetchall()
        return [dict(r) for r in rs]
    def document(self,id):
        o=self.object(id);raw=o['data'];h=o['content_hash'];target='method:'+id
        return {'target':target,'source_hash':h,'source':{'kind':'method_artifact','object_id':id,'ownership':o.get('scope',{}),'object_kind':o['kind']},
            'raw':raw,'raw_text':encode(raw),'parse_error':None,'view':normalize_record(raw),'report':diagnose(raw),
            'reviews':self.inspections.reviews(target,h) if self.inspections else [],'demo':o.get('demo') is True}
    def related(self,id):
        self.object(id)
        with self.lock:rows=self.conn.execute('SELECT DISTINCT o.payload,o.hash,o.content_hash,o.created_at FROM method_links l JOIN objects o ON o.id=l.source_id WHERE l.target_id=? ORDER BY o.rowid',(id,)).fetchall()
        return [{**json.loads(r['payload']),'hash':r['hash'],'content_hash':r['content_hash'],'imported_at':r['created_at']} for r in rows]
    def lineage(self,id):
        seen=set();nodes=[];edges=[]
        def walk(x):
            if x in seen:return
            if len(seen)>=1000:return
            seen.add(x);o=self.object(x);nodes.append({k:o.get(k) for k in ('id','kind','title','usage','content_hash','scope','demo')})
            for parent in o.get('derived_from',[]):
                edges.append({'source':parent,'target':x,'relation':'derived_from','causal_claim':False});walk(parent)
            for ref in o.get('evidence_refs',[]):
                if 'object_id' in ref:
                    edges.append({'source':ref['object_id'],'target':x,'relation':'source_evidence','causal_claim':False});walk(ref['object_id'])
        walk(id)
        restricted=[n for n in nodes if n.get('usage') in ('validation','test')]
        unknown=[n['id'] for n in nodes if not n.get('usage') or n.get('usage')=='unknown']
        return {'nodes':nodes,'edges':edges,'restricted':restricted,'unknown_usage':unknown,
            'complete_within_recorded_links':len(seen)<1000,'scope':'Only declared sources; absence of conflict is not a semantic leakage guarantee.',
            'training_export_ready':not restricted and not unknown,'causal_claim':False}
    def append_review(self,id,source_hash,status,note,pointer='',refs=None):
        o=self.object(id)
        if source_hash!=o['content_hash']:raise ValidationError('Stale method content hash')
        if status not in ('pending','confirmed','rejected'):raise ValidationError('Unknown review status')
        if not isinstance(note,str) or not note.strip() or len(note)>20000:raise ValidationError('Review note required, max 20,000 characters')
        self.resolve_ref({'object_id':id,'pointer':pointer,'sha256':source_hash})
        checked=[self.resolve_ref(r,owner=o)['ref'] for r in refs or []]
        with self.lock:
            cur=self.conn.execute('INSERT INTO reviews(object_id,source_hash,pointer,status,note,evidence,created_at) VALUES(?,?,?,?,?,?,?)',
                (id,source_hash,pointer,status,note,encode(checked),now()))
        return {'id':cur.lastrowid,'status':status,'causal_claim':False}
    def reviews(self,id):
        with self.lock:rs=self.conn.execute('SELECT * FROM reviews WHERE object_id=? ORDER BY id',(id,)).fetchall()
        return [{**dict(r),'evidence':json.loads(r['evidence'])} for r in rs]
    def _ref_info(self,ref,owner=None):
        if not ref:return {'available':False,'reason':'未记录'}
        r=self.resolve_ref(ref,owner=owner)
        return {'available':True,'ref':r['ref'],'target':r['target'],'preview':text(r['value'])[:400]}
    def cycle(self,id):
        cycle=self.object(id)
        if cycle['kind']!='selfplay_cycle':raise ValidationError('Expected selfplay_cycle')
        related=[o for o in self.related(id) if self._same_scope(cycle,o)];proposals=[o for o in related if o['kind']=='proposal'];attempts=[o for o in related if o['kind']=='solver_attempt']
        qids=list(dict.fromkeys(o['data']['question_id'] for o in proposals+attempts));questions=[]
        for qid in qids:
            q=self.object(qid);rels=[o for o in self.related(qid) if self._same_scope(cycle,o)];reviews=[o for o in rels if o['kind']=='question_review']
            superseded={r['data'].get('supersedes') for r in reviews};active=[r for r in reviews if r['id'] not in superseded]
            statuses={r['data']['status'] for r in active}
            status=next(iter(statuses)) if len(statuses)==1 else 'conflicting' if statuses else 'unreviewed'
            use=[o for o in rels if o['kind']=='training_use']
            qa=[o for o in attempts if o['data']['question_id']==qid]
            questions.append({'question':q,'proposals':[p for p in proposals if p['data']['question_id']==qid],
                'reviews':reviews,'active_review_ids':[r['id'] for r in active],'review_status':status,'attempts':qa,
                'training_uses':use,'lineage':self.lineage(qid)})
        return {'cycle':cycle,'questions':questions,'counts':{'generated':len({p['data']['question_id'] for p in proposals}),
            'questions_with_attempts':sum(bool(q['attempts']) for q in questions),'attempts':len(attempts),
            'accepted':sum(q['review_status']=='accepted' for q in questions),
            'rejected':sum(q['review_status']=='rejected' for q in questions),
            'sampled_questions':sum(any(u['data']['status']=='sampled' for u in q['training_uses']) for q in questions),
            'selected_questions':sum(any(u['data']['status']=='selected' for u in q['training_uses']) for q in questions)},
            'updates':[o for o in self.objects(cycle.get('scope',{})) if o['kind']=='training_update' and o['data'].get('cycle_id')==id],
            'diagnostics':self.diagnostics(id)}
    def skill_bank(self,id):
        o=self.object(id)
        if o['kind']!='skill_bank':raise ValidationError('Expected skill_bank')
        view=adapt_bank(o['data'])
        if o['data'].get('asset_ids'):
            from .skill_tracking import bank_asset_members
            assets=bank_asset_members(self,o['data'])
            view['members']+=assets;view['skill_count']+=len(assets)
            view['assets']=assets
            view['warnings']=[w for w in view['warnings'] if w!='No recognized skills. Inspect raw JSON; missing fields were not filled.']
        return {'snapshot':o,**view,'related':self.related(id),'imports':self.imports_for(id)}
    def skill_usage(self,bank_id,key):
        from .skill_tracking import bank_member
        bank=self.object(bank_id);m=bank_member(self,bank['data'],key);uses=[]
        for r in self.related(bank_id):
            if r['kind']!='skill_retrieval':continue
            d=r['data']
            def matches(v):
                return bank_member(self,bank['data'],v if isinstance(v,str) else v['member'])['pointer']==m['pointer']
            candidates=[x for x in d.get('candidates',[]) if matches(x)]
            selected=any(matches(x) for x in d.get('selected',[]))
            injections=[]
            for x in d.get('injections',[]):
                if not matches(x):continue
                evidence=self._ref_info(x.get('evidence_ref'),r)
                match=None
                from .diagnostics import visibility_evidence
                if x.get('evidence_ref') and x.get('rendered_text') and visibility_evidence(x['evidence_ref']):
                    from .skill_tracking import input_contains
                    value=self.resolve_ref(x['evidence_ref'],owner=r)['value'];match=input_contains(x['rendered_text'],value)
                injections.append({'recorded':True,'exact_rendered_fragment_visible':match,'evidence':evidence,'rendered_text':x.get('rendered_text')})
            if candidates or selected or injections:
                uses.append({'record':r,'candidate':bool(candidates),'selected':selected,'injections':injections,
                    'behavior_use':'not_inferred','effect':'requires_controlled_evaluation','trace':self._ref_info(d.get('trace_ref'),r)})
        return {'member':m,'bank':{k:bank[k] for k in ('id','content_hash')},'uses':uses,
            'counts':{'candidate':sum(x['candidate'] for x in uses),'selected':sum(x['selected'] for x in uses),
                'injection_recorded':sum(bool(x['injections']) for x in uses),
                'fragment_verified':sum(any(z['exact_rendered_fragment_visible'] is True for z in x['injections']) for x in uses)},
            'not_causal':'入选、注入与相关成功都不证明技能造成改善。'}
    def diff(self,a,b,cross_project=False):
        aa,bb=self.object(a),self.object(b)
        if aa['kind']!='skill_bank' or bb['kind']!='skill_bank':raise ValidationError('Expected two skill_bank snapshots')
        if aa.get('scope',{}).get('project_id')!=bb.get('scope',{}).get('project_id') and not cross_project:
            raise ValidationError('Cross-project snapshot comparison requires explicit confirmation')
        return {'a':aa,'b':bb,**compare_banks(aa['data'],bb['data'])}
    def diagnostics(self,id):
        from .diagnostics import diagnose_method
        return diagnose_method(self,self.object(id))
    def details(self,id):
        o=self.object(id)
        return {'object':o,'related':self.related(id),'lineage':self.lineage(id),'reviews':self.reviews(id),
                'diagnostics':self.diagnostics(id),'imports':self.imports_for(id)}
    def evidence_for(self,params):
        if params.get('method'):
            root=self.object(params['method']);related=self.related(root['id']);objects=[root]+related
        else:
            target='trajectory:'+params['trajectory'] if params.get('trajectory') else 'file:'+params.get('file','')+':'+str(params.get('record',''))
            objects=[]
            eval_id=self.store.trajectory(params['trajectory'])['evaluation_id'] if params.get('trajectory') and self.store else None
            for o in self.objects():
                if eval_id and o['kind']=='evaluation_binding' and o['data']['evaluation_id']==eval_id:
                    objects.append(o);continue
                for r in evidence_refs(o):
                    rt='method:'+r['object_id'] if r.get('object_id') else 'trajectory:'+r['trajectory'] if r.get('trajectory') else 'file:'+r.get('file','')+':'+str(r.get('record',''))
                    if rt==target:objects.append(o);break
        return {'objects':objects,'empty_means':'未登记关联，不代表方法未使用','no_inferred_links':True}
    def evaluation_matrix(self,bank_id):
        """List recorded checkpoint×bank cells, grouped by all other known conditions.

        No arbitrary winning repeat, pooled score or causal decomposition is made.
        """
        bank=self.object(bank_id)
        if bank['kind']!='skill_bank':raise ValidationError('Expected skill_bank')
        sc=bank.get('scope',{}) or {'project_id':''}
        bindings=self.objects(sc,kind='evaluation_binding')
        groups={}
        for binding in bindings:
            d=binding['data']
            if not self.store:continue
            ev=self.store.evaluation(d['evaluation_id'])
            runtime=self.evaluation_state(ev['id'])
            settings={'panel_hash':ev['panel_hash'],'protocol_hash':ev['protocol_hash'],
                      'retrieval_config':runtime.get('runtime',{}).get('retrieval_config'),
                      'extensions':runtime.get('runtime',{}).get('extensions'),
                      'base_model':ev['run'].get('base_model'),'model_revision':ev['run'].get('model_revision')}
            key=digest(settings)
            if key not in groups:groups[key]={'id':key,'settings':settings,'panel':ev['panel'],'cells':[],
                'conditions_complete':True,'causal_claim':False}
            g=groups[key]
            g['conditions_complete']=g['conditions_complete'] and runtime['status']=='recorded' and ev.get('protocol_complete',False)
            g['cells'].append({'evaluation_id':ev['id'],'checkpoint':ev['checkpoint'],'bank_id':runtime.get('bank_id'),
                'stats':ev['stats'],'status':ev['status'],'demo':bool(ev.get('demo') or ev['run'].get('demo') or binding.get('demo')),
                'binding_origin':d['binding_origin'],'created_at':ev['created_at']})
        return {'groups':list(groups.values()),'scope':sc,'statement':'同格的复测独立列出；不同题单/协议/检索配置分组，不输出技能或权重贡献。'}

    @cached_read
    def evaluation_state(self,evaluation_id):
        with self.lock:ids=[r[0] for r in self.conn.execute("SELECT source_id FROM method_links WHERE target_id=? AND relation='evaluation_binding' ORDER BY source_id",('evaluation:'+evaluation_id,))]
        bs=[self.object(x) for x in ids]
        if not bs:return {'status':'not_recorded','fingerprint':None,'binding':None}
        o=bs[0];d=o['data'];bank=None
        if d.get('bank_id'):
            # An evaluation compares the bank's immutable identity, not all of
            # its skill text. A large bank is expanded only in the skill viewer.
            with self.lock:
                bank=self.conn.execute('SELECT id,content_hash FROM objects WHERE id=?',(d['bank_id'],)).fetchone()
            if bank is None:raise NotFoundError('Method object not found: '+str(d['bank_id'])[:180])
        payload={'bank_content_hash':bank['content_hash'] if bank else None,'retrieval_config':d.get('retrieval_config',{}),'extensions':d.get('extensions',{})}
        def has_unknown(v):
            if v is None:return True
            if isinstance(v,str):return v.strip().lower() in ('','unknown','unrecorded','not_recorded')
            if isinstance(v,dict):return any(has_unknown(x) for x in v.values())
            if isinstance(v,list):return any(has_unknown(x) for x in v)
            return False
        complete=(d.get('state_complete') is True and not has_unknown(d.get('retrieval_config',{}))
                  and (bank is not None or d.get('skills_disabled') is True)
                  and (bank is None or bool(d.get('retrieval_config'))))
        return {'status':'recorded' if complete else 'incomplete','fingerprint':digest(payload),'binding':o,
                'bank_id':bank['id'] if bank else None,'runtime':payload,'execution_verified':False}
    def pair_state(self,a,b):
        aa,bb=self.evaluation_state(a),self.evaluation_state(b)
        if aa['status']==bb['status']=='not_recorded':return {'compatible':True,'status':'not_recorded','reasons':[], 'a':aa,'b':bb}
        reasons=[]
        if aa['status']!='recorded' or bb['status']!='recorded':reasons.append('方法外部状态未完整登记，不能将差异只归给 checkpoint')
        elif aa['fingerprint']!=bb['fingerprint']:reasons.append('SkillBank / 方法外部状态或检索配置不同；不是固定外部条件的权重比较')
        return {'compatible':not reasons,'status':'checked','reasons':reasons,'a':aa,'b':bb}

    @cached_read
    def method_assessment(self,experiment_id,run_id='',family='',offset=0,limit=25,evaluation_id=''):
        """Small method-specific evidence assessment; never executes a model."""
        from .assessment import method_assessment
        return method_assessment(self,experiment_id,run_id,family,offset,limit,evaluation_id)

    @cached_read
    def skill_runtime(self,experiment_id,run_id='',evaluation_id='',trajectory_id='',offset=0,limit=25,step=None):
        from .skill_tracking import skill_runtime
        return skill_runtime(self,experiment_id,run_id,evaluation_id,trajectory_id,offset,limit,step)

    @cached_read
    def skill_lifecycle(self,experiment_id,run_id='',offset=0,limit=25):
        from .skill_tracking import skill_lifecycle
        return skill_lifecycle(self,experiment_id,run_id,offset,limit)

    def opd_training(self,experiment_id,run_id='',offset=0,limit=25):
        from .opd_tracking import opd_training
        return opd_training(self,experiment_id,run_id,offset,limit)

    def opd_runtime(self,experiment_id,run_id='',evaluation_id='',trajectory_id='',offset=0,limit=25,step=None):
        from .opd_tracking import opd_runtime
        return opd_runtime(self,experiment_id,run_id,evaluation_id,trajectory_id,offset,limit,step)

    def opd_record(self,id):
        from .opd_tracking import opd_record
        return opd_record(self,id)

    def opd_tokens(self,id,offset=0,limit=50):
        from .opd_tracking import opd_tokens
        return opd_tokens(self,id,offset,limit)

    def opd_recovery(self,id):
        from .opd_recovery import opd_recovery
        return opd_recovery(self,id)

    def entropy_training(self,experiment_id,run_id='',offset=0,limit=25):
        from .entropy_tracking import entropy_training
        return entropy_training(self,experiment_id,run_id,offset,limit)

    def selfplay_training(self,experiment_id,run_id='',cycle_id='',reference_id='',evaluation_id='',offset=0,limit=25):
        from .selfplay_tracking import selfplay_training
        return selfplay_training(self,experiment_id,run_id,cycle_id,reference_id,evaluation_id,offset,limit)

    def selfplay_runtime(self,experiment_id,run_id='',evaluation_id=''):
        from .selfplay_tracking import selfplay_runtime
        return selfplay_runtime(self,experiment_id,run_id,evaluation_id)

    def selfplay_question(self,id,cycle_id=''):
        from .selfplay_tracking import selfplay_question
        return selfplay_question(self,id,cycle_id)

    def selfplay_comparison(self,id):
        from .selfplay_evaluation import selfplay_comparison
        return selfplay_comparison(self,id)

    def entropy_runtime(self,experiment_id,run_id='',evaluation_id='',trajectory_id='',offset=0,limit=25,step=None):
        from .entropy_tracking import entropy_runtime
        return entropy_runtime(self,experiment_id,run_id,evaluation_id,trajectory_id,offset,limit,step)

    def entropy_record(self,id):
        from .entropy_tracking import entropy_record
        return entropy_record(self,id)

    def entropy_tokens(self,id,offset=0,limit=50):
        from .entropy_tracking import entropy_tokens
        return entropy_tokens(self,id,offset,limit)

    def entropy_comparison(self,id):
        from .entropy_evaluation import entropy_comparison
        return entropy_comparison(self,id)

    @cached_read
    def skill_assets(self,experiment_id,run_id='',offset=0,limit=25):
        from .skill_tracking import skill_assets
        return skill_assets(self,experiment_id,run_id,offset,limit)

    @cached_read
    def skill_instance(self,id):
        from .skill_tracking import skill_instance
        return skill_instance(self,id)

    @cached_read
    def skill_comparison(self,id):
        from .skill_comparison import skill_comparison
        return skill_comparison(self,id)
