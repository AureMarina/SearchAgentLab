"""Four-layer scorecard: observations, contract checks, unknowns, separate gates."""
from __future__ import annotations
import re
from ..inspection.adapters import resolve, pointer
from ..inspection.repository import strict_json,digest
from .contracts import schema_check, matches_type
from .evidence import enumerate_calls,get,lineage,context_of

VERSION='layered-validator-v1'

def scorecard(doc, contract=None, binding='retrospective'):
    raw=doc['raw']; view=doc['view']; c=contract or {};checks=[]
    def add(id,layer,status,title,evidence=None,detail='',origin='validator'):
        # No missing pointers may be presented as existing evidence.
        ev=[]
        for p in evidence or []:
            try:resolve(raw,p);ev.append(p)
            except (ValueError,KeyError,IndexError,TypeError):pass
        checks.append({'id':id,'layer':layer,'status':status,'title':title,'evidence':ev,'detail':detail,'origin':origin})
    def path(k):return view['pointers'].get(k)
    reference=view['execution_kind']=='reference_only'
    if doc.get('parse_error') or reference:
        for l in ('Outcome','Decision','Action','Reliability'):
            add(l.lower(),l,'not_applicable' if reference else 'insufficient','仅参考材料' if reference else '原记录无法解析',detail='不从题单或损坏日志生成模型失败结论。')
    else:
        em=view['scores'].get('em')
        if type(em) in (int,float) and em in (0,1):
            add('answer_score','Outcome','pass' if em==1 else 'fail','已记录的答案 EM',[pointer(path('scores'),'em')] if path('scores') else [],'只读取已有评分；不重算、不与 reward 混用。','recorded_score')
        elif 'is_correct' in view:
            add('answer_score','Outcome','pass' if view['is_correct'] else 'fail','记录的 is_correct（不是 EM）',[path('is_correct')],origin='recorded_score')
        else:add('answer_score','Outcome','insufficient','答案结果未记录',detail='不能用运行 completed 或 reward 推断正确。')
        out=view['raw_final_output'];op=path('raw_final_output');fmt=c.get('output',{}).get('format','any')
        if fmt=='any':add('output_contract','Outcome','not_applicable','未要求固定输出格式')
        elif out is None:add('output_contract','Outcome','insufficient','缺少最终输出')
        elif fmt=='answer_tag':
            ok=isinstance(out,str) and len(re.findall(r'<answer>(.*?)</answer>',out,re.S))==1 and out.count('<answer>')==1 and out.count('</answer>')==1
            add('output_contract','Outcome','pass' if ok else 'fail','恰好一个完整 answer 标签',[op],origin='contract')
        else:
            try:
                parsed=strict_json(out) if isinstance(out,str) else out
                r=schema_check(parsed,c.get('output',{}).get('schema',{}))
                add('output_contract','Outcome',r['status'],'JSON 输出契约',[op],str(r['errors']+r['unsupported']),origin='contract')
            except (ValueError,RecursionError):add('output_contract','Outcome','fail','最终输出不是严格 JSON',[op],origin='contract')
        calls=enumerate_calls(doc);names={x['name'] for x in calls if x['name']}
        complete=get(raw,'/trace_complete') is True or get(raw,'/data/trace_complete') is True
        for category,forbidden in (('required_tools',False),('forbidden_tools',True)):
            tools=c.get(category,[])
            if not tools:add(category,'Decision','not_applicable','未规定'+('禁止工具' if forbidden else '必需工具'))
            for name in tools:
                exists=name in names
                st=('fail' if exists else 'pass' if complete else 'insufficient') if forbidden else ('pass' if exists else 'fail' if complete else 'insufficient')
                add(category+':'+name,'Decision',st,('禁止调用 ' if forbidden else '必须调用 ')+name,[x['pointer'] for x in calls if x['name']==name],
                    '只判断日志中的实际调用；缺少调用且未声明 trace_complete 时为证据不足，不反推计划。',origin='contract')
        for skill in c.get('required_skills',[]):
            ss=get(raw,'/selected_skills')
            st='insufficient' if not isinstance(ss,list) else 'pass' if skill in ss else 'fail' if complete else 'insufficient'
            add('required_skill:'+skill,'Decision',st,'必须选择 Skill '+skill,['/selected_skills'],'仅检查显式 selected_skills，未定义 Skill 的实验不判错。',origin='contract')
        add('semantic_decision','Decision','insufficient','证据支持、实体保持与结束时机',detail='需要前缀证据、语义 Judge 或人工检查；规则未命中不等于决策正确。')
        schemas=c.get('tool_schemas',{})
        if not schemas:add('tool_schema','Action','not_applicable','未绑定当时的工具 schema')
        for i,call in enumerate(calls):
            if call['name'] not in schemas:continue
            args=call['arguments'];ap=call['arguments_pointer']
            if args is None:add('tool_schema:'+str(i),'Action','insufficient','缺少工具参数',[call['pointer']]);continue
            try:
                val=strict_json(args) if isinstance(args,str) else args
                r=schema_check(val,schemas[call['name']]);add('tool_schema:'+str(i),'Action',r['status'],call['name']+' 参数校验',[ap],str(r['errors']+r['unsupported']),origin='contract')
            except (ValueError,RecursionError):add('tool_schema:'+str(i),'Action','fail',call['name']+' 参数不是 JSON',[ap],origin='contract')
        for name in schemas.keys()-names:add('schema_uncalled:'+name,'Action','not_applicable',name+' 未观察到调用，schema 未执行')
        failures=[f for f in doc['report']['findings'] if f['rule'] in ('runtime_error','tool_error')]
        if failures:
            for i,f in enumerate(failures):add('observed_error:'+str(i),'Action','fail',f['title'] if 'title' in f else '日志记录了运行异常',[e['pointer'] for e in f['evidence']],
                '确认异常现象，不证明是最终失败根因；是否恢复需另查。')
        else:add('observed_error','Action','insufficient','未观察到显式异常字段',detail='没有异常字段不能证明所有调用成功。')
        for a in c.get('assertions',[]):
            try:v=resolve(raw,a['pointer']);exists=True
            except (ValueError,KeyError,TypeError,IndexError):v=None;exists=False
            if not exists:st='insufficient';detail='断言字段缺少；不将日志缺失自动判业务失败。'
            elif a['op']=='exists':st='pass';detail='字段已记录。'
            elif a['op']=='equals':st='pass' if digest(v)==digest(a['expected']) else 'fail';detail='确定性字段比较。'
            else:st='insufficient' if not matches_type(v,'number') else 'pass' if abs(v-a['expected'])<=a.get('tolerance',0) else 'fail';detail='绝对误差容差检查。'
            add('assertion:'+a['id'],a['layer'],st,a['id'],[a['pointer']],detail,origin='contract')
        add('repeat_runs','Reliability','insufficient','单条轨迹不能证明稳定性',detail='使用“验证与修复”中的同题复测结果；至少 3 次仅作开发诊断，少样本分位数不稳定。')
    layers=[]
    for name in ('Outcome','Decision','Action','Reliability'):
        part=[x for x in checks if x['layer']==name];s=[x['status'] for x in part]
        status='fail' if 'fail' in s else 'insufficient' if 'insufficient' in s else 'pass' if 'pass' in s else 'not_applicable'
        layers.append({'name':name,'status':status,'counts':{k:s.count(k) for k in ('pass','fail','insufficient','not_applicable')}})
    gates=c.get('hard_gates',[]);ids={x['id']:x for x in checks}
    gate_checks=[ids.get(g,{'id':g,'status':'insufficient','title':'Hard Gate 未找到对应检查项'}) for g in gates]
    unknown=any(x['status'] in ('insufficient','not_applicable') for x in gate_checks)
    fail=any(x['status']=='fail' for x in gate_checks)
    gate_status='not_configured' if not gates else 'block' if fail else 'insufficient' if unknown else 'pass'
    return {'validator_version':VERSION,'source_hash':doc['source_hash'],'contract_hash':digest(c) if contract else None,
            'binding':binding,'layers':layers,'checks':checks,'runtime_status':view['runtime_status'],
            'gate':{'mode':c.get('mode','shadow'),'status':gate_status,'checks':gate_checks,'enforced':False,
                    'release_ready':None,'note':'仅本地检查意见，不发布、不拦截生产。不由 completed 或规则通过推断 Ready for Release；历史回填不追认运行前契约。'},
            'lineage':lineage(doc),'context':context_of(doc)}
