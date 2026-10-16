"""Local evidence benchmark; always copies the supplied immutable database files."""
import argparse
import hashlib
import json
import shutil
import statistics
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--source', required=True)
p.add_argument('--data', required=True)
p.add_argument('--output', required=True)
p.add_argument('--repeat', type=int, default=30)
p.add_argument('--query', default='the')
a=p.parse_args()
sys.path.insert(0,a.source)
from searchagent_lab.store import Store
from searchagent_lab.server import LabServer

with tempfile.TemporaryDirectory(prefix='searchlab-perf-') as tmp:
    for f in Path(a.data).glob('*.sqlite3'):
        shutil.copy2(f,Path(tmp)/f.name)
    store=Store(tmp)
    server=LabServer(('127.0.0.1',0),store)
    thread=threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base='http://127.0.0.1:'+str(server.server_address[1])
    evs=[dict(r) for r in store.conn.execute('SELECT e.id,e.panel_id,c.run_id,ex.project_id FROM evaluations e JOIN checkpoints c ON e.checkpoint_id=c.id JOIN run_ownership o ON o.run_id=c.run_id JOIN experiments ex ON ex.id=o.experiment_id LEFT JOIN trajectories t ON t.evaluation_id=e.id GROUP BY e.id ORDER BY max(length(t.data)) DESC')]
    chosen=evs[0]
    tids=[r[0] for r in store.conn.execute('SELECT id FROM trajectories ORDER BY length(data) DESC LIMIT 1')]
    endpoints={
       'session': '/api/session',
       'session_brief': '/api/session?brief=1',
       'catalog_main': '/api/studio/catalog?track=main',
       'catalog_reproduction': '/api/studio/catalog?track=reproduction',
       'project_catalog':'/api/catalog?project='+chosen['project_id'],
       'evaluation':'/api/evaluation?id='+chosen['id'],
       'trajectories':'/api/trajectories?evaluation='+chosen['id'],
       'trajectory_search':'/api/trajectories?evaluation='+chosen['id']+'&q='+urllib.parse.quote(a.query),
       'trajectory_detail':'/api/trajectory?id='+tids[0],
       'results':'/api/studio/results?run='+chosen['run_id']+'&panel='+chosen['panel_id'],
    }
    sql_counts={}
    for name,conn in [('store',store.conn),('studio',server.studio.conn),('methods',server.methods.conn),('inspection',server.inspections.conn),('loop',server.loop.conn)]:
        sql_counts[name]=0
        def trace(query, key=name):
            sql_counts[key]+=1
        conn.set_trace_callback(trace)
    results={}
    try:
        for name,path in endpoints.items():
            timings=[]
            counts=[]
            response=None
            for i in range(a.repeat+1):
                sql_counts.update({k:0 for k in sql_counts})
                start=time.perf_counter()
                with urllib.request.urlopen(base+path,timeout=20) as r:
                    response=r.read()
                    status=r.status
                elapsed=(time.perf_counter()-start)*1000
                if i:
                    timings.append(elapsed)
                    counts.append(dict(sql_counts))
            normalized=json.loads(response)
            normalized.pop('csrf_token',None)
            results[name]={
                'path':path,'status':status,'bytes':len(response),
                'body_sha256_without_csrf':hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
                'median_ms':round(statistics.median(timings),3),
                'p95_ms':round(sorted(timings)[int(len(timings)*.95)-1],3),
                'queries':counts[0], 'query_counts_consistent':all(c==counts[0] for c in counts),
            }
        print(json.dumps(results,ensure_ascii=False,indent=2))
        Path(a.output).write_text(json.dumps({'source':a.source,'repeat':a.repeat,'results':results},ensure_ascii=False,indent=2)+'\n')
    finally:
        server.shutdown();server.server_close();thread.join();store.close()
