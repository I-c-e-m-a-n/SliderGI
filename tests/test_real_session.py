"""Opt-in genuine Qwen assessment+generation session test; requires real weights."""
import contextlib
import io
import os
from pathlib import Path
import py_compile
import tempfile
import unittest
from unittest.mock import patch

from gi_coder.config import load_config
from gi_coder.models import ensure_model
from gi_coder.pipeline import process_request
from gi_coder.runtime import Runtime
from gi_coder.setup import ensure_engine


@unittest.skipUnless(os.environ.get('GI_CODER_INFERENCE') == '1', 'Requires opt-in real Qwen weights')
class RealSessionTest(unittest.TestCase):
    def test_resident_qwen_assesses_generates_and_answers_with_isolated_context(self):
        config = dict(load_config(), model=os.environ.get('GI_CODER_TEST_MODEL', '7b'), model_path=None)
        ensure_engine(config, offline=True)
        model = ensure_model(config, offline=True)
        with tempfile.TemporaryDirectory(prefix='gi real session ') as directory:
            root = Path(directory)
            runtime = Runtime(config, model)
            with runtime:
                pid = runtime.process.pid
                payloads = []
                original_request = runtime.request
                def capture(route, data=None, **kwargs):
                    if data is not None and route == '/v1/chat/completions':
                        payloads.append(data)
                    return original_request(route, data, **kwargs)
                with patch.object(runtime, 'request', side_effect=capture), contextlib.redirect_stdout(io.StringIO()):
                    code = process_request(runtime, 'Write one complete Python source file containing '
                        'a function add(a, b) returning their sum. Choose a descriptive filename. '
                        'Use request marker alpha_secret_726 only as a comment.', output_dir=root, root=root)
                    self.assertEqual(code['type'], 'code')
                    self.assertEqual(code['path'].suffix, '.py')
                    py_compile.compile(str(code['path']), doraise=True)
                    files = {p.name for p in root.iterdir()}
                    text = process_request(runtime, 'Explain in two English sentences what a compiler does. '
                        'Answer only in prose; do not generate code or a file.', root=root)
                    self.assertEqual(text['type'], 'text')
                    self.assertEqual(files, {p.name for p in root.iterdir()})
                self.assertEqual(runtime.process.pid, pid)
                self.assertIsNone(runtime.process.poll())
                self.assertGreaterEqual(len(payloads), 5)
                import json
                self.assertNotIn('alpha_secret_726', json.dumps(payloads[-2:]))
                self.assertTrue(all(p['cache_prompt'] is False for p in payloads))
            self.assertIsNotNone(runtime.process.poll())
            self.assertEqual(list((root/'.gi-coder/tmp').iterdir()), [])
