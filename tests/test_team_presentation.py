"""Workspace grouping uses the existing center admin/Origin/CSRF boundary."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from test_team import plan, seed_source
from searchagent_lab.team.auth import Auth
from searchagent_lab.team.hub import Hub
from searchagent_lab.team.package import export_run
from searchagent_lab.team.server import TeamServer


class TeamPresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        seed_source(root / 'source')
        package = export_run(root / 'source', 'run')
        self.hub = Hub(root / 'center')
        self.hub.apply_plan(plan())
        self.hub.ingest(package['path'])
        auth = Auth(self.hub.data_dir)
        self.admin, _ = auth.issue('administrator', 'admin')
        self.viewer, _ = auth.issue('reader', 'viewer')
        self.uploader, _ = auth.issue('collector', 'uploader', ['e1'])
        self.server = TeamServer(('127.0.0.1', 0), self.hub)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = self.server.public_origin
        status, headers, _ = self.request('/api/team/login', {'token': self.admin}, headers={'Origin': self.origin})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';', 1)[0]
        self.change = {'experiment_id': 'e1', 'family': 'self_distillation', 'is_baseline': False}

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.hub.close()
        self.tmp.cleanup()

    def request(self, path, data=None, *, token=None, headers=None):
        headers = dict(headers or {})
        if token:
            headers['Authorization'] = 'Bearer ' + token
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request('POST' if data is not None else 'GET', path, body, headers)
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.headers), json.loads(raw)
        finally:
            connection.close()

    def admin_headers(self):
        return {'Cookie': self.cookie, 'Origin': self.origin, 'X-Local-Token': self.server.csrf_token}

    def evidence(self):
        tables = ('projects', 'experiments', 'run_ownership', 'runs', 'metrics', 'checkpoints', 'panels',
                  'panel_samples', 'evaluations', 'trajectories', 'trajectory_summaries', 'sx.profiles',
                  'sx.training_batches', 'mx.objects', 'ix.records')
        with self.hub.lock:
            return {table: [tuple(row) for row in self.hub.store.conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                    for table in tables}

    def test_admin_can_save_display_group_without_changing_frozen_evidence(self):
        before = self.evidence()
        status, _, data = self.request('/api/studio/presentation', self.change, headers=self.admin_headers())
        self.assertEqual(status, 201)
        self.assertEqual(data['family'], 'self_distillation')
        self.assertEqual(data['source'], 'explicit')
        self.assertEqual(self.evidence(), before)
        status, _, data = self.request('/api/studio/workspace', token=self.viewer)
        self.assertEqual(status, 200)
        entry = next(x for x in data['experiments'] if x['experiment']['id'] == 'e1')
        self.assertEqual(entry['family'], 'self_distillation')
        self.assertEqual(entry['profile']['track'], 'main')

    def test_viewer_and_uploader_cannot_change_display_group(self):
        before = self.evidence()
        for token in (self.viewer, self.uploader):
            status, _, _ = self.request('/api/studio/presentation', self.change, token=token)
            self.assertEqual(status, 403)
        self.assertEqual(self.evidence(), before)
        self.assertEqual(self.hub.studio.conn.execute('SELECT count(*) FROM workspace_presentations').fetchone()[0], 0)

    def test_cookie_admin_requires_csrf(self):
        headers = self.admin_headers()
        headers.pop('X-Local-Token')
        status, _, data = self.request('/api/studio/presentation', self.change, headers=headers)
        self.assertEqual(status, 403)
        self.assertIn('CSRF', data['error'])
        self.assertEqual(self.hub.studio.conn.execute('SELECT count(*) FROM workspace_presentations').fetchone()[0], 0)

    def test_cookie_admin_requires_matching_origin(self):
        headers = self.admin_headers()
        headers['Origin'] = 'https://different.invalid'
        status, _, data = self.request('/api/studio/presentation', self.change, headers=headers)
        self.assertEqual(status, 403)
        self.assertIn('Origin', data['error'])

    def test_invalid_group_and_unknown_experiment_do_not_create_rows(self):
        for data, expected in ((dict(self.change, family='unknown'), 400),
                               (dict(self.change, experiment_id='unknown'), 404)):
            status, _, _ = self.request('/api/studio/presentation', data, headers=self.admin_headers())
            self.assertEqual(status, expected)
        self.assertEqual(self.hub.studio.conn.execute('SELECT count(*) FROM workspace_presentations').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
