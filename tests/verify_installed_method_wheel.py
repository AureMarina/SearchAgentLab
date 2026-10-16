"""Run with a clean venv's Python from outside the source directory.
Only the given real SkillRL ZIP is imported; no synthetic training runs are made.
"""
import argparse, gzip, hashlib, json, pathlib, subprocess, sys, tempfile, threading
from urllib.parse import urlencode
from urllib.request import Request, urlopen

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',required=True,type=pathlib.Path)
    p.add_argument('--report',required=True,type=pathlib.Path)
    args=p.parse_args(); checks=[]
    def check(name,value):
        if not value:raise AssertionError(name)
        checks.append({'name':name,'passed':True});print('PASS',name,flush=True)
    import searchagent_lab
    from searchagent_lab.methods.sdk import MethodRecorder
    from searchagent_lab.server import LabServer
    check('installed package is 0.8.1',searchagent_lab.__version__=='0.8.1')
    check('imports come from clean venv, not source',pathlib.Path(sys.prefix) in pathlib.Path(searchagent_lab.__file__).parents)
    check('CLI version from installed wheel',subprocess.check_output([sys.executable,'-m','searchagent_lab','--version'],text=True).strip().endswith('0.8.1'))
    with tempfile.TemporaryDirectory() as tmp:
        cmd=[sys.executable,'-m','searchagent_lab','import-methods',str(args.archive),'--data-dir',tmp]
        imported=json.loads(subprocess.check_output(cmd,text=True));again=json.loads(subprocess.check_output(cmd,text=True))
        check('real ZIP imports through installed CLI',imported['snapshot_count']==23)
        check('repeating installed CLI import reuses archive identity',imported['archive_id']==again['archive_id'])
        with MethodRecorder(tmp) as rec:
            check('no invented experiments or runs',not rec.store.runs() and rec.store.conn.execute('SELECT count(*) FROM experiments').fetchone()[0]==0)
            check('original ZIP preserved in installed package',rec.repo.original(imported['import_id'])['content']==args.archive.read_bytes())
            with LabServer(('127.0.0.1',0),rec.store) as server:
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_address[1])
                def get(path,q=None,headers=None):
                    response=urlopen(Request(base+path+('?' + urlencode(q) if q else ''),headers=headers or {}),timeout=10)
                    with response:return response.read(),dict(response.headers)
                def data(path,q=None):return json.loads(get(path,q)[0])
                try:
                    archive=data('/api/methods/skill-archive',{'id':imported['archive_id']})
                    check('HTTP returns real initial/latest counts',archive['initial_counts']['skill_count']==30 and archive['last_training_counts']['skill_count']==92)
                    frozen=archive['snapshots'][-1];bank=frozen['bank_id']
                    check('frozen evaluation is step100, not latest105',frozen['step']==100 and frozen['counts']['skill_count']==89 and archive['snapshots'][-2]['step']==105)
                    members=data('/api/methods/skill-members',{'bank':bank,'limit':25})
                    check('HTTP member pagination is body-free',members['total']==100 and len(members['items'])==25 and all('content' not in m for m in members['items']))
                    life=data('/api/methods/skill-lifecycle',{'archive':imported['archive_id'],'member':'general_skills/dyn_060'})
                    check('HTTP lifecycle distinguishes missing in frozen bank',life['first_observed']['step']==105 and life['timeline'][-1]['event']=='absent_in_frozen_snapshot')
                    text,headers=get('/studio/method-data.js')
                    check('wheel contains new JavaScript module',b'mountSkillLibrary' in text and bool(headers.get('ETag')))
                    text,_=get('/studio/experiment-page.js')
                    check('wheel includes experiment-owned tab shell',b'mountExperimentPage' in text and b'experiment-overview' in text)
                    text,_=get('/studio/experiment-methods.js')
                    check('wheel includes embedded method panes',b'mountExperimentMethod' in text)
                    text,_=get('/')
                    check('global method metadata sidebar removed from installed page',b'id="s-methods"' not in text)
                    text,_=get('/studio/method-data.css')
                    check('wheel contains new CSS',b'md-' in text)
                    body,headers=get('/api/methods/skill-archive',{'id':imported['archive_id']},{'Accept-Encoding':'gzip'})
                    check('installed HTTP JSON gzip works',headers.get('Content-Encoding')=='gzip' and json.loads(gzip.decompress(body))['last_training_counts']['skill_count']==92)
                finally:server.shutdown();thread.join()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps({'version':searchagent_lab.__version__,'module_path':searchagent_lab.__file__,'count':len(checks),'checks':checks,'source_zip_sha256':hashlib.sha256(args.archive.read_bytes()).hexdigest(),'scope':'clean venv + real local HTTP; no user-server deployment, no model training'},ensure_ascii=False,indent=2))
    print('PASSED',len(checks),'installed-wheel checks')
if __name__=='__main__':main()
