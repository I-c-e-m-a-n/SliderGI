"""Real subprocess/HTTP lifecycle tests using a synthetic engine, not Qwen."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

from gi_coder.config import ROOT, load_config
from gi_coder.pipeline import process_request
from gi_coder.runtime import Runtime


@unittest.skipIf(os.name == 'nt', 'macOS/POSIX process and signal integration')
class SessionProcessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='gi session ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = self.root/'fake-engine'
        self.engine.write_bytes((ROOT/'tests/fixtures/fake_engine.py').read_bytes())
        self.engine.chmod(0o700)
        self.config = dict(load_config(), engine_path=str(self.engine), load_timeout=10)

    def test_multiple_two_stage_requests_share_actual_child_pid(self):
        runtime = Runtime(self.config, self.root/'not-a-model')
        with runtime, contextlib.redirect_stdout(io.StringIO()):
            pid = runtime.process.pid
            code = process_request(runtime, 'Write a greeting', output_dir=self.root/'out', root=self.root)
            text = process_request(runtime, 'Explain a compiler', root=self.root)
            for result in (code, text):
                self.assertEqual(result['statistics']['assessment']['engine_pid'], pid)
                self.assertEqual(result['statistics']['generation']['engine_pid'], pid)
            self.assertEqual(runtime.process.pid, pid)
            self.assertIsNone(runtime.process.poll())
            self.assertEqual(code['path'].parent, self.root/'out')
        self.assertIsNotNone(runtime.process.poll())
        self.assertEqual(list((self.root/'.gi-coder/tmp').iterdir()), [])

    def test_terminal_signals_reap_the_actual_child(self):
        harness = '''
import json, sys
from gi_coder.runtime import Runtime
from gi_coder.session import session_signals, SessionTerminated
try:
    with session_signals():
        with Runtime(json.loads(sys.argv[1]), sys.argv[2]) as runtime:
            print(runtime.process.pid, flush=True)
            while True:
                import signal
                signal.pause()
except SessionTerminated as exc:
    sys.exit(128 + exc.signum)
'''
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=signum):
                parent = subprocess.Popen([sys.executable, '-c', harness, json.dumps(self.config), str(self.root/'model')],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                child_pid = None
                try:
                    # Blocking readline is bounded by the runtime's load timeout.
                    line = parent.stdout.readline()
                    self.assertTrue(line.strip().isdigit(), line)
                    child_pid = int(line)
                    parent.send_signal(signum)
                    _, err = parent.communicate(timeout=20)
                    self.assertEqual(parent.returncode, 128 + signum, err)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child_pid, 0)
                finally:
                    if parent.poll() is None:
                        parent.kill()
                        parent.communicate()
                    if child_pid is not None:
                        try:
                            os.kill(child_pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
