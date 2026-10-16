"""Method-specific evidence checks. No semantic judge and no reward mutation."""
from __future__ import annotations
from ..inspection.repository import digest
from ..inspection.adapters import text
from .skills import adapt_bank, member
RULESET='method-evidence-checks-v1'

def visibility_evidence(ref):
    """Restrict literal injection checks to an explicitly stored model-input field.
    Merely mentioning the text in a tool result or assistant output isn't proof.
    """
    p=ref.get('pointer','') if isinstance(ref,dict) else ''
    parts=p.split('/')
    return any(x in ('input','final_input','model_visible','request_messages','model_request') for x in parts)

def diagnose_method(repo,o):
    findings=[];d=o['data'];kind=o['kind']
    def add(rule,title,basis,explanation,pointer='',refs=None,next_check='',limitation='记录事实或候选，不证明因果；不改变得分。'):
        findings.append({'id':digest([RULESET,o['id'],rule,pointer])[:20],'rule':rule,'title':title,'basis':basis,
            'explanation':explanation,'evidence':[{'object_id':o['id'],'sha256':o['content_hash'],'pointer':pointer}]+(refs or []),
            'next_check':next_check,'limitation':limitation})
    if not o.get('scope'):add('unassigned','尚未绑定研究归属','insufficient','可以独立检查文件，不会自动创建训练或评测。')
    if kind=='generated_question':
        if 'proposed_answer' in d:add('proposed_not_gold','出题答案与已核验答案分开保存','fact','proposed_answer 是出题者的主张，不自动作为 Groundtruth。','/proposed_answer')
        reviews=[x for x in repo.related(o['id']) if x['kind']=='question_review']
        if not reviews:add('review_missing','生成题尚无审核记录','insufficient','无法从 solver 全错判断题目无效还是困难。',next_check='补来源证据与独立审核。')
        if not d.get('source_documents') and not o.get('evidence_refs'):add('question_source_missing','生成题来源证据未记录','insufficient','看板不补造可解性或多跳链。',next_check='导入出题时真实读取的文档及审核记录。')
    elif kind=='selfplay_cycle':
        if not d.get('participants'):add('participants_missing','参与者版本未记录','insufficient','无法区分出题者与解题者使用的实际策略。')
        for i,p in enumerate(d.get('participants',[])):
            if not p.get('checkpoint_id') and not p.get('policy_ref'):
                add('participant_policy_missing','参与者 '+p['id']+' 缺少策略版本','insufficient','同一底座不代表同一份权重。','/participants/'+str(i))
        rel=repo.related(o['id'])
        if not any(x['kind']=='proposal' for x in rel):add('proposal_missing','尚无明确出题记录','insufficient','没有根据文件顺序推测互相出题关系。')
    elif kind=='proposal':
        if not d.get('trace_ref'):add('proposer_trace_missing','出题轨迹未记录','insufficient','只有生成题内容不能重建出题过程。',next_check='保存出题者实际可见输入、工具返回和原始生成。')
    elif kind=='solver_attempt':
        if not d.get('trace_ref'):add('solver_trace_missing','解题过程未记录','insufficient','没有补造搜索步骤。')
        if 'reference_used' not in d:add('reference_unknown','评分实际使用的参考答案未记录','insufficient','不能默认使用出题者答案或后来修订答案。')
        if d.get('groundtruth_visible') is True:
            add('answer_assisted','记录声明 solver 看到了参考答案','fact','该尝试不能当作无参考自主成功。','/groundtruth_visible')
        if d.get('group_id') and d.get('advantage') is None:
            add('advantage_missing','有 group ID，但 advantage 未记录','insufficient','reward 相同不证明梯度为零。','/group_id')
    elif kind=='training_use':
        add('training_stage','记录的训练消费阶段：'+d['status'],'fact',
            'selected、sampled、excluded 分开统计；sampled 只是所导入 batch 日志的声明。','/status',next_check='需要时对照训练器 batch、mask 和更新日志。')
        for aid in d['artifact_ids']:
            lin=repo.lineage(aid)
            if lin['restricted']:
                add('restricted_source','训练材料具有验证/测试来源','candidate','已登记来源链包含受限材料；需核查真实用途。','/artifact_ids',
                    [{'object_id':x['id'],'pointer':''} for x in lin['restricted']],next_check='停止把该记录当成独立验证证明；检查完整训练与演化来源。')
            elif lin['unknown_usage']:add('usage_unknown','部分材料用途或来源未知','insufficient','仅检查已登记来源；不能据此认证没有泄漏。','/artifact_ids')
    elif kind=='skill_bank':
        view=adapt_bank(d)
        if not o.get('derived_from') and not o.get('evidence_refs'):
            add('skill_provenance_missing','技能的生成来源未记录','insufficient','可以查看技能内容；不能补写来源成功/失败轨迹。',next_check='关联提炼或演化过程与原始轨迹。')
        for w in view['warnings']:add('bank_format','技能格式提示','insufficient',w)
        if d.get('common_mistakes'):
            add('stored_lesson_not_root','经验教训不是当前轨迹的已证根因','fact','common_mistakes 原文按产物保存，不升级成当前案例的归因。','/common_mistakes')
    elif kind=='skill_retrieval':
        from .skill_tracking import bank_member,input_contains
        if not d.get('mode'):add('retrieval_mode_missing','技能检索方式未记录','insufficient','不默认 template 或 embedding。')
        bank=repo.object(d['bank_id'])['data']
        for i,v in enumerate(d.get('selected',[])):
            m=bank_member(repo,bank,v if isinstance(v,str) else v['member'])
            injections=[x for x in d.get('injections',[]) if bank_member(repo,bank,x['member'])['pointer']==m['pointer']]
            if not injections:
                basis='candidate' if d.get('injections_complete') is True else 'insufficient'
                add('selected_without_injection','入选技能没有对应注入记录',basis,'只有 selected 不能证明模型实际看到技能。','/selected/'+str(i),next_check='核对当轮真实请求和上下文构造日志。')
        for i,x in enumerate(d.get('injections',[])):
            p='/injections/'+str(i);ref=x.get('evidence_ref');rendered=x.get('rendered_text')
            if not ref or not rendered or not visibility_evidence(ref):
                add('injection_unverified','注入缺少实际输入字段证据','insufficient','需要 rendered_text 与指向 input/model_visible 的可定位证据。',p);continue
            got=repo.resolve_ref(ref,owner=o)
            if not input_contains(rendered,got['value']):
                add('injection_fragment_missing','渲染片段未出现在所引用输入中','candidate','只做精确片段核对；可能截断、模板转换、错误引用或内容变化。',p,[got['ref']],next_check='对照完整真实请求和上下文处理版本；不同表达不直接判无效。')
            else:add('injection_fragment_present','在记录的模型输入中核对到技能片段','fact','只证明日志字段含该片段，不证明模型使用它或因此答对。',p,[got['ref']])
    elif kind=='skill_evolution':
        lin=repo.lineage(o['id'])
        if lin['restricted']:add('evaluation_feedback','演化来源包含固定验证/测试材料','candidate','更新外部技能库也会影响后续预测；不更新权重不等于未学习。',refs=[{'object_id':x['id'],'pointer':''} for x in lin['restricted']],next_check='区分演化反馈池与独立 validation/test，核查完整来源。')
        if not d.get('teacher_trace_ref'):add('teacher_trace_missing','教师提炼/改写过程未记录','insufficient','只保留产物差异，不能推断为什么产生这个修改。')
    elif kind=='evaluation_binding':
        add('external_state','本次评测已绑定外部方法状态','fact','评测对象包含权重和 SkillBank/检索状态；不同快照不得只归因为权重提升。')
        if d.get('binding_origin')=='retrospective':add('retrospective_state','外部状态为历史回填','fact','导入元数据不证明远端当时确实执行该配置。','/binding_origin')
    return {'ruleset':RULESET,'findings':findings,'causal_claim':False,'scores_modified':False}
