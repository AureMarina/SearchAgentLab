"""Bounded read models: summary lists never include complete trace/bank payloads."""
from __future__ import annotations
import difflib
import json
from ..core import ValidationError, NotFoundError
from .contracts import FAMILIES, family_for
from .skills import member, compare_banks


def page_args(offset=0,limit=25):
    offset=int(offset);limit=int(limit)
    if offset<0 or not 1<=limit<=200:raise ValidationError('offset >= 0 and limit 1–200 required')
    return offset,limit


def summary(repo,oid):
    with repo.lock:row=repo.conn.execute('SELECT summary FROM method_summaries WHERE id=?',(oid,)).fetchone()
    if not row:raise NotFoundError('Method object not found')
    return json.loads(row[0])


def overview(repo,scope=None):
    sc={'project_id':''} if scope is None else scope
    cat=repo.catalog(sc,limit=1)
    profiles=repo.objects(sc,kind='method_profile')
    declared={f for o in profiles for f in o['data']['families']}
    recorded={family_for(k) for k,n in cat['counts'].items() if n}
    families=[]
    for name,spec in FAMILIES.items():
        counts={kind:cat['counts'].get(kind,0) for kind in spec['kinds']}
        families.append({'id':name,**spec,'counts':counts,'declared':name in declared,
                         'recorded':name in recorded,'state':'recorded' if name in recorded else 'declared_only' if name in declared else 'not_recorded'})
    return {'scope':sc,'profiles':profiles,'families':families,'counts':cat['counts'],
            'rule':'Method families are independent of project and main/reproduction classification. Missing evidence is not zero.'}


def archive(repo,oid):
    obj=repo.object(oid)
    if obj['kind']!='skill_archive':raise ValidationError('Expected skill_archive')
    d=obj['data']; snaps=[dict(s,bank_id=bid) for bid,s in zip(d['bank_ids'],d['snapshots'])]
    training=[s for s in snaps if s['phase']!='evaluation']
    return {'archive':summary(repo,oid),'snapshots':snaps,'archive_sha256':d.get('archive_sha256'),
            'coverage':d.get('coverage',{}),'limitations':d.get('limitations',[]),
            'initial_counts':training[0]['counts'] if training else None,
            'last_training_counts':training[-1]['counts'] if training else None,
            'imports':repo.imports_for(oid),
            'recorded_only':True}


def members(repo,bank_id,query='',group='',offset=0,limit=25):
    header=summary(repo,bank_id)
    if header['kind']!='skill_bank':raise ValidationError('Expected skill_bank')
    offset,limit=page_args(offset,limit)
    if len(query)>500:raise ValidationError('query max 500 characters')
    where=['bank_id=?'];args=[bank_id]
    if group:where.append('group_name=?');args.append(group)
    if query:where.append('instr(search_text,?)>0');args.append(query.casefold())
    predicate=' AND '.join(where)
    with repo.lock:
        total=repo.conn.execute('SELECT count(*) FROM skill_members WHERE '+predicate,args).fetchone()[0]
        groups={r[0]:r[1] for r in repo.conn.execute('SELECT group_name,count(*) FROM skill_members WHERE bank_id=? GROUP BY group_name',(bank_id,))}
        rows=repo.conn.execute('SELECT member_key,pointer,source_id,group_name,category,title,content_hash,ambiguous, '
            "substr(coalesce(json_extract(content,'$.principle'),json_extract(content,'$.description'),''),1,180) AS preview "
            'FROM skill_members WHERE '+predicate+" ORDER BY CASE group_name WHEN 'general_skills' THEN 0 WHEN 'query_type_skills' THEN 1 WHEN 'task_specific_skills' THEN 2 ELSE 3 END,category,member_key,pointer LIMIT ? OFFSET ?",args+[limit,offset]).fetchall()
    return {'bank':header,'items':[dict(r) for r in rows],'total':total,'offset':offset,'limit':limit,
            'groups':groups,'has_more':offset+len(rows)<total,'body_loaded':False}


