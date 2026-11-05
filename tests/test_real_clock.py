"""Genuine Qwen single-file clock generation, opt-in; never starts the GUI."""
import json
import os
from pathlib import Path
import py_compile
import tempfile
import unittest
from gi_coder.config import load_config
from gi_coder.models import ensure_model
from gi_coder.runtime import Runtime
from gi_coder.setup import ensure_engine
from gi_coder.pipeline import process_request
from clock_project_fixture import REQUEST


@unittest.skipUnless(os.environ.get('GI_CODER_INFERENCE') == '1', 'Requires official weights and explicit opt-in')
class RealClockTest(unittest.TestCase):
    def test_qwen_builds_reviewed_clock_without_executing_it(self):
        config = dict(load_config(), model=os.environ.get('GI_CODER_TEST_MODEL', '7b'), model_path=None)
        ensure_engine(config, offline=True)
        model = ensure_model(config, offline=True)
        with tempfile.TemporaryDirectory(prefix='gi real clock ') as directory:
            root = Path(directory)/'SliderGI'
            root.mkdir()
            with Runtime(config, model) as runtime:
                pid = runtime.process.pid
                result = process_request(runtime, REQUEST, filename='clock.py', root=root)
                self.assertEqual(runtime.process.pid, pid)
                self.assertIsNone(runtime.process.poll())
            py_compile.compile(str(result['path']), doraise=True)
            manifest = json.loads(result['path'].with_name('clock.py.gi-coder.json').read_text())
            self.assertEqual(manifest['status'], 'statically_valid')
            self.assertGreater(len(manifest['plan']['components']), 1)
            self.assertTrue(manifest['reviews'])
            self.assertEqual(manifest['original_request'], REQUEST)
            self.assertNotIn('tkinter', ' '.join(manifest['verification']['inventory']['imports']).lower())
            self.assertIn('pygame', ' '.join(manifest['verification']['inventory']['imports']).lower())
