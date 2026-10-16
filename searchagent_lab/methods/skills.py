"""Read-only SkillRL format adapter; supports actual Search query_type_skills.
No implicit default embedding mode, no inferred skill invocation boundaries.
"""
from __future__ import annotations
import difflib
from ..inspection.repository import digest, encode
def ptr(base,key):return base+'/'+str(key).replace('~','~0').replace('/','~1')
from ..core import ValidationError

BANK_KEYS={'general_skills','query_type_skills','task_specific_skills','common_mistakes'}
def looks_like_bank(d):return isinstance(d,dict) and bool(set(d)&BANK_KEYS)

def adapt_bank(raw):
    if not isinstance(raw,dict):raise ValidationError('SkillBank root must be an object')
    members=[];warnings=[]
    def items(vals,group,category,path):
        if not isinstance(vals,list):
            warnings.append(path+' is not an array; retained in raw JSON');return
        for i,value in enumerate(vals):
            p=ptr(path,i)
            if not isinstance(value,dict):
                warnings.append(p+' is not a skill object; retained');continue
            sid=value.get('skill_id') if group!='common_mistakes' else value.get('mistake_id')
            # A stable source pointer is not an invented original skill ID.
            missing=not isinstance(sid,str) or not sid
            display=sid if not missing else '[原始 ID 未记录]'
            key=group+'/'+(category+'/' if category else '')+(sid if not missing else '@'+str(i))
            members.append({'key':key,'source_id':None if missing else sid,'title':value.get('title') or value.get('name') or display,
                'group':group,'category':category,'pointer':p,'content_hash':digest(value),'content':value,
                'principle':value.get('principle'),'when_to_apply':value.get('when_to_apply'),
                'source_id_missing':missing})
    if 'general_skills' in raw:items(raw['general_skills'],'general_skills','', '/general_skills')
    for group in ('query_type_skills','task_specific_skills'):
        if group not in raw:continue
        groups=raw[group]
        if not isinstance(groups,dict):warnings.append(group+' is not an object; retained');continue
        for category,vals in groups.items():items(vals,group,str(category),ptr('/'+group,category))
    if 'common_mistakes' in raw:items(raw['common_mistakes'],'common_mistakes','','/common_mistakes')
    keys=[m['key'] for m in members]
    for key in set(keys):
        if keys.count(key)>1:
            warnings.append('Duplicate member key '+key+'; use exact JSON pointer to disambiguate')
            for m in members:
                if m['key']==key:m['ambiguous_key']=True
    if not members:warnings.append('No recognized skills. Inspect raw JSON; missing fields were not filled.')
    return {'members':members,'warnings':warnings,'metadata':raw.get('metadata'),
            'recognized_keys':[k for k in raw if k in BANK_KEYS],
            'unknown_keys':[k for k in raw if k not in BANK_KEYS|{'metadata'}],
            'skill_count':sum(m['group']!='common_mistakes' for m in members),
            'mistake_count':sum(m['group']=='common_mistakes' for m in members)}

def member(bank,ref):
    if not isinstance(ref,str):raise ValidationError('Skill member reference must be key, source ID or JSON pointer')
    found=[m for m in adapt_bank(bank)['members'] if ref in (m['key'],m['pointer'],m['source_id'])]
    if len(found)!=1:raise ValidationError('Skill member missing or ambiguous: '+ref[:200])
    return found[0]

def compare_banks(a,b):
    va,vb=adapt_bank(a),adapt_bank(b)
    if any(m.get('source_id_missing') for m in va['members']+vb['members']):
        return {'comparable':False,'reason':'Missing stable source skill IDs; no positional pairing across snapshots','changes':[]}
    if any(m.get('ambiguous_key') for m in va['members']+vb['members']):
        return {'comparable':False,'reason':'Duplicate skill IDs within a category; no guessed pairing','changes':[]}
    ma={m['key']:m for m in va['members']};mb={m['key']:m for m in vb['members']};changes=[]
    for k in sorted(set(ma)|set(mb)):
        old,new=ma.get(k),mb.get(k)
        state='added' if not old else 'removed' if not new else 'unchanged' if old['content_hash']==new['content_hash'] else 'modified'
        def lines(v):
            import json
            return json.dumps(v['content'],ensure_ascii=False,indent=2,sort_keys=True).splitlines() if v else []
        changes.append({'key':k,'change':state,'before':old,'after':new,
            'diff':'\n'.join(difflib.unified_diff(lines(old),lines(new),fromfile='baseline',tofile='candidate',lineterm='')) if state!='unchanged' else ''})
    return {'comparable':True,'changes':changes,'counts':{s:sum(c['change']==s for c in changes) for s in ('added','removed','modified','unchanged')},
            'metadata_changed':digest(a.get('metadata'))!=digest(b.get('metadata')),
            'pairing':'Explicit source group/category/id; renamed IDs appear as removed + added, not guessed ancestry.',
            'causal_claim':False}
