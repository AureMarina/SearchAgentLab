"""Loopback-only HTTP viewer. No remote model calls, no CDN, no file browsing.

This stdlib server is intended for personal/local research use, not untrusted
multi-user production hosting. Use SSH forwarding for a remote training host.
"""
from __future__ import annotations
import csv
import io
import json
import mimetypes
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from . import __version__
from .read_cache import request_scope
from .studio.repository import StudioRepository
from .studio import routes as studio_routes
from .methods.repository import MethodRepository
from .methods import routes as method_routes
from .inspection.repository import InspectionRepository, MAX_FILE_BYTES
from .inspection import routes as inspection_routes
from .diagnostic_loop import routes as loop_routes
from .diagnostic_loop.repository import LoopRepository
from .store import Store, ValidationError, NotFoundError, dumps

STATIC = Path(__file__).resolve().parent / "static"
INSPECTOR_ASSETS = {"/inspector/" + f.name for f in (STATIC / "inspector").glob("*") if f.is_file() and f.suffix in (".js", ".css")}


METHOD_ASSETS = {"/methods/" + f.name for f in (STATIC / "methods").glob("*") if f.is_file() and f.suffix in (".js", ".css")}


STUDIO_ASSETS = {"/studio/" + f.name for f in (STATIC / "studio").glob("*") if f.is_file() and f.suffix in (".js", ".css")}

BUNDLED_ASSETS = {"/assets/" + f.name for f in (STATIC / "assets").glob("*") if f.is_file() and f.suffix in (".js", ".css")}

VENDOR_ASSETS = {"/vendor/" + f.name for f in (STATIC / "vendor").glob("*") if f.is_file() and f.suffix in (".js", ".css", ".LICENSE")}

class LabServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], store: Store, hub=None):
        if address[0] not in ("127.0.0.1", "localhost"):
            raise ValidationError("This local edition binds only to 127.0.0.1 or localhost. Use SSH port forwarding")
        self.store = store
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)
        self.external_hub = hub
        if hub is not None:
            self.inspections, self.loop, self.methods, self.studio = hub.inspections, hub.loop, hub.methods, hub.studio
            return
        self.inspections = InspectionRepository(store.data_dir)
        self.loop = LoopRepository(store.data_dir)
        self.methods = MethodRepository(store.data_dir,store,self.inspections)
        self.studio = StudioRepository(store,self.methods,self.inspections)
        store._team_methods=self.methods
        store._team_studio=self.studio

    def server_close(self):
        super().server_close()
        if getattr(self, "external_hub", None) is not None:
            return
        if getattr(self.store,'_team_methods',None) is getattr(self,'methods',None):del self.store._team_methods
        if getattr(self.store,'_team_studio',None) is getattr(self,'studio',None):del self.store._team_studio
        if hasattr(self, "studio"):
            self.studio.close()
        if hasattr(self, "methods"):
            self.methods.close()
        if hasattr(self, "inspections"):
            self.inspections.close()
        if hasattr(self, "loop"):
            self.loop.close()

    def get_request(self):
        client, address = super().get_request()
        client.settimeout(15)
        return client, address


