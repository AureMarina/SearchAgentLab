"""Single-writer receiver using the existing validators and attached databases.

Center mode uses SQLite rollback journals + FULL synchronization, not WAL, so the
main DB and attached sidecars participate in one SQLite transaction on local disk.
Original package blobs are written/fsynced before the DB commit. A crash can leave
an unreferenced immutable blob, never a receipt pointing to an unwritten blob.
"""
from __future__ import annotations
import json
import re
import threading
from pathlib import Path
from contextlib import ExitStack
from ..core import ValidationError, NotFoundError, clone, dumps, digest, now, Store as CoreStore
from ..store import Store
from ..studio.repository import StudioRepository
from ..inspection.repository import InspectionRepository
from ..methods.repository import MethodRepository
from ..methods.indexes import TABLES as METHOD_TABLES
from ..methods.schema import SCHEMA, REF_FIELDS, evidence_refs
from ..diagnostic_loop.repository import LoopRepository
from .package import read_package
from .util import process_lock, durable_write, file_sha, sha, json_bytes


class ConflictError(ValidationError):pass
class PermissionError(ValidationError):pass


def gid(workspace, kind, original):
    return 'remote-'+kind+'-'+digest([workspace,kind,original])[:32]


class AttachedConnection:
    """Route constant repository table names, while retaining one outer transaction.

    Values always stay SQL parameters. This is NOT an arbitrary SQL rewriting API.
    Inner repository BEGIN/commit calls never commit a partial package.
    """
    def __init__(self, connection, schema, tables):
        self.base=connection;self.schema=schema;self.tables=set(tables)
        self.pattern=re.compile(r'\b(FROM|JOIN|INTO|UPDATE)\s+('+ '|'.join(re.escape(t) for t in tables) + r')\b',re.I)
    def _sql(self, sql):
        return self.pattern.sub(lambda m:m[1]+' '+self.schema+'.'+m[2],sql)
    def execute(self, sql, parameters=()):
        if sql.strip().upper().startswith('BEGIN'):
            if not self.base.in_transaction:raise RuntimeError('Team repository write requires outer transaction')
            return self.base.execute('SELECT 1')
        return self.base.execute(self._sql(sql),parameters)
    def executemany(self, sql, parameters):return self.base.executemany(self._sql(sql),parameters)
    def commit(self):pass
    def rollback(self):pass
    def close(self):pass
    def __enter__(self):
        if not self.base.in_transaction:raise RuntimeError('Team write requires outer transaction')
        return self
    def __exit__(self,*args):return False


def clean_profile(p):return {k:v for k,v in p.items() if k not in ('created_at','profile_hash')}


