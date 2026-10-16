"""Run-scoped method read models, deliberately separate from task grading.

All aggregate values come from recorded observations and explicit links. The
three headline cards do not infer skill credit, ground-truth answers, parameter
updates or autonomous success from scalar training rewards.
"""
from __future__ import annotations

from collections import defaultdict
import json

from ..core import ValidationError
from .contracts import FAMILIES
from .indexes import ensure_assessment_index
from .views import experiment_overview,page_args


def _metric(mid,label,unit='ratio',value=None,numerator=None,denominator=None,
            unknown=0,status=None,missing_reason=None,note='',**extra):
    if status is None:status=('partial' if unknown else 'measured') if value is not None else 'not_recorded'
    return {'id':mid,'label':label,'unit':unit,'value':value,'status':status,
            'numerator':numerator,'denominator':denominator,'unknown':unknown,
            'missing_reason':missing_reason if value is None else None,'note':note,**extra}


def _ratio(mid,label,yes,no,unknown,note='',**extra):
    n=yes+no
    return _metric(mid,label,value=yes/n if n else None,numerator=yes,denominator=n,
                   unknown=unknown,missing_reason='缺少可计分的完整记录' if not n else None,note=note,**extra)


def _read(repo,scope,kinds):
    ensure_assessment_index(repo,scope,kinds)
    where,args=repo._where(scope,prefix='o.')
    where+=(' AND ' if where else ' WHERE ')+'o.kind IN ('+','.join('?' for _ in kinds)+')'
    with repo.lock:
        rows=repo.conn.execute('SELECT s.summary FROM objects o JOIN method_summaries s ON s.id=o.id'+where+' ORDER BY o.rowid',args+list(kinds)).fetchall()
    return [json.loads(x[0]) for x in rows]


def _p(row):return row.get('assessment',{})


def _update_status(use_ids,updates):
    matches=[x for x in updates if set(_p(x).get('consumption_ids',[])).intersection(use_ids)]
    if any(_p(x).get('status')=='completed' for x in matches):return True
    if matches and all(_p(x).get('status') in ('failed','skipped') for x in matches):return False
    return None