def skill_detail(repo,bank_id,key):
    header=summary(repo,bank_id)
    if header['kind']!='skill_bank':raise ValidationError('Expected skill_bank')
    with repo.lock:
        rows=repo.conn.execute('SELECT * FROM skill_members WHERE bank_id=? AND (member_key=? OR pointer=?)',(bank_id,key,key)).fetchall()
    if not rows:raise NotFoundError('Skill not present in this snapshot')
    if len(rows)!=1:raise ValidationError('Skill ambiguous; select its exact source pointer')
    m=dict(rows[0]);m['content']=json.loads(m['content']);m.pop('search_text')
    if m['group_name']=='assets' and m['content'].get('asset_id'):
        asset=repo.object(m['content']['asset_id'])
        m['content']={**m['content'],'package':asset['data']['package']}
    return {'bank':header,'member':m}


def lifecycle(repo,archive_id,key):
    a=archive(repo,archive_id);snaps=a['snapshots']; ids=[s['bank_id'] for s in snaps]
    rows=[]
    with repo.lock:
        for i in range(0,len(ids),500):
            chunk=ids[i:i+500];marks=','.join('?' for _ in chunk)
            rows+=repo.conn.execute('SELECT bank_id,member_key,pointer,source_id,content_hash,ambiguous FROM skill_members WHERE member_key=? AND bank_id IN ('+marks+')',[key]+chunk).fetchall()
    if not rows:raise NotFoundError('Skill not observed in this archive')
    if any(r['ambiguous'] for r in rows) or len({r['bank_id'] for r in rows})!=len(rows):
        return {'comparable':False,'reason':'Missing/duplicate stable source ID; do not pair by position or guess ancestry','timeline':[]}
    mapping={r['bank_id']:dict(r) for r in rows}; timeline=[];previous=None;seen=False;previous_step=None
    for s in snaps:
        m=mapping.get(s['bank_id']); h=m['content_hash'] if m else None
        if s['phase']=='evaluation':
            event='present_in_frozen_snapshot' if m else 'absent_in_frozen_snapshot'
        elif h is not None:
            event=('first_observed' if not seen else 'reappeared') if previous is None else ('unchanged' if h==previous else 'modified')
        else:event='removed_between_saved_snapshots' if previous is not None else 'not_observed'
        timeline.append({'bank_id':s['bank_id'],'phase':s['phase'],'step':s['step'],'previous_saved_step':previous_step,
                         'event':event,'present':m is not None,'content_hash':h,'source_file':s['file'],
                         'source_sha256':s['file_sha256'],'byte_identical_training_index':s.get('byte_identical_training_index')})
        if s['phase']!='evaluation':
            seen=seen or m is not None;previous=h;previous_step=s['step']
    return {'comparable':True,'member_key':key,'timeline':timeline,
            'first_observed':next(x for x in timeline if x['present'] and x['phase']!='evaluation') if any(x['present'] and x['phase']!='evaluation' for x in timeline) else None,
            'creation_time':'not_recorded','retirement_reason':'not_recorded','causal_claim':False,
            'limitation':'Snapshot presence/differences only. No inferred creation, acceptance, selection, injection, training use or effectiveness.'}


def member_diff(repo,a_id,b_id,key):
    a,b=summary(repo,a_id),summary(repo,b_id)
    if a.get('scope',{})!=b.get('scope',{}):raise ValidationError('Member diff requires the same explicit scope; do not silently merge runs')
    def get(oid):
        try:return skill_detail(repo,oid,key)['member']
        except NotFoundError:return None
    old,new=get(a_id),get(b_id)
    if old is None and new is None:raise NotFoundError('Skill not present in either selected snapshot')
    if any(m and m['ambiguous'] for m in (old,new)):raise ValidationError('Ambiguous or missing stable skill ID')
    def lines(x):return json.dumps(x['content'],ensure_ascii=False,sort_keys=True,indent=2).splitlines() if x else []
    return {'a':a_id,'b':b_id,'member_key':key,'before':old,'after':new,
            'change':'added' if not old else 'removed' if not new else 'unchanged' if old['content_hash']==new['content_hash'] else 'modified',
            'diff':'\n'.join(difflib.unified_diff(lines(old),lines(new),fromfile=a.get('version') or a_id,tofile=b.get('version') or b_id,lineterm=''))}


