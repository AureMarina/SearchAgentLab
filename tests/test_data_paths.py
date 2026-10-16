"""Workspace paths must be stable and must not couple code to private data."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from searchagent_lab.config import default_data_dir
from searchagent_lab.cli import main
from searchagent_lab.sdk import Recorder


class DataPathsTests(unittest.TestCase):
    def test_source_default_is_outside_repository(self):
        with patch.dict(os.environ, {}, clear=True):
            expected = Path(__file__).resolve().parents[2] / 'eval-data/workspace'
            self.assertEqual(Path(default_data_dir()), expected)

    def test_config_relative_to_config_file_and_environment_overrides_it(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'config.json'
            config.write_text('{"data_dir":"private/workspace"}')
            with patch.dict(os.environ, {'SEARCHAGENT_LAB_CONFIG':str(config)}, clear=True):
                self.assertEqual(default_data_dir(), str(Path(temp)/'private/workspace'))
                os.environ['SEARCHAGENT_LAB_DATA_DIR'] = str(Path(temp)/'other')
                self.assertEqual(default_data_dir(), str(Path(temp)/'other'))

    def test_sdk_and_cli_share_configured_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / 'external'
            with patch.dict(os.environ, {'SEARCHAGENT_LAB_DATA_DIR':str(data)}, clear=True):
                with contextlib.redirect_stdout(io.StringIO()):
                    main(['init'])
                with Recorder() as rec:
                    self.assertEqual(rec.store.db_path, data/'searchagent_lab.sqlite3')

    def test_explicit_cli_and_sdk_path_override_invalid_config(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {'SEARCHAGENT_LAB_CONFIG':str(Path(temp)/'missing.json')}, clear=True):
                target = Path(temp) / 'explicit'
                with contextlib.redirect_stdout(io.StringIO()):
                    main(['init', '--data-dir', str(target)])
                with Recorder(target) as rec:
                    self.assertEqual(rec.store.data_dir, target)

    def test_invalid_config_does_not_create_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp)/'bad.json'
            config.write_text('{"data_dir":null}')
            with patch.dict(os.environ, {'SEARCHAGENT_LAB_CONFIG':str(config)}, clear=True):
                with self.assertRaisesRegex(ValueError, 'Invalid SearchAgent Lab config'):
                    default_data_dir()
            self.assertEqual(list(Path(temp).iterdir()), [config])

    def test_team_commands_use_external_default(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {'SEARCHAGENT_LAB_DATA_DIR':temp}, clear=True):
                with contextlib.redirect_stdout(io.StringIO()):
                    main(['team-init', '--operator', 'synthetic', '--platform', 'synthetic'])
                self.assertTrue((Path(temp)/'.team/source.json').is_file())