class Hub:
    def __init__(self, data_dir):
        self.data_dir=Path(data_dir).expanduser().resolve()
        if (self.data_dir/'.team'/'source.json').exists():raise ValidationError('Use a separate center workspace; never turn an active collector into its own center')
        self.stack=ExitStack();self.lock=threading.RLock()
        self.stack.enter_context(process_lock(self.data_dir/'.team'/'center.lock'))
        try:
            self.store=Store(self.data_dir,_center=True)
            self.inspections=InspectionRepository(self.data_dir)
            self.methods=MethodRepository(self.data_dir,self.store,self.inspections)
            self.studio=StudioRepository(self.store,self.methods,self.inspections)
            self.loop=LoopRepository(self.data_dir)
            c=self.store.conn
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            mode=c.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
            if mode.lower()!='delete':raise ValidationError('Center must use a local single-writer rollback-journal database')
            c.execute('PRAGMA synchronous=FULL')
            for schema,repo,tables in [
                ('sx',self.studio,('profiles','training_batches','saved_reports','training_batch_summaries','harness_checks')),
                ('mx',self.methods,METHOD_TABLES),
                ('ix',self.inspections,('files','records','reviews')),
                ('lx',self.loop,('objects','events'))]:
                repo.conn.commit();repo.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                mode=repo.conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
                if mode.lower()!='delete':raise ValidationError('Stop other local writers before starting center mode')
                repo.conn.close()
                c.execute('ATTACH DATABASE ? AS '+schema,(str(repo.path),))
                c.execute('PRAGMA '+schema+'.synchronous=FULL')
                repo.conn=AttachedConnection(c,schema,tables)
                repo.lock=self.lock
            self.store.lock=self.lock
            self.store._team_methods=self.methods
            self.store._team_studio=self.studio
            c.executescript('''
            CREATE TABLE IF NOT EXISTS team_sources(workspace_id TEXT PRIMARY KEY,owner TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS team_runs(run_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,original_id TEXT NOT NULL,
              revision INTEGER NOT NULL,state_hash TEXT NOT NULL,package_id TEXT NOT NULL,received_at TEXT NOT NULL,
              snapshot TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS team_receipts(package_id TEXT PRIMARY KEY,owner TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS team_conflicts(id INTEGER PRIMARY KEY AUTOINCREMENT,received_at TEXT NOT NULL,owner TEXT NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS team_artifacts(run_id TEXT NOT NULL,key TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(run_id,key));
            ''')
            durable_write(self.data_dir/'.team'/'center.json',json_bytes({'mode':'single_writer_center','created_at':now()}))
        except BaseException:
            if hasattr(self,'store'):self.store.close()
            self.stack.close();raise

    def __enter__(self):return self
    def __exit__(self,*a):self.close()
    def close(self):
        self.store.close();self.stack.close()

    def apply_plan(self, plan):
        if not isinstance(plan,dict) or plan.get('plan_schema')!='searchagent-lab.plan/v1':raise ValidationError('Expected plan_schema=searchagent-lab.plan/v1')
        with self.lock,self.store.transaction():
            self.store.register_project(plan['project'])
            self.store.register_experiment(plan['experiment'])
            if plan['experiment']['project_id']!=plan['project']['id']:raise ValidationError('Plan project mismatch')
            if plan['profile']['experiment_id']!=plan['experiment']['id']:raise ValidationError('Plan profile mismatch')
            for p in plan.get('panels',[]):self.store.register_panel(p)
            profile=self.studio.register_profile(clean_profile(plan['profile']))
            return {'project_id':plan['project']['id'],'experiment_id':plan['experiment']['id'],
                    'track':profile['track'],'panels':[p['id'] for p in plan.get('panels',[])]}

    def _authorize(self,m,p,principal):
        if principal['role'] not in ('admin','uploader'):raise PermissionError('Read-only credential cannot import results')
        if principal['role']=='uploader' and m['experiment_id'] not in principal['experiments']:
            raise PermissionError('Credential is not assigned to this experiment')
        if m['workspace_id']!=m['source'].get('workspace_id'):raise ValidationError('Source/workspace mismatch')
        for label,key in [('project','project_id'),('experiment','experiment_id'),('run','run_id')]:
            if not isinstance(p.get(label),dict) or p[label].get('id')!=m[key]:raise ValidationError('Manifest/record identity mismatch: '+label)
        if p['experiment'].get('project_id')!=m['project_id'] or p['run'].get('project_id')!=m['project_id'] or p['run'].get('experiment_id')!=m['experiment_id']:
            raise ValidationError('Run/project/experiment ownership mismatch')
        if not isinstance(p.get('profile'),dict):raise ValidationError('profile must be an object')
        if p['profile'].get('experiment_id')!=m['experiment_id'] or p['profile'].get('track')!=m['track']:
            raise ValidationError('Research purpose mismatch')
        for key in ('panels','metrics','checkpoints','evaluations','trajectories','batches','methods','inspection_files','artifacts','method_sources'):
            if not isinstance(p.get(key),list) or any(not isinstance(x,dict) for x in p[key]):raise ValidationError('records.'+key+' must be an array of objects')
        if not isinstance(p.get('harness_checks',[]),list) or any(not isinstance(x,dict) for x in p.get('harness_checks',[])):
            raise ValidationError('records.harness_checks must be an array of objects')
        old=self.store.conn.execute('SELECT owner,data FROM team_sources WHERE workspace_id=?',(m['workspace_id'],)).fetchone()
        if old and json.loads(old[1])!=m['source']:raise ConflictError('Source provenance changed; create a separate source identity')
        if old and old[0]!=principal['owner'] and principal['role']!='admin':raise PermissionError('Source workspace belongs to a different submitter')

    def _definitions(self,m,p,allow):
        for table,record,register in [('projects',p['project'],self.store.register_project),('experiments',p['experiment'],self.store.register_experiment)]:
            try:old=self.store._get(table,record['id'])
            except NotFoundError:
                if not allow:raise PermissionError('Center must register this project/experiment plan first')
                register(record);continue
            if old!=record:raise ConflictError('Frozen '+table+' definition differs from center')
        for panel in p['panels']:
            try:old=CoreStore.panel(self.store,panel['id'])
            except NotFoundError:
                if not allow:raise PermissionError('Center must approve this fixed panel before upload')
                self.store.register_panel(panel);continue
            if old!=panel:raise ConflictError('Frozen Groundtruth differs; uploads cannot edit it')
        existing=self.studio.profile(m['experiment_id'])
        if existing['track']=='unassigned' and not allow:raise PermissionError('Center must approve the experiment profile first')
        self.studio.register_profile(clean_profile(p['profile']))

    def _validate_members(self,m,p,blobs):
        run=m['run_id']; cps={x['id'] for x in p['checkpoints']};evs={x['id'] for x in p['evaluations']}
        for k in ('checkpoints','evaluations','trajectories','batches','methods'):
            ids=[x.get('id') for x in p[k]]
            if len(set(ids))!=len(ids):raise ValidationError('Duplicate '+k+' ID')
        if any(x.get('run_id')!=run for x in p['checkpoints']+p['metrics']+p['batches']):raise ValidationError('Records escape package run')
        if any(x.get('checkpoint_id') not in cps for x in p['evaluations']):raise ValidationError('Missing/outside checkpoint dependency')
        if any(x.get('evaluation_id') not in evs for x in p['trajectories']):raise ValidationError('Missing/outside evaluation dependency')
        checks=p.get('harness_checks',[])
        check_ids=[x.get('id') for x in checks]
        if len(set(check_ids))!=len(check_ids):raise ValidationError('Duplicate harness check ID')
        if any(x.get('run_id')!=run or (x.get('evaluation_id') and x['evaluation_id'] not in evs) for x in checks):
            raise ValidationError('Harness check escapes package run/evaluation')
        for obj in p['methods']:
            sc=obj.get('scope',{})
            if any(sc.get(k) not in (None,'',m[k]) for k in ('project_id','experiment_id','run_id')):
                raise PermissionError('Method evidence must belong to the same run/experiment or be an unscoped dependency')
        for item in p['inspection_files']+p['method_sources']+p['artifacts']:
            if item.get('path'):
                if item['path'] not in blobs or sha(blobs[item['path']])!=item.get('sha256'):
                    raise ValidationError('Referenced artifact missing/corrupt')
        keys=[a.get('key') for a in p['artifacts']]
        if len(set(keys))!=len(keys):raise ValidationError('Duplicate artifact key')
        for a in p['artifacts']:
            from ..core import ident
            ident(a.get('key'),'artifact.key')
            if type(a.get('required')) is not bool:raise ValidationError('artifact.required must be explicit boolean')
            if a.get('storage') not in ('embedded','missing','external_unverified'):raise ValidationError('Unknown artifact storage state')
            if a['storage']=='embedded' and not a.get('path'):raise ValidationError('Embedded artifact needs packaged bytes')

    def _append_only(self,old,new):
        # Snapshots must retain every previously recorded immutable item. A new
        # revision may add metrics at an existing step, never rewrite a value.
        for group in ('checkpoints','trajectories','batches','methods'):
            index={x['id']:x for x in new[group]}
            for rec in old[group]:
                if index.get(rec['id'])!=rec:raise ConflictError('Snapshot removed/changed immutable '+group+'/'+rec['id'])
        checks={x['id']:x for x in new.get('harness_checks',[])}
        for record in old.get('harness_checks',[]):
            if checks.get(record['id'])!=record:raise ConflictError('Snapshot removed/changed immutable harness check')
        artifacts={a['key']:a for a in new['artifacts']}
        for a in old['artifacts']:
            b=artifacts.get(a['key'])
            if b is None or any(b.get(k)!=a.get(k) for k in ('sha256','required','role','uri','source_path')):raise ConflictError('Artifact identity changed or removed')
        index={(x['step'],k):v for x in new['metrics'] for k,v in x['metrics'].items()}
        for x in old['metrics']:
            for k,v in x['metrics'].items():
                if (x['step'],k) not in index or index[(x['step'],k)]!=v:raise ConflictError('Recorded training metric removed or changed')
        index={e['id']:e for e in new['evaluations']}
        for e in old['evaluations']:
            n=index.get(e['id'])
            if n is None:raise ConflictError('Recorded evaluation removed')
            if {k:v for k,v in e.items() if k not in ('status','finished_at')}!={k:v for k,v in n.items() if k not in ('status','finished_at')}:
                raise ConflictError('Evaluation configuration changed')
            if e['status']!='running' and n!=e:raise ConflictError('Finished evaluation changed')
        if {k:v for k,v in old['run'].items() if k!='status'}!={k:v for k,v in new['run'].items() if k!='status'}:
            raise ConflictError('Run metadata changed; create a new run')
        if old['run']['status']!='running' and old['run']['status']!=new['run']['status']:raise ConflictError('Terminal run status cannot be rewritten')

    def _project_records(self,m,p):
        wid=m['workspace_id'];orig=p;v=clone(p)
        maps={kind:{x['id']:gid(wid,kind,x['id']) for x in p[key]}
              for kind,key in [('checkpoint','checkpoints'),('evaluation','evaluations'),('trajectory','trajectories'),('method','methods'),('batch','batches')]}
        maps['run']={m['run_id']:gid(wid,'run',m['run_id'])}
        maps['harness_check']={x['id']:gid(wid,'harness-check',x['id']) for x in p.get('harness_checks',[])}
        def remap(kind,value):
            if value not in maps[kind]:raise ValidationError('Reference outside package: '+kind+'/'+str(value))
            return maps[kind][value]
        v['run']['id']=remap('run',m['run_id'])
        v['run']['team_origin']={'workspace_id':wid,'original_run_id':m['run_id'],
                                'operator':m['source']['operator'],'platform':m['source']['platform']}
        for c in v['checkpoints']:
            c['id']=remap('checkpoint',c['id']);c['run_id']=remap('run',c['run_id'])
        for e in v['evaluations']:
            e['id']=remap('evaluation',e['id']);e['checkpoint_id']=remap('checkpoint',e['checkpoint_id'])
            if 'run_id' in e:e['run_id']=remap('run',e['run_id'])
        for t in v['trajectories']:
            t['id']=remap('trajectory',t['id']);t['evaluation_id']=remap('evaluation',t['evaluation_id'])
        original_methods={o['id']:o for o in orig['methods']};original_traces={t['id']:t for t in orig['trajectories']}
        def ref(r):
            if not isinstance(r,dict):raise ValidationError('Invalid evidence reference')
            if r.get('object_id'):
                target=original_methods.get(r['object_id'])
                if not target:raise ValidationError('Missing method reference')
                expected=digest(target['data'])
                if r.get('sha256') and r['sha256']!=expected:raise ValidationError('Original method evidence hash mismatch')
                r['object_id']=remap('method',r['object_id'])
                if r.get('sha256'):r['source_sha256']=r.pop('sha256')
            elif r.get('trajectory'):
                target=original_traces.get(r['trajectory'])
                if not target:raise ValidationError('Missing formal trajectory reference')
                if r.get('sha256') and r['sha256']!=digest(target):raise ValidationError('Original trajectory evidence hash mismatch')
                r['trajectory']=remap('trajectory',r['trajectory'])
                if r.get('sha256'):r['source_sha256']=r.pop('sha256')
            # Content-addressed inspection-file IDs/bytes are unchanged.
        for obj in v['methods']:
            original_obj=original_methods[obj['id']]
            # An asset-backed bank accepts asset IDs as exact member selectors.
            # Rewrite only those documented selectors; skill names, pointers
            # and raw prompt strings keep their original meaning and bytes.
            source_bank=original_methods.get(obj['data'].get('bank_id'),{})
            source_assets=source_bank.get('data',{}).get('asset_ids',[])
            if obj['kind'] in ('skill_instance','skill_evolution','skill_comparison') and obj['data'].get('member') in source_assets:
                obj['data']['member']=remap('method',obj['data']['member'])
            if obj['kind']=='skill_retrieval' and source_assets:
                for field in ('candidates','selected','injections'):
                    for index,entry in enumerate(obj['data'].get(field,[])):
                        if isinstance(entry,str) and entry in source_assets:
                            obj['data'][field][index]=remap('method',entry)
                        elif isinstance(entry,dict) and entry.get('member') in source_assets:
                            entry['member']=remap('method',entry['member'])
            obj['id']=remap('method',obj['id'])
            if obj.get('scope',{}).get('run_id'):obj['scope']['run_id']=remap('run',obj['scope']['run_id'])
            obj['derived_from']=[remap('method',x) for x in obj.get('derived_from',[])] if 'derived_from' in obj else []
            for key in REF_FIELDS.get(obj['kind'],{}):
                if key in obj['data']:
                    val=obj['data'][key];obj['data'][key]=[remap('method',x) for x in val] if isinstance(val,list) else remap('method',val)
            if obj['kind']=='training_use' and obj['data'].get('batch_id') in maps['batch']:
                obj['data']['batch_id']=remap('batch',obj['data']['batch_id'])
            if obj['kind']=='training_update' and obj['data'].get('batch_id') in maps['batch']:
                obj['data']['batch_id']=remap('batch',obj['data']['batch_id'])
            if obj['kind']=='trace' and obj['data'].get('selfplay_evidence_type')=='run_accounting':
                accounting=obj['data']
                for field,category in (('run_id','run'),('output_checkpoint_id','checkpoint')):
                    if accounting.get(field) in maps[category]:accounting[field]=remap(category,accounting[field])
                accounting['cycle_ids']=[remap('method',oid) for oid in accounting.get('cycle_ids',[])]
            if obj['kind']=='evaluation_binding':obj['data']['evaluation_id']=remap('evaluation',obj['data']['evaluation_id'])
            if obj['kind'] in ('selfplay_cycle','selfplay_reference','selfplay_pool_event','selfplay_comparison','solver_attempt'):
                # Rewrite documented Lab identities only. Stable question IDs,
                # native rollout IDs, source text and frozen protocol hashes
                # retain their original producer meaning and bytes.
                sd=obj['data']
                for field,category in (('run_id','run'),('checkpoint_id','checkpoint'),
                                       ('proposer_checkpoint_id','checkpoint'),('solver_checkpoint_id','checkpoint'),
                                       ('evaluation_id','evaluation'),('trajectory_id','trajectory'),('batch_id','batch')):
                    if sd.get(field) in maps[category]:sd[field]=remap(category,sd[field])
                if obj['kind']=='selfplay_comparison':
                    for arm in sd.get('arms',[]):
                        for field,category in (('run_id','run'),('checkpoint_id','checkpoint'),('evaluation_id','evaluation')):
                            if arm.get(field) in maps[category]:arm[field]=remap(category,arm[field])
                        arm['cycle_ids']=[remap('method',oid) for oid in arm.get('cycle_ids',[])]
            if obj['kind'] in ('entropy_observation','entropy_probe','entropy_intervention','entropy_comparison'):
                # Formal entropy identities are namespaced; native raw rows,
                # external traj_uid and original log bytes are preserved.
                ed=obj['data']
                for field, category in (('checkpoint_id','checkpoint'),('evaluation_id','evaluation'),
                                        ('trajectory_id','trajectory'),('batch_id','batch'),('run_id','run')):
                    if ed.get(field) in maps[category]:ed[field]=remap(category,ed[field])
                selection=ed.get('selection',{})
                if isinstance(selection,dict) and selection.get('definition_id') in maps['method']:
                    selection['definition_id']=remap('method',selection['definition_id'])
                # A typed calibration proof can itself be an entropy probe.
                if ed.get('fit_evaluation_id') in maps['evaluation']:
                    ed['fit_evaluation_id']=remap('evaluation',ed['fit_evaluation_id'])
                if obj['kind']=='entropy_comparison':
                    for pair in ed.get('pairs',[]):
                        for key in ('before_observation_id','after_observation_id'):
                            if pair.get(key):pair[key]=remap('method',pair[key])
                    ed['trajectory_ids']=[remap('trajectory',tid) for tid in ed.get('trajectory_ids',[])] if 'trajectory_ids' in ed else ed.get('trajectory_ids')
                    if ed.get('trajectory_ids') is None:ed.pop('trajectory_ids',None)
                    for estimator in ed.get('estimators',[]):
                        estimator['definition_id']=remap('method',estimator['definition_id'])
                        for score in estimator.get('scores',[]):
                            score['trajectory_id']=remap('trajectory',score['trajectory_id'])
                        protocol=estimator.get('probe_protocol',{})
                        if protocol.get('sample_set_hash'):
                            # A declared cohort fingerprint is derived from the
                            # typed Lab trajectory IDs. Keep the source digest
                            # alongside it; never rewrite the native log bytes.
                            source_tids=original_obj['data'].get('trajectory_ids',[])
                            original_hash=protocol['sample_set_hash']
                            if original_hash==digest(sorted(source_tids)):
                                estimator['source_sample_set_hash']=original_hash
                                protocol['sample_set_hash']=digest(sorted(ed.get('trajectory_ids',[])))
                        calibration=estimator.get('calibration',{})
                        if calibration.get('fit_evaluation_id') in maps['evaluation']:
                            calibration['fit_evaluation_id']=remap('evaluation',calibration['fit_evaluation_id'])
                    for arm in ed.get('arms',[]):
                        for field,category in (('checkpoint_id','checkpoint'),('evaluation_id','evaluation')):
                            if arm.get(field) in maps[category]:arm[field]=remap(category,arm[field])
                        selection=arm.get('selection',{})
                        if selection.get('definition_id') in maps['method']:
                            selection['definition_id']=remap('method',selection['definition_id'])
                        arm['intervention_ids']=[remap('method',oid) for oid in arm.get('intervention_ids',[])]
            # These are typed provenance identifiers, not strings inside raw
            # prompts or tool output. External identities remain unchanged.
            if obj['kind']=='opd_failure' and obj['data'].get('student_checkpoint_id') in maps['checkpoint']:
                obj['data']['student_checkpoint_id']=remap('checkpoint',obj['data']['student_checkpoint_id'])
            if obj['kind'] in ('opd_failure','opd_teacher','opd_supervision','opd_diagnostic','opd_recovery'):
                # Only typed execution links are namespaced. Native traj_uid,
                # sample IDs, raw prompts and frozen state hashes keep their
                # source meaning and bytes.
                for key in ('student_checkpoint_id','checkpoint_id','baseline_checkpoint_id','current_checkpoint_id'):
                    if obj['data'].get(key) in maps['checkpoint']:
                        obj['data'][key]=remap('checkpoint',obj['data'][key])
                for key in ('evaluation_id',):
                    if obj['data'].get(key) in maps['evaluation']:
                        obj['data'][key]=remap('evaluation',obj['data'][key])
                if obj['data'].get('trajectory_id') in maps['trajectory']:
                    obj['data']['trajectory_id']=remap('trajectory',obj['data']['trajectory_id'])
                if obj['data'].get('batch_id') in maps['batch']:
                    obj['data']['batch_id']=remap('batch',obj['data']['batch_id'])
                if obj['kind']=='opd_recovery':
                    for case in obj['data'].get('cases',[]):
                        case['failure_id']=remap('method',case['failure_id'])
                        for attempt in case.get('attempts',[]):
                            if attempt.get('teacher_record_id'):
                                attempt['teacher_record_id']=remap('method',attempt['teacher_record_id'])
                            if attempt.get('checkpoint_id') in maps['checkpoint']:
                                attempt['checkpoint_id']=remap('checkpoint',attempt['checkpoint_id'])
                    for key in ('before_evaluation_id','after_evaluation_id'):
                        full=obj['data'].get('full_task',{})
                        if full.get(key) in maps['evaluation']:
                            full[key]=remap('evaluation',full[key])
            if obj['kind']=='skill_retrieval' and obj['data'].get('trajectory_id') in maps['trajectory']:
                obj['data']['trajectory_id']=remap('trajectory',obj['data']['trajectory_id'])
            if obj['kind'] in ('skill_instance','skill_evolution','skill_comparison'):
                effective=obj['data'].get('effective_scope')
                if isinstance(effective,dict) and effective.get('run_id'):
                    effective['run_id']=remap('run',effective['run_id'])
                for key in ('checkpoint_id','initial_checkpoint_id','current_checkpoint_id'):
                    if obj['data'].get(key) in maps['checkpoint']:
                        obj['data'][key]=remap('checkpoint',obj['data'][key])
                for key in ('evaluation_id','with_evaluation_id','without_evaluation_id'):
                    if obj['data'].get(key) in maps['evaluation']:
                        obj['data'][key]=remap('evaluation',obj['data'][key])
                if obj['data'].get('trajectory_id') in maps['trajectory']:
                    obj['data']['trajectory_id']=remap('trajectory',obj['data']['trajectory_id'])
                for cell in obj['data'].get('cells',[]):
                    if isinstance(cell,dict) and cell.get('evaluation_id') in maps['evaluation']:
                        cell['evaluation_id']=remap('evaluation',cell['evaluation_id'])
            if obj['kind']=='training_use' and obj['data'].get('credit_assignment'):
                credits=obj['data']['credit_assignment']
                for credit in credits if isinstance(credits,list) else [credits]:
                    if isinstance(credit,dict) and credit.get('instance_id'):
                        credit['instance_id']=remap('method',credit['instance_id'])
            if obj['kind']=='training_update' and obj['data'].get('input_checkpoint_id') in maps['checkpoint']:
                obj['data']['input_checkpoint_id']=remap('checkpoint',obj['data']['input_checkpoint_id'])
            if obj['kind']=='training_update' and obj['data'].get('output_checkpoint_id'):
                cp=obj['data']['output_checkpoint_id']
                if cp in maps['checkpoint']:obj['data']['output_checkpoint_id']=remap('checkpoint',cp)
                elif obj['data'].get('checkpoint_ref_status')!='external_unverified':raise ValidationError('Cross-run checkpoint needs explicit external reference')
            for rr in evidence_refs(obj):ref(rr)
        for b in v['batches']:
            oldhash=b.pop('content_hash',None)
            if oldhash is not None and oldhash!=digest(b):raise ValidationError('Original batch hash mismatch')
            b['id']=remap('batch',b['id']);b['run_id']=remap('run',b['run_id'])
            if b.get('output_checkpoint_id') in maps['checkpoint']:b['output_checkpoint_id']=remap('checkpoint',b['output_checkpoint_id'])
            for g in b['groups']:
                for s in g['samples']:
                    if s.get('trace_ref'):ref(s['trace_ref'])
        for x in v['metrics']:x['run_id']=remap('run',x['run_id'])
        for check in v.get('harness_checks',[]):
            original_hash=check.pop('content_hash',None)
            if original_hash is None or original_hash!=digest(check):raise ValidationError('Original harness check hash mismatch')
            check['id']=remap('harness_check',check['id']);check['run_id']=remap('run',check['run_id'])
            if check.get('evaluation_id'):check['evaluation_id']=remap('evaluation',check['evaluation_id'])
            check['source_content_hash']=original_hash
        return v,maps

    def ingest(self,path,principal=None,allow_definitions=False,submitter_owner=None):
        principal=principal or {'owner':'local-admin','role':'admin','experiments':[]}
        if submitter_owner is not None:
            if principal['role']!='admin':raise PermissionError('Only administrators can import on behalf of a collaborator')
            if not isinstance(submitter_owner,str) or not submitter_owner.strip() or len(submitter_owner)>200:raise ValidationError('Explicit submitter owner required')
            principal=principal|{'owner':submitter_owner}
        with self.lock:
            m,p,blobs=read_package(path)
            try:
                self._authorize(m,p,principal);self._validate_members(m,p,blobs)
                c=self.store.conn
                prior=c.execute('SELECT data FROM team_receipts WHERE package_id=?',(m['package_id'],)).fetchone()
                if prior:return json.loads(prior[0])|{'delivery_status':'duplicate'}
                global_run=gid(m['workspace_id'],'run',m['run_id'])
                old=c.execute('SELECT * FROM team_runs WHERE run_id=?',(global_run,)).fetchone()
                if old and m['revision']<old['revision']:
                    return {'package_id':m['package_id'],'run_id':global_run,'revision':m['revision'],
                            'delivery_status':'superseded','center_revision':old['revision'],'received_at':now()}
                if old and m['revision']==old['revision'] and old['state_hash']!=m['state_sha256']:
                    raise ConflictError('Same source/run revision has different content; do not clone a collector workspace')
                if old:self._append_only(json.loads(old['snapshot']),p)
                v,maps=self._project_records(m,p)
                # Persist immutable originals before any database claims completeness.
                archive=self.data_dir/'.team'/'received'/(m['package_id']+'.zip')
                if not archive.exists():durable_write(archive,Path(path).read_bytes(),exclusive=True)
                elif read_package(archive)[0]['package_id']!=m['package_id']:raise ConflictError('Archived package is corrupt')
                for a in p['artifacts']:
                    if a['storage']=='embedded':
                        dest=self.data_dir/'.team'/'blobs'/a['sha256']
                        if not dest.exists():durable_write(dest,blobs[a['path']],exclusive=True)
                        elif file_sha(dest)!=a['sha256']:raise ConflictError('Archived artifact is corrupt')
                with self.store.transaction():
                    self._definitions(m,p,allow_definitions and principal['role']=='admin')
                    c.execute('INSERT OR IGNORE INTO team_sources VALUES (?,?,?)',(m['workspace_id'],principal['owner'],dumps(m['source'])))
                    run=v['run'];end_status=run['status'];initial=clone(run)
                    existing=c.execute('SELECT data FROM runs WHERE id=?',(run['id'],)).fetchone()
                    initial['status']=json.loads(existing[0])['status'] if existing else 'running'
                    self.store.register_run(initial)
                    for cp in v['checkpoints']:self.store.register_checkpoint(cp)
                    for metric in v['metrics']:self.store.log_metrics(metric['run_id'],metric['step'],metric['metrics'])
                    for ev in v['evaluations']:
                        start={k:value for k,value in ev.items() if k!='finished_at'}
                        self.store.start_evaluation(start)
                    for tr in v['trajectories']:self.store.log_trajectory(tr['evaluation_id'],tr)
                    for ins in p['inspection_files']:
                        result=self.inspections.import_bytes(blobs[ins['path']],ins['name'],ins['format'])
                        if result['id']!=ins['id'] or result['sha256']!=ins['sha256']:raise ValidationError('Inspection identity mismatch')
                    if v['methods']:self.methods.import_bundle({'method_schema':SCHEMA,'objects':v['methods']},name='team-normalized-methods.json')
                    # Preserve original source bytes and explicit normalized-object links.
                    # Old packages lacked object_ids; keep them in the original result ZIP.
                    for source in p['method_sources']:
                        source_ids=source.get('object_ids',[])
                        if not isinstance(source_ids,list) or any(x not in maps['method'] for x in source_ids):
                            raise ValidationError('Method source contains unknown object IDs')
                        linked=[maps['method'][x] for x in source_ids]
                        if not linked:continue
                        source_name=Path(str(source['name']).replace('\\','/')).name[:180]
                        if not source_name or any(ord(x)<32 for x in source_name):raise ValidationError('Invalid method source name')
                        imp='team-src-'+digest([source['sha256'],run['id'],linked])[:28]
                        self.methods.conn.execute('INSERT OR IGNORE INTO imports VALUES (?,?,?,?,?,?,?)',
                            (imp,source_name,source['sha256'],dumps({'project_id':run['project_id'],'experiment_id':run['experiment_id'],'run_id':run['id']}),dumps(linked),blobs[source['path']],now()))
                        self.methods.conn.executemany('INSERT OR IGNORE INTO method_import_objects VALUES (?,?)',[(imp,oid) for oid in linked])
                    for batch in v['batches']:self.studio.record_batch(batch)
                    for check in v.get('harness_checks',[]):self.studio.record_harness_check(check)
                    for ev in v['evaluations']:
                        if ev['status']!='running':
                            self.store.finish_evaluation(ev['id'],ev['status'])
                            # Preserve source completion timestamp, not network arrival time.
                            if ev.get('finished_at'):
                                saved=self.store._get('evaluations',ev['id']);saved['finished_at']=ev['finished_at']
                                c.execute('UPDATE evaluations SET data=? WHERE id=?',(dumps(saved),ev['id']))
                    self.store.set_run_status(run['id'],end_status)
                    for a in p['artifacts']:
                        previous=c.execute('SELECT data FROM team_artifacts WHERE run_id=? AND key=?',(run['id'],a['key'])).fetchone()
                        saved=clone(a)
                        if previous and json.loads(previous[0]).get('storage')=='embedded':
                            before=json.loads(previous[0])
                            if saved.get('sha256')!=before.get('sha256'):raise ConflictError('Artifact key/content changed')
                            if saved['storage']=='missing':saved=before|{'source_missing':True}
                        c.execute('INSERT INTO team_artifacts VALUES (?,?,?) ON CONFLICT(run_id,key) DO UPDATE SET data=excluded.data',(run['id'],saved['key'],dumps(saved)))
                    received=now()
                    artifacts=[json.loads(x[0]) for x in c.execute('SELECT data FROM team_artifacts WHERE run_id=?',(run['id'],))]
                    required_missing=[a['key'] for a in artifacts if a.get('required') and a.get('storage')!='embedded']
                    info={'run_id':run['id'],'original_run_id':m['run_id'],'project_id':m['project_id'],'experiment_id':m['experiment_id'],
                          'track':m['track'],'revision':m['revision'],'package_id':m['package_id'],
                          'operator':m['source']['operator'],'platform':m['source']['platform'],
                          'submitted_by':principal['owner'],'received_at':received,'source_created_at':m['created_at'],
                          'run_status':end_status,'sync_status':'received_snapshot',
                          'evidence_status':'required_artifacts_pending' if required_missing else 'declared_records_received',
                          'required_missing':required_missing,'coverage':p.get('coverage',{}),
                          'last_metric_step':max([x['step'] for x in p['metrics']],default=None),
                          'live_status':'unknown; last submission is not a training heartbeat'}
                    receipt={'package_id':m['package_id'],'run_id':run['id'],'revision':m['revision'],
                             'delivery_status':'accepted','received_at':received,'evidence_status':info['evidence_status']}
                    c.execute('INSERT INTO team_runs VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET revision=excluded.revision,state_hash=excluded.state_hash,package_id=excluded.package_id,received_at=excluded.received_at,snapshot=excluded.snapshot,data=excluded.data',
                              (run['id'],m['workspace_id'],m['run_id'],m['revision'],m['state_sha256'],m['package_id'],received,dumps(p),dumps(info)))
                    c.execute('INSERT INTO team_receipts VALUES (?,?,?)',(m['package_id'],principal['owner'],dumps(receipt)))
                return receipt
            except (ValidationError,NotFoundError,KeyError,TypeError) as exc:
                report={'package_id':m.get('package_id'),'run_id':m.get('run_id'),'workspace_id':m.get('workspace_id'),
                        'project_id':m.get('project_id'),'experiment_id':m.get('experiment_id'),'reason':str(exc)[:1000],
                        'status':'rejected','remote_execution_verified':False}
                c=self.store.conn
                c.execute('INSERT INTO team_conflicts(received_at,owner,data) VALUES (?,?,?)',(now(),principal['owner'],dumps(report)))
                raise

    def runs(self,project='',track=''):
        with self.lock:
            rows=[json.loads(r[0]) for r in self.store.conn.execute('SELECT data FROM team_runs ORDER BY received_at DESC')]
            return [r for r in rows if (not project or r['project_id']==project) and (not track or r['track']==track)]

    def run(self,run_id):
        with self.lock:
            r=self.store.conn.execute('SELECT data FROM team_runs WHERE run_id=?',(run_id,)).fetchone()
            if not r:return {'run_id':run_id,'sync_status':'local_only','evidence_status':'not_transferred'}
            d=json.loads(r[0]);d['artifacts']=[json.loads(x[0]) for x in self.store.conn.execute('SELECT data FROM team_artifacts WHERE run_id=?',(run_id,))]
            for a in d['artifacts']:
                if a.get('storage')=='embedded':
                    path=self.data_dir/'.team'/'blobs'/a['sha256']
                    a['availability']='archived_present' if path.is_file() and path.stat().st_size==a.get('size') else 'missing_or_size_mismatch'
                else:a['availability']=a['storage']
            d['missing_archived_files']=[a['key'] for a in d['artifacts'] if a['availability']=='missing_or_size_mismatch']
            if d['missing_archived_files']:d['evidence_status']='archived_files_missing'
            d['availability_note']='Listing checks existence/size; download verifies SHA256. External URI access is never assumed.'
            return d

    def receipts(self,owner=None,limit=200):
        with self.lock:
            c=self.store.conn
            where=' WHERE owner=?' if owner else ''
            args=(owner,limit) if owner else (limit,)
            return {'receipts':[json.loads(r[0]) for r in c.execute('SELECT data FROM team_receipts'+where+' ORDER BY rowid DESC LIMIT ?',args)],
                    'conflicts':[dict(r)|{'data':json.loads(r['data'])} for r in c.execute('SELECT id,received_at,owner,data FROM team_conflicts'+where+' ORDER BY id DESC LIMIT ?',args)],
                    'limit':limit,'note':'Recent entries, not an exhaustive attempt count'}
