"""Observable provenance only: links require explicit IDs or pointers.

Adjacency in a list and similar timestamps are never treated as causation or
proof that a model saw a tool result. Contexts may be enriched from formal runs.
"""
from __future__ import annotations
from ..inspection.adapters import resolve, pointer, text
from ..inspection.repository import digest
from ..core import ValidationError

CONTEXT_FIELDS=('checkpoint_id','weights_sha256','base_model','model_revision','panel_hash',
                'prompt','chat_template','tokenizer','tool_schema','retriever','context_builder',
                'parser','scorer','budget','decoding','seed','runtime','state_reset','harness_version')
CORE_REQUIRED=['/checkpoint_id','/base_model','/model_revision','/panel_hash','/prompt','/chat_template','/tokenizer','/retriever','/parser','/scorer','/budget','/decoding','/runtime','/state_reset']
MISSING={'unknown','not_recorded','未记录','未提供',''}

def known(v):
    if v is None:return False
    if isinstance(v,str):return v.strip().lower() not in MISSING
    if isinstance(v,dict):return bool(v) and all(known(x) for x in v.values())
    if isinstance(v,list):return bool(v) and all(known(x) for x in v)
    return True

def get(raw,p,default=None):
    try:return resolve(raw,p)
    except (ValueError,KeyError,TypeError,IndexError):return default

def context_of(doc):
    raw=doc['raw']; context={}; provenance={}
    # Only explicitly named containers are recognized; no private field guesses.
    for p in ('/versions','/protocol','/execution_context','/data/protocol','/data/execution_context',
              '/episode/protocol','/episode/execution_context'):
        v=get(raw,p)
        if isinstance(v,dict):
            for k,x in v.items():context[k]=x;provenance[k]=pointer(p,k)
    for k,x in doc.get('formal_context',{}).items():
        if k not in context:context[k]=x;provenance[k]='formal_metadata:'+k
    # Explicit v2 protocol aliases, with provenance retained. No inference of
    # tool schema/context-builder versions from unrelated configuration.
    inputs=context.get('input')
    if isinstance(inputs,dict):
        for dst,key in [('prompt','prompt_sha256'),('chat_template','chat_template_sha256'),('tokenizer','tokenizer_revision')]:
            if dst not in context and key in inputs:
                context[dst]=inputs[key];provenance[dst]=str(provenance.get('input','formal_metadata:input'))+'/'+key
    return {'values':context,'provenance':provenance,
            'missing':[k for k in CONTEXT_FIELDS if not known(context.get(k))],
            'limitation':'登记的版本元数据，不证明远程执行实际遵守；内容差异可展开核查。'}

def enumerate_calls(doc):
    calls=[]
    for t in doc['view']['trajectories']:
        for step in t['steps']:
            raw=get(doc['raw'],step['pointer'],{})
            if not isinstance(raw,dict):continue
            entries=[]
            if isinstance(raw.get('tool_call'),dict):entries.append((raw['tool_call'],pointer(step['pointer'],'tool_call')))
            if isinstance(raw.get('tool_calls'),list):entries.extend((x,pointer(pointer(step['pointer'],'tool_calls'),i)) for i,x in enumerate(raw['tool_calls']) if isinstance(x,dict))
            for call,p in entries:
                fun=call.get('function',call);fun=fun if isinstance(fun,dict) else {}
                ap=pointer(pointer(p,'function'),'arguments') if isinstance(call.get('function'),dict) else pointer(p,'arguments')
                calls.append({'id':call.get('id',raw.get('call_id')),'name':fun.get('name'),
                              'arguments':fun.get('arguments'),'arguments_pointer':ap,'pointer':p,
                              'step_pointer':step['pointer'],'step_index':step['index']})
    return calls

