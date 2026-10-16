"""Authenticated loopback receiver behind HTTPS reverse proxy / SSH forwarding.

Not an Internet-facing replacement for an application gateway. All old data-write
routes are denied unless explicitly allowlisted for a center administrator.
"""
from __future__ import annotations
import json
import secrets
import socket
import tempfile
import threading
import time
from collections import deque
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from ..server import LabServer, Handler, STATIC
from ..core import ValidationError, NotFoundError
from ..inspection.repository import strict_json
from ..studio import routes as studio_routes
from .auth import Auth
from .hub import ConflictError, PermissionError
from .package import MAX_ZIP
from .client import endpoint
from .util import file_sha

LOGIN = b'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SearchAgent Lab - Team sign in</title><link rel="stylesheet" href="/studio/team.css"><script type="module" src="/studio/team-login.js"></script><main class="team-login"><div class="team-eyebrow">SEARCHAGENT LAB / TEAM</div><h1>\xe4\xb8\xad\xe5\xbf\x83\xe7\xa0\x94\xe7\xa9\xb6\xe5\xb7\xa5\xe4\xbd\x9c\xe5\x8c\xba</h1><p>\xe4\xbd\xbf\xe7\x94\xa8\xe7\xae\xa1\xe7\x90\x86\xe8\x80\x85\xe6\x88\x96\xe5\x8f\xaa\xe8\xaf\xbb\xe5\x87\xad\xe8\xaf\x81\xe7\x99\xbb\xe5\xbd\x95\xe3\x80\x82\xe4\xb8\x8a\xe4\xbc\xa0\xe5\x87\xad\xe8\xaf\x81\xe4\xbb\x85\xe4\xbe\x9b\xe9\x87\x87\xe9\x9b\x86\xe7\xab\xaf\xe4\xbd\xbf\xe7\x94\xa8\xe3\x80\x82</p><form id="team-login"><label>Access token<input id="team-token" type="password" autocomplete="off" required></label><button type="submit">\xe7\x99\xbb\xe5\xbd\x95</button><p id="team-error" role="status"></p></form><small>HttpOnly session \xc2\xb7 credentials are not saved in browser storage</small></main></html>'''