def _external(repo,scope,rows,family,evaluation_id=''):
    """Only one explicitly designated independent evaluation is eligible.

    Multiple evaluations stay separate; no best score, newest checkpoint or
    accidental mean is selected. The Agent evaluation page remains the selector.
    """
    candidates=[r for r in rows if r['kind']=='evaluation_binding' and
        family in _p(r).get('assessment_families',[]) and _p(r).get('independent') is True and
        _p(r).get('autonomous') is True and _p(r).get('binding_origin')=='recorded_execution']
    metric=_metric('external_em','独立评测 EM',missing_reason='尚未明确关联本运行的独立自主评测',
        note='固定题单与评分协议；训练 reward、teacher 成功和历史回填不替代此项。')
    cost=_metric('tokens_per_task','每题 token 成本',unit='tokens/task',missing_reason='尚无对应完整评测的 token 用量',
        note='仅实际记录的输入+输出 tokens；建库/出题/teacher 成本单独保存在原始产物。')
    info={'candidates':[{'binding_id':r['id'],'evaluation_id':_p(r)['evaluation_id']} for r in candidates]}
    options=[];eligible=[];excluded=[]
    if repo.store:
        with repo.store.lock:
            for c in candidates:
                eid=_p(c)['evaluation_id'];e=repo.store._get('evaluations',eid);cp=repo.store._get('checkpoints',e['checkpoint_id'])
                if e.get('purpose') not in ('agent_eval','checkpoint_eval'):
                    excluded.append({'evaluation_id':eid,'reason':'评测用途未明确声明，旧结果仍在 Agent 执行评测保留'});continue
                if cp['run_id']!=scope.get('run_id'):
                    excluded.append({'evaluation_id':eid,'reason':'评测不属于此运行'});continue
                if e.get('protocol',{}).get('groundtruth_to_agent') is True:
                    excluded.append({'evaluation_id':eid,'reason':'评测协议声明向 Agent 暴露答案'});continue
                if family=='opd':
                    context=_p(c).get('opd_context',{})
                    flags=('teacher_hints','peer_answers','future_observations','groundtruth_visible')
                    if any(context.get(k) is not False for k in flags) or context.get('privileged_information')!=[]:
                        excluded.append({'evaluation_id':eid,'reason':'OPD 评测未明确撤去教师提示、成功同伴、未来观察与标准答案等训练特权信息'});continue
                options.append({'id':eid,'evaluation_id':eid,'label':eid+' · step '+str(cp['step']),
                    'checkpoint_id':cp['id'],'step':cp['step'],'status':e.get('status')})
                eligible.append(c)
        candidates=eligible
    info['options']=options
    info['excluded']=excluded
    info['independence_basis']='显式评测绑定声明；不是自动完成的数据隔离审计'
    if evaluation_id:
        selected=[c for c in candidates if _p(c)['evaluation_id']==evaluation_id]
        if not selected:raise ValidationError('Selected evaluation is not a declared independent autonomous evaluation for this run and method family')
        candidates=selected
    if len(candidates)>1:
        for m in (metric,cost):m.update(status='selection_required',missing_reason='有多个明确关联的评测，请选择一次评测；不自动合并 checkpoint')
        return metric,cost,info
    if not candidates or repo.store is None:return metric,cost,info
    ev=repo.store.evaluation(_p(candidates[0])['evaluation_id'])
    if ev['checkpoint']['run_id']!=scope.get('run_id'):
        raise ValidationError('Designated evaluation belongs to another run')
    stats=ev['stats'];info.update(evaluation_id=ev['id'],checkpoint_id=ev['checkpoint']['id'],
        panel_id=ev['panel_id'],panel_hash=ev.get('panel_hash'),protocol_hash=ev.get('protocol_hash'),
        expected=stats['expected'],scored=stats['scored'],datasets=stats['datasets'],demo=bool(ev['run'].get('demo')))
    metric.update(evaluation_id=ev['id'],denominator=stats['expected'],unknown=stats['expected']-stats['scored'])
    # Macro EM is not the micro answer_correct/expected ratio.
    metric['note']='各数据集 EM 的宏平均；分母显示题单题数，不把答对总数除以总题数当作宏平均。'
    if ev.get('status')=='completed' and stats.get('fully_scored') and stats['macro_em'] is not None:
        metric.update(value=stats['macro_em'],status='measured',missing_reason=None)
    else:metric.update(status='partial' if stats['scored'] else 'not_recorded',missing_reason='评测未完成或题单尚未全量评分')
    with repo.store.lock:
        token=repo.store.conn.execute("""SELECT count(*) AS recorded,
            sum(CASE WHEN json_extract(data,'$.usage.input_tokens') IS NOT NULL AND json_extract(data,'$.usage.output_tokens') IS NOT NULL THEN 1 ELSE 0 END) AS measured,
            avg(CASE WHEN json_extract(data,'$.usage.input_tokens') IS NOT NULL AND json_extract(data,'$.usage.output_tokens') IS NOT NULL THEN json_extract(data,'$.usage.input_tokens')+json_extract(data,'$.usage.output_tokens') END) AS value
            FROM trajectory_summaries WHERE evaluation_id=?""",(ev['id'],)).fetchone()
    measured=token['measured'] or 0;unknown=stats['expected']-measured
    cost.update(evaluation_id=ev['id'],denominator=measured,unknown=unknown)
    if measured:
        cost.update(value=token['value'],status='partial' if unknown else 'measured',missing_reason=None)
        if unknown:cost['note']='仅在输入和输出 token 都有记录的样本上求平均；缺失用量单列，不能作为全题成本。'
    return metric,cost,info


