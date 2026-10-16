"""Frozen contracts and a deliberately bounded deterministic schema validator.

Unsupported JSON Schema keywords produce INSUFFICIENT, never silently PASS.
No remote references, dynamic expressions, regexes or executable assertions.
"""
from __future__ import annotations
import math
from typing import Any
from ..core import ValidationError
from ..inspection.adapters import pointer
from ..inspection.repository import digest

STATES = ('pass', 'fail', 'insufficient', 'not_applicable')
SCHEMA_KEYS = {'type','required','properties','additionalProperties','items','enum','const',
               'minimum','maximum','minLength','maxLength','minItems','maxItems',
               'title','description','$schema'}
TYPES = {'object':dict,'array':list,'string':str,'boolean':bool,'null':type(None)}

def matches_type(v, t):
    if t == 'number': return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
    if t == 'integer': return isinstance(v,int) and not isinstance(v,bool)
    return t in TYPES and isinstance(v,TYPES[t])

def json_equal(a,b):
    if matches_type(a,'number') and matches_type(b,'number'): return a==b
    if type(a)!=type(b):return False
    if isinstance(a,dict):return a.keys()==b.keys() and all(json_equal(a[k],b[k]) for k in a)
    if isinstance(a,list):return len(a)==len(b) and all(json_equal(x,y) for x,y in zip(a,b))
    return a==b

def schema_check(value: Any, schema: Any, path: str = '') -> dict:
    errors, unknown = [], []
    count = 0
    def walk(v,s,p,depth):
        nonlocal count
        count += 1
        if count > 10000 or depth > 60:
            if not unknown or unknown[-1].get('message') != 'schema safety limit':
                unknown.append({'pointer':p,'message':'schema safety limit'})
            return
        if isinstance(s,bool):
            if not s: errors.append({'pointer':p,'message':'schema=false'})
            return
        if not isinstance(s,dict): unknown.append({'pointer':p,'message':'schema must be an object or boolean'}); return
        for k in s.keys()-SCHEMA_KEYS: unknown.append({'pointer':p,'message':'Unsupported JSON Schema keyword: '+k})
        typ=s.get('type')
        if typ is not None:
            ts=typ if isinstance(typ,list) else [typ]
            if any(not isinstance(t,str) or t not in (*TYPES,'number','integer') for t in ts) or not ts:
                unknown.append({'pointer':p,'message':'Invalid schema type'}); return
            if not any(matches_type(v,t) for t in ts):
                errors.append({'pointer':p,'message':'type must be '+str(typ)}); return
        if 'enum' in s:
            if not isinstance(s['enum'],list): unknown.append({'pointer':p,'message':'enum must be an array'})
            elif not any(json_equal(v,x) for x in s['enum']):errors.append({'pointer':p,'message':'not in enum'})
        if 'const' in s and not json_equal(v,s['const']):errors.append({'pointer':p,'message':'const mismatch'})
        if 'additionalProperties' in s and not isinstance(s['additionalProperties'],(bool,dict)):
            unknown.append({'pointer':p,'message':'invalid additionalProperties'})
        if isinstance(v,dict):
            required=s.get('required',[]); props=s.get('properties',{})
            if not isinstance(required,list) or any(not isinstance(k,str) for k in required):unknown.append({'pointer':p,'message':'invalid required'})
            else:
                for k in required:
                    if k not in v:errors.append({'pointer':p,'message':'missing required field: '+k})
            if not isinstance(props,dict):unknown.append({'pointer':p,'message':'invalid properties'});props={}
            for k,x in v.items():
                if k in props:walk(x,props[k],pointer(p,k),depth+1)
                elif s.get('additionalProperties') is False:errors.append({'pointer':pointer(p,k),'message':'additional property forbidden'})
                elif isinstance(s.get('additionalProperties'),dict):walk(x,s['additionalProperties'],pointer(p,k),depth+1)
        if isinstance(v,list) and 'items' in s:
            if len(v)>10000:unknown.append({'pointer':p,'message':'array schema limit exceeded'})
            for i,x in enumerate(v[:10000]):walk(x,s['items'],pointer(p,i),depth+1)
        for k,op,actual in [('minimum',lambda a,b:a>=b,v),('maximum',lambda a,b:a<=b,v),
                            ('minLength',lambda a,b:a>=b,len(v) if isinstance(v,str) else None),
                            ('maxLength',lambda a,b:a<=b,len(v) if isinstance(v,str) else None),
                            ('minItems',lambda a,b:a>=b,len(v) if isinstance(v,list) else None),
                            ('maxItems',lambda a,b:a<=b,len(v) if isinstance(v,list) else None)]:
            if k not in s:continue
            if k in ('minimum','maximum') and not matches_type(v,'number'):continue
            if actual is None:continue
            bound=s[k]
            if (k not in ('minimum','maximum') and (type(bound)!=int or bound<0)) or not matches_type(bound,'number'):unknown.append({'pointer':p,'message':'invalid bound '+k})
            elif not op(actual,bound):errors.append({'pointer':p,'message':f'{k}={bound} violated'})
    walk(value,schema,path,0)
    return {'status':'fail' if errors else 'insufficient' if unknown else 'pass','errors':errors,'unsupported':unknown}

