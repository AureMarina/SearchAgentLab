"""Research purpose is independent of algorithm and training activity.

A reproduction may contain training, repeated evaluations and troubleshooting.
It never enters the main iteration lineage or automatic improvement aggregates.
Old records are unclassified, not guessed from names. Original evidence is read
through the existing Store; profiles and batch links live in a separate sidecar.
"""
from __future__ import annotations
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from ..core import ValidationError, NotFoundError, ident, text, integer, number, dumps, digest, clone, now

from ..read_cache import request_scope

TRACKS = ('main', 'reproduction', 'unassigned')
KINDS = ('agent_iteration', 'evaluation_calibration')


def diff_values(a: Any, b: Any, path: str = '') -> list[dict]:
    if isinstance(a, dict) and isinstance(b, dict):
        result = []
        for key in sorted(set(a) | set(b)):
            p = path + '/' + key.replace('~', '~0').replace('/', '~1')
            if key not in a or key not in b:
                result.append({'path': p, 'a': a.get(key), 'b': b.get(key), 'missing': 'a' if key not in a else 'b'})
            else:
                result.extend(diff_values(a[key], b[key], p))
        return result
    return [] if a == b else [{'path': path or '/', 'a': a, 'b': b}]


class StudioRepository:
    def __init__(self, store, methods=None, inspections=None):
        self.store = store
        self.methods = methods
        self.inspections = inspections
        self.lock = threading.RLock()
        self.path = store.data_dir / 'experiment_studio.sqlite3'
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA busy_timeout=10000')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS profiles(experiment_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_batches(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step INTEGER NOT NULL, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS batch_run_step ON training_batches(run_id,step);
        CREATE TABLE IF NOT EXISTS training_batch_summaries(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,step INTEGER NOT NULL,group_count INTEGER NOT NULL,trajectory_count INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS batch_summary_run ON training_batch_summaries(run_id,step);
        CREATE TABLE IF NOT EXISTS saved_reports(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, track TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspace_presentations(experiment_id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS harness_checks(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,evaluation_id TEXT,data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS harness_check_run ON harness_checks(run_id);
        ''')
        rows=self.conn.execute('SELECT b.data FROM training_batches b LEFT JOIN training_batch_summaries s ON s.id=b.id WHERE s.id IS NULL').fetchall()
        for row in rows:
            b=json.loads(row[0]);self._index_batch(b)
        self.conn.commit()
        from .workspace import WorkspaceIndex
        self.workspace_index = WorkspaceIndex(self)
        from .evaluation import EvaluationViews
        self.evaluation_views = EvaluationViews(self)

    def evaluation_summary(self, run_id='', evaluation_id=''):
        return self.evaluation_views.summary(run_id, evaluation_id)

    def evaluation_pair(self, a_id, b_id, axis='checkpoint'):
        return self.evaluation_views.pair(a_id, b_id, axis)

    def training_summary(self, run_id):
        return self.evaluation_views.training_summary(run_id)

    def training_rollout(self, run_id, batch_id, group_id, sample_id='', sample_index=None):
        return self.evaluation_views.training_rollout(run_id, batch_id, group_id, sample_id, sample_index)

    def register_agent_evaluation(self, record):
        return self.evaluation_views.register_agent_evaluation(record)

    def record_harness_check(self, record):
        return self.evaluation_views.record_harness_check(record)

    def workspace(self) -> dict:
        return self.workspace_index.workspace()

    def reference(self, experiment_id: str = '', run_id: str = '', baseline_run: str = '') -> dict:
        return self.workspace_index.reference(experiment_id, run_id, baseline_run)

    def register_presentation(self, data: dict) -> dict:
        return self.workspace_index.register_presentation(data)

    def _index_batch(self,b):
        self.conn.execute('INSERT OR IGNORE INTO training_batch_summaries VALUES (?,?,?,?,?)',
            (b['id'],b['run_id'],b['step'],len(b.get('groups',[])),sum(len(g.get('samples',[])) for g in b.get('groups',[]))))

    def close(self):
        with self.lock:
            self.conn.close()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    def profile(self, experiment_id: str) -> dict:
        exp = self.store._get('experiments', experiment_id)
        with self.lock:
            row = self.conn.execute('SELECT data FROM profiles WHERE experiment_id=?', (experiment_id,)).fetchone()
        if row:
            return json.loads(row[0])
        # Old experiment metadata remains untouched. Do not infer from its name.
        return {'experiment_id': experiment_id, 'track': 'unassigned', 'kind': None,
                'question': exp.get('objective', ''), 'change': exp.get('change', ''),
                'constants': exp.get('constants', ''), 'classification': 'not_registered',
                'participates_in_main': False, 'reproduction': {}}

    def register_profile(self, data: dict) -> dict:
        if not isinstance(data,dict): raise ValidationError('profile must be an object')
        d = clone(data)
        eid = ident(d.get('experiment_id'), 'profile.experiment_id')
        exp = self.store._get('experiments', eid)
        if d.get('track') not in TRACKS[:-1]:
            raise ValidationError('明确选择 main 或 reproduction；未分类不能自动当作主实验')
        d.setdefault('kind', 'agent_iteration')
        if d['kind'] not in KINDS: raise ValidationError('Unknown experiment kind')
        d.setdefault('question', exp.get('objective', ''))
        d.setdefault('change', exp.get('change', ''))
        d.setdefault('constants', exp.get('constants', ''))
        d.setdefault('allowed_changes', [])
        d.setdefault('reproduction', {})
        for key in ('question', 'change', 'constants'):
            if not isinstance(d[key], str) or len(d[key]) > 20000: raise ValidationError(key+' must be short text')
        if not isinstance(d['allowed_changes'], list) or not all(isinstance(x,str) and x.startswith('/') and x!='/' for x in d['allowed_changes']):
            raise ValidationError('allowed_changes must contain specific JSON pointers')
        if not isinstance(d['reproduction'], dict): raise ValidationError('reproduction must be an object')
        if d['track'] == 'reproduction':
            text(d['reproduction'].get('method_name'), 'reproduction.method_name')
            d['reproduction'].setdefault('protocol_mode', 'unknown')
            if d['reproduction']['protocol_mode'] not in ('original', 'adapted', 'unknown'):
                raise ValidationError('protocol_mode must be original/adapted/unknown')
            if d.get('parent_experiment_id') or d.get('baseline_evaluation_id'):
                raise ValidationError('文献复现不能接入主实验父子迭代或设为主线改进候选；可记录独立论文/配置引用')
        elif d['reproduction']:
            raise ValidationError('主实验的文献来源请写 source_reference，不要伪装为复现档案')
        for key in ('parent_experiment_id', 'baseline_evaluation_id'):
            if not d.get(key): continue
            if key == 'parent_experiment_id':
                parent = self.store._get('experiments', d[key])
            else:
                ev = self.store.evaluation(d[key]); parent = self.store._get('experiments', ev['experiment_id'])
            if parent['id'] == eid: raise ValidationError('主实验不能把自己作为父实验/外部基线')
            if parent['project_id'] != exp['project_id'] or self.profile(parent['id'])['track'] != 'main':
                raise ValidationError('主迭代只能引用同项目、已明确归类的主实验；复现结果不进入主实验增量')
        d['participates_in_main'] = d['track'] == 'main' and d['kind'] == 'agent_iteration'
        d['classification'] = 'explicit'
        d.setdefault('binding_origin', 'retrospective')
        if d['binding_origin'] not in ('retrospective','prospective'): raise ValidationError('Invalid binding_origin')
        d['project_id'] = exp['project_id']
        # Stable, immutable purpose and design. Notes/decisions have a separate append-only channel.
        with self.lock, self.conn:
            row = self.conn.execute('SELECT data FROM profiles WHERE experiment_id=?', (eid,)).fetchone()
            if row:
                old=json.loads(row[0])
                for key in ('created_at','profile_hash'):d.pop(key,None)
                clean={k:v for k,v in old.items() if k not in ('created_at','profile_hash')}
                if clean != d: raise ValidationError('实验用途/设计已冻结，不能改名把复现变成主实验；请新建实验')
                return old
            d['profile_hash']=digest(d);d['created_at']=now()
            self.conn.execute('INSERT INTO profiles VALUES (?,?)',(eid,dumps(d)))
        return d

    def create_experiment(self, data: dict) -> dict:
        if not isinstance(data,dict) or not isinstance(data.get('experiment'),dict) or not isinstance(data.get('profile'),dict): raise ValidationError('experiment and profile objects required')
        exp=clone(data['experiment']); ident(exp.get('id'),'experiment.id'); ident(exp.get('project_id'),'experiment.project_id')
        profile=clone(data['profile']);profile['experiment_id']=exp['id']
        # Roll back the new parent record if profile validation fails.
        with self.store.transaction():
            self.store.register_experiment(exp)
            result=self.register_profile(profile)
        return {'experiment':self.store.experiment(exp['id']),'profile':result}

    def catalog(self, project_id: str = '', track: str = 'main') -> dict:
        if track not in TRACKS: raise ValidationError('Invalid track')
        projects=self.store.projects()
        if project_id: self.store._get('projects',project_id)
        counts={t:0 for t in TRACKS};exps=[]
        for p in projects:
            if project_id and p['id']!=project_id:continue
            for e in self.store.experiments(p['id']):
                profile=self.profile(e['id']);counts[profile['track']]+=1
                if profile['track']!=track:continue
                runs=self.store.runs(p['id'],e['id'])
                with self.store.lock:
                    eval_ids=[row[0] for row in self.store.conn.execute('''SELECT ev.id FROM evaluations ev JOIN checkpoints c ON c.id=ev.checkpoint_id
                        JOIN run_ownership o ON o.run_id=c.run_id WHERE o.experiment_id=? ORDER BY ev.created_at DESC,ev.rowid DESC''',(e['id'],))]
                latest=self.store.evaluation(eval_ids[0]) if eval_ids else None
                exps.append({'experiment':e,'profile':profile,'runs':runs,'evaluation_count':len(eval_ids),'latest_evaluation':latest})
        return {'projects':projects,'project_id':project_id,'track':track,'counts':counts,'experiments':exps,
                'main_iteration_count':sum(1 for e in exps if e['profile']['participates_in_main']),
                'notice':'按研究用途隔离，不按是否训练或算法名称推断；旧实验请显式分类。'}

    def _context(self, ev: dict) -> dict:
        return {'checkpoint':ev['checkpoint']['id'], 'model':ev['run'].get('base_model'),
                'model_revision':ev['run'].get('model_revision'), 'protocol':ev['protocol'],
                'method_state':ev.get('method_state',{}).get('fingerprint')}

    def pair(self, a_id: str, b_id: str) -> dict:
        a,b=self.store.evaluation(a_id),self.store.evaluation(b_id)
        pa,pb=self.profile(a['experiment_id']),self.profile(b['experiment_id'])
        reasons=[]
        if pa['track']!=pb['track']:reasons.append('主实验与文献复现分开归档；不计算跨分区增量')
        if 'unassigned' in (pa['track'],pb['track']):reasons.append('实验用途尚未登记，只可查看原始结果')
        if a['project_id']!=b['project_id']:reasons.append('不同研究项目不能自动汇总或计算迭代收益')
        if pa.get('kind')!=pb.get('kind'):reasons.append('Agent 迭代与评测校准不能直接计算模型增量')
        # Reuse frozen-protocol and method-state checks; new UI does not loosen old scientific guards.
        comp=self.store.compare(a_id,b_id)
        reasons+=comp.get('reasons',[])
        if not a['readiness']['complete'] or not b['readiness']['complete']:
            reasons.append('执行协议未完整登记，仅展示原分；不能据此声称受控提升')
        if pa.get('kind')=='evaluation_calibration' or pb.get('kind')=='evaluation_calibration':
            reasons.append('评分校准记录只比较判定差异，不计作模型提升')
        if reasons:
            comp={'a':a,'b':b,'compatible':False,'reasons':list(dict.fromkeys(reasons)), 'full':False,
                  'groups':None,'macro_delta':None}
        comp['track']=pb['track'];comp['included_in_main_iteration']=bool(comp.get('compatible') and pb['participates_in_main'])
        comp['delta_kind']='复现内部 checkpoint 变化' if pb['track']=='reproduction' else '主实验固定条件对比'
        comp['configuration_differences']=diff_values(self._context(a),self._context(b))
        # Delta is never reported as statistical or causal proof.
        comp['net_correct_change']=(b['stats']['answer_correct']-a['stats']['answer_correct']) if comp.get('compatible') and comp.get('full') else None
        return comp

    @request_scope
    def dynamics(self, run_id: str, panel_id: str, selections: dict | None = None, baseline: str = 'previous') -> dict:
        if baseline not in ('previous','initial'): raise ValidationError('baseline must be previous/initial')
        ov=self.store.overview(run_id,panel_id)
        matrix=self.store.matrix(run_id,panel_id,selections=selections)
        profile=self.profile(ov['experiment']['id'])
        previous=None;first=None;changes=[]
        for column in matrix['columns']:
            ev=column['evaluation'];reference=first if baseline=='initial' else previous
            change=None
            if ev and reference and reference['id']!=ev['id']:
                change=self.evaluation_pair(reference['id'],ev['id'],'checkpoint')
                change={k:v for k,v in change.items() if k not in ('a','b','items')}
                change['reference_id']=reference['id']
            changes.append(change)
            if ev:
                previous=ev
                if first is None:first=ev
        matrix['comparable']=any(change is not None for change in changes) and all(change is None or change['compatible'] for change in changes)
        for row in matrix['rows']:
            for cell in row['cells']:
                if cell: cell['change']=None # frontend uses explicitly selected A/B; no hidden previous-column assumption
        with self.lock:
            batches=[dict(r) for r in self.conn.execute('SELECT id,step,group_count,trajectory_count FROM training_batch_summaries WHERE run_id=? ORDER BY step,id',(run_id,))]
        return {'overview':ov,'matrix':matrix,'profile':profile,'changes':changes,
                'batches':batches,
                'method_summary':self.method_summary(run_id)}

    def method_summary(self, run_id: str) -> dict:
        if not self.methods:return {'counts':{},'objects':[]}
        catalog=self.methods.catalog({'run_id':run_id})
        return {'counts':catalog.get('counts',{}),'objects':catalog.get('objects',[]),
                'note':'仅显示明确关联到该运行的过程记录；采样声明不证明参数已更新。'}

    def report_catalog(self, project_id: str, track: str) -> dict:
        cat=self.catalog(project_id,track);exps={x['experiment']['id']:x for x in cat['experiments']}
        rows=[]
        with self.store.lock:
            ids=[r[0] for r in self.store.conn.execute('''SELECT e.id FROM evaluations e
                JOIN checkpoints c ON c.id=e.checkpoint_id JOIN run_ownership o ON o.run_id=c.run_id
                JOIN experiments ex ON ex.id=o.experiment_id
                WHERE ex.project_id=? ORDER BY e.created_at DESC,e.rowid DESC''',(project_id,))]
        for eid in ids:
            ev=self.store.evaluation(eid)
            if ev['experiment_id'] in exps:
                rows.append({'evaluation':ev,'experiment':exps[ev['experiment_id']]['experiment'],
                             'profile':exps[ev['experiment_id']]['profile']})
        with self.lock:
            saved=[json.loads(r[0]) for r in self.conn.execute('SELECT data FROM saved_reports WHERE project_id=? AND track=? ORDER BY rowid DESC',(project_id,track))]
        return {'evaluations':rows,'saved_reports':saved,'project_id':project_id,'track':track}

    @request_scope
    def results(self, run_id: str, panel_id: str = '', b_id: str = '', a_id: str = '', scope: str = '') -> dict:
        """Result-first read model: never loads metrics, batches or method bodies.

        Explicit run/panel ownership is checked before reading any selected report.
        The default is the latest saved evaluation, never the highest score.
        """
        run = self.store.run(run_id)
        with self.store.lock:
            ids = [r[0] for r in self.store.conn.execute(
                """SELECT e.id FROM evaluations e JOIN checkpoints c ON c.id=e.checkpoint_id
                   WHERE c.run_id=? ORDER BY e.created_at DESC,e.rowid DESC""", (run_id,))]
        if b_id and b_id not in ids:
            raise ValidationError('Selected evaluation does not belong to this run')
        if a_id and a_id not in ids:
            raise ValidationError('Comparison evaluation does not belong to this run; use Reports for cross-run comparisons')
        all_evaluations = [self.store.evaluation(eid) for eid in ids]
        if scope == 'diagnostic':
            from .trace_browser import describe
            all_evaluations = [e for e in all_evaluations if describe(e)['scope'] == 'diagnostic']
        if not panel_id:
            panel_id = next((e['panel_id'] for e in all_evaluations if not b_id or e['id'] == b_id), '')
        if not panel_id:
            panel_id = next((p['id'] for p in self.store.panels() if scope != 'diagnostic' or p.get('split') != 'test'), '')
        panel = self.store.panel(panel_id) if panel_id else None
        evaluations = [e for e in all_evaluations if e['panel_id'] == panel_id]
        selected = next((e for e in evaluations if e['id'] == b_id), None) if b_id else next(iter(evaluations), None)
        if b_id and not selected:
            raise ValidationError('Selected evaluation does not belong to this fixed panel')
        if a_id and not any(e['id'] == a_id for e in evaluations):
            raise ValidationError('Comparison evaluation does not belong to this fixed panel')
        if a_id and selected and selected['id'] == a_id:
            raise ValidationError('Choose two distinct evaluations')
        report = self.report(selected['id'], a_id) if selected else None
        return {'run': run, 'panel': panel, 'evaluations': evaluations,
                'report': report, 'selection_rule': 'explicit' if b_id else 'latest_record_not_best'}

    def report(self, b_id: str, a_id: str = '') -> dict:
        b=self.store.evaluation(b_id);a=self.store.evaluation(a_id) if a_id else None
        pair=self.pair(a_id,b_id) if a_id else None
        panel=self.store.panel(b['panel_id'])
        maps=[]
        for ev in (a,b):
            with self.store.lock:
                rows=[json.loads(x[0]) for x in self.store.conn.execute('SELECT data FROM trajectory_summaries WHERE evaluation_id=?',(ev['id'],))] if ev else []
            maps.append({(r['dataset'],r['sample_id']):r for r in rows})
        items=[]
        for s in panel['samples']:
            key=s['dataset'],s['id'];x,y=[m.get(key) for m in maps]
            group='unpaired'
            if pair and pair['compatible'] and x and y:
                vx,vy=x['scores'].get('em'),y['scores'].get('em')
                group='unscored' if vx is None or vy is None else 'rescued' if vx==0 and vy==1 else 'harmed' if vx==1 and vy==0 else 'both_correct' if vy==1 else 'both_wrong'
            items.append({'sample':s,'a':self.store._summary(x) if x else None,'b':self.store._summary(y) if y else None,'group':group})
        return {'a':a,'b':b,'profile':self.profile(b['experiment_id']),'pair':pair,'items':items,
                'summary_is_generated_conclusion':False,'source':'saved executions and existing scores; no inference or rescoring'}

    def save_report(self, data: dict) -> dict:
        ident(data.get('id'),'report.id');text(data.get('name'),'report.name')
        report=self.report(data['b'],data.get('a',''))
        if data.get('axis') and not data.get('a'):
            raise ValidationError('An axis-specific comparison report requires a and b')
        if data.get('a'):
            axis=data.get('axis') or 'checkpoint'
            pair=self.evaluation_pair(data['a'],data['b'],axis)
            report.update(pair=pair,items=pair['items'],axis=axis)
        if report['pair'] and not report['pair']['compatible']: raise ValidationError('不可比的记录只可浏览差异，不能冻结为对照报告')
        d={**clone(data),'created_at':now(),'snapshot':report}
        d['snapshot_hash']=digest(report);p=report['profile']
        with self.lock,self.conn:
            old=self.conn.execute('SELECT data FROM saved_reports WHERE id=?',(d['id'],)).fetchone()
            if old:raise ValidationError('报告 ID 已存在；新快照使用新 ID，不覆盖历史报告')
            self.conn.execute('INSERT INTO saved_reports VALUES (?,?,?,?)',(d['id'],report['b']['project_id'],p['track'],dumps(d)))
        return d

    def saved_report(self, rid: str) -> dict:
        with self.lock:
            row=self.conn.execute('SELECT data FROM saved_reports WHERE id=?',(rid,)).fetchone()
        if not row:raise NotFoundError('Report does not exist')
        return json.loads(row[0])

    def record_batch(self, record: dict) -> dict:
        if not isinstance(record,dict): raise ValidationError('batch must be an object')
        d=clone(record);supplied_hash=d.pop('content_hash',None)
        ident(d.get('id'),'batch.id');integer(d.get('step'),'batch.step')
        run=self.store.run(d.get('run_id',''))
        from .training_stage import validate_stage,validate_sft_sample,stage_of
        if 'training_stage' in d:validate_stage(d['training_stage'])
        batch_stage=d.get('training_stage') or stage_of(run)['value']
        if run.get('run_type') in ('evaluation','harness_check'):
            raise ValidationError('Training batches require a training run, not an independent evaluation/check run')
        if not isinstance(d.get('groups'),list):raise ValidationError('groups must be an array of explicitly linked rollout groups')
        seen=set()
        for g in d['groups']:
            if not isinstance(g,dict):raise ValidationError('Each group must be an object')
            gid=ident(g.get('id'),'group.id')
            if gid in seen:raise ValidationError('Duplicate rollout group ID in batch')
            seen.add(gid)
            if not isinstance(g.get('samples'),list):raise ValidationError('group.samples must be an array')
            sample_ids=[str(s['id']) for s in g['samples'] if isinstance(s,dict) and s.get('id') not in (None,'')]
            if len(set(sample_ids))!=len(sample_ids):raise ValidationError('Duplicate sample ID within training group')
            for s in g['samples']:
                if not isinstance(s,dict):raise ValidationError('Sample must be an object')
                self.evaluation_views.training_reference(s,run)
                if batch_stage=='sft':validate_sft_sample(s)
                for metric in ('reward','advantage','loss'):
                    if metric in s:number(s[metric],metric)
                if 'token_ids' in s:
                    if not isinstance(s['token_ids'],list):raise ValidationError('token_ids must be an integer array')
                    for token in s['token_ids']:integer(token,'token_id')
                if 'response_mask' in s and (not isinstance(s['response_mask'],list) or any(x not in (0,1) for x in s['response_mask'])):
                    raise ValidationError('response_mask must be a 0/1 array')
                if 'token_ids' in s and 'response_mask' in s and len(s['token_ids'])!=len(s['response_mask']):
                    raise ValidationError('token_ids and response_mask must have identical lengths')
        d['content_hash']=digest(d)
        if supplied_hash is not None and supplied_hash!=d['content_hash']:raise ValidationError('Batch content hash does not match')
        with self.lock,self.conn:
            row=self.conn.execute('SELECT data FROM training_batches WHERE id=?',(d['id'],)).fetchone()
            if row:
                if json.loads(row[0])!=d:raise ValidationError('Batch record is immutable')
                return d
            self.conn.execute('INSERT INTO training_batches VALUES (?,?,?,?)',(d['id'],run['id'],d['step'],dumps(d)))
            self._index_batch(d)
        return d

    def batches(self, run_id: str, step: int) -> dict:
        self.store.run(run_id);integer(step,'step')
        with self.lock:
            rows=[json.loads(r[0]) for r in self.conn.execute('SELECT data FROM training_batches WHERE run_id=? AND step=? ORDER BY id',(run_id,step))]
        return {'run_id':run_id,'step':step,'batches':rows,'notice':'实际日志关联，不从最终 messages 重建 token 或梯度；没有记录时返回空。'}
