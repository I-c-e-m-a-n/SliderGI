"""Regression tests for wrong upstream HEAD and interrupted setup recovery."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gi_coder.config import CoderError
from gi_coder.setup import prepare_source, run


class SetupRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.upstream = self.root / 'official-fixture'
        self.git('init', self.upstream)
        self.git('-C', self.upstream, 'config', 'user.name', 'Test')
        self.git('-C', self.upstream, 'config', 'user.email', 'test@example.invalid')
        (self.upstream/'source.txt').write_text('pinned version')
        self.git('-C', self.upstream, 'add', '.')
        self.git('-C', self.upstream, 'commit', '-m', 'pinned')
        self.pin = self.git('-C', self.upstream, 'rev-parse', 'HEAD').strip()
        (self.upstream/'source.txt').write_text('moving upstream head')
        self.git('-C', self.upstream, 'commit', '-am', 'new head')
        self.source = self.root/'third_party/llama.cpp'

    def git(self, *args):
        return subprocess.check_output(['git', *map(str, args)], text=True, stderr=subprocess.DEVNULL)

    def local_run(self, command, **kwargs):
        command = list(map(str, command))
        if command[-1] == 'https://github.com/ggml-org/llama.cpp.git':
            command[-1] = str(self.upstream)
        run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def prepare(self):
        with patch('gi_coder.setup.LLAMA_COMMIT', self.pin), patch('gi_coder.setup.run', side_effect=self.local_run):
            prepare_source(self.source)

    def test_fresh_setup_selects_exact_pin_not_default_head(self):
        self.prepare()
        self.assertEqual(self.git('-C', self.source, 'rev-parse', 'HEAD').strip(), self.pin)
        with patch('gi_coder.setup.LLAMA_COMMIT', self.pin), patch('gi_coder.setup.run') as commands:
            prepare_source(self.source)
            commands.assert_not_called()

    def test_wrong_head_preserves_dirty_work_build_and_local_commits(self):
        self.source.parent.mkdir()
        self.git('clone', self.upstream, self.source)
        old_head = self.git('-C', self.source, 'rev-parse', 'HEAD')
        (self.source/'source.txt').write_text('uncommitted work')
        (self.source/'build').mkdir()
        (self.source/'build/artifact').write_bytes(b'old build')
        self.prepare()
        backups = list(self.source.parent.glob('llama.cpp.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0]/'source.txt').read_text(), 'uncommitted work')
        self.assertEqual((backups[0]/'build/artifact').read_bytes(), b'old build')
        self.assertEqual(self.git('-C', backups[0], 'rev-parse', 'HEAD'), old_head)
        self.assertEqual((self.source/'source.txt').read_text(), 'pinned version')

    def test_failed_fetch_is_recoverable_on_next_attempt(self):
        def fail_fetch(command, **kwargs):
            if 'fetch' in command:
                raise CoderError('simulated network interruption')
            self.local_run(command)
        with patch('gi_coder.setup.LLAMA_COMMIT', self.pin), patch('gi_coder.setup.run', side_effect=fail_fetch):
            with self.assertRaises(CoderError):
                prepare_source(self.source)
        self.prepare()
        self.assertEqual(self.git('-C', self.source, 'rev-parse', 'HEAD').strip(), self.pin)
