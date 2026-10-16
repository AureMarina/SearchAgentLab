"""Method family contracts are independent of research project and main/reproduction.

Definitions and observations describe recorded evidence, not inferred causality.
"""
from __future__ import annotations
import math
from ..core import ValidationError

FAMILIES = {
 'skill': {'title':'Skill · 资产与版本', 'kinds':['skill_archive','skill_bank','skill_asset','skill_instance','skill_retrieval','skill_evolution','skill_comparison'],
  'stages':['冻结资产与版本','Agent 运行实例','训练 / 开发生命周期','实例信用与参数更新','固定评测对照'],
  'fields':[
   ['bank_version','版本与内容哈希','区分权重 checkpoint 与外部技能状态'],
   ['skill_identity','group / category / skill_id','版本配对；名称相似不等于同一技能'],
   ['snapshot_step','快照 step 与来源','只证明在该快照中出现，不推断精确创建时刻'],
   ['evolution_source','提炼依据、父技能、接受/拒绝原因','需要显式日志，不能从新增文本倒推'],
   ['retrieval','候选、分数、入选、检索配置','区分可检索与实际入选'],
   ['injection','渲染文本、模型请求引用、注入位置','核对当时实际输入'],
   ['consumption','batch / group / credit / loss mask','入选不等于参与参数更新'],
   ['evaluation','checkpoint / bank hash / 评分协议','库变化与权重变化分别控制']]},
 'self_play': {'title':'Self-play · 供给、信号与 Solver', 'kinds':['selfplay_cycle','generated_question','proposal','question_review','solver_attempt','selfplay_pool_event','selfplay_reference','selfplay_comparison','training_use','training_update'],
  'stages':['角色与轮次','生成题与来源','审核与解题','筛选 / 采样','参数更新与独立验证'],
  'fields':[
   ['participants','角色、模型 revision、轮次','明确谁出题、谁解题；不按文本猜角色'],
   ['question_source','题目、答案、来源证据','检查生成题是否有可验证来源'],
   ['review','审核器版本、结果、原因','不能只记录通过数'],
   ['attempts','全部尝试、reward、原评分','保留失败与同题多次采样'],
   ['consumption','selected / sampled / excluded、batch、step','被选中不等于已用于更新'],
   ['update','输入/输出 checkpoint、参数更新证据','区分流程跑通与真正更新'],
   ['validation','固定题单、检索与评分协议','生成题难度变化不等于自主能力提升']]},
 'opd': {'title':'OPD · 失败现场与监督', 'kinds':['opd_failure','opd_teacher','opd_supervision','opd_diagnostic','opd_recovery'],
  'stages':['学生失败状态','教师信息与接续','监督位置与有效信号','训练消费与更新','独立任务评测'],
  'fields':[
   ['failure','学生 checkpoint、sample、状态与剩余预算','文本前缀不等于可恢复的工具环境'],
   ['teacher','教师版本、额外信息、rescore / continuation','概率重打分不算教师接续成功'],
   ['verification','尝试预算 K、全部分支、环境验证器','失败、错误和未测都保留，不只看接受分支'],
   ['supervision','目标、实际 mask、权重、原始/最终 advantage','乘法重加权不能使零 advantage 自行变成非零'],
   ['diagnostics','完整奖励组、观察对照、逐 token 与分布支持','raw / effective、任务 / 蒸馏信号分别保存'],
   ['recovery','同一失败状态的 teacher / student 续跑','状态、历史、剩余预算和真实验证分别核对'],
   ['update','sampled batch、完成的更新记录','学习项有效不证明该样本造成参数或能力变化'],
   ['evaluation','独立评测与训练专用信息撤去','不将 teacher 的完成率当作 student 的自主成绩']]},
 'entropy': {'title':'Entropy · 熵与探索评测', 'kinds':['entropy_definition','entropy_observation','entropy_probe','entropy_intervention','entropy_comparison'],
  'stages':['量的定义','训练现场 / 固定前缀','实际干预与更新','同预算探索 / 指标有效性','自主问答结果'],
  'fields':[
   ['definition','估计器、单位、实现版本','full-vocab、top-k 和 sampled surprisal 不混写'],
   ['distribution','policy revision、温度、top-k/p、logits 位置','明确原始策略分布还是变换后的采样分布'],
   ['token_scope','区域、mask 定义、有效 token 数','排除 prompt / tool observation 等非动作区域'],
   ['aggregation','token→decision→group 的聚合与权重','同名曲线不自动视为同口径'],
   ['context','training_on_policy / fixed_prefix_probe、prefix hash','不同前缀的熵变化不能当作同状态变化'],
   ['use','reward / loss / sampling 的使用方式与系数','需要训练消费证据，不倒推'],
   ['outcome','续跑预算、后果、对照与固定验证','高熵或低熵都不直接等同于探索质量']]},
 'latent': {'title':'Latent · 状态与干预', 'kinds':['state_snapshot'],
  'stages':['状态定义','携带 / 重置','干预与独立验证'],
  'fields':[['state','形状、dtype、更新与重置规则','不把隐藏态快照视为因果解释'],
            ['intervention','同前缀 carry / reset、预算与后果','需要匹配状态的对照']]}
}