class Handler(BaseHTTPRequestHandler):
    server_version = "SearchAgentLab/" + __version__
    protocol_version = "HTTP/1.0"

    def log_message(self, format, *args):
        # Avoid printing questions, raw text, or tokens in access logs.
        pass

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed |= {"localhost", "127.0.0.1"}
        return host in allowed

    def _send(self, data: bytes, content_type: str, status: int = 200, filename: str | None = None) -> None:
        import gzip, hashlib
        from urllib.parse import urlsplit
        path=urlsplit(self.path).path
        static=content_type.startswith(('text/javascript','text/css','image/svg+xml','font/ttf')) and not path.startswith('/api/')
        etag='"'+hashlib.sha256(data).hexdigest()+'"' if static else None
        if etag and self.headers.get('If-None-Match')==etag and status==200:
            status=304;data=b''
        accepts={}
        for value in self.headers.get('Accept-Encoding','').split(','):
            parts=value.strip().split(';');q=1.0
            for param in parts[1:]:
                if param.strip().startswith('q='):
                    try:q=float(param.strip()[2:])
                    except ValueError:q=0
            accepts[parts[0].strip()]=q
        compressed=bool(status==200 and len(data)>1024 and not filename and path!='/api/session' and
                        content_type.startswith(('application/json','text/')) and accepts.get('gzip',accepts.get('*',0))>0)
        if compressed:data=gzip.compress(data,compresslevel=4,mtime=0)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if etag:self.send_header('ETag',etag)
        if compressed:self.send_header('Content-Encoding','gzip')
        self.send_header('Vary','Accept-Encoding')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=0, must-revalidate" if static else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, data, status=200, filename=None):
        self._send(dumps(data).encode("utf-8"), "application/json; charset=utf-8", status, filename)

    def _error(self, exc):
        if isinstance(exc, NotFoundError):
            self._json({"error": str(exc)}, 404)
        elif isinstance(exc, (ValidationError, ValueError, TypeError, KeyError)):
            self._json({"error": str(exc)}, 400)
        else:
            # Do not disclose arbitrary filesystem paths or stack traces to a browser.
            print(f"SearchAgent Lab request error: {type(exc).__name__}: {exc}")
            self._json({"error": "Internal error; check the local server console"}, 500)

    def do_HEAD(self):
        self.do_GET()

    @request_scope
    def do_GET(self):
        if not self._host_ok():
            self._json({"error": "Host rejected; use the printed localhost address"}, 403)
            return
        parsed = urlparse(self.path)
        q = {key: values[0] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
        p = parsed.path
        store = self.server.store
        try:
            if p == "/api/ui/font":
                from .ui_fonts import font_status
                self._json(font_status(store.data_dir))
                return
            if p == "/fonts/TsangerYuKai-Regular.ttf":
                from .ui_fonts import font_bytes
                data = font_bytes(store.data_dir)
                if data is None:
                    self._json({"error": "Font not installed or not the verified original"}, 404)
                else:
                    self._send(data, "font/ttf")
                return
            if studio_routes.get(self, p, q):
                return
            if method_routes.get(self, p, q):
                return
            if loop_routes.get(self, p, q):
                return
            if inspection_routes.get(self, p, q):
                return
            if p == "/api/session":
                runs=store.runs()
                if q.get('brief')=='1':
                    keys=('id','name','project_id','experiment_id','base_model','model_revision','seed','status','algorithm','demo')
                    runs=[{k:r.get(k) for k in keys} for r in runs]
                self._json({"version": __version__, "csrf_token": self.server.csrf_token, "projects": store.projects(), "runs": runs, "panels": store.panels(), "local_only": True})
            elif p == "/api/health":
                self._json(store.health())
            elif p == "/api/overview":
                self._json(store.overview(q["run"], q["panel"], q.get("project", ""), q.get("experiment", "")))
            elif p == "/api/catalog":
                self._json(store.catalog(q["project"], q.get("experiment", ""), q.get("model", "")))
            elif p == "/api/experiment":
                self._json(store.experiment(q["id"]))
            elif p == "/api/matrix":
                self._json(store.matrix(q["run"], q["panel"], q.get("dataset", ""), json.loads(q.get("selections", "{}")), q.get("project", "")))
            elif p == "/api/reproducibility":
                self._json(store.reproducibility(q["a"], q["b"]))
            elif p == "/api/audit":
                self._json(store.audit(int(q["id"])))
            elif p == "/api/export/panel":
                self._json(store.panel(q["id"]), filename=q["id"]+"-groundtruth.json")
            elif p == "/api/export/reproducibility":
                self._json(store.reproducibility(q["a"], q["b"]), filename="reproducibility.json")
            elif p == "/api/panel":
                self._json(store.panel(q["id"]))
            elif p == "/api/evaluation":
                self._json(store.evaluation(q["id"]))
            elif p == "/api/trajectories":
                self._json(store.trajectories(q["evaluation"], q.get("dataset", ""), q.get("outcome", ""), q.get("q", ""), int(q.get("offset", "0")), int(q.get("limit", "50"))))
            elif p == "/api/trajectory":
                self._json(store.trajectory(q["id"]))
            elif p == "/api/compare":
                self._json(store.compare(q["a"], q["b"], q.get("dataset", ""), q.get("group", ""), q.get("q", ""), q.get("cross_project")=="1"))
            elif p == "/api/export/trajectory":
                d = store.trajectory(q["id"])
                self._json(d, filename=d["id"] + ".json")
            elif p == "/api/export/evaluation":
                self._json(store.export_evaluation(q["id"]), filename=q["id"] + ".json")
            elif p == "/api/export/csv":
                ev = store.export_evaluation(q["id"])
                out = io.StringIO(newline="")
                fields = ["evaluation_id", "checkpoint_id", "panel_hash", "protocol_hash", "dataset", "sample_id", "em", "f1", "runtime_status", "parsed_answer", "termination_reason", "question"]
                writer = csv.DictWriter(out, fieldnames=fields)
                writer.writeheader()
                for t in ev["trajectories"]:
                    row = {"evaluation_id": q["id"], "checkpoint_id": ev["evaluation"]["checkpoint_id"], "panel_hash": ev["evaluation"]["panel_hash"], "protocol_hash": ev["evaluation"]["protocol_hash"], **{k: t.get(k) for k in fields if k in t}, **{k: t["scores"].get(k) for k in ("em", "f1")}}
                    # Neutralize spreadsheet formulas in all user-controlled text.
                    row = {k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else v) for k, v in row.items()}
                    writer.writerow(row)
                self._send(("\ufeff" + out.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", filename=q["id"] + ".csv")
            elif p in ("/", "/index.html", "/legacy.html", "/app.js", "/style.css", "/favicon.svg", "/reference_evidence.json") or (p.startswith("/inspector/") and p in INSPECTOR_ASSETS) or p in METHOD_ASSETS or p in STUDIO_ASSETS or p in VENDOR_ASSETS or p in BUNDLED_ASSETS:
                name = "index.html" if p == "/" else p[1:]
                path = STATIC / name
                ctype = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json; charset=utf-8", ".LICENSE": "text/plain; charset=utf-8"}[path.suffix]
                self._send(path.read_bytes(), ctype)
            else:
                self._json({"error": "Not found"}, 404)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return
        except Exception as exc:
            self._error(exc)

    def do_POST(self):
        if not self._host_ok():
            self._json({"error": "Host rejected"}, 403)
            return
        origin = self.headers.get("Origin")
        expected_origin = "http://" + self.headers.get("Host", "")
        if (origin is not None and origin != expected_origin) or not secrets.compare_digest(self.headers.get("X-Local-Token", ""), self.server.csrf_token):
            self._json({"error": "Local origin / CSRF token check failed"}, 403)
            return
        if urlparse(self.path).path in ("/api/inspection/upload", "/api/methods/upload"):
            try:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream":
                    self._json({"error":"Upload original UTF-8 bytes as application/octet-stream"},415)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_FILE_BYTES:
                    raise ValidationError("Upload size must be 1 byte to 50 MiB")
                raw = self.rfile.read(length)
                if len(raw) != length: raise ValidationError("Incomplete upload")
                query = {k:v[0] for k,v in parse_qs(urlparse(self.path).query).items()}
                if urlparse(self.path).path == "/api/methods/upload":
                    from .inspection.repository import strict_json
                    scope = strict_json(query['scope']) if query.get('scope') else None
                    self._json(self.server.methods.import_bytes(raw, query.get('name','method.json'),scope,query.get('snapshot_id')),201)
                else:
                    self._json(self.server.inspections.import_bytes(raw, query.get("name","trace.jsonl"), query.get("format","auto")),201)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                return
            except Exception as exc:
                self._error(exc)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._json({"error": "Use application/json"}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 * 1024:
                raise ValidationError("Request must be between 1 byte and 1 MiB")
            raw = self.rfile.read(length)
            from .inspection.repository import strict_json
            data = strict_json(raw.decode('utf-8')) if urlparse(self.path).path.startswith(('/api/methods/','/api/studio/')) else json.loads(raw)
            if studio_routes.post(self, urlparse(self.path).path, data):
                return
            if method_routes.post(self, urlparse(self.path).path, data):
                return
            if loop_routes.post(self, urlparse(self.path).path, data):
                return
            if inspection_routes.post(self, urlparse(self.path).path, data):
                return
            if urlparse(self.path).path == "/api/annotations":
                result = self.server.store.annotate(data["trajectory_id"], data["annotation"])
            elif urlparse(self.path).path == "/api/experiment-notes":
                result = self.server.store.note_experiment(data["experiment_id"], data["note"])
            else:
                self._json({"error": "Not found"}, 404)
                return
            self._json(result, 201)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            return
        except Exception as exc:
            self._error(exc)
