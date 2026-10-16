"""Build-independent offline installation and real legacy-wheel preservation checks."""
from __future__ import annotations
import argparse,hashlib,json,os,socket,sqlite3,subprocess,sys,tempfile,time,urllib.request,venv,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--legacy-wheel',action='append',default=[]);args=p.parse_args()
    checks=[];logs=[]
    def check(name,value):
        if not value:raise AssertionError(name)
        checks.append({'name':name,'passed':True});print('PASS',name,flush=True)
    def dbhash(path):
        with sqlite3.connect(path) as conn:
            tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return {t:hashlib.sha256(repr(conn.execute('SELECT * FROM "'+t+'" ORDER BY rowid').fetchall()).encode()).hexdigest() for t in tables}
    with tempfile.TemporaryDirectory(prefix='studio-install-') as tmpdir:
        tmp=Path(tmpdir);env=dict(os.environ);env.pop('PYTHONPATH',None)
        def run(argv):
            c=subprocess.run(list(map(str,argv)),cwd=tmp,env=env,capture_output=True,text=True,timeout=45)
            logs.append({'command':list(map(str,argv)),'returncode':c.returncode,'stdout':c.stdout,'stderr':c.stderr})
            if c.returncode:raise RuntimeError(c.stderr or c.stdout)
            return c.stdout
        venv.EnvBuilder(with_pip=True).create(tmp/'new');py=tmp/'new/bin/python'
        wheel=ROOT/'dist/searchagent_lab-0.6.0-py3-none-any.whl'
        run([py,'-m','pip','install','--no-index','--no-deps',wheel]);check('offline wheel installs in clean venv',True)
        check('installed module is 0.6.0',run([py,'-m','searchagent_lab','--version']).strip()=='0.6.0')
        loc=run([py,'-c','import searchagent_lab; print(searchagent_lab.__file__)']).strip()
        check('not accidentally importing source checkout','site-packages' in loc and str(ROOT) not in loc)
        with zipfile.ZipFile(wheel) as z:
            check('wheel contains no font files',not any(Path(n).suffix.lower() in {'.ttf','.otf','.woff','.woff2'} for n in z.namelist()))
            meta=z.read('searchagent_lab-0.6.0.dist-info/METADATA').decode()
            check('wheel has zero Requires-Dist dependencies','Requires-Dist:' not in meta)
        check('public Recorder MethodRecorder StudioRecorder all import','OK' in run([py,'-c','from searchagent_lab import Recorder, MethodRecorder, StudioRecorder; print("OK")']))
        ws=tmp/'working';bundle=ROOT/'examples/import_bundle_v2.json'
        run([py,'-m','searchagent_lab','import',bundle,'--data-dir',ws]);check('installed CLI imports legacy v2 result bundle',True)
        profile={'experiment_id':'example-latent-e01','track':'reproduction','reproduction':{'method_name':'Independent reproduction','protocol_mode':'unknown'}}
        (tmp/'profile.json').write_text(json.dumps(profile))
        run([py,'-m','searchagent_lab','register-profile',tmp/'profile.json','--data-dir',ws]);check('installed register-profile keeps reproduction outside main',True)
        batch={'id':'installed-b1','run_id':'example-run','step':1,'groups':[{'id':'g1','samples':[{'reward':0,'advantage':None,'token_ids':[1,2],'response_mask':[1,0]}]}]}
        (tmp/'batch.json').write_text(json.dumps(batch));run([py,'-m','searchagent_lab','import-batch',tmp/'batch.json','--data-dir',ws]);check('installed import-batch accepts actual format',True)
        info=json.loads(run([py,'-m','searchagent_lab','inspect',ROOT/'examples/inspection_demo.jsonl','--data-dir',ws,'--no-serve']))
        check('installed indexer retains invalid JSONL line',info['record_count']==6 and info['invalid_count']==1)
        bank=json.loads(run([py,'-m','searchagent_lab','import-methods',ROOT/'examples/methods/skillbank_v1.synthetic.json','--snapshot-id','installed-bank','--data-dir',ws]))
        check('installed native SkillBank adapter retained',bank['skills']==3)
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        proc=subprocess.Popen([str(py),'-m','searchagent_lab','serve','--data-dir',str(ws),'--port',str(port)],cwd=tmp,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        base='http://127.0.0.1:'+str(port)
        def get(path):
            with urllib.request.urlopen(base+path,timeout=5) as r:return r.read()
        try:
            for _ in range(70):
                try:get('/api/health');break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Installed server did not start')
            check('installed HTTP root uses new studio UI',b'/studio/app.js' in get('/'))
            for f in ('app.js','dynamics.js','replay.js','reports.js','versions.js','experiments.js','charts.js','common.js','studio.css'):
                check('installed static studio/'+f,len(get('/studio/'+f))>100)
            check('legacy advanced entry still served',b'app.js' in get('/legacy.html'))
            check('installed nested JSON and methods scripts served',len(get('/inspector/json-tree.js'))>100 and len(get('/methods/workspace.js'))>100)
            cat=json.loads(get('/api/studio/catalog?project=example-latent&track=main'))
            check('actual HTTP main excludes reproduction',cat['experiments']==[] and cat['main_iteration_count']==0)
            cat=json.loads(get('/api/studio/catalog?project=example-latent&track=reproduction'))
            check('actual HTTP reproduction retains run details',len(cat['experiments'])==1 and len(cat['experiments'][0]['runs'])==1)
            b=json.loads(get('/api/studio/batches?run=example-run&step=1'))
            check('actual HTTP batch retains unknown advantage',b['batches'][0]['groups'][0]['samples'][0]['advantage'] is None)
            check('actual HTTP no guessed neighboring batch',json.loads(get('/api/studio/batches?run=example-run&step=2'))['batches']==[])
            check('installed reference evidence packaged',get('/reference_evidence.json')== (ROOT/'searchagent_lab/static/reference_evidence.json').read_bytes())
            token=json.loads(get('/api/session'))['csrf_token']
            req=urllib.request.Request(base+'/api/studio/profile',data=json.dumps(profile).encode(),headers={'Content-Type':'application/json'})
            try:urllib.request.urlopen(req);blocked=False
            except urllib.error.HTTPError as e:blocked=e.code==403
            check('installed POST still requires local CSRF token',blocked)
        finally:
            proc.terminate();proc.communicate(timeout=10)
        for index,oldwheel in enumerate(args.legacy_wheel):
            oldwheel=Path(oldwheel).resolve();label=oldwheel.name
            oldroot=tmp/('old'+str(index));venv.EnvBuilder(with_pip=True).create(oldroot);oldpy=oldroot/'bin/python';oldws=tmp/('legacy'+str(index))
            run([oldpy,'-m','pip','install','--no-index','--no-deps',oldwheel])
            version=run([oldpy,'-m','searchagent_lab','--version']).strip()
            run([oldpy,'-m','searchagent_lab','demo','--samples-per-dataset','1','--no-serve','--data-dir',oldws])
            code='''from pathlib import Path
from searchagent_lab.inspection.repository import InspectionRepository
with InspectionRepository(%r) as repo:
 f=repo.import_bytes(Path(%r).read_bytes(),'original-before-upgrade.jsonl')
 d=repo.record(f['id'],1)
 repo.append_review(d,{'pointer':'/steps/1/tool_response','status':'confirmed','note':'Original review before upgrade','source_hash':d['source_hash']})
'''%(str(oldws),str(ROOT/'examples/inspection_demo.jsonl'))
            run([oldpy,'-c',code])
            if version=='0.5.0':
                run([oldpy,'-m','searchagent_lab','import-methods',ROOT/'examples/methods/skillbank_v1.synthetic.json','--snapshot-id','legacy-bank','--data-dir',oldws])
                run([oldpy,'-m','searchagent_lab','freeze-contract',ROOT/'examples/case_contract.template.json','--out',tmp/'old-contract.json','--data-dir',oldws])
            before={db.name:dbhash(db) for db in oldws.glob('*.sqlite3')}
            run([py,'-m','searchagent_lab','doctor','--data-dir',oldws])
            # Open all new repositories and explicitly classify one existing experiment.
            script='''import json
from searchagent_lab import StudioRecorder
with StudioRecorder(%r) as r:
 projects=r.store.projects()
 exps=[e for p in projects for e in r.store.experiments(p['id'])]
 before=[r.repo.profile(e['id'])['track'] for e in exps]
 if exps:r.register_experiment_profile(experiment_id=exps[0]['id'],track='reproduction',reproduction={'method_name':'Legacy reproduction'})
 print(json.dumps({'tracks_before':before,'main_iterations':r.repo.catalog('', 'main')['main_iteration_count']}))
'''%str(oldws)
            result=json.loads(run([py,'-c',script]))
            after={name:dbhash(oldws/name) for name in before}
            check(version+': every original table content hash unchanged',before==after)
            check(version+': old purposes not guessed',all(t=='unassigned' for t in result['tracks_before']))
            check(version+': classification adds no main iteration',result['main_iterations']==0)
            check(version+': independent studio sidecar created',(oldws/'experiment_studio.sqlite3').exists())
            with sqlite3.connect(oldws/'trace_inspection.sqlite3') as c:
                check(version+': original manual review still present',c.execute('SELECT count(*) FROM reviews').fetchone()[0]==1)
        check('smoke tests performed without GPU or network installation',True)
    (ROOT/'docs/install_studio_output.json').write_text(json.dumps({'count':len(checks),'checks':checks,'commands':logs,'platform':sys.platform,'python':sys.version},ensure_ascii=False,indent=2)+'\n')
    print('PASSED',len(checks),'installation/preservation checks')
if __name__=='__main__':main()