def validate_method_contract(o):
    d=o['data']; kind=o['kind']
    def need(key,typ):
        if not isinstance(d.get(key),typ) or (typ is str and not d[key].strip()):
            raise ValidationError(kind+'.'+key+' is required ('+typ.__name__+')')
    if kind=='method_profile':
        need('families',list)
        if not d['families'] or len(set(d['families']))!=len(d['families']) or any(x not in FAMILIES for x in d['families']):
            raise ValidationError('families must be unique explicit method families; not project names')
        need('method_name',str)
        if 'configuration' in d: need('configuration',dict)
    elif kind=='skill_archive':
        need('bank_ids',list);need('snapshots',list)
        if not 1<=len(d['bank_ids'])<=2000 or len(d['bank_ids'])!=len(d['snapshots']):
            raise ValidationError('skill_archive needs 1–2000 parallel bank_ids/snapshots')
        if len(set(d['bank_ids']))!=len(d['bank_ids']):raise ValidationError('Duplicate bank IDs in archive')
        for s in d['snapshots']:
            if not isinstance(s,dict) or s.get('phase') not in ('initial','training','evaluation'):
                raise ValidationError('Each snapshot needs explicit initial/training/evaluation phase')
            if s.get('step') is not None and (type(s['step']) is not int or s['step']<0):
                raise ValidationError('snapshot step is null or nonnegative integer')
    elif kind=='entropy_definition':
        for key in ('name','estimator','unit'):need(key,str)
        if d['unit'] not in ('nats','bits','dimensionless','score'):raise ValidationError('entropy unit must be nats, bits, dimensionless or score')
        for key in ('distribution','token_scope','aggregation','implementation'):need(key,dict)
    elif kind=='entropy_observation':
        need('definition_id',str)
        if 'value' not in d:raise ValidationError('value required; use null with missing_reason when not measured')
        value=d['value']
        if value is None:
            need('missing_reason',str)
        elif isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            raise ValidationError('entropy value must be finite and nonnegative, or null')
        if d.get('context') not in ('training_on_policy','fixed_prefix_probe','evaluation','unknown'):
            raise ValidationError('Explicit entropy context required')
        if d['context']=='fixed_prefix_probe':
            need('prefix_hash',str)
            import re
            if not re.fullmatch(r'[0-9a-f]{64}',d['prefix_hash']):raise ValidationError('fixed_prefix_probe requires SHA256 prefix_hash')
        for key in ('step','token_count','sample_count'):
            if key in d and d[key] is not None and (type(d[key]) is not int or d[key]<0):
                raise ValidationError(key+' must be a nonnegative integer or null')
        if 'use' in d:need('use',dict)
    validate_iteration_contract(o)


