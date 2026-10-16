"""Font delivery contract: optional install, no DB writes, local-only HTTP.

Small test bytes stand in for the pinned official asset; the release does not
redistribute the font. Original-file/browser checks are recorded separately.
"""
import contextlib
import hashlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from searchagent_lab import ui_fonts
from searchagent_lab.cli import main
from searchagent_lab.server import LabServer
from searchagent_lab.store import Store
from searchagent_lab.team.auth import Auth
from searchagent_lab.team.hub import Hub
from searchagent_lab.team.server import TeamServer


PAYLOAD = b"audited font fixture\0" * 100
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


class FontFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "workspace"
        self.ttf = self.root / "original.ttf"
        self.ttf.write_bytes(PAYLOAD)
        for name, value in (("FONT_SHA256", SHA256), ("FONT_BYTES", len(PAYLOAD))):
            mock = patch.object(ui_fonts, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def install(self):
        return ui_fonts.setup_font(self.data_dir, self.ttf)


class FontInstallTests(FontFixture):
    def test_absent_status_is_read_only_and_does_not_create_workspace(self):
        with patch.object(ui_fonts, "urlopen", side_effect=AssertionError("Unexpected network")):
            result = ui_fonts.font_status(self.data_dir)
            self.assertFalse(result["available"])
            self.assertEqual(result["status"], "not_installed")
            self.assertIsNone(ui_fonts.font_bytes(self.data_dir))
            self.assertFalse(self.data_dir.exists())

    def test_local_install_copies_original_atomically_without_databases(self):
        with patch.object(ui_fonts, "urlopen", side_effect=AssertionError("Unexpected network")):
            result = self.install()
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["source"], "local_file")
        self.assertEqual(ui_fonts.font_bytes(self.data_dir), PAYLOAD)
        self.assertEqual({p.name for p in self.data_dir.iterdir()}, {"ui-fonts"})
        self.assertEqual({p.name for p in (self.data_dir / "ui-fonts").iterdir()}, {ui_fonts.FONT_FILENAME, "font.json"})

    def test_existing_verified_install_skips_network_and_does_not_rewrite(self):
        self.install()
        files = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.data_dir / "ui-fonts").iterdir()}
        with patch.object(ui_fonts, "urlopen", side_effect=AssertionError("Unexpected network")):
            result = ui_fonts.setup_font(self.data_dir)
        self.assertEqual(result["status"], "already_installed")
        self.assertFalse(result["changed"])
        self.assertEqual(files, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files})

    def test_official_download_is_pinned_and_preserves_source(self):
        response = io.BytesIO(PAYLOAD)
        response.geturl = lambda: ui_fonts.OFFICIAL_DOWNLOAD
        with patch.object(ui_fonts, "urlopen", return_value=response) as request:
            result = ui_fonts.setup_font(self.data_dir, timeout=7)
        self.assertEqual(result["source"], "official_download")
        self.assertEqual(request.call_args.args[0].full_url, ui_fonts.OFFICIAL_DOWNLOAD)
        self.assertEqual(request.call_args.kwargs["timeout"], 7)
        self.assertEqual(ui_fonts.font_status(self.data_dir)["source"], "official_download")

    def test_timeout_leaves_no_workspace_and_explains_local_fallback(self):
        with patch.object(ui_fonts, "urlopen", side_effect=URLError("timed out")):
            with self.assertRaisesRegex(ui_fonts.FontSetupError, "--font-file"):
                ui_fonts.setup_font(self.data_dir)
        self.assertFalse(self.data_dir.exists())

    def test_changed_download_is_not_installed(self):
        response = io.BytesIO(b"x" * len(PAYLOAD))
        response.geturl = lambda: ui_fonts.OFFICIAL_DOWNLOAD
        with patch.object(ui_fonts, "urlopen", return_value=response):
            with self.assertRaisesRegex(ui_fonts.FontSetupError, "SHA256"):
                ui_fonts.setup_font(self.data_dir)
        self.assertFalse(self.data_dir.exists())

    def test_local_font_with_matching_size_but_other_hash_is_rejected(self):
        self.ttf.write_bytes(b"x" * len(PAYLOAD))
        with self.assertRaisesRegex(ui_fonts.FontSetupError, "SHA256"):
            self.install()
        self.assertFalse(self.data_dir.exists())

    def test_unapproved_download_redirect_is_rejected(self):
        response = io.BytesIO(PAYLOAD)
        response.geturl = lambda: "https://unverified.example/font.ttf"
        with patch.object(ui_fonts, "urlopen", return_value=response):
            with self.assertRaisesRegex(ui_fonts.FontSetupError, "未核验来源"):
                ui_fonts.setup_font(self.data_dir)
        self.assertFalse(self.data_dir.exists())

    def test_readers_reject_corrupt_font_and_external_symlink(self):
        self.install()
        target = self.data_dir / "ui-fonts" / ui_fonts.FONT_FILENAME
        target.write_bytes(b"x" * len(PAYLOAD))
        self.assertEqual(ui_fonts.font_status(self.data_dir)["status"], "invalid_file")
        self.assertIsNone(ui_fonts.font_bytes(self.data_dir))
        target.unlink()
        target.symlink_to(self.ttf)
        self.assertFalse(ui_fonts.font_status(self.data_dir)["available"])
        self.assertIsNone(ui_fonts.font_bytes(self.data_dir))
        with self.assertRaisesRegex(ui_fonts.FontSetupError, "符号链接"):
            self.install()

    def test_font_directory_symlink_is_not_followed(self):
        self.data_dir.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.data_dir / "ui-fonts").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ui_fonts.FontSetupError, "符号链接"):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(ui_fonts.font_status(self.data_dir)["available"])

    def test_atomic_write_failure_does_not_leave_partial_font(self):
        with patch.object(ui_fonts.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(ui_fonts.FontSetupError, "查看器仍可继续"):
                self.install()
        self.assertFalse(ui_fonts.font_status(self.data_dir)["available"])
        self.assertEqual(list((self.data_dir / "ui-fonts").iterdir()), [])

    def test_cli_setup_bypasses_store_and_reports_optional_failure(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, patch("searchagent_lab.cli.Store", side_effect=AssertionError("DB opened")):
            main(["setup-font", "--data-dir", str(self.data_dir), "--font-file", str(self.ttf)])
        self.assertEqual(json.loads(out.getvalue())["status"], "installed")
        with contextlib.redirect_stderr(io.StringIO()) as err, patch.object(ui_fonts, "urlopen", side_effect=URLError("offline")):
            with self.assertRaises(SystemExit) as exit_code:
                main(["setup-font", "--data-dir", str(self.root / "offline")])
        self.assertEqual(exit_code.exception.code, 2)
        self.assertIn("--font-file", err.getvalue())
        self.assertFalse((self.root / "offline").exists())


class FontHTTPTests(FontFixture):
    def start_server(self, team=False):
        if team:
            self.hub = Hub(self.data_dir)
            self.addCleanup(self.hub.close)
            self.server = TeamServer(("127.0.0.1", 0), self.hub)
            self.viewer, _ = Auth(self.data_dir).issue("font-reader", "viewer")
        else:
            self.store = Store(self.data_dir)
            self.addCleanup(self.store.close)
            self.server = LabServer(("127.0.0.1", 0), self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        def close():
            self.server.shutdown()
            self.thread.join()
            self.server.server_close()
        self.addCleanup(close)

    def request(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            conn.close()

    def test_absent_font_does_not_block_viewer_or_call_network(self):
        self.start_server()
        with patch.object(ui_fonts, "urlopen", side_effect=AssertionError("Unexpected network")):
            status, _, body = self.request("/api/ui/font")
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)["available"])
            self.assertEqual(self.request(ui_fonts.FONT_URL)[0], 404)
            self.assertEqual(self.request("/")[0], 200)

    def test_font_get_head_cache_and_existing_csp(self):
        self.install()
        self.start_server()
        status, headers, body = self.request(ui_fonts.FONT_URL)
        self.assertEqual((status, body), (200, PAYLOAD))
        self.assertEqual(headers["Content-Type"], "font/ttf")
        self.assertEqual(headers["ETag"], '"' + SHA256 + '"')
        self.assertEqual(headers["Cache-Control"], "private, max-age=0, must-revalidate")
        self.assertEqual(headers["Content-Security-Policy"], self.request("/")[1]["Content-Security-Policy"])
        status, head, body = self.request(ui_fonts.FONT_URL, "HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(head["Content-Length"], str(len(PAYLOAD)))
        self.assertEqual(self.request(ui_fonts.FONT_URL, headers={"If-None-Match": headers["ETag"]})[0], 304)
        status, _, body = self.request("/api/ui/font")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["source"], "local_file")

    def test_only_exact_font_resource_is_exposed(self):
        self.install()
        self.start_server()
        for path in ("/fonts/font.json", "/fonts/../font.json", "/fonts/%2e%2e/searchagent_lab.sqlite3", "/fonts/other.ttf"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request(ui_fonts.FONT_URL, headers={"Host": "evil.example"})[0], 403)
        (self.data_dir / "ui-fonts" / ui_fonts.FONT_FILENAME).write_bytes(b"x" * len(PAYLOAD))
        self.assertEqual(self.request(ui_fonts.FONT_URL)[0], 404)
        self.assertFalse(json.loads(self.request("/api/ui/font")[2])["available"])

    def test_team_font_routes_keep_authentication_and_head_support(self):
        self.install()
        self.start_server(team=True)
        self.assertEqual(self.request("/api/ui/font")[0], 401)
        self.assertEqual(self.request(ui_fonts.FONT_URL)[0], 302)
        headers = {"Authorization": "Bearer " + self.viewer}
        self.assertTrue(json.loads(self.request("/api/ui/font", headers=headers)[2])["available"])
        self.assertEqual(self.request(ui_fonts.FONT_URL, headers=headers)[2], PAYLOAD)
        status, _, body = self.request(ui_fonts.FONT_URL, "HEAD", headers)
        self.assertEqual((status, body), (200, b""))


if __name__ == "__main__":
    unittest.main()
