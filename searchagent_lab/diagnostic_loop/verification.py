"""Safe local component experiments and conservative imported-run comparisons.

No GPU/retrieval calls, exec, shell commands, arbitrary imports, or overwrites.
Auxiliary evidence experiments are explicitly excluded from autonomous uplift.
"""
from __future__ import annotations
import math
import re
from ..core import ValidationError
from ..inspection.repository import strict_json, digest
from ..inspection.adapters import resolve
from .contracts import schema_check
from .evidence import compare_context, validate_paths

OPERATIONS=('answer_parser','tool_schema','context_render')

def component(value, operation, config):
    if not isinstance(config,dict):raise ValidationError('Component config must be an object')
    if operation=='answer_parser':
        allowed={'mode','tag','pointer'}
        if config.keys()-allowed:raise ValidationError('Unknown parser config')
        mode=config.get('mode','answer_tag')
        if not isinstance(value,str):raise ValidationError('Parser input must be the recorded string')
        if mode=='strip':return {'parsed':value.strip(),'status':'pass'}
        if mode=='answer_tag':
            tag=config.get('tag','answer')
            if not isinstance(tag,str) or not re.fullmatch('[a-zA-Z][a-zA-Z0-9_]{0,40}',tag):raise ValidationError('Unsafe / invalid tag')
            vals=re.findall('<'+tag+'>(.*?)</'+tag+'>',value,re.S)
            if len(vals)!=1 or value.count('<'+tag+'>')!=1 or value.count('</'+tag+'>')!=1:
                return {'parsed':None,'status':'fail','error':'Expected exactly one complete tag'}
            return {'parsed':vals[0].strip(),'status':'pass'}
        if mode=='json_pointer':
            try:
                obj=strict_json(value);p=config.get('pointer','/answer');ans=resolve(obj,p)
                return {'parsed':ans,'status':'pass'}
            except (ValueError,TypeError,KeyError,IndexError):return {'parsed':None,'status':'fail','error':'Invalid JSON or missing field'}
        raise ValidationError('Unknown built-in parser mode')
    if operation=='tool_schema':
        if config.keys()-{'schema'} or 'schema' not in config:raise ValidationError('tool_schema requires only schema')
        try:obj=strict_json(value) if isinstance(value,str) else value
        except (ValueError,RecursionError):return {'status':'fail','errors':[{'pointer':'','message':'Input is not strict JSON'}]}
        return schema_check(obj,config['schema'])
    if operation=='context_render':
        if config.keys()-{'max_chars','max_documents','selection','text_key','separator'}:raise ValidationError('Unknown context-render config')
        budget=config.get('max_chars',2048);count=config.get('max_documents',5)
        if type(budget)!=int or not 1<=budget<=1000000 or type(count)!=int or not 1<=count<=1000:raise ValidationError('Invalid character/document budget')
        selection=config.get('selection','first')
        if selection not in ('first','last'):raise ValidationError('selection must be first/last, no answer-based ranking')
        sep=config.get('separator','\n\n');key=config.get('text_key','text')
        if not isinstance(sep,str) or len(sep)>100 or not isinstance(key,str):raise ValidationError('Invalid renderer config')
        docs=value if isinstance(value,list) else [value]
        chosen=list(enumerate(docs))[:count] if selection=='first' else list(enumerate(docs))[-count:]
        parts=[]
        for i,d in chosen:
            val=d.get(key) if isinstance(d,dict) else d
            if not isinstance(val,str):raise ValidationError('Renderer needs strings or objects with configured text_key')
            parts.append(val)
        full=sep.join(parts)
        return {'status':'pass','rendered':full[:budget],'input_chars':len(full),'visible_chars':min(len(full),budget),
                'truncated':len(full)>budget,'selected_indices':[i for i,d in chosen],
                'budget_unit':'Unicode characters, NOT tokens',
                'note':'Built-in diagnostic renderer, not a claim to reproduce the production context builder.'}
    raise ValidationError('Unknown local component; remote execution must be performed by your evaluator')