class TeamServer(LabServer):
    daemon_threads=False
    block_on_close=True
    def __init__(self,address,hub,public_origin=None):
        self.hub=hub;self.auth=Auth(hub.data_dir);self.sessions={}
        self.session_lock=threading.RLock();self.login_attempts=deque();self.slots=threading.BoundedSemaphore(8)
        if public_origin:endpoint(public_origin)
        self.public_origin=public_origin.rstrip('/') if public_origin else None
        super().__init__(address,hub.store,hub=hub)
        if self.public_origin is None:self.public_origin='http://127.0.0.1:'+str(self.server_address[1])
        self.public_host=urlsplit(self.public_origin).netloc
        self.secure_cookie=self.public_origin.startswith('https://')
        self.RequestHandlerClass=TeamHandler
    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):self.shutdown_request(request);return
        try:super().process_request(request,client_address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.slots.release()
    def get_request(self):
        client,address=super().get_request();client.settimeout(120);return client,address


class TeamHandler(Handler):
    response_cookie=None
    def _host_ok(self):
        return super()._host_ok() or self.headers.get('Host','')==self.server.public_host
    def end_headers(self):
        if self.response_cookie:self.send_header('Set-Cookie',self.response_cookie)
        super().end_headers()
    def _principal(self):
        bearer=self.headers.get('Authorization','')
        if bearer.startswith('Bearer '):return self.server.auth.authenticate(bearer[7:])
        try:
            cookie=SimpleCookie(self.headers.get('Cookie',''))
            sid=cookie['sal_session'].value if 'sal_session' in cookie else ''
        except Exception:return None
        with self.server.session_lock:
            entry=self.server.sessions.get(sid)
            if not entry or entry['expires']<=time.time():
                self.server.sessions.pop(sid,None);return None
            return self.server.auth.active(entry['key_id'])
    def _origin_ok(self):
        origin=self.headers.get('Origin')
        if self.headers.get('Authorization','').startswith('Bearer ') and origin is None:return True
        expected=self.server.public_origin if self.headers.get('Host')==self.server.public_host else 'http://'+self.headers.get('Host','')
        return origin==expected
    def _body(self,limit):
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:raise ValidationError('One Content-Length is required; chunked bodies are not accepted')
        try:n=int(self.headers['Content-Length'])
        except ValueError:raise ValidationError('Invalid Content-Length')
        if not 0<n<=limit:raise ValidationError('Request exceeds allowed body size')
        return n
    def _json_body(self,limit=1024*1024):
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValidationError('Expected application/json')
        n=self._body(limit);raw=self.rfile.read(n)
        if len(raw)!=n:raise ValidationError('Incomplete request body')
        return strict_json(raw.decode('utf-8'))
    def _json(self,data,status=200,filename=None):
        if urlsplit(self.path).path=='/api/session' and isinstance(data,dict) and status==200:
            who=self._principal()
            data=data|{'local_only':False,'team':{'enabled':True,'role':who['role'],'owner':who['owner'],
                         'upload_limit_bytes':MAX_ZIP,'deployment':'loopback_receiver'},'read_only':who['role']!='admin'}
        super()._json(data,status,filename)
    def _reject(self,exc):
        if isinstance(exc,PermissionError):self._json({'error':str(exc)},403)
        elif isinstance(exc,ConflictError):self._json({'error':str(exc),'conflict':True},409)
        else:self._error(exc)
    def _send(self,data,content_type,status=200,filename=None):
        if getattr(self,'_buffer_get',False):
            self._get_response=(data,content_type,status,filename);return
        return super()._send(data,content_type,status,filename)
    def do_GET(self):
        self._buffer_get=True;self._get_response=None
        try:self._get_buffered()
        finally:
            self._buffer_get=False
            if self._get_response is not None:
                response=self._get_response;self._get_response=None
                try:self._send(*response)
                except (BrokenPipeError,ConnectionResetError,socket.timeout):pass
    def _get_buffered(self):
        if not self._host_ok():self._json({'error':'Host rejected'},403);return
        parsed=urlsplit(self.path);p=parsed.path
        if p=='/team-login':self._send(LOGIN,'text/html; charset=utf-8');return
        if p in ('/studio/team-login.js','/studio/team.css'):
            self._send((STATIC/p.lstrip('/')).read_bytes(),'text/javascript; charset=utf-8' if p.endswith('.js') else 'text/css; charset=utf-8');return
        principal=self._principal()
        if not principal:
            if p.startswith('/api/'):self._json({'error':'Authentication required'},401)
            else:
                self.send_response(302);self.send_header('Location','/team-login');self.send_header('Content-Length','0');self.send_header('Cache-Control','no-store');self.end_headers()
            return
        if principal['role']=='uploader' and p!='/api/team/receipts':self._json({'error':'Upload-only credential cannot read the shared workspace'},403);return
        q={k:v[0] for k,v in parse_qs(parsed.query).items()}
        try:
            with self.server.hub.lock:
                h=self.server.hub
                if p=='/api/team/runs':self._json({'runs':h.runs(q.get('project',''),q.get('track',''))});return
                if p=='/api/team/run':self._json(h.run(q['run']));return
                if p=='/api/team/receipts':self._json(h.receipts(principal['owner'] if principal['role']=='uploader' else None));return
                if p=='/api/team/package':
                    pid=q['id']
                    row=h.store.conn.execute('SELECT 1 FROM team_receipts WHERE package_id=?',(pid,)).fetchone()
                    if not row:raise NotFoundError('Accepted package not found')
                    path=h.data_dir/'.team'/'received'/(pid+'.zip')
                    self._send(path.read_bytes(),'application/zip',filename='result-'+pid[:16]+'.zip');return
                if p=='/api/team/artifact':
                    row=h.store.conn.execute('SELECT data FROM team_artifacts WHERE run_id=? AND key=?',(q['run'],q['key'])).fetchone()
                    if not row:raise NotFoundError('Artifact not registered')
                    a=json.loads(row[0])
                    if a['storage']!='embedded':raise NotFoundError('Artifact is not archived here; external access has not been verified')
                    path=h.data_dir/'.team'/'blobs'/a['sha256']
                    if not path.is_file() or file_sha(path)!=a['sha256']:raise ValidationError('Archived artifact missing/corrupt; restore from backup')
                    self._send(path.read_bytes(),'application/octet-stream',filename='artifact-'+a['sha256'][:16]+'.bin');return
                if p.startswith('/api/team/'):self._json({'error':'Not found'},404);return
                super().do_GET()
        except (BrokenPipeError,ConnectionResetError,socket.timeout):return
        except Exception as exc:self._reject(exc)
    def do_POST(self):
        if not self._host_ok():self._json({'error':'Host rejected'},403);return
        p=urlsplit(self.path).path
        try:
            if p=='/api/team/login':
                if not self._origin_ok():raise PermissionError('Login origin rejected')
                with self.server.session_lock:
                    t=time.time()
                    while self.server.login_attempts and self.server.login_attempts[0]<t-60:self.server.login_attempts.popleft()
                    if len(self.server.login_attempts)>=30:self._json({'error':'Too many login attempts; retry later'},429);return
                    self.server.login_attempts.append(t)
                d=self._json_body(4096);principal=self.server.auth.authenticate(d.get('token',''))
                if not principal or principal['role']=='uploader':self._json({'error':'Valid viewer/admin token required'},401);return
                sid=secrets.token_urlsafe(32)
                with self.server.session_lock:
                    self.server.sessions={k:v for k,v in self.server.sessions.items() if v['expires']>time.time()}
                    if len(self.server.sessions)>=1000:raise ValidationError('Session capacity reached')
                    self.server.sessions[sid]={'key_id':principal['id'],'expires':min(time.time()+28800,principal['expires_at'])}
                self.response_cookie='sal_session='+sid+'; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800'+('; Secure' if self.server.secure_cookie else '')
                self._json({'authenticated':True,'role':principal['role']});return
            principal=self._principal()
            if not principal:self._json({'error':'Authentication required'},401);return
            if not self._origin_ok():raise PermissionError('Origin rejected')
            if not self.headers.get('Authorization','').startswith('Bearer ') and not secrets.compare_digest(self.headers.get('X-Local-Token',''),self.server.csrf_token):
                raise PermissionError('Session CSRF token required')
            if p=='/api/team/logout':
                cookie=SimpleCookie(self.headers.get('Cookie',''))
                with self.server.session_lock:
                    if 'sal_session' in cookie:self.server.sessions.pop(cookie['sal_session'].value,None)
                self.response_cookie='sal_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'+('; Secure' if self.server.secure_cookie else '')
                self._json({'logged_out':True});return
            if p=='/api/team/packages':
                if principal['role'] not in ('uploader','admin'):raise PermissionError('Read-only credential cannot upload')
                if self.headers.get('Content-Type','').split(';')[0]!='application/zip':raise ValidationError('Expected application/zip')
                n=self._body(MAX_ZIP)
                with tempfile.TemporaryDirectory(prefix='team-upload-') as tmp:
                    path=Path(tmp)/'result.zip'
                    with path.open('wb') as f:
                        left=n
                        while left:
                            b=self.rfile.read(min(left,1024*1024))
                            if not b:raise ValidationError('Incomplete upload; no success receipt was issued')
                            f.write(b);left-=len(b)
                    result=self.server.hub.ingest(path,principal,allow_definitions=False,submitter_owner=self.headers.get('X-Team-Owner'))
                self._json(result,201 if result['delivery_status']=='accepted' else 200);return
            if principal['role']!='admin':raise PermissionError('Center mutations require an administrator')
            if p=='/api/team/plan':
                self._json(self.server.hub.apply_plan(self._json_body()),201);return
            if p in ('/api/studio/project','/api/studio/experiment','/api/studio/profile','/api/studio/presentation'):
                data=self._json_body()
                with self.server.hub.lock,self.server.store.transaction():studio_routes.post(self,p,data)
                return
            self._json({'error':'This legacy write route is disabled in center mode; use approved result packages or plans'},403)
        except (BrokenPipeError,ConnectionResetError,socket.timeout):return
        except Exception as exc:self._reject(exc)
