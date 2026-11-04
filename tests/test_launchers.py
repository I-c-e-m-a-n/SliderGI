"""Opt-in launcher tests, including Windows CMD and PowerShell integration."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from gi_coder.config import ROOT


@unittest.skipUnless(os.environ.get('GI_CODER_LAUNCHERS') == '1', 'Set GI_CODER_LAUNCHERS=1 to test bootstrap launchers')
class LauncherTest(unittest.TestCase):
    def check_launcher(self, prefix):
        with tempfile.TemporaryDirectory(prefix='gi caller ') as cwd:
            result = subprocess.run([*prefix, 'config'], cwd=cwd, text=True, capture_output=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"context_size"', result.stdout)
            self.assertEqual(list(Path(cwd).iterdir()), [])

    @unittest.skipIf(os.name == 'nt', 'Unix launcher only')
    def test_unix(self):
        self.check_launcher([str(ROOT / 'start')])

    @unittest.skipUnless(os.name == 'nt', 'Requires Windows CMD')
    def test_cmd(self):
        self.check_launcher(['cmd.exe', '/d', '/c', str(ROOT / 'start.cmd')])

    @unittest.skipUnless(os.name == 'nt', 'Requires Windows PowerShell')
    def test_powershell(self):
        self.check_launcher(['powershell.exe', '-NoProfile', '-File', str(ROOT / 'start.ps1')])