def entropy_observations(repo,definition_id,context='',offset=0,limit=50,scope=None):
    definition=repo.object(definition_id)
    if definition['kind']!='entropy_definition':raise ValidationError('Expected entropy_definition')
    offset,limit=page_args(offset,limit)
    if context and context not in ('training_on_policy','fixed_prefix_probe','evaluation','unknown'):raise ValidationError('Unknown entropy context')
    where="l.target_id=? AND o.kind='entropy_observation'"
    args=[definition_id]
    scoped=scope if scope else (definition.get('scope') or {'project_id':''})
    clause,values=repo._where(scoped,prefix='o.')
    if clause:where+=' AND '+clause[7:];args+=values
    if context:where+=" AND json_extract(o.payload,'$.data.context')=?";args.append(context)
    with repo.lock:
        total=repo.conn.execute('SELECT count(*) FROM method_links l JOIN objects o ON o.id=l.source_id WHERE '+where,args).fetchone()[0]
        rows=repo.conn.execute('SELECT o.payload FROM method_links l JOIN objects o ON o.id=l.source_id WHERE '+where+
             " ORDER BY coalesce(json_extract(o.payload,'$.data.step'),-1),o.id LIMIT ? OFFSET ?",args+[limit,offset]).fetchall()
    from .entropy_tracking import _bounded_detail
    observations=[]
    for row in rows:
        obj=json.loads(row[0]);data,omitted=_bounded_detail(obj['data'])
        observations.append({**obj,'data':data,'omitted_fields':omitted})
    return {'definition':definition,'observations':observations,'total':total,
            'offset':offset,'limit':limit,'has_more':offset+len(rows)<total,
            'claim':'A single entropy series does not establish exploration quality or autonomous task improvement.'}


def experiment_overview(repo, experiment_id, run_id=''):
    """Only applicable families for one explicit experiment and one execution.

    An omitted run means experiment-level (unbound) evidence, never all runs.
    Experiment-level configuration can be inherited, but sibling-run records
    cannot. The name of a project, experiment or algorithm is not a classifier.
    """
    if not experiment_id:
        raise ValidationError('An explicit experiment_id is required')
    scope = repo._scope({'experiment_id': experiment_id, **({'run_id': run_id} if run_id else {})})
    scope['run_id'] = run_id or ''
    current = overview(repo, scope)
    parent_scope = {**scope, 'run_id': ''}
    parent_profiles = repo.objects(parent_scope, kind='method_profile') if run_id else []
    parent_evidence_counts = repo.catalog(parent_scope,limit=1)['counts'] if run_id else {}
    # Run-specific configurations describe an ablation more precisely than its
    # experiment default. Preserve inherited records separately for inspection.
    profiles = current['profiles'] or parent_profiles
    declared = {f for p in profiles for f in p['data']['families']}
    signals = {
        'skill': ['skill_archive', 'skill_bank', 'skill_asset', 'skill_instance', 'skill_retrieval', 'skill_evolution', 'skill_comparison'],
        'self_play': ['selfplay_cycle', 'generated_question', 'proposal', 'question_review', 'solver_attempt', 'selfplay_pool_event', 'selfplay_reference', 'selfplay_comparison'],
        'opd': ['opd_failure','opd_teacher','opd_supervision','opd_diagnostic','opd_recovery'],
        'entropy': ['entropy_definition', 'entropy_observation', 'entropy_probe', 'entropy_intervention', 'entropy_comparison'],
        'latent': ['state_snapshot'],
    }
    families = []
    for f in current['families']:
        name = f['id']
        recorded = any(current['counts'].get(kind, 0) for kind in signals[name])
        if recorded or name in declared:
            families.append({**f, 'recorded': recorded, 'declared': name in declared,
                             'state': 'recorded' if recorded else 'declared_only'})
    return {
        'scope': scope, 'profiles': profiles, 'counts': current['counts'], 'families': families,
        'profile_origin': 'run' if current['profiles'] and run_id else 'experiment' if profiles else 'not_recorded',
        'inherited_profiles': parent_profiles,
        'parent_evidence_counts': parent_evidence_counts,
        'rule': 'One explicit experiment and one run (or unbound experiment evidence). No sibling-run union; no inference from names.',
    }
