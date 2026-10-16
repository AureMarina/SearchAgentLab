"""Seed two independent synthetic projects. No model/search/training execution."""
from pathlib import Path
import json,sys,argparse
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from searchagent_lab.methods import MethodRecorder
from searchagent_lab.inspection.repository import digest

def seed(data_dir):
    with MethodRecorder(data_dir) as rec:
        s=rec.store
        specs=[('demo-selfplay','Self-play · 方法示例','sp'),('demo-skillrl','SkillRL · 方法示例','sk')]
        for pid,name,prefix in specs:
            s.register_project({'id':pid,'name':name,'demo':True,'description':'合成界面/机制测试，不是真实训练。'})
            s.register_experiment({'id':pid+'-e01','project_id':pid,'name':'E01 · 合成接入演示','demo':True})
            s.register_run({'id':pid+'-s42','experiment_id':pid+'-e01','name':'seed42 · 合成运行','demo':True,'seed':42,'base_model':'No actual model executed','model_revision':'synthetic-fixture-v1','status':'completed'})
            for step in (0,100):s.register_checkpoint({'id':prefix+'-cp-'+str(step),'run_id':pid+'-s42','step':step,'name':'step'+str(step),'artifact_uri':'synthetic://no-weights/'+str(step)})
            for step in range(0,101,10):s.log_metrics(pid+'-s42',step,{'train/loss':1.1-step*.005,'train/reward':.1+step*.004})
        a=rec.import_file(ROOT/'examples/methods/selfplay.synthetic.json',{'run_id':'demo-selfplay-s42'})
        b=rec.import_file(ROOT/'examples/methods/skillrl.synthetic.json',{'run_id':'demo-skillrl-s42'})
        panel={'id':'method-fixture-panel','name':'2 道合成固定题 · 非真实 validation','version':'v1','demo':True,'split':'probe','samples':[{'id':'fixture-1','dataset':'FixtureQA','question':'合成固定题 A','answers':['fixture-a']},{'id':'fixture-2','dataset':'FixtureQA','question':'合成固定题 B','answers':['fixture-b']}]}
        s.register_panel(panel)
        protocol=json.loads((ROOT/'examples/protocol_v2.synthetic.json').read_text())
        binding=[]
        for step,bank,ncorrect in [(0,'skill-bank-v1',0),(0,'skill-bank-v2',1),(100,'skill-bank-v1',1),(100,'skill-bank-v2',2)]:
            eid='sk-eval-'+str(step)+'-'+bank
            # Idempotent seed: do not append into completed evaluations.
            existing=bool(s.conn.execute('SELECT 1 FROM evaluations WHERE id=?',(eid,)).fetchone())
            if not existing:
                s.start_evaluation({'id':eid,'checkpoint_id':'sk-cp-'+str(step),'panel_id':panel['id'],'protocol':protocol})
                for i,sample in enumerate(panel['samples']):
                    correct=i<ncorrect;ans=sample['answers'][0] if correct else 'synthetic-wrong'
                    s.log_trajectory(eid,{'dataset':sample['dataset'],'sample_id':sample['id'],'scores':{'em':int(correct)},'raw_final_output':ans,'parsed_answer':ans,'runtime_status':'ok','termination_reason':'final_answer','demo':True,
                        'steps':[{'index':0,'kind':'model','input':sample['question'],'output':ans}]})
                s.finish_evaluation(eid)
            binding.append({'id':'bind-'+eid,'kind':'evaluation_binding','demo':True,'usage':'unknown','data':{'evaluation_id':eid,'bank_id':bank,'binding_origin':'recorded_execution','state_complete':True,'retrieval_config':{'mode':'template','version':'synthetic-v1'}},'title':'合成运行快照 · '+eid})
        rec.record_batch(binding,{'run_id':'demo-skillrl-s42'},demo=True)
        return {'selfplay':a,'skillrl':b,'projects':[x[0] for x in specs],'notice':'All trajectories, grades and training curves are synthetic. No GPU/model was used.'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[2]/'eval-data/demos/methods-demo'));args=p.parse_args()
    print(json.dumps(seed(args.data_dir),ensure_ascii=False,indent=2))