def execute_component(doc,task):
    if task.get('mode')!='component':raise ValidationError('Only component tasks execute locally')
    cfg=task['component'];p=cfg['input_pointer']
    try:val=resolve(doc['raw'],p)
    except (ValueError,KeyError,TypeError,IndexError) as e:raise ValidationError('Component input evidence missing') from e
    if digest(val)!=cfg['input_hash']:raise ValidationError('Component input changed')
    a=component(val,cfg['operation'],cfg['baseline']);b=component(val,cfg['operation'],cfg['candidate'])
    budget_same=None
    if cfg['operation']=='context_render':budget_same=cfg['baseline'].get('max_chars',2048)==cfg['candidate'].get('max_chars',2048) and cfg['baseline'].get('max_documents',5)==cfg['candidate'].get('max_documents',5)
    return {'kind':'component_result','execution':'executed_locally','operation':cfg['operation'],'task_id':task['id'],
            'input_hash':digest(val),'input_pointer':p,'baseline':a,'candidate':b,'changed':digest(a)!=digest(b),
            'matched_budgets':budget_same,'causal_claim':False,'autonomous_uplift':None,
            'limitation':'组件输出变化不是模型能力提升，也不自动支持根因。须接从头回归验证。'}

def valid_score(v):return type(v) in (int,float) and v in (0,1)

def regression(baseline,candidate,expected_cases):
    """Same-key join, no zero-filling; repeated rollouts belong in reliability."""
    def key(r):
        if not isinstance(r,dict) or not isinstance(r.get('dataset'),str) or not isinstance(r.get('sample_id'),str):raise ValidationError('Each result requires dataset and sample_id strings')
        return (r['dataset'],r['sample_id'])
    def index(items):
        if not isinstance(items,list) or len(items)>10000:raise ValidationError('Invalid case results')
        m={}
        for r in items:
            k=key(r)
            if k in m:raise ValidationError('Duplicate case in one arm; repeat runs use separate repeat entries')
            v=r.get('em')
            if v is not None and not valid_score(v):raise ValidationError('EM must be 0, 1, or null, not bool/percentage')
            m[k]=r
        return m
    expected=[]
    for x in expected_cases:expected.append(key(x))
    if not expected or len(set(expected))!=len(expected):raise ValidationError('Expected case keys must be nonempty and unique')
    a,b=index(baseline),index(candidate)
    if (a.keys()|b.keys())-set(expected):raise ValidationError('Result contains a case outside the frozen task manifest')
    rows=[];counts={k:0 for k in ('repaired','regressed','both_correct','both_wrong','unscored','missing')}
    for k in expected:
        x,y=a.get(k),b.get(k);sx=x.get('em') if x else None;sy=y.get('em') if y else None
        state='missing' if x is None or y is None else 'unscored' if sx is None or sy is None else 'repaired' if sx==0 and sy==1 else 'regressed' if sx==1 and sy==0 else 'both_correct' if sx==sy==1 else 'both_wrong'
        counts[state]+=1;rows.append({'dataset':k[0],'sample_id':k[1],'baseline':x,'candidate':y,'change':state})
    complete=counts['missing']==counts['unscored']==0
    per_dataset=[]
    for ds in sorted({k[0] for k in expected}):
        part=[r for r in rows if r['dataset']==ds];n=len(part)
        ss=[]
        for label in ('baseline','candidate'):
            scored=[r[label]['em'] for r in part if r[label] is not None and r[label].get('em') is not None]
            ss.append({'correct':sum(scored),'scored':len(scored),'total':n,'em':sum(scored)/n if len(scored)==n else None})
        per_dataset.append({'dataset':ds,'baseline':ss[0],'candidate':ss[1]})
    macro_a=sum(x['baseline']['em'] for x in per_dataset)/len(per_dataset) if complete else None
    macro_b=sum(x['candidate']['em'] for x in per_dataset)/len(per_dataset) if complete else None
    return {'counts':counts,'items':rows,'complete':complete,'datasets':per_dataset,'macro_baseline':macro_a,
            'macro_candidate':macro_b,'macro_delta':macro_b-macro_a if complete else None,
            'note':'未评分和缺题不补零；正确数以固定分母展示，宏平均仅完整覆盖时计算。'}