def _skill(rows,external,cost):
    retrievals=[r for r in rows if r['kind']=='skill_retrieval'];instances=[r for r in rows if r['kind']=='skill_instance'];groups=defaultdict(list);unkeyed=0
    for r in retrievals:
        key=_p(r).get('trajectory_id')
        if key:groups[key].append(r)
        else:unkeyed+=1
    injected=absent=unknown=0
    for records in groups.values():
        complete=all(_p(r).get('log_complete') is True for r in records)
        if not complete:unknown+=1
        elif any(_p(r).get('verified_injections',0) for r in records):injected+=1
        else:absent+=1
    injection=_ratio('injection_coverage','实际注入覆盖率',injected,absent,unknown+unkeyed,
        note='分母为完整记录最终输入与全部检索调用的轨迹；按轨迹去重。实例级加载观测在 Agent 运行视图另列，不能代替整条轨迹的完整性声明。',
        recorded_trajectories=len(groups),missing_identity_records=unkeyed)
    counts={'banks':sum(r['kind']=='skill_bank' for r in rows),'retrieval_records':len(retrievals),
        'recorded_trajectories':len(groups),'complete_trajectories':injected+absent,
        'injected_trajectories':injected,'unknown_trajectories':unknown,'missing_identity_records':unkeyed,
        'evolution_events':sum(r['kind']=='skill_evolution' for r in rows),
        'asset_versions':sum(r['kind']=='skill_asset' for r in rows),'runtime_instances':len(instances)}
    evidence=instances+retrievals or [r for r in rows if r['kind'] in ('skill_asset','skill_bank','skill_evolution')]
    return [external,injection,cost],counts,evidence,{}


def _review(reviews):
    superseded={_p(r).get('supersedes') for r in reviews}
    active=[r for r in reviews if r['id'] not in superseded]
    statuses={_p(r).get('status') for r in active}
    status=next(iter(statuses)) if len(statuses)==1 else 'conflicting' if statuses else 'unreviewed'
    if status=='rejected':return status,False
    if status!='accepted':return status,None
    flags=[]
    for r in active:
        p=_p(r);v=p.get('verification_status')
        flags.append(True if v=='passed' and p.get('verifier_recorded') or v is None and p.get('legacy_verified_answer') else False if v=='failed' else None)
    if flags and all(x is True for x in flags):return status,True
    if flags and all(x is False for x in flags):return status,False
    return status,None


