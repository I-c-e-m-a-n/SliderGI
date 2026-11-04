import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from gi_coder.config import CoderError, ROOT, load_config, switch_model
from gi_coder.models import ensure_model, verify_file, sha256
from gi_coder.generation import choose_language, extract_source, output_path, write_source
from gi_coder.runtime import discover_engine, Runtime
from gi_coder.cli import parser, main, deliver


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/settings.json').write_text((ROOT / 'config/settings.json').read_text())

    def test_config_and_switch(self):
        self.assertEqual(load_config(self.root)['model'], '7b')
        switch_model('3b', self.root)
        self.assertEqual(load_config(self.root)['model'], '3b')
        with self.assertRaises(CoderError):
            switch_model('bad', self.root)

    def test_invalid_configuration(self):
        for data in ({'threads': 0}, {'max_tokens': 2048}, {'model': 'other'}, {'model_path': 42}):
            (self.root / 'config/local.json').write_text(json.dumps(data))
            with self.subTest(data=data), self.assertRaises(CoderError):
                load_config(self.root)
        (self.root / 'config/local.json').write_text('{')
        with self.assertRaises(CoderError):
            load_config(self.root)

    def model_fixture(self):
        path = self.root / 'model.gguf'
        path.write_bytes(b'GGUF-test-fixture-not-a-real-model')
        config = load_config(self.root)
        config['model_path'] = str(path)
        return path, config

    def test_hash_and_size_failures(self):
        path, _ = self.model_fixture()
        verify_file(path, sha256(path), path.stat().st_size)
        for digest, size in [('0' * 64, path.stat().st_size), (sha256(path), 2)]:
            with self.assertRaises(CoderError):
                verify_file(path, digest, size)
        path.write_bytes(b'HTML')
        with self.assertRaises(CoderError):
            verify_file(path, sha256(path), 4)

    def test_download_checks_official_metadata_and_reuses_offline(self):
        config = load_config(self.root)
        path = self.root / 'models/qwen2.5-coder-7b-instruct-q4_k_m.gguf'
        content = b'GGUF-fake-test-only'
        import hashlib
        digest = hashlib.sha256(content).hexdigest()
        entry = Mock(rfilename=path.name, size=len(content), lfs=Mock(sha256=digest))
        info = Mock(sha='official-revision', siblings=[entry])
        def download(**kwargs):
            self.assertEqual(kwargs['repo_id'], 'Qwen/Qwen2.5-Coder-7B-Instruct-GGUF')
            self.assertEqual(kwargs['revision'], 'official-revision')
            path.write_bytes(content)
            return str(path)
        with patch('huggingface_hub.HfApi') as api, patch('huggingface_hub.hf_hub_download', side_effect=download) as fetch:
            api.return_value.model_info.return_value = info
            self.assertEqual(ensure_model(config, self.root), path)
            self.assertEqual(ensure_model(config, self.root, offline=True, full_verify=True), path)
            self.assertEqual(fetch.call_count, 1)
        path.write_bytes(b'GGUF-corrupted')
        with self.assertRaises(CoderError):
            ensure_model(config, self.root, offline=True, full_verify=True)

    def test_missing_model_offline(self):
        with self.assertRaisesRegex(CoderError, 'Run online setup'):
            ensure_model(load_config(self.root), self.root, offline=True)

    def test_insufficient_disk(self):
        entry = Mock(rfilename='qwen2.5-coder-7b-instruct-q4_k_m.gguf', size=1000,
                     lfs=Mock(sha256='0'*64))
        with patch('huggingface_hub.HfApi') as api, patch('gi_coder.models.shutil.disk_usage') as usage:
            api.return_value.model_info.return_value = Mock(sha='revision', siblings=[entry])
            usage.return_value.free = 1
            with self.assertRaisesRegex(CoderError, 'Insufficient disk'):
                ensure_model(load_config(self.root), self.root)

    def test_failed_download(self):
        with patch('huggingface_hub.HfApi') as api:
            api.return_value.model_info.side_effect = OSError('offline')
            with self.assertRaisesRegex(CoderError, 'Model setup failed'):
                ensure_model(load_config(self.root), self.root)

    def test_engine_discovery(self):
        config = load_config(self.root)
        with self.assertRaises(CoderError):
            discover_engine(config, self.root)
        engine = self.root / 'third_party/llama.cpp/build/bin/Release/llama-server.exe'
        engine.parent.mkdir(parents=True)
        engine.touch()
        self.assertEqual(discover_engine(config, self.root), engine)
        config['engine_path'] = 'external/llama-server'
        with self.assertRaises(CoderError):
            discover_engine(config, self.root)

    def test_cli_parsing(self):
        args = parser().parse_args(['generate', 'Make a calculator', '-l', 'python', '-f', 'calc.py', '-o', 'out', '--overwrite'])
        self.assertTrue(args.overwrite)
        self.assertEqual(args.filename, 'calc.py')
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser().parse_args(['generate'])

    def test_languages(self):
        self.assertEqual(choose_language('write C++ hello'), 'cpp')
        self.assertEqual(choose_language('calculator', filename='calc.py'), 'python')
        self.assertEqual(choose_language('thing', 'js'), 'javascript')
        for args in [('thing', None, None), ('python to javascript', None, None), ('x', 'python', 'x.js')]:
            with self.subTest(args=args), self.assertRaises(CoderError):
                choose_language(*args)

    def test_source_extraction(self):
        code = 'def f():\n    return "é"\n'
        self.assertEqual(extract_source('Here is the code:\n```python\n' + code + '```\nDone.', 'python'), code)
        self.assertEqual(extract_source(code, 'python'), code)
        for text in ('```python\nx =\n```', '```python\nx=1', '```js\nlet x=1;\n```', 'An explanation',
                     '```python\nx=1\n```\n```python\nx=2\n```'):
            with self.subTest(text=text), self.assertRaises(CoderError):
                extract_source(text, 'python')

    def test_output_directory_and_extension(self):
        self.assertEqual(output_path('python', 'hello', 'out', self.root), self.root/'out/hello.py')
        self.assertEqual(output_path('python', cwd=self.root), self.root/'generated.py')
        for filename in ('../escape.py', '/tmp/a.py', 'C:\\a.py', 'a.js'):
            with self.subTest(filename=filename), self.assertRaises(CoderError):
                output_path('python', filename, cwd=self.root)

    def test_write_overwrite_and_encoding(self):
        path = self.root / 'nested/a.py'
        write_source(path, 'print("é")\n')
        self.assertEqual(path.read_bytes(), 'print("é")\n'.encode('utf-8'))
        with self.assertRaises(CoderError):
            write_source(path, 'other')
        self.assertIn('é', path.read_text())
        write_source(path, 'new\n', overwrite=True)
        self.assertEqual(path.read_text(), 'new\n')

    def test_generation_pipeline_mocked(self):
        runtime = Mock()
        runtime.generate.return_value = ('```python\nprint("hello")\n```', {'completion_tokens': 8})
        path = output_path('python', 'hello.py', 'chosen', self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            deliver(runtime, 'Python hello', 'python', path)
        import py_compile
        py_compile.compile(str(path), doraise=True)
        self.assertEqual(path.parent, self.root / 'chosen')
        with self.assertRaises(CoderError):
            deliver(runtime, 'Python hello', 'python', path)
        self.assertEqual(runtime.generate.call_count, 1)

    def test_no_file_on_inference_or_syntax_failure(self):
        runtime = Mock()
        path = self.root / 'failure.py'
        runtime.generate.side_effect = CoderError('failed inference')
        with self.assertRaises(CoderError), contextlib.redirect_stdout(io.StringIO()):
            deliver(runtime, 'x', 'python', path)
        self.assertFalse(path.exists())
        runtime.generate.side_effect = None
        runtime.generate.return_value = ('```python\nx =\n```', {})
        with self.assertRaises(CoderError), contextlib.redirect_stdout(io.StringIO()):
            deliver(runtime, 'x', 'python', path)
        self.assertFalse(path.exists())

    def test_cli_failure_exit_code(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['generate', 'ambiguous request']), 1)

    def test_runtime_truncation_and_empty(self):
        runtime = Runtime(load_config(self.root), self.root/'test.gguf')
        for choice in ({'finish_reason': 'length', 'message': {'content': 'x=1'}},
                       {'finish_reason': 'stop', 'message': {'content': ''}}):
            with patch.object(runtime, 'request', return_value={'choices': [choice]}), self.assertRaises(CoderError):
                runtime.generate('x', 'python')


if __name__ == '__main__':
    unittest.main()