def lineage(doc):
    raw=doc['raw'];calls=enumerate_calls(doc);links=[];gaps=[];timings=[]; nodes=[]
    idmap={}
    for c in calls:
        if c['id'] is not None:idmap.setdefault(str(c['id']),[]).append(c['pointer'])
    for t in doc['view']['trajectories']:
        for s in t['steps']:
            v=get(raw,s['pointer'],{})
            if not isinstance(v,dict):continue
            p=s['pointer']; nodes.append({'pointer':p,'kind':s['kind'],'title':s['title']})
            call_id=v.get('tool_call_id',v.get('call_id'))
            if call_id is not None and ('tool_response' in v or s['kind']=='tool'):
                candidates=idmap.get(str(call_id),[])
                if len(candidates)==1:links.append({'from':candidates[0],'to':pointer(p,'tool_response') if 'tool_response' in v else p,'basis':'explicit_call_id','label':str(call_id)})
                else:gaps.append({'pointer':p,'message':'工具关联 ID 缺少唯一对应调用（可能缺失或重复）。'})
            if 'tool_response' in v and 'model_visible' in v:
                links.append({'from':pointer(p,'tool_response'),'to':pointer(p,'model_visible'),'basis':'recorded_same_step_pair','label':'同一步保存的 raw / visible；处理过程仍需单独记录'})
            for tp in ('context_transform','provenance'):
                tr=v.get(tp)
                if isinstance(tr,dict):
                    for key in ('source_pointer','target_pointer','input_pointer','output_pointer'):
                        ref=tr.get(key)
                        if ref is not None:
                            try:resolve(raw,ref)
                            except (ValueError,TypeError,KeyError,IndexError):gaps.append({'pointer':pointer(p,tp),'message':key+' 指向不存在的证据'});continue
                            links.append({'from':pointer(p,tp),'to':ref,'basis':'explicit_pointer','label':key})
            if 'tool_response' in v and 'model_visible' not in v:gaps.append({'pointer':p,'message':'未单独保存实际可见内容，不能确认 raw 进入了下一轮模型输入。'})
            for k in ('latency_ms','queue_ms','generation_ms','retrieval_ms','retry_wait_ms','processing_ms'):
                x=v.get(k)
                if isinstance(x,(int,float)) and not isinstance(x,bool) and x>=0:timings.append({'pointer':pointer(p,k),'metric':k,'value_ms':x})
    return {'calls':calls,'nodes':nodes,'links':links,'gaps':gaps,'timings':timings,
            'limitation':'连线为显式记录关联，不是推断依赖图。分段耗时可能重叠，不自动相加。'}

def validate_paths(paths):
    if not isinstance(paths,list) or len(paths)>100 or any(not isinstance(p,str) or not p.startswith('/') or len(p)<2 or '*' in p for p in paths):
        raise ValidationError('Allowed changes must be explicit non-root JSON pointers, no wildcards')
    return paths

def diff_values(a,b,path=''):
    if isinstance(a,dict) and isinstance(b,dict):
        out=[]
        for k in sorted(a.keys()|b.keys()):
            p=pointer(path,k)
            if k not in a:out.append({'pointer':p,'before':None,'after':b[k],'kind':'added'})
            elif k not in b:out.append({'pointer':p,'before':a[k],'after':None,'kind':'removed'})
            else:out.extend(diff_values(a[k],b[k],p))
        return out
    if digest(a)==digest(b):return []
    return [{'pointer':path,'before':a,'after':b,'kind':'changed'}]

def compare_context(a,b,allowed_changes,required_paths=None):
    validate_paths(allowed_changes)
    required_paths=required_paths if required_paths is not None else ['/checkpoint_id','/panel_hash','/retriever','/parser','/scorer','/budget','/decoding','/runtime']
    validate_paths(required_paths)
    diffs=diff_values(a,b)
    for d in diffs:d['expected']=any(d['pointer']==p or d['pointer'].startswith(p+'/') for p in allowed_changes)
    missing=[]
    for p in required_paths:
        if not known(get(a,p)) or not known(get(b,p)):missing.append(p)
    extra=[d for d in diffs if not d['expected']]
    return {'eligible':not extra and not missing,'differences':diffs,'unexpected_changes':extra,
            'missing_required':missing,'allowed_changes':allowed_changes,'required_paths':required_paths,
            'expected_change_observed':any(d['expected'] for d in diffs),
            'limitation':'仅核对提交的元数据；不是执行环境证明。训练预算仍需另行核查。'}