def _selfplay(rows,external):
    bykind=defaultdict(list)
    for r in rows:bykind[r['kind']].append(r)
    questions=bykind['generated_question'];cycles={r['id']:r for r in bykind['selfplay_cycle']}
    reviews=defaultdict(list);uses=defaultdict(list)
    for r in bykind['question_review']:reviews[_p(r)['question_id']].append(r)
    for r in bykind['training_use']:
        for aid in _p(r).get('artifact_ids',[]):uses[aid].append(r)
    sets={k:set() for k in ('unique','verified','consumed','sampled','supply','reused')}
    unknown_supply=set();known_supply=set();unknown_identity=0;evidence=[];rounds={}
    # An original generation round may not be the only consumption round: old
    # questions can be reused later. Absence is negative evidence only when all
    # recorded rounds in this run explicitly declare complete consumption logs.
    consumption_complete=bool(cycles) and all(_p(c).get('coverage',{}).get('consumption_complete') is True for c in cycles.values())
    for cid,r in cycles.items():rounds[cid]={'cycle_id':cid,'title':r.get('title') or cid,'new_verified_consumed_questions':0,'first_generated_questions':0,'consumed_questions':0}
    for r in questions:
        p=_p(r);uid=p.get('question_uid');key=uid or r['id'];first=p.get('first_generated_cycle_id')
        status,verified=_review(reviews[r['id']]);sampled=[u for u in uses[r['id']] if _p(u).get('status')=='sampled']
        consumed=True if sampled else False if consumption_complete else None
        completed=_update_status({u['id'] for u in sampled},bykind['training_update']) if sampled else None
        dedup=p.get('deduplication','unknown')
        if uid is None:unknown_identity+=1
        if uid and dedup=='unique':sets['unique'].add(uid)
        if uid and verified is True:sets['verified'].add(uid)
        if uid and sampled:sets['sampled'].add(uid)
        if uid and completed is True:sets['consumed'].add(uid)
        reused=bool(first and any(_p(u).get('cycle_id') and _p(u)['cycle_id']!=first for u in sampled))
        if uid and reused:sets['reused'].add(uid)
        # A failed verification or declared duplicate is a known nonqualifier.
        if verified is False or dedup=='duplicate' or completed is False or consumed is False:
            known_supply.add(key)
        elif uid and first in cycles and dedup=='unique' and verified is True and completed is True:
            sets['supply'].add(uid);known_supply.add(key)
        else:unknown_supply.add(key)
        signal=[_p(u).get('signal_active') for u in sampled]
        assessed={**p,'review_status':status,'verified':verified,'consumed':consumed,
            'update_completed':completed,'signal_active':True if True in signal else False if signal and all(x is False for x in signal) else None,
            'reused':reused,'review_ids':[x['id'] for x in reviews[r['id']]],'training_use_ids':[x['id'] for x in sampled]}
        evidence.append({**r,'assessment':assessed})
    # Count stable IDs once even when collectors preserve multiple revisions.
    unknown_supply-=known_supply
    for cid in rounds:
        qs=[r for r in evidence if _p(r).get('first_generated_cycle_id')==cid]
        rounds[cid]['first_generated_questions']=len({(_p(r).get('question_uid') or r['id']) for r in qs})
        rounds[cid]['new_verified_consumed_questions']=len({_p(r)['question_uid'] for r in qs if _p(r).get('question_uid') in sets['supply']})
        rounds[cid]['consumed_questions']=len({_p(r)['question_uid'] for r in evidence if _p(r).get('question_uid') and any(_p(u).get('cycle_id')==cid and _update_status({u['id']},bykind['training_update']) is True for u in uses[r['id']] if _p(u).get('status')=='sampled')})
    qids={r['id'] for r in questions}
    relevant_uses=[u for u in bykind['training_use'] if _p(u).get('status')=='sampled' and (qids.intersection(_p(u).get('artifact_ids',[])) or _p(u).get('cycle_id') in cycles)]
    flags=[_p(u).get('signal_active') for u in relevant_uses]
    signal=_ratio('training_signal_coverage','训练信号覆盖率',sum(x is True for x in flags),sum(x is False for x in flags),sum(x is None for x in flags),
        note='按有明确题目/轮次关联的 sampled 记录计数；实际 mask、权重和必要的 advantage 决定该任务学习项是否有效。批级记录不推算为逐题梯度。')
    complete_empty=bool(cycles) and all(_p(c).get('coverage',{}).get('generated_questions_complete') is True for c in cycles.values())
    observed=bool(known_supply or sets['supply'] or complete_empty)
    supply=_metric('new_verified_consumed','本运行新增有效训练题',unit='count',value=len(sets['supply']) if observed else None,
        numerator=len(sets['supply']),denominator=None,unknown=len(unknown_supply),
        missing_reason='尚无稳定题号、首次生成轮次、验证、去重与完成更新的完整关联',
        note='按稳定题号去重，要求验证通过、首次生成轮次、明确 unique、实际 sampled 且关联 completed 更新；仅报告已证实记录，不声称全部供给已采集。')
    counts={'generated_questions':len(questions),'unique_questions':len(sets['unique']),
        'verified_questions':len(sets['verified']),'sampled_questions':len(sets['sampled']),
        'consumed_questions':len(sets['consumed']),'new_verified_consumed_questions':len(sets['supply']),
        'reused_questions':len(sets['reused']),'unknown_identity_questions':unknown_identity,
        'unknown_supply_questions':len(unknown_supply),
        'review_errors':sum(_p(r).get('verification_status')=='error' for r in bykind['question_review']),
        'sampled_records':len(relevant_uses),'signal_active_records':sum(x is True for x in flags),
        'signal_unknown_records':sum(x is None for x in flags)}
    return [supply,signal,external],counts,evidence,{'cycles':list(rounds.values())}


