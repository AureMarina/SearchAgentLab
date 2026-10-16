"""Separate, lossless JSON/JSONL inspection archive (not an evaluation import).

Formal project/run/checkpoint tables are untouched. Originals are immutable;
review notes append to a sidecar database and bind to exact content hashes.
"""
from __future__ import annotations
import hashlib
import json
import math
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from ..core import ValidationError, NotFoundError
from .adapters import normalize_record, leaves, resolve, text
from .diagnostics import diagnose, RULESET_VERSION

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 20000


def encode(data: Any) -> str:
    return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(data: Any) -> str:
    return hashlib.sha256(encode(data).encode('utf-8')).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def strict_json(s: str) -> Any:
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d: raise ValueError('Duplicate JSON key: '+k[:120])
            d[k]=v
        return d
    def constant(v): raise ValueError('Non-finite JSON constant: '+v)
    out=json.loads(s,object_pairs_hook=unique,parse_constant=constant)
    pending=[(out,0)]
    while pending:
        v,depth=pending.pop()
        if depth>120: raise ValueError('JSON nesting exceeds the inspection safety limit (120)')
        if isinstance(v,dict): pending.extend((x,depth+1) for x in v.values())
        elif isinstance(v,list): pending.extend((x,depth+1) for x in v)
        elif isinstance(v,float) and not math.isfinite(v): raise ValueError('Non-finite JSON number')
    return out


