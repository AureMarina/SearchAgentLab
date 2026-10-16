"""Generate SYNTHETIC streaming metric points, for testing live refresh only.

Run viewer in another terminal with the same --data-dir. This is not a veRL
training script and must not be used to report model performance.
"""
import argparse
import math
import time
import sys
from pathlib import Path

# Allow the source-package quickstart without a global pip installation.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from searchagent_lab import Recorder


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',default='./live_demo_workspace')
    p.add_argument('--steps',type=int,default=30)
    p.add_argument('--interval',type=float,default=.5)
    args=p.parse_args()
    with Recorder(args.data_dir) as recorder:
        recorder.register_project(id='live-demo-project',name='实时日志 · 合成演示',demo=True)
        recorder.register_experiment(id='live-demo-experiment',project_id='live-demo-project',name='无真实模型的日志接入演示',demo=True)
        recorder.register_run(experiment_id='live-demo-experiment',id='live-demo',name='实时日志 · 合成演示',demo=True,algorithm='synthetic generator',base_model='No model loaded')
        recorder.register_panel({'id':'live-demo-panel','name':'待评测的演示题单','version':'v1','demo':True,'samples':[{'id':'q1','dataset':'HotpotQA','question':'Synthetic question for UI setup only.','answers':['Synthetic answer']} ]})
        for step in range(args.steps+1):
            recorder.log_metrics('live-demo',step,{'train/loss':round(math.exp(-step/20),6),'train/reward':round(1-math.exp(-step/35),6)})
            if step%10==0:recorder.register_checkpoint(id=f'live-demo-cp-{step}',run_id='live-demo',step=step,name=f'checkpoint-{step}')
            print(f'Logged synthetic step {step}',flush=True)
            time.sleep(args.interval)
        recorder.set_run_status('live-demo','completed')

if __name__=='__main__':main()