def validate_contract(c: dict) -> dict:
    if not isinstance(c,dict):raise ValidationError('Contract must be an object')
    allowed={'id','version','name','risk','mode','required_tools','forbidden_tools','required_skills',
             'output','tool_schemas','assertions','hard_gates','notes','source_case'}
    if c.keys()-allowed:raise ValidationError('Unknown contract keys: '+', '.join(sorted(c.keys()-allowed)))
    for k in ('id','version','name'):
        if not isinstance(c.get(k),str) or not c[k].strip() or len(c[k])>180:raise ValidationError('Contract requires '+k)
    if c.get('mode','shadow') not in ('shadow','warning','blocking'):raise ValidationError('Invalid gate mode')
    if c.get('risk','normal') not in ('low','normal','high','critical'):raise ValidationError('Invalid risk')
    for k in ('required_tools','forbidden_tools','required_skills','hard_gates'):
        arr=c.get(k,[])
        if not isinstance(arr,list) or len(arr)>100 or any(not isinstance(x,str) or not x for x in arr):raise ValidationError('Invalid '+k)
    if set(c.get('required_tools',[])) & set(c.get('forbidden_tools',[])):raise ValidationError('Tool cannot be required and forbidden')
    output=c.get('output',{})
    if not isinstance(output,dict) or output.keys()-{'format','schema'}:raise ValidationError('Invalid output contract')
    if output.get('format','any') not in ('any','json','answer_tag'):raise ValidationError('output.format must be any/json/answer_tag')
    if not isinstance(c.get('tool_schemas',{}),dict):raise ValidationError('tool_schemas must be an object')
    if len(c.get('tool_schemas',{}))>100:raise ValidationError('Too many tool schemas')
    assertions=c.get('assertions',[])
    if not isinstance(assertions,list) or len(assertions)>100:raise ValidationError('Invalid assertions')
    seen=set()
    for a in assertions:
        if not isinstance(a,dict) or a.keys()-{'id','layer','pointer','op','expected','tolerance'}:raise ValidationError('Invalid assertion')
        if not isinstance(a.get('id'),str) or not a['id'] or a['id'] in seen:raise ValidationError('Assertion IDs must be unique')
        seen.add(a['id'])
        if a.get('layer') not in ('Outcome','Decision','Action'):raise ValidationError('Invalid assertion layer')
        if not isinstance(a.get('pointer'),str) or not a['pointer'].startswith('/'):raise ValidationError('Assertion needs a JSON pointer')
        if a.get('op') not in ('exists','equals','number_close'):raise ValidationError('Unsupported assertion op')
        if a['op']!='exists' and 'expected' not in a:raise ValidationError('Assertion requires expected')
        if a['op']=='number_close':
            if not matches_type(a.get('expected'),'number') or not matches_type(a.get('tolerance',0),'number') or a.get('tolerance',0)<0:raise ValidationError('Invalid numeric assertion')
    return c

def contract_template(case_id: str = 'case-contract') -> dict:
    return {'id':case_id,'version':'v1','name':'填写本题执行契约','risk':'normal','mode':'shadow',
            'required_tools':[],'forbidden_tools':[],'required_skills':[],
            'output':{'format':'any'},'tool_schemas':{},'assertions':[],'hard_gates':[],
            'notes':'历史轨迹回填是 retrospective；不反向改变原评测规则。参考推理链不是唯一路径。'}