class InspectionRepository:
    def __init__(self, data_dir: str | Path):
        self.path=Path(data_dir).resolve()/'trace_inspection.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.conn=sqlite3.connect(self.path,check_same_thread=False,timeout=20,isolation_level=None)
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS files (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,
          format TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL,
          record_count INTEGER NOT NULL, invalid_count INTEGER NOT NULL,
          blank_lines INTEGER NOT NULL, content BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS records (
          file_id TEXT NOT NULL REFERENCES files(id), ordinal INTEGER NOT NULL,
          source_line INTEGER, source_pointer TEXT, raw_json TEXT NOT NULL,
          raw_text TEXT NOT NULL, raw_hash TEXT NOT NULL, summary TEXT NOT NULL,
          search_text TEXT NOT NULL, report TEXT NOT NULL, error TEXT,
          PRIMARY KEY(file_id,ordinal)
        );
        CREATE TABLE IF NOT EXISTS reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL,
          source_hash TEXT NOT NULL, finding_id TEXT, pointer TEXT NOT NULL,
          status TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS reviews_target ON reviews(target,source_hash);
        ''')

    def close(self):
        with self.lock: self.conn.close()

    def __enter__(self): return self
    def __exit__(self,*args): self.close()

    def files(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute('SELECT id,name,sha256,format,size,created_at,record_count,invalid_count,blank_lines FROM files ORDER BY created_at DESC,id')]

    def file(self, file_id: str) -> dict:
        with self.lock:
            r=self.conn.execute('SELECT id,name,sha256,format,size,created_at,record_count,invalid_count,blank_lines FROM files WHERE id=?',(file_id,)).fetchone()
            if r is None: raise NotFoundError('Inspection file not found')
            return dict(r)

    def original(self, file_id: str) -> bytes:
        with self.lock:
            r=self.conn.execute('SELECT content FROM files WHERE id=?',(file_id,)).fetchone()
            if r is None: raise NotFoundError('Inspection file not found')
            return bytes(r[0])

    def import_bytes(self, content: bytes, name: str, fmt: str='auto') -> dict:
        if not isinstance(content,bytes) or not content: raise ValidationError('Empty upload')
        if len(content)>MAX_FILE_BYTES: raise ValidationError('File exceeds 50 MiB; split it or use the original evaluator export')
        name=Path(str(name).replace('\\','/')).name[:180]
        if not name or any(ord(c)<32 for c in name): raise ValidationError('Invalid filename')
        if name.lower().endswith('.eval'):
            raise ValidationError('Inspect .eval binary logs are not supported here; export an expanded single-sample JSON or sample JSONL with Inspect first')
        if fmt not in ('auto','jsonl','json'): raise ValidationError('format must be jsonl or json')
        try: decoded=content.decode('utf-8-sig')
        except UnicodeDecodeError as e: raise ValidationError('Only UTF-8 JSON/JSONL is supported; no bytes were imported') from e
        if fmt=='auto': fmt='json' if name.lower().endswith('.json') else 'jsonl'
        sha=hashlib.sha256(content).hexdigest(); file_id=hashlib.sha256((fmt+':'+sha).encode()).hexdigest()[:24]
        with self.lock:
            if self.conn.execute('SELECT 1 FROM files WHERE id=?',(file_id,)).fetchone():
                return {**self.file(file_id),'already_present':True}
        blank=0; material=[]
        if fmt=='jsonl':
            # Physical line numbers are never renumbered after blank/error lines.
            for number,line in enumerate(decoded.split('\n'),1):
                if not line.strip(): blank+=1;continue
                material.append((number,'',line,None))
        else:
            try: value=strict_json(decoded)
            except (ValueError,RecursionError) as e: raise ValidationError(f'Invalid JSON: {str(e)[:300]}') from e
            if isinstance(value,list): material=[(None,'/'+str(i),encode(v),v) for i,v in enumerate(value)]
            elif isinstance(value,dict) and value.get('schema_version') in (1,2) and isinstance(value.get('trajectories'),list):
                material=[(None,'/trajectories/'+str(i),encode(v),v) for i,v in enumerate(value['trajectories'])]
            elif isinstance(value,dict) and isinstance(value.get('episodes'),list):
                material=[(None,'/episodes/'+str(i),encode(v),v) for i,v in enumerate(value['episodes'])]
            else: material=[(None,'',decoded,value)]
        if not material: raise ValidationError('No nonempty records')
        if len(material)>MAX_RECORDS: raise ValidationError('More than 20,000 records; split this file')
        rows=[]; invalid=0
        for ordinal,(line,source_pointer,raw_text,parsed) in enumerate(material,1):
            error=None
            try:
                if len(raw_text.encode('utf-8'))>MAX_RECORD_BYTES: raise ValueError('Record exceeds 8 MiB; original bytes preserved, not expanded')
                if fmt=='jsonl': parsed=strict_json(raw_text)
            except (ValueError,RecursionError) as e:
                error=str(e)[:500]; parsed=None;invalid+=1
            view=normalize_record(parsed)
            report=diagnose(parsed,parse_error=error)
            em=view['scores'].get('em')
            if isinstance(em,bool) or not isinstance(em,(int,float)) or em not in (0,1): em=None
            result='correct' if em==1 else 'incorrect' if em==0 else 'unscored'
            # An is_correct field is labelled as such, never silently renamed EM.
            if em is None and 'is_correct' in view: result='correct' if view['is_correct'] else 'incorrect'
            if error: result='invalid'
            question=view['question']
            summary={'ordinal':ordinal,'source_line':line,'source_pointer':source_pointer,
                     'question':question if isinstance(question,str) else (text(question) if question is not None else None),
                     'dataset':str(view['dataset'] or ''),'sample_id':str(view['sample_id'] or ''),
                     'adapter':view['adapter'],'execution_kind':view['execution_kind'], 'outcome':result,
                     'record_purpose':view['record_purpose'],
                     'has_recorded_scores':bool(view['scores'] or view['scorer_results'] or 'is_correct' in view),
                     'score_label':'EM' if em is not None else 'is_correct' if 'is_correct' in view else None,
                     'score_value':em if em is not None else view.get('is_correct'),
                     'trajectory_count':len(view['trajectories']), 'step_count':sum(t['step_count'] for t in view['trajectories']),
                     'finding_count':len(report['findings']), 'candidate_count':sum(f['basis']!='insufficient' for f in report['findings']),
                     'rules':[f['rule'] for f in report['findings']], 'error':error}
            raw_json=encode(parsed)
            rows.append((file_id,ordinal,line,source_pointer,raw_json,raw_text,
                         hashlib.sha256(raw_text.encode('utf-8')).hexdigest(),encode(summary),raw_text.casefold(),encode(report),error))
        with self.lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                if self.conn.execute('SELECT 1 FROM files WHERE id=?',(file_id,)).fetchone():
                    self.conn.rollback();return {**self.file(file_id),'already_present':True}
                self.conn.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (file_id,name,sha,fmt,len(content),now(),len(rows),invalid,blank,content))
                self.conn.executemany('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?)',rows)
                self.conn.commit()
            except BaseException:
                self.conn.rollback();raise
        return {**self.file(file_id),'already_present':False}

    def list_records(self, file_id: str, query: str='', outcome: str='', rule: str='', offset: int=0, limit: int=50) -> dict:
        f=self.file(file_id)
        if offset<0 or not 1<=limit<=200: raise ValidationError('Invalid pagination')
        query=query.strip().casefold()
        with self.lock:
            candidates=self.conn.execute('SELECT summary FROM records WHERE file_id=? AND (?=\'\' OR instr(search_text,?)>0) ORDER BY ordinal',(file_id,query,query)).fetchall()
            summaries=[json.loads(r[0]) for r in candidates]
        all_counts={k:sum(s['outcome']==k for s in summaries) for k in ('correct','incorrect','unscored','invalid')}
        rules={}
        for s in summaries:
            for r in set(s['rules']): rules[r]=rules.get(r,0)+1
        filtered=[s for s in summaries if (not outcome or s['outcome']==outcome) and (not rule or rule in s['rules'])]
        return {'file':f,'total':len(filtered),'counts':all_counts,'rules':rules,'offset':offset,'limit':limit,'items':filtered[offset:offset+limit]}

    def record(self, file_id: str, ordinal: int) -> dict:
        with self.lock:
            r=self.conn.execute('SELECT * FROM records WHERE file_id=? AND ordinal=?',(file_id,ordinal)).fetchone()
            if r is None: raise NotFoundError('Inspection record not found')
            raw=json.loads(r['raw_json'])
            target=f'file:{file_id}:{ordinal}'
            return {'target':target,'source_hash':r['raw_hash'],'source':{'kind':'jsonl_inspection','file':self.file(file_id),'ordinal':ordinal,'line':r['source_line'],'pointer':r['source_pointer']},
                'raw':raw,'raw_text':r['raw_text'],'parse_error':r['error'],'view':normalize_record(raw),
                'report':diagnose(raw,parse_error=r['error']), 'reviews':self.reviews(target,r['raw_hash'])}

    def evaluation_record(self, record: dict) -> dict:
        raw={k:v for k,v in record.items() if k!='annotations'}
        target='trajectory:'+str(record['id']); source_hash=digest(raw)
        return {'target':target,'source_hash':source_hash,'source':{'kind':'evaluation','trajectory_id':record['id'],'evaluation_id':record.get('evaluation_id')},
                'raw':raw,'raw_text':None,'parse_error':None,'view':normalize_record(raw),'report':diagnose(raw),
                'reviews':self.reviews(target,source_hash),'legacy_annotations':record.get('annotations',[])}

    def reviews(self, target: str, source_hash: str) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute('SELECT * FROM reviews WHERE target=? AND source_hash=? ORDER BY id',(target,source_hash))]

    def append_review(self, document: dict, note: dict) -> dict:
        status=note.get('status','pending')
        if status not in ('pending','confirmed','rejected'): raise ValidationError('Invalid review status')
        comment=note.get('note','')
        if not isinstance(comment,str) or not comment.strip() or len(comment)>20000: raise ValidationError('Review note must have 1–20,000 characters')
        p=note.get('pointer',''); fid=note.get('finding_id') or None
        if not isinstance(p,str) or len(p)>3000: raise ValidationError('Invalid JSON pointer')
        try: resolve(document['raw'],p)
        except (ValueError,TypeError,KeyError,IndexError) as e: raise ValidationError('Evidence pointer does not exist in this record') from e
        if note.get('source_hash')!=document['source_hash']: raise ValidationError('Record changed; reload before reviewing')
        if fid is not None and fid not in {f['id'] for f in document['report']['findings']}: raise ValidationError('Finding is not from this record / ruleset')
        with self.lock:
            cur=self.conn.execute('INSERT INTO reviews(target,source_hash,finding_id,pointer,status,note,created_at) VALUES (?,?,?,?,?,?,?)',
                (document['target'],document['source_hash'],fid,p,status,comment.strip(),now()))
            return dict(self.conn.execute('SELECT * FROM reviews WHERE id=?',(cur.lastrowid,)).fetchone())

    def search_record(self, document: dict, query: str, limit: int=200) -> dict:
        if not isinstance(query,str) or not query.strip(): return {'items':[],'total':0,'returned':0,'truncated':False}
        if len(query)>500: raise ValidationError('Search query exceeds 500 characters')
        if not 1<=limit<=500: raise ValidationError('Invalid search limit')
        needle=query.casefold();hits=[];total=0
        for p,v in leaves(document['raw']):
            s=text(v)
            if needle in s.casefold() or needle in p.casefold():
                total+=1
                if len(hits)<limit:
                    at=s.casefold().find(needle);start=max(0,at-70)
                    hits.append({'pointer':p,'preview':s[start:start+260],'value_length':len(s)})
        return {'items':hits,'total':total,'returned':len(hits),'truncated':total>len(hits)}

    def report(self, document: dict) -> dict:
        return {'kind':'trace_diagnostic_evidence','version':1,'created_at':now(),
                'source':document['source'],'target':document['target'],'source_hash':document['source_hash'],
                'parse_error':document['parse_error'],'raw':document['raw'],
                'diagnostics':document['report'],'reviews':document['reviews'],
                'legacy_annotations':document.get('legacy_annotations',[]),
                'limitations':['This report is not a rescored evaluation.','Rule candidates are not proof of causal failure.','Missing evidence is not reconstructed.']}
