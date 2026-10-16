"""Entirely synthetic interaction fixture. No external queries or model updates.

Not benchmark data and not results of Dr.Zero. Real Search11 is not touched.
"""
import argparse,json,math,random,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from searchagent_lab.studio.sdk import StudioRecorder
ROOT=Path(__file__).resolve().parents[1]

def seed(directory):
    rng=random.Random(42)
    with StudioRecorder(directory) as rec:
        s=rec.store;r=rec.repo
        for pid,name in [('studio-selfplay','Self-play'),('studio-latent','Latent'),('studio-entropy','Entropy')]:
            s.register_project({'id':pid,'name':name,'demo':True,'description':'合成 UI 工作区；不是实际训练。'})
        specs=[('studio-e01','E01 · 初始模型基线','main'),('studio-e03','E03 · 交替出题与解题','main'),('studio-drzero','Dr.Zero · 独立复现示例','reproduction')]
        for eid,name,track in specs:
            s.register_experiment({'id':eid,'project_id':'studio-selfplay','name':name,'demo':True,'objective':'合成界面验收，不是研究结论。'})
            p={'experiment_id':eid,'track':track,'question':'检查固定题上的行为变化（合成示例）','change':'只演示日志关联；没有运行算法。','constants':'合成固定题单、协议与环境快照','binding_origin':'prospective'}
            if track=='reproduction':p['reproduction']={'method_name':'Dr.Zero','source_url':'https://arxiv.org/abs/2601.07055','protocol_mode':'unknown','code_revision':'unknown','repository_url':'','adaptations':['本例没有实现或执行 Dr.Zero，只演示复现归档界面。'],'published_reference':{'value':None,'note':'未录入论文成绩；不会用合成数字代替。'}}
            r.register_profile(p)
        run_specs=[('studio-run','studio-e03','seed42 · run001'),('studio-base','studio-e01','初始模型 · 接入演示'),('studio-repro-run','studio-drzero','复现 seed42 · demo')]
        for rid,eid,name in run_specs:
            s.register_run({'id':rid,'experiment_id':eid,'name':name,'base_model':'Qwen3-4B-Instruct-2507','model_revision':'synthetic-ui-only','seed':42,'demo':True,'algorithm':'合成 UI fixture · 未运行训练','status':'completed','created_at':'2026-10-04T08:00:00+00:00'})
        questions=[]
        for i in range(1,12):
            ds='TriviaQA' if i<=3 else 'MuSiQue' if i<=5 else '2WikiMQA'
            questions.append({'id':f'SYN-{i:02d}','dataset':ds,'question':f'[合成题 {i:02d}] Which archive contains the field notes for the fictional Aster expedition {i}?','answers':[f'Aster Archive {i}'],'metadata':{'source_id':f'ui-fixture-{i}','source_revision':'synthetic-v1','source_split':'UI-demo-only','selection_reason':'测试逐题结果与回放，不是真实基准样本。'}})
        panel={'id':'studio-synthetic-11','name':'Search11 · 11 道合成演示题','version':'ui-v1','demo':True,'split':'probe','samples':questions}
        s.register_panel(panel)
        protocol=json.loads((ROOT/'examples/protocol_v2.synthetic.json').read_text())
        cp_masks={0:{1,2,3,4,5},50:{2,3,4,5,6,7},100:{2,3,4,6,7,8,9},200:{2,3,4,6,7,9,10}}
        for rid,steps in [('studio-run',[0,50,100,200]),('studio-base',[0]),('studio-repro-run',[0,100])]:
            for step in steps:s.register_checkpoint({'id':rid+'-cp'+str(step),'run_id':rid,'step':step,'name':'初始模型 · step 0' if step==0 else 'checkpoint-'+str(step),'artifact_uri':'synthetic://no-weights/'+rid+'/'+str(step)})
            if rid!='studio-base':
                for step in range(0,max(steps)+1,2):
                    s.log_metrics(rid,step,{'actor/pg_loss':.035*math.sin(step*.4)+rng.uniform(-.025,.025),'train/reward_mean':.21+.0015*step+rng.uniform(-.025,.025),'selfplay/questions_generated':step*2,'selfplay/review_acceptance':.6+.08*math.sin(step/50)})
            for step in steps:
                eid=rid+'-eval'+str(step)
                if not s.conn.execute('SELECT 1 FROM evaluations WHERE id=?',(eid,)).fetchone():
                    s.start_evaluation({'id':eid,'checkpoint_id':rid+'-cp'+str(step),'panel_id':panel['id'],'protocol':protocol,'created_at':f'2026-10-04T08:{step//10:02d}:00+00:00'})
                    for i,sample in enumerate(questions,1):
                        correct=i in cp_masks[step];answer=sample['answers'][0] if correct else f'Other Archive {i}'
                        trace={'demo':True,'dataset':sample['dataset'],'sample_id':sample['id'],'scores':{'em':int(correct)},'raw_final_output':f'<answer>{answer}</answer>','parsed_answer':answer,'runtime_status':'ok','termination_reason':'final_answer','usage':{'model_calls':3,'tool_calls':2,'input_tokens':1820,'output_tokens':296,'latency_ms':6400},'steps':[
                            {'index':0,'kind':'model','title':'读取问题并生成查询','input':[{'role':'user','content':sample['question']}],'output':'<search>Aster expedition field notes</search>','latency_ms':320},
                            {'index':1,'kind':'tool','title':'第一次搜索 · search','tool_call':{'id':'call-s1','name':'search','arguments':{'query':f'Aster expedition {i} field notes'}},'tool_response':{'documents':[{'title':f'Fictional Aster Expedition {i}','id':f'synthetic-doc-{i}','text':f'The fictional expedition {i} stored its field notes in Aster Archive {i}. All content is synthetic.'},{'title':'Unrelated synthetic record','text':'This other archive holds maps only. hidden_signal_8472'}]},'model_visible':f'Expedition {i}: field notes are held by Aster Archive {i}.','latency_ms':820},
                            {'index':2,'kind':'model','title':'根据已保存观察继续查询','input':[{'role':'user','content':sample['question']},{'role':'tool','content':f'Expedition {i}: field notes are held by Aster Archive {i}.'}],'output':f'<search>Aster Archive {i} collection catalogue</search>','latency_ms':410},
                            {'index':3,'kind':'tool','title':'第二次搜索 · search','tool_call':{'id':'call-s2','name':'search','arguments':{'query':f'Aster Archive {i} catalogue'}},'tool_response':{'documents':[{'title':f'Aster Archive {i}','text':f'Aster Archive {i} contains the expedition field notes. Synthetic fixture.'}]},'model_visible':f'Aster Archive {i} contains the expedition field notes.','latency_ms':670},
                            {'index':4,'kind':'final','title':'输出最终答案','input':[{'role':'user','content':sample['question']},{'role':'tool','content':f'Aster Archive {i} contains the expedition field notes.'}],'output':f'<answer>{answer}</answer>','latency_ms':310}]}
                        s.log_trajectory(eid,trace)
                    s.finish_evaluation(eid)
        scope={'run_id':'studio-run'}
        objects=[
          {'id':'studio-cycle03','kind':'selfplay_cycle','title':'Round 03 · 合成双方轨迹','data':{'participants':[{'id':'A','model':'Synthetic proposer','policy_ref':'synthetic-policy-a'},{'id':'B','model':'Synthetic solver','policy_ref':'synthetic-policy-b'}]}},
          {'id':'studio-q-train','kind':'generated_question','title':'训练侧生成题 · 不在固定验证集中','data':{'question':'[合成训练题] Where are the Lumen notes stored?','proposed_answer':'Lumen shelf','source_documents':[{'id':'synthetic-train-doc','text':'Synthetic source for training UI only.'}]}},
          {'id':'studio-proposer','kind':'trace','title':'A 的出题轨迹','data':{'question':'[合成] 生成训练任务','steps':[{'index':0,'kind':'model','input':'Create a synthetic archive question.','output':'Where are the Lumen notes stored?'}]}},
          {'id':'studio-solver','kind':'trace','title':'B 的解题轨迹','data':{'question':'[合成训练题] Where are the Lumen notes stored?','steps':[{'index':0,'kind':'model','input':'Where are the Lumen notes stored?','output':'<search>Lumen notes</search>'},{'index':1,'kind':'tool','tool_call':{'name':'search','arguments':{'query':'Lumen notes'}},'tool_response':'Synthetic Lumen shelf record.','model_visible':'Synthetic Lumen shelf record.'},{'index':2,'kind':'final','output':'<answer>Lumen shelf</answer>'}],'scores':{'em':1},'parsed_answer':'Lumen shelf','runtime_status':'ok'}},
          {'id':'studio-proposal','kind':'proposal','title':'A 出题给 B','data':{'cycle_id':'studio-cycle03','question_id':'studio-q-train','actor_id':'A','solver_actor_id':'B','trace_ref':{'object_id':'studio-proposer'}}},
          {'id':'studio-review','kind':'question_review','title':'合成审核记录','data':{'question_id':'studio-q-train','status':'accepted','reviewer':'synthetic-fixture','verified_answer':'Lumen shelf'}},
          {'id':'studio-attempt','kind':'solver_attempt','title':'B 的尝试 1','data':{'cycle_id':'studio-cycle03','question_id':'studio-q-train','actor_id':'B','trace_ref':{'object_id':'studio-solver'},'reference_used':'Lumen shelf','scores':{'em':1},'reward':1,'advantage':.5,'groundtruth_visible':False}},
          {'id':'studio-use','kind':'training_use','title':'合成采样声明','data':{'artifact_ids':['studio-q-train'],'status':'sampled','batch_id':'studio-b100','step':100}},
          {'id':'studio-update','kind':'training_update','title':'合成训练更新关联','data':{'cycle_id':'studio-cycle03','consumption_ids':['studio-use'],'output_checkpoint_id':'studio-run-cp100'}}]
        for o in objects:o.update(demo=True,usage='training')
        rec.methods.import_bundle({'method_schema':'method-evidence-v1','objects':objects},scope=scope)
        r.record_batch({'id':'studio-b100','run_id':'studio-run','step':100,'demo':True,'behavior_policy':'synthetic-rollout-policy-90','output_checkpoint_id':'studio-run-cp100','groups':[{'id':'studio-group1','samples':[{'id':'train-attempt-1','reward':1,'advantage':.5,'token_ids':[101,42,17,99,102],'response_mask':[0,1,1,0,1],'trace_ref':{'object_id':'studio-solver'}},{'id':'train-attempt-2','reward':0,'advantage':-.5}]}]})
        return {'notice':'全部合成，无模型执行；不是 Dr.Zero 实现或成绩。','project':'studio-selfplay','main_run':'studio-run','reproduction_run':'studio-repro-run','panel':panel['id'],'url':'/#/training?project=studio-selfplay&track=main&run=studio-run&panel='+panel['id']+'&cp=studio-run-cp100'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[2]/'eval-data/demos/studio-demo'));args=p.parse_args()
    print(json.dumps(seed(args.data_dir),ensure_ascii=False,indent=2))
