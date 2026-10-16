"""Append-only local case workspace. Sidecar DB; formal evaluations untouched."""
from __future__ import annotations
import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from ..core import ValidationError,NotFoundError
from ..inspection.repository import encode,digest,now
from ..inspection.adapters import resolve
from .contracts import validate_contract,contract_template
from .scorecards import scorecard
from .evidence import context_of,validate_paths,known,CORE_REQUIRED
from .verification import execute_component,validate_external_result,reliability,component
from .judge import make_packet,validate_response

COMPONENTS=('prompt','tool_schema','retrieval','context_builder','parser','scorer','runtime','policy','skill','training','unknown')

def ident(prefix):return prefix+'-'+uuid.uuid4().hex[:16]

def required_text(data,k,maxlen=12000):
    v=data.get(k)
    if not isinstance(v,str) or not v.strip() or len(v)>maxlen:raise ValidationError(f'{k} requires 1–{maxlen} characters')
    return v.strip()

class LoopRepository:
    def __init__(self,data_dir):
        self.path=Path(data_dir)/'diagnostic_loop.sqlite3';self.path.parent.mkdir(exist_ok=True,parents=True)
        self.lock=threading.RLock()
        self.conn=sqlite3.connect(self.path,timeout=20,check_same_thread=False,isolation_level=None);self.conn.row_factory=sqlite3.Row
        self.conn.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS objects(id TEXT PRIMARY KEY,kind TEXT NOT NULL,target TEXT NOT NULL,
          source_hash TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS by_target ON objects(target,source_hash,kind);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,object_id TEXT NOT NULL,
          kind TEXT NOT NULL,data TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS by_object ON events(object_id);
        ''')
    def close(self):
        with self.lock:self.conn.close()
    def __enter__(self):return self
    def __exit__(self,*a):self.close()
    def _put(self,id,kind,doc,data):
        s=encode(data)
        with self.lock:
            r=self.conn.execute('SELECT * FROM objects WHERE id=?',(id,)).fetchone()
            if r:
                if r['kind']==kind and r['data']==s and r['target']==doc['target'] and r['source_hash']==doc['source_hash']:return json.loads(r['data'])
                raise ValidationError('Immutable object ID already exists with different content')
            self.conn.execute('INSERT INTO objects VALUES (?,?,?,?,?,?)',(id,kind,doc['target'],doc['source_hash'],s,now()))
        return data
    def object(self,id,doc=None,kind=None):
        with self.lock:r=self.conn.execute('SELECT * FROM objects WHERE id=?',(id,)).fetchone()
        if r is None:raise NotFoundError('Diagnostic object not found')
        if doc and (r['target']!=doc['target'] or r['source_hash']!=doc['source_hash']):raise ValidationError('Object belongs to another source / source changed')
        if kind and r['kind']!=kind:raise ValidationError('Unexpected diagnostic object kind')
        return json.loads(r['data'])
    def objects(self,doc,kind=None):
        with self.lock:rs=self.conn.execute('SELECT * FROM objects WHERE target=? AND source_hash=? ORDER BY rowid',(doc['target'],doc['source_hash'])).fetchall()
        return [json.loads(r['data']) for r in rs if kind is None or r['kind']==kind]
    def event(self,id,kind,data):
        self.object(id)
        with self.lock:
            cur=self.conn.execute('INSERT INTO events(object_id,kind,data,created_at) VALUES (?,?,?,?)',(id,kind,encode(data),now()))
            return {'event_id':cur.lastrowid,'object_id':id,'kind':kind,'data':data}
    def events(self,id):
        with self.lock:rs=self.conn.execute('SELECT * FROM events WHERE object_id=? ORDER BY id',(id,)).fetchall()
        return [{**dict(r),'data':json.loads(r['data'])} for r in rs]
    def guard(self,doc,data):
        if data.get('source_hash')!=doc['source_hash']:raise ValidationError('Source hash missing/changed: reload before writing')
    def evidence(self,doc,paths):
        if not isinstance(paths,list) or len(paths)>30:raise ValidationError('Invalid evidence pointers')
        for p in paths:
            if not isinstance(p,str) or not p.startswith('/'):raise ValidationError('Select a non-root evidence pointer')
            try:resolve(doc['raw'],p)
            except (ValueError,KeyError,TypeError,IndexError) as e:raise ValidationError('Evidence pointer not in this source: '+str(p)) from e
        return list(dict.fromkeys(paths))
    def bind_contract(self,doc,data):
        self.guard(doc,data);c=validate_contract(data['contract']);binding=data.get('binding','retrospective')
        if binding not in ('retrospective','recorded_at_execution'):raise ValidationError('Unknown contract binding')
        if binding=='recorded_at_execution':
            context=context_of(doc)['values']
            if context.get('contract_hash')!=digest(c):raise ValidationError('Recorded-at-execution requires matching contract_hash in source context')
        obj={'id':ident('contract-binding'),'kind':'contract','contract':c,'contract_hash':digest(c),'binding':binding,'created_at':now()}
        # The same named version is immutable across all cases in the workspace.
        key='frozen:'+c['id']+':'+c['version'];global_doc={'target':'contract-registry','source_hash':'global'}
        self._put(key,'contract_definition',global_doc,c)
        return self._put(obj['id'],'contract',doc,obj)
    def add_hypothesis(self,doc,data):
        self.guard(doc,data)
        if doc.get('parse_error') or doc['view']['execution_kind']=='reference_only':raise ValidationError('Need actual execution evidence, not reference-only or invalid JSON')
        h=data['hypothesis'];title=required_text(h,'title',300);observation=required_text(h,'observation');check=required_text(h,'minimal_test')
        component_name=h.get('component','unknown')
        if component_name not in COMPONENTS:raise ValidationError('Unknown fault component')
        if h.get('layer') not in ('Outcome','Decision','Action','Reliability'):raise ValidationError('Unknown failure layer')
        ev=self.evidence(doc,h.get('evidence',[]));counter=self.evidence(doc,h.get('counter_evidence',[]))
        if not ev:raise ValidationError('Hypothesis needs at least one concrete evidence pointer')
        alternatives=h.get('alternatives',[])
        if not isinstance(alternatives,list) or not alternatives or any(not isinstance(x,str) or not x.strip() for x in alternatives):raise ValidationError('Keep at least one competing explanation')
        obj={'id':ident('hypothesis'),'kind':'hypothesis','title':title,'layer':h['layer'],'component':component_name,
             'observation':observation,'evidence':ev,'counter_evidence':counter,'alternatives':alternatives,
             'minimal_test':check,'origin':'manual','state':'unverified','created_at':now()}
        return self._put(obj['id'],'hypothesis',doc,obj)
    def hypothesis_from_rule(self,doc,data):
        f=next((x for x in doc['report']['findings'] if x['id']==data.get('finding_id')),None)
        if not f:raise ValidationError('Rule finding not in source')
        mapping={'alias_lost':('Action','context_builder'), 'parser_mismatch':('Outcome','parser'),
                 'score_mismatch':('Outcome','scorer'),'tool_args':('Action','tool_schema'),
                 'tool_error':('Action','runtime'),'runtime_error':('Action','runtime'),
                 'uniform_rewards':('Reliability','training'),'repeated_query':('Decision','policy')}
        layer,component_name=mapping.get(f['rule'],('Action','unknown'))
        h={'title':f['title'],'layer':layer,'component':component_name,'observation':f['explanation'],
           'evidence':[e['pointer'] for e in f['evidence']],'alternatives':[f['limitation']],
           'minimal_test':f['next_check']}
        return self.add_hypothesis(doc,{**data,'hypothesis':h})
    def add_task(self,doc,data):
        self.guard(doc,data);spec=data['task'];h=self.object(spec['hypothesis_id'],doc,'hypothesis')
        mode=spec.get('mode')
        if mode not in ('component','prefix','autonomous'):raise ValidationError('Task mode must be component/prefix/autonomous')
        allowed=validate_paths(spec.get('allowed_changes',[]));required=validate_paths(spec.get('required_paths',[]))
        task={'id':ident('verification'),'kind':'verification_task','hypothesis_id':h['id'],'mode':mode,
              'title':required_text(spec,'title',300),'change_candidate':required_text(spec,'change_candidate'),
              'expected_observation':required_text(spec,'expected_observation'),'allowed_changes':allowed,
              'required_paths':required,'source_hash':doc['source_hash'],'source_target':doc['target'],
              'baseline_context':context_of(doc)['values'],'source_ref':doc['source'],'created_at':now(),'state':'planned',
              'contract_hash':(self.objects(doc,'contract')[-1]['contract_hash'] if self.objects(doc,'contract') else None),
              'no_auto_publish':True,'no_auto_training':True}
        if mode=='component':
            comp=spec.get('component',{})
            self.evidence(doc,[comp.get('input_pointer')]);v=resolve(doc['raw'],comp['input_pointer'])
            # Validate configs with bounded pure functions now; do not persist their
            # outputs as execution until user explicitly runs the task.
            for a in ('baseline','candidate'):component(v,comp.get('operation'),comp.get(a))
            task['component']={**comp,'input_hash':digest(v)}
            task['expected_cases']=[]
        else:
            if not allowed or not required:raise ValidationError('External task requires explicit allowed changes and invariant paths')
            task['required_paths']=list(dict.fromkeys(CORE_REQUIRED+required))
            keys=spec.get('expected_cases') or [{'dataset':doc['view']['dataset'] or '', 'sample_id':doc['view']['sample_id'] or ''}]
            if not isinstance(keys,list) or len(keys)>10000 or not keys or any(not isinstance(k,dict) or not isinstance(k.get('dataset'),str) or not k['dataset'] or not isinstance(k.get('sample_id'),str) or not k['sample_id'] for k in keys):raise ValidationError('Need frozen dataset/sample_id keys')
            if len({(k['dataset'],k['sample_id']) for k in keys})!=len(keys):raise ValidationError('Duplicate expected case')
            task['expected_cases']=keys;task['manifest_hash']=digest(keys)
            if mode=='prefix':
                p=spec.get('prefix_pointer');self.evidence(doc,[p]);task['prefix_pointer']=p
                task['restore_mode']=spec.get('restore_mode','approximate_text_reconstruction')
                if task['restore_mode'] not in ('approximate_text_reconstruction','recorded_state_artifact'):raise ValidationError('Invalid restore mode')
                if task['restore_mode']=='recorded_state_artifact':task['state_artifact']=required_text(spec,'state_artifact')
                task['restoration_verified']=False
        task['task_hash']=digest(task)
        return self._put(task['id'],'task',doc,task)
    def execute(self,doc,data):
        self.guard(doc,data);task=self.object(data['task_id'],doc,'task')
        result=execute_component(doc,task);result.update(id=ident('result'),kind='result',task_hash=task['task_hash'],created_at=now())
        return self._put(result['id'],'result',doc,result)
    def import_result(self,doc,data):
        self.guard(doc,data);task=self.object(data['task_id'],doc,'task');report=validate_external_result(task,data['result'])
        if doc.get('demo') or (isinstance(doc['raw'],dict) and (doc['raw'].get('demo') is True or isinstance(doc['raw'].get('data'),dict) and doc['raw']['data'].get('demo') is True)):
            report['synthetic']=True;report['autonomous_comparison_eligible']=False;report['autonomous_macro_delta']=None
            report['note']='合成机制示例，不是真实 Agent 执行结果。'+report['note']
        obj={'id':ident('result'),'kind':'result','task_id':task['id'],'task_hash':task['task_hash'],'created_at':now(),
             'execution':'declared_external_not_attested','report':report,'submitted':data['result']}
        return self._put(obj['id'],'result',doc,obj)
    def review_hypothesis(self,doc,data):
        self.guard(doc,data);h=self.object(data['hypothesis_id'],doc,'hypothesis');status=data.get('status')
        if status not in ('unverified','supported_by_experiment','refuted','inconclusive'):raise ValidationError('Invalid hypothesis state')
        if status in ('supported_by_experiment','refuted'):
            r=self.object(data.get('result_id'),doc,'result');t=self.object(r['task_id'],doc,'task')
            if t['hypothesis_id']!=h['id']:raise ValidationError('Result belongs to another hypothesis')
            rep=r.get('report')
            if rep and not rep['comparison']['eligible']:raise ValidationError('Confounded / incomplete context; cannot mark experiment as supporting/refuting')
        return self.event(h['id'],'hypothesis_review',{'status':status,'result_id':data.get('result_id'),
              'note':required_text(data,'note'),'causal_proof':False,'basis':'human_interpretation_of_evidence'})
    def record_fix(self,doc,data):
        self.guard(doc,data);task=self.object(data['task_id'],doc,'task')
        decision=data.get('decision')
        if decision not in ('proposed','accept_for_further_evaluation','reject','needs_more_evidence'):raise ValidationError('Invalid fix decision')
        rid=data.get('result_id')
        if rid:
            r=self.object(rid,doc,'result')
            if r['task_id']!=task['id']:raise ValidationError('Fix result must belong to task')
        elif decision=='accept_for_further_evaluation':raise ValidationError('Acceptance requires a verification result')
        return self.event(task['id'],'fix_review',{'decision':decision,'patch_ref':required_text(data,'patch_ref'),
                          'note':required_text(data,'note'),'result_id':rid,'applied':False,'published':False})
    def add_judge_packet(self,doc,data):
        self.guard(doc,data);p=make_packet(doc,data['mode'],data.get('step_pointer'));p['id']=ident('judge-packet');p['created_at']=now()
        return self._put(p['id'],'judge_packet',doc,p)
    def import_judge(self,doc,data):
        self.guard(doc,data);p=self.object(data['packet_id'],doc,'judge_packet');r=validate_response(p,data['response'])
        r.update(id=ident('judge-result'),packet_id=p['id'],created_at=now())
        return self._put(r['id'],'judge_result',doc,r)
    def review_judge(self,doc,data):
        self.guard(doc,data);obj=self.object(data['judge_result_id'],doc,'judge_result')
        if data.get('status') not in ('pending','confirmed','rejected'):raise ValidationError('Invalid Judge review')
        return self.event(obj['id'],'judge_review',{'status':data['status'],'note':required_text(data,'note'),'affects_original_score':False})
    def add_reliability(self,doc,data):
        self.guard(doc,data);runs=data.get('runs',[])
        ds,sid=doc['view']['dataset'],doc['view']['sample_id']
        if any(r.get('dataset')!=ds or r.get('sample_id')!=sid for r in runs):raise ValidationError('Repeat data not for the selected case')
        paths=data.get('required_paths',['/checkpoint_id','/panel_hash','/prompt','/retriever','/parser','/scorer','/budget','/decoding','/runtime'])
        report=reliability(runs,paths)
        obj={'id':ident('repeats'),'kind':'reliability','report':report,'runs':runs,'required_paths':paths,'created_at':now()}
        return self._put(obj['id'],'reliability',doc,obj)
    def workspace(self,doc):
        all=self.objects(doc);contracts=[x for x in all if x.get('kind')=='contract'];active=contracts[-1] if contracts else None
        # Objects may store descriptive kind names. Use table classification for API lists.
        def items(kind):
            out=self.objects(doc,kind)
            return [{**x,'history':self.events(x['id'])} for x in out]
        return {'source_target':doc['target'],'source_hash':doc['source_hash'],'source':doc['source'],
                'contract':active,'contract_history':contracts,
                'scorecard':scorecard(doc,active['contract'] if active else None,active['binding'] if active else 'not_bound'),
                'contract_template':contract_template('case-'+digest(doc['target'])[:10]),
                'hypotheses':items('hypothesis'),'tasks':items('task'),'results':items('result'),
                'judge_packets':items('judge_packet'),'judge_results':items('judge_result'),'reliability':items('reliability')}
    def export(self,doc):
        return {'kind':'case_diagnosis_verification_evidence','version':1,'created_at':now(),'source':doc['source'],
                'source_hash':doc['source_hash'],'raw':doc['raw'],'workflow':self.workspace(doc),
                'limitations':['Imported metadata and external execution declarations are not runtime attestations.',
                  'Hypotheses and Judge judgements are not causal proof.','No original scores, training data or checkpoints were changed.']}
