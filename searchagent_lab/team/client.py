"""Disk-backed outbox and a separate uploader process. No training dependency.

Enqueue before sending. Timeouts and lost acknowledgements retry the exact same
package. A 4xx conflict/permission failure is blocked for explicit inspection.
"""
from __future__ import annotations
import http.client
import json
import os
import random
import shutil
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit
from ..core import ValidationError, dumps, now
from ..inspection.repository import strict_json
from .package import read_package, export_run, local_dir
from .util import connect, durable_write, file_sha, process_lock


class DeliveryError(Exception):
    def __init__(self,message,retryable=True,status=None):
        super().__init__(message);self.retryable=retryable;self.status=status


def endpoint(url):
    u=urlsplit(url)
    if u.username or u.password or u.query or u.fragment or u.path not in ('','/') or not u.hostname:
        raise ValidationError('Center URL must be an origin without path, credentials, query or fragment')
    if u.scheme!='https' and not (u.scheme=='http' and u.hostname in ('localhost','127.0.0.1','::1')):
        raise ValidationError('Use HTTPS for remote transport; plaintext HTTP is allowed only over loopback/SSH tunnels')
    try:port=u.port
    except ValueError as e:raise ValidationError('Invalid center port') from e
    return u


def upload(path,url,token,timeout=120):
    u=endpoint(url)
    if not token or any(x in token for x in ('\r','\n')):raise ValidationError('Upload token missing or malformed; supply it through an environment variable')
    m,_,_=read_package(path)
    size=Path(path).stat().st_size
    cls=http.client.HTTPSConnection if u.scheme=='https' else http.client.HTTPConnection
    kwargs={'timeout':timeout}
    if u.scheme=='https':kwargs['context']=ssl.create_default_context()
    conn=cls(u.hostname,u.port,**kwargs)
    try:
        conn.putrequest('POST','/api/team/packages')
        conn.putheader('Authorization','Bearer '+token)
        conn.putheader('Content-Type','application/zip')
        conn.putheader('Content-Length',str(size));conn.endheaders()
        with Path(path).open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):conn.send(chunk)
        response=conn.getresponse();raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise DeliveryError('Oversized server response')
        try:data=strict_json(raw.decode('utf-8'))
        except (ValueError,UnicodeError):raise DeliveryError('Invalid server response',response.status>=500,response.status)
        if response.status not in (200,201):
            # Never follow redirects with a credential. Never persist request headers.
            raise DeliveryError(str(data.get('error','Upload rejected'))[:1000],response.status>=500 or response.status in (408,429),response.status)
        if (data.get('package_id')!=m['package_id'] or data.get('revision')!=m['revision'] or
            data.get('delivery_status') not in ('accepted','duplicate','superseded')):
            raise DeliveryError('Acknowledgement did not match submitted package')
        return data
    except (OSError,http.client.HTTPException) as e:
        raise DeliveryError('Network delivery failed ('+type(e).__name__+'); package retained') from e
    finally:conn.close()