def validate_iteration_contract(o):
    """Additive v0.9.3 contracts; old unmeasured records remain importable.

    Zero is a measurement. Omitted or null observation fields remain unknown.
    Stored evidence is never recomputed by calling a teacher or an environment.
    """
    d=o['data'];kind=o['kind']
    def need(key,typ):
        if not isinstance(d.get(key),typ) or (typ is str and not d[key].strip()):
            raise ValidationError(kind+'.'+key+' is required ('+typ.__name__+')')
    def integer(value,label,minimum=0):
        if value is not None and (type(value) is not int or value<minimum):
            raise ValidationError(label+' must be an integer >= '+str(minimum)+' or null')
    def boolean(value,label):
        if value is not None and not isinstance(value,bool):raise ValidationError(label+' must be boolean or null')
    def number(value,label):
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
            raise ValidationError(label+' must be a finite number or null')
    def verification(value):
        if not isinstance(value,dict):raise ValidationError('verification must be an object')
        if value.get('status') not in ('passed','failed','error','not_run'):
            raise ValidationError('verification.status is passed/failed/error/not_run')
        if value.get('method') is not None and value['method'] not in ('environment','scorer','model_judge','human'):
            raise ValidationError('Unknown verification method')
        if value['status'] in ('passed','failed') and not value.get('verifier'):
            raise ValidationError('A passed/failed verification requires verifier identity/version')
    def signal(value):
        if not isinstance(value,dict):raise ValidationError('signal must be an object')
        for key in ('original_advantage','effective_advantage','loss_weight'):number(value.get(key),key)
        integer(value.get('loss_mask_tokens'),'loss_mask_tokens')
        boolean(value.get('loss_active'),'loss_active')
        if 'loss_mask' in value:
            mask=value['loss_mask']
            if not isinstance(mask,list) or any(type(x) not in (bool,int,float) or not math.isfinite(x) or x<0 for x in mask):
                raise ValidationError('loss_mask must be an array of finite nonnegative mask values')
            observed=sum(x!=0 for x in mask)
            if value.get('loss_mask_tokens') is not None and value['loss_mask_tokens']!=observed:
                raise ValidationError('loss_mask_tokens differs from the recorded nonzero loss_mask entries')
            if not observed and value.get('loss_active') is True:
                raise ValidationError('loss_active conflicts with empty/zero loss_mask')
        # This contract names a *pure multiplicative* advantage transformation.
        # Auxiliary objectives must have their own supervision record.
        if value.get('objective')=='advantage_reweight' and value.get('original_advantage')==0:
            if value.get('effective_advantage') not in (None,0):
                raise ValidationError('Pure advantage_reweight cannot turn zero original advantage into nonzero')
            if value.get('loss_active') is True:
                raise ValidationError('Zero advantage reweight is not an active task signal; log auxiliary loss separately')
        if value.get('loss_active') is True and (value.get('loss_mask_tokens')==0 or value.get('loss_weight')==0):
            raise ValidationError('loss_active conflicts with zero mask or loss weight')
        if value.get('loss_active') is True and value.get('objective') in ('policy_gradient','advantage_reweight') and value.get('effective_advantage')==0:
            raise ValidationError('Zero effective advantage is not an active policy-gradient task signal')
    if kind=='generated_question':
        for key in ('question_uid','stable_question_id'):
            if key in d and (not isinstance(d[key],str) or not d[key].strip()):raise ValidationError(key+' must be a stable nonempty string')
        if d.get('question_uid') and d.get('stable_question_id') and d['question_uid']!=d['stable_question_id']:
            raise ValidationError('question_uid and stable_question_id disagree')
        if 'deduplication' in d:
            if not isinstance(d['deduplication'],dict) or d['deduplication'].get('status') not in ('unique','duplicate','unknown'):
                raise ValidationError('deduplication.status is unique/duplicate/unknown')
        integer(d.get('generation_step'),'generation_step')
    elif kind=='question_review' and 'verification' in d:verification(d['verification'])
    elif kind=='selfplay_cycle' and 'coverage' in d:
        if not isinstance(d['coverage'],dict):raise ValidationError('coverage must be an object')
        for key in ('generated_questions_complete','reviews_complete','consumption_complete','signals_complete'):
            boolean(d['coverage'].get(key),key)
    elif kind=='skill_retrieval':
        boolean(d.get('trajectory_complete'),'trajectory_complete')
        if 'final_input_sha256' in d:
            from ..inspection.repository import digest
            if 'final_input' not in d or digest(d['final_input'])!=d['final_input_sha256']:
                raise ValidationError('final_input_sha256 must match the recorded final_input canonical JSON')
    elif kind=='training_use' and 'signal' in d:signal(d['signal'])
    elif kind=='training_update':
        # Older collectors used their own status vocabulary. Preserve it, and
        # only recognize the exact explicit "completed" value in assessments.
        # Missing or unrecognized statuses are not successful updates.
        pass
    elif kind=='evaluation_binding':
        if 'assessment_families' in d:
            fs=d['assessment_families']
            if not isinstance(fs,list) or len(fs)!=len(set(fs)) or any(x not in FAMILIES for x in fs):
                raise ValidationError('assessment_families must contain distinct known method families')
        for key in ('independent','autonomous'):boolean(d.get(key),key)
    elif kind=='opd_failure':
        for key in ('sample_id','student_checkpoint_id'):need(key,str)
        need('state',dict)
        if d['state'].get('status') not in ('captured','replayed','text_only','not_recorded'):
            raise ValidationError('opd_failure.state.status is captured/replayed/text_only/not_recorded')
        integer(d.get('failure_step'),'failure_step');integer(d.get('attempt_budget'),'attempt_budget',1)
        boolean(d.get('eligible'),'eligible');number(d.get('original_advantage'),'original_advantage')
    elif kind=='opd_teacher':
        for key in ('failure_id','teacher_id'):need(key,str)
        need('context',dict)
        if d.get('mode') not in ('rescore','continuation'):raise ValidationError('opd_teacher.mode is rescore/continuation')
        if d.get('status') not in ('completed','failed','error','not_run'):raise ValidationError('Unknown opd_teacher.status')
        if d['mode']=='continuation':
            integer(d.get('attempt_index'),'attempt_index',1)
            if d.get('attempt_index') is None:raise ValidationError('continuation requires attempt_index')
            if d.get('state_alignment') not in ('restored','replayed','text_only','not_recorded'):
                raise ValidationError('continuation requires explicit state_alignment')
        if 'verification' in d:
            verification(d['verification'])
            if d['mode']=='rescore' and d['verification'].get('method') in ('environment','scorer') and d['verification']['status']=='passed':
                raise ValidationError('A rescore cannot claim environment/scorer-verified continuation success')
    elif kind=='opd_supervision':
        need('failure_id',str);need('teacher_ids',list)
        if not d['teacher_ids'] or len(set(d['teacher_ids']))!=len(d['teacher_ids']):
            raise ValidationError('opd_supervision needs distinct teacher_ids')
        if d.get('objective') not in ('distillation','advantage_reweight','supervised_finetuning','other'):
            raise ValidationError('Unknown opd_supervision objective')
        if d.get('status') not in ('accepted','rejected','error'):raise ValidationError('Unknown opd_supervision status')
        signal(d)


def family_for(kind):
    return next((name for name, spec in FAMILIES.items() if kind in spec['kinds']), None)
