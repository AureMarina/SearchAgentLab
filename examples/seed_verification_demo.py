"""Create a SYNTHETIC diagnostic workflow, never run a model or award real EM.

This script only imports invented mechanism fixtures. Use a separate demo dir.
The component replay really executes local pure functions; external/semantic
results below are synthetic test payloads, not model/evaluator executions.
"""
import argparse,copy,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from searchagent_lab.inspection.repository import InspectionRepository,digest
from searchagent_lab.diagnostic_loop.repository import LoopRepository
from searchagent_lab.diagnostic_loop.contracts import contract_template

def seed(data_dir):
    with InspectionRepository(data_dir) as repo,LoopRepository(data_dir) as loop:
        f=repo.import_bytes((ROOT/'examples/verification_demo.jsonl').read_bytes(),'verification_demo.jsonl')
        doc=repo.record(f['id'],1);hash=doc['source_hash'];ctx=doc['raw']['execution_context']
        if loop.objects(doc,'task'):return f  # Do not duplicate a seeded demonstration.
        c=contract_template('synthetic-search-contract');c.update(name='合成 Search Case · 必需检索与 answer 标签',required_tools=['search'],output={'format':'answer_tag'},
            tool_schemas={'search':{'type':'object','required':['query'],'properties':{'query':{'type':'string'}},'additionalProperties':False}},
            hard_gates=['output_contract'])
        loop.bind_contract(doc,{'source_hash':hash,'contract':c})
        h=loop.add_hypothesis(doc,{'source_hash':hash,'hypothesis':{'title':'上下文选择可能丢掉关键关系，而不是模型不会回答',
            'layer':'Action','component':'context_builder','observation':'合成记录中，完整返回包含 Copper Harbor；实际可见内容只保留了无地点的第一段。',
            'evidence':['/steps/1/tool_response/documents/1/text','/steps/1/model_visible','/steps/1/context_transform'],
            'counter_evidence':[],'alternatives':['包含答案词面不等于真实支持；也可能是对同名实体的另一份资料。','模型续跑能否使用保留的证据尚未验证。'],
            'minimal_test':'固定候选文档与字符预算，改变选择顺序；确认渲染差异后，再从空状态回归全部合成题。'}})
        task=loop.add_task(doc,{'source_hash':hash,'task':{'hypothesis_id':h['id'],'title':'固定文档与预算，对比上下文选择',
            'mode':'component','change_candidate':'从 first 改为 last；max_documents=1、max_chars=120 保持一致。',
            'expected_observation':'候选渲染保留地点信息；这一步不证明最终 QA 成功。',
            'allowed_changes':[],'required_paths':[],
            'component':{'operation':'context_render','input_pointer':'/steps/1/tool_response/documents',
                'baseline':{'selection':'first','max_documents':1,'max_chars':120},
                'candidate':{'selection':'last','max_documents':1,'max_chars':120}}}})
        result=loop.execute(doc,{'source_hash':hash,'task_id':task['id']})
        loop.review_hypothesis(doc,{'source_hash':hash,'hypothesis_id':h['id'],'status':'inconclusive','result_id':result['id'],
            'note':'本地渲染结果不同，但未执行真实模型；尚不能宣布修复因果成立。'})
        regression=loop.add_task(doc,{'source_hash':hash,'task':{'hypothesis_id':h['id'],'title':'从头回归：同时检查修复与退化（合成机制示例）',
            'mode':'autonomous','change_candidate':'context_builder v1 → v2，其他推理配置保持一致。','expected_observation':'原错题修复，不损坏原来正确题；预设检查全三题。',
            'allowed_changes':['/context_builder'],'required_paths':['/checkpoint_id','/panel_hash','/retriever','/prompt','/parser','/scorer','/budget','/decoding','/runtime'],
            'expected_cases':[{'dataset':'SYNTHETIC','sample_id':s} for s in ('fixture-1','fixture-2','fixture-3')]}})
        arms=[]
        for label,scores in [('baseline',[0,1,1]),('candidate',[1,0,1])]:
            context=copy.deepcopy(ctx)
            if label=='candidate':context['context_builder']['version']='v2'
            cases=[{'dataset':'SYNTHETIC','sample_id':'fixture-'+str(i+1),'em':score,'runtime_status':'ok','synthetic':True} for i,score in enumerate(scores)]
            arms.append({'evaluation_id':'synthetic-'+label,'trace_sha256':digest(cases),'context':context,'cases':cases,
                'from_empty_state':True,'groundtruth_assisted':False})
        r=loop.import_result(doc,{'source_hash':hash,'task_id':regression['id'],'result':{'task_id':regression['id'],'task_hash':regression['task_hash'],
            'execution':'executed_externally','synthetic_fixture':True,'baseline':arms[0],'candidate':arms[1]}})
        loop.record_fix(doc,{'source_hash':hash,'task_id':regression['id'],'decision':'needs_more_evidence',
            'patch_ref':'synthetic-fixture/context-v2 (not a real patch)','result_id':r['id'],'note':'合成数据展示：1 题修复，1 题退化。拒绝用单条修复宣布整体变好；没有真实模型执行。'})
        return f

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[2]/'eval-data/demos/verification-demo'));args=p.parse_args()
    result=seed(args.data_dir)
    print('SYNTHETIC MECHANISM FIXTURE ONLY — 没有训练或执行模型。')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    print('Start: python -m searchagent_lab inspect --data-dir '+args.data_dir+' --port 8765')