class Outbox:
    def __init__(self,data_dir,max_bytes=1024*1024*1024):
        self.root=local_dir(data_dir)/'outbox';self.root.mkdir(parents=True,exist_ok=True)
        self.max_bytes=max_bytes
        self.conn=connect(self.root/'queue.sqlite3')
        self.conn.executescript('''CREATE TABLE IF NOT EXISTS queue(
            package_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,revision INTEGER NOT NULL,path TEXT NOT NULL,
            file_hash TEXT NOT NULL,size INTEGER NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL,
            next_attempt REAL NOT NULL,created_at TEXT NOT NULL,error TEXT,receipt TEXT);''')
    def close(self):self.conn.close()
    def __enter__(self):return self
    def __exit__(self,*a):self.close()
    def enqueue(self,path):
        m,_,_=read_package(path)
        existing=self.conn.execute('SELECT * FROM queue WHERE package_id=?',(m['package_id'],)).fetchone()
        if existing:return self._row(existing)
        size=Path(path).stat().st_size
        # Include acknowledged packages too: retention must not fill the disk invisibly.
        used=sum(p.stat().st_size for p in self.root.glob('*.zip'))
        if used+size>self.max_bytes or shutil.disk_usage(self.root).free-size<64*1024*1024:
            raise ValidationError('Outbox capacity/low-disk alert: package not enqueued; original local records retained. Archive/prune acknowledged deliveries explicitly')
        dest=self.root/(m['package_id']+'.zip')
        if not dest.exists():durable_write(dest,Path(path).read_bytes(),exclusive=True)
        self.conn.execute('INSERT OR IGNORE INTO queue VALUES (?,?,?,?,?,?,?,0,0,?,NULL,NULL)',
          (m['package_id'],m['run_id'],m['revision'],str(dest),file_sha(dest),size,'pending',now()))
        return self._row(self.conn.execute('SELECT * FROM queue WHERE package_id=?',(m['package_id'],)).fetchone())
    @staticmethod
    def _row(row):
        d=dict(row)
        if d.get('receipt'):d['receipt']=json.loads(d['receipt'])
        return d
    def status(self):
        rows=[self._row(r) for r in self.conn.execute('SELECT * FROM queue ORDER BY rowid DESC')]
        return {'items':rows,'counts':{s:sum(r['state']==s for r in rows) for s in ('pending','blocked','acknowledged')},
                'retained_bytes':sum(p.stat().st_size for p in self.root.glob('*.zip')),'capacity_bytes':self.max_bytes,
                'note':'Training state is independent of delivery state. Acknowledged files remain until explicit prune.'}
    def flush(self,url,token,force=False):
        endpoint(url);result=[]
        with process_lock(self.root/'flush.lock'):
            rows=self.conn.execute('SELECT * FROM queue WHERE state=\'pending\' AND (? OR next_attempt<=?) ORDER BY rowid',(bool(force),time.time())).fetchall()
            for row in rows:
                try:
                    path=Path(row['path'])
                    if not path.is_file() or file_sha(path)!=row['file_hash']:raise DeliveryError('Queued file missing or changed; refusing delivery',False)
                    receipt=upload(path,url,token)
                    self.conn.execute('UPDATE queue SET state=\'acknowledged\',attempts=attempts+1,error=NULL,receipt=? WHERE package_id=?',(dumps(receipt),row['package_id']))
                    result.append({'package_id':row['package_id'],'state':'acknowledged','receipt':receipt})
                except DeliveryError as e:
                    attempts=row['attempts']+1;delay=min(1800,2**min(attempts,10))+random.uniform(0,2)
                    state='pending' if e.retryable else 'blocked'
                    self.conn.execute('UPDATE queue SET state=?,attempts=?,next_attempt=?,error=? WHERE package_id=?',
                                      (state,attempts,time.time()+delay,str(e),row['package_id']))
                    result.append({'package_id':row['package_id'],'state':state,'error':str(e),'http_status':e.status})
        return {'deliveries':result,**self.status()}
    def retry_blocked(self):
        return self.conn.execute('UPDATE queue SET state=\'pending\',next_attempt=0 WHERE state=\'blocked\'').rowcount
    def prune(self):
        count=0
        with process_lock(self.root/'flush.lock'):
            for r in self.conn.execute('SELECT * FROM queue WHERE state=\'acknowledged\''):
                Path(r['path']).unlink(missing_ok=True);count+=1
        return {'pruned_acknowledged_files':count,'receipt_rows_retained':True}


def sync_once(data_dir,runs,url=None,token=None,flush=True):
    results=[]
    with Outbox(data_dir) as box:
        for run in runs:
            try:
                pkg=export_run(data_dir,run)
                queued=box.enqueue(pkg['path'])
                results.append({'run_id':run,'package_id':pkg['package_id'],'state':queued['state']})
                # Outbox owns the fsynced copy. Automatic collector cache is expendable.
                Path(pkg['path']).unlink(missing_ok=True)
            except (ValidationError,OSError,KeyError) as e:
                results.append({'run_id':run,'state':'collection_error','error':str(e)})
        result=box.flush(url,token) if flush else box.status()
        return {'collection':results,**result}