def _opd(rows,external):
    failures=[r for r in rows if r['kind']=='opd_failure'];teachers=defaultdict(list);supervisions=defaultdict(list);uses=defaultdict(list)
    updates=[r for r in rows if r['kind']=='training_update']
    for r in rows:
        if r['kind']=='opd_teacher':teachers[_p(r)['failure_id']].append(r)
        elif r['kind']=='opd_supervision':supervisions[_p(r)['failure_id']].append(r)
        elif r['kind']=='training_use' and _p(r).get('status')=='sampled':
            for aid in _p(r).get('artifact_ids',[]):uses[aid].append(r)
    budgets=defaultdict(lambda:{'eligible':0,'attempted':0,'successes':0,'failures':0,'unknown':0})
    evidence=[];super_flags=[];eligible_unknown=0
    for f in failures:
        p=_p(f);ts=teachers[f['id']];ss=supervisions[f['id']]
        attempts=[t for t in ts if _p(t)['mode']=='continuation' and _p(t)['status']!='not_run']
        passed=sum(_p(t).get('verified_success') is True for t in attempts)
        failed=sum(_p(t).get('verified_failure') is True for t in attempts)
        errors=sum(_p(t).get('verification_status')=='error' or _p(t).get('status')=='error' for t in attempts)
        k=p.get('attempt_budget');repair=None
        if k is not None and p.get('state_status') in ('captured','replayed'):
            if passed:repair=True
            elif len(attempts)==k and failed==k:repair=False
        active=[s for s in ss if _p(s).get('signal_active') is True]
        consumed={u['id'] for s in active for u in uses[s['id']]}
        update=_update_status(consumed,updates) if consumed else None
        if update is True:supervised=True
        elif ss and all(_p(s).get('signal_active') is False for s in ss):supervised=False
        elif active and update is False:supervised=False
        else:supervised=None
        if p.get('eligible') is True:
            group=budgets[k];group['eligible']+=1;group['attempted']+=bool(attempts)
            group['successes']+=repair is True;group['failures']+=repair is False;group['unknown']+=repair is None
            super_flags.append(supervised)
        elif p.get('eligible') is None:eligible_unknown+=1
        assessment={**p,'continuation_attempts':len(attempts),'rescore_count':sum(_p(t)['mode']=='rescore' for t in ts),
            'verification_passes':passed,'verification_failures':failed,'verification_errors':errors,'repair_success':repair,
            'supervision_count':len(ss),'active_supervision_count':len(active),'completed_update':update,
            'supervision_used':supervised,'teacher_ids':[t['id'] for t in ts],
            'supervision_ids':[s['id'] for s in ss],'training_use_ids':sorted(consumed)}
        evidence.append({**f,'assessment':assessment})
    budget_groups=[{'attempt_budget':k,**v} for k,v in sorted(budgets.items(),key=lambda x:-1 if x[0] is None else x[0])]
    declared=[(k,v) for k,v in budgets.items() if k is not None]
    if len(declared)==1:
        k,b=declared[0]
        repair_metric=_ratio('teacher_repair','Teacher 接续成功率',b['successes'],b['failures'],b['unknown']+eligible_unknown+budgets.get(None,{}).get('eligible',0),
            note='同一预先声明的最多 K 次尝试；环境或确定性评分器验证成功可提前结束，零成功需完整 K 次已验证失败。未测、验证错误和状态未恢复单列；不外推未记录状态。',
            attempt_budget=k,eligible=b['eligible'],attempted=b['attempted'])
    else:
        repair_metric=_metric('teacher_repair','Teacher 接续成功率',unknown=sum(v['eligible'] for v in budgets.values())+eligible_unknown,
            status='selection_required' if len(declared)>1 else 'not_recorded',
            missing_reason='不同尝试预算 K 分组保存，不混成一个成功率' if len(declared)>1 else '缺少候选范围、尝试预算 K 或可还原状态',
            note='重打分不计教师接续；每个失败状态保留全部成功、失败和错误分支。')
    all_teachers=[r for r in rows if r['kind']=='opd_teacher']
    if all_teachers and all(_p(t)['mode']=='rescore' for t in all_teachers):
        repair_metric.update(value=None,status='not_applicable',missing_reason='本运行只记录重打分，未测教师接续')
    supervision_metric=_ratio('supervision_coverage','有效监督覆盖率',sum(x is True for x in super_flags),sum(x is False for x in super_flags),sum(x is None for x in super_flags)+eligible_unknown,
        note='以明确 eligible 的失败状态计数：接受的非零学习项 → sampled → completed 更新。分母仅包含可判定状态；缺日志单列。不是逐样本因果贡献。',
        eligible=sum(v['eligible'] for v in budgets.values()))
    counts={'failure_states':len(failures),'eligible_states':sum(v['eligible'] for v in budgets.values()),
        'unknown_eligibility_states':eligible_unknown,'teacher_records':len(all_teachers),
        'continuation_attempts':sum(_p(x)['continuation_attempts'] for x in evidence),
        'rescore_records':sum(_p(x)['rescore_count'] for x in evidence),
        'verified_successful_states':sum(_p(x).get('repair_success') is True for x in evidence),
        'verification_errors':sum(_p(x)['verification_errors'] for x in evidence),
        'supervision_records':sum(len(x) for x in supervisions.values()),
        'supervised_updated_states':sum(x is True for x in super_flags)}
    return [repair_metric,supervision_metric,external],counts,evidence,{'budget_groups':budget_groups}