def validate_external_result(task,result):
    if not isinstance(result,dict):raise ValidationError('External result must be an object')
    if result.get('task_hash')!=task['task_hash'] or result.get('task_id')!=task['id']:raise ValidationError('Result must bind to exact frozen task ID/hash')
    if task['mode']=='component':raise ValidationError('Use local component execution, not external run import')
    if result.get('execution')!='executed_externally':raise ValidationError('External execution must be explicitly declared, not merely planned')
    arms=[]
    for name in ('baseline','candidate'):
        arm=result.get(name)
        if not isinstance(arm,dict):raise ValidationError('Missing arm '+name)
        for f in ('evaluation_id','trace_sha256','context','cases','groundtruth_assisted','from_empty_state'):
            if f not in arm:raise ValidationError('Missing '+name+'.'+f)
        if not isinstance(arm['evaluation_id'],str) or not arm['evaluation_id']:raise ValidationError('Invalid evaluation ID')
        if not isinstance(arm['trace_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',arm['trace_sha256']):raise ValidationError('Need recorded trace SHA256')
        if not isinstance(arm['context'],dict) or type(arm['groundtruth_assisted'])!=bool or type(arm['from_empty_state'])!=bool:raise ValidationError('Invalid context/reset/assistance declarations')
        arms.append(arm)
    if arms[0]['evaluation_id']==arms[1]['evaluation_id']:raise ValidationError('Comparison needs two distinct evaluation records')
    diff=compare_context(arms[0]['context'],arms[1]['context'],task['allowed_changes'],task['required_paths'])
    # Detect changing both arms away from the planned control context.
    base_check=compare_context(task['baseline_context'],arms[0]['context'],[],task['required_paths'])
    if not base_check['eligible']:diff['eligible']=False;diff['baseline_drift']=base_check
    reg=regression(arms[0]['cases'],arms[1]['cases'],task['expected_cases'])
    autonomous=task['mode']=='autonomous' and all(x['from_empty_state'] and not x['groundtruth_assisted'] for x in arms)
    measurement_changed=any(d['pointer'].split('/')[1] in ('parser','scorer') for d in diff['differences'])
    eligible=diff['eligible'] and reg['complete'] and autonomous and not measurement_changed
    return {'kind':'external_verification_result','execution':'declared_external_not_attested',
            'task_id':task['id'],'measurement_changed':measurement_changed,'comparison':diff,'regression':reg,'autonomous_comparison_eligible':eligible,
            'autonomous_macro_delta':reg['macro_delta'] if eligible else None,
            'causal_claim':False,'source_artifacts_verified':False,
            'note':'保存外部执行声明与结果，不假装已经加载权重或核验外部 trace 文件。前缀/参考辅助实验不得记为自主提升。'}

def reliability(runs,required_paths):
    if not isinstance(runs,list) or len(runs)>1000:raise ValidationError('Invalid repeat runs')
    if not runs:return {'status':'insufficient','n':0,'note':'没有重复运行记录'}
    ids=set();case=None;comparison=[]
    for r in runs:
        if not isinstance(r,dict):raise ValidationError('Repeat run must be an object')
        for f in ('run_id','dataset','sample_id','context','em','independent_reset'):
            if f not in r:raise ValidationError('Repeat run needs '+f)
        if not isinstance(r['run_id'],str) or not r['run_id'] or r['run_id'] in ids:raise ValidationError('Independent repeat run IDs must be unique')
        ids.add(r['run_id']);k=(r['dataset'],r['sample_id'])
        if case is not None and k!=case:raise ValidationError('Reliability is for the same case, not pooled questions')
        case=k
        if r['em'] is not None and not valid_score(r['em']):raise ValidationError('Invalid repeat EM')
        if type(r['independent_reset'])!=bool:raise ValidationError('independent_reset must be explicit boolean')
        comparison.append(compare_context(runs[0]['context'],r['context'],['/seed'],required_paths))
    if any(not x['eligible'] for x in comparison) or any(not r['independent_reset'] for r in runs):
        return {'status':'insufficient','n':len(runs),'context_checks':comparison,'note':'配置不完整/变化超过 seed，或未声明每次独立重置。'}
    scores=[r['em'] for r in runs if r['em'] is not None]
    times=[r.get('latency_ms') for r in runs if type(r.get('latency_ms')) in (float,int) and math.isfinite(r['latency_ms']) and r['latency_ms']>=0]
    def q(p):
        v=sorted(times);x=(len(v)-1)*p;i=int(x);return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(x-i) if v else None
    return {'status':'reported' if len(runs)>=3 and len(scores)==len(runs) else 'insufficient',
            'n':len(runs),'scored':len(scores),'correct':sum(scores),'success_rate':sum(scores)/len(scores) if scores else None,
            'all_pass':len(scores)==len(runs) and all(scores),'latency_n':len(times),'p50_ms':q(.5) if times else None,'p90_ms':q(.9) if times else None,
            'note':'来自外部独立运行声明；分位数为线性插值。少样本不稳定，>=3 不是统计显著性保证；从不由单轨迹推算。'}