def method_assessment(repo,experiment_id,run_id='',family='',offset=0,limit=25,evaluation_id=''):
    offset,limit=page_args(offset,limit)
    if family not in ('skill','self_play','opd'):
        raise ValidationError('Assessment family is skill/self_play/opd')
    context=experiment_overview(repo,experiment_id,run_id);scope=context['scope']
    relevant=set(FAMILIES[family]['kinds'])|{'evaluation_binding','training_use','training_update'}
    # Large archives have their own member/lifecycle read models, never here.
    relevant.discard('skill_archive')
    rows=_read(repo,scope,sorted(relevant))
    external,cost,ev_info=_external(repo,scope,rows,family,evaluation_id)
    if family=='skill':metrics,counts,evidence,extra=_skill(rows,external,cost)
    elif family=='self_play':metrics,counts,evidence,extra=_selfplay(rows,external)
    else:metrics,counts,evidence,extra=_opd(rows,external)
    total=len(evidence)
    for r in evidence:r.pop('assessment_version',None)
    return {'scope':scope,'family':family,'status':'recorded' if any(r['kind'] in FAMILIES[family]['kinds'] for r in rows) else 'not_recorded',
        'metrics':metrics,'counts':counts,'evaluation':ev_info,'evaluation_options':ev_info['options'],
        'evidence':{'items':evidence[offset:offset+limit],'total':total,'offset':offset,'limit':limit,'has_more':offset+limit<total},
        'protocol_notes':['只统计此实验的选定运行；未选运行时只看实验级未绑定记录。',
            '未知不等于零。比率的分母、缺失数与记录范围分别显示。',
            '中间产物、训练使用与参数/任务效果分别记录，关联不等于因果。'],
        'causal_claim':False,'models_executed':False,**extra}
