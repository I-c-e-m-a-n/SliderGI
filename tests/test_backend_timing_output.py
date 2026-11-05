"""Deterministic tests; simulated Metal capability/timings are not Mac benchmarks."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from gi_coder.backend import wants_metal, metal_device
from gi_coder.config import CoderError, load_config, ROOT
from gi_coder.assessment import assess, TEXT_FIELDS
from gi_coder.runtime import Runtime, LLAMA_COMMIT
from gi_coder.setup import ensure_engine
from gi_coder.pipeline import process_request
from test_pipeline import assessment


class BackendTimingOutputTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'installation/SliderGI'
        self.root.mkdir(parents=True)
        self.runtime = Mock()

    def test_macos_selection_and_cpu_override(self):
        with patch('gi_coder.backend.platform.system', return_value='Darwin'):
            self.assertTrue(wants_metal({'backend': 'auto', 'gpu_layers': 99}))
            self.assertFalse(wants_metal({'backend': 'cpu', 'gpu_layers': 99}))
            self.assertFalse(wants_metal({'backend': 'metal', 'gpu_layers': 0}))
        with patch('gi_coder.backend.platform.system', return_value='Linux'):
            self.assertFalse(wants_metal({'backend': 'metal'}))

    def test_probe_reports_only_real_engine_device_listing(self):
        with patch('gi_coder.backend.subprocess.run') as run:
            run.return_value.stdout = 'Available devices:\n  Metal: Apple M2 (16384 MiB)\n'
            self.assertEqual(metal_device('/engine'), 'Metal')
            run.return_value.stdout = 'Available devices:\n'
            self.assertIsNone(metal_device('/engine'))
            run.side_effect = subprocess.TimeoutExpired('probe', 15)
            self.assertIsNone(metal_device('/engine'))

    def test_metal_startup_failure_retries_cpu_once(self):
        runtime = Runtime(load_config(), self.base/'model')
        with patch('gi_coder.runtime.discover_engine', return_value=Path('/engine')), \
             patch('gi_coder.runtime.wants_metal', return_value=True), \
             patch('gi_coder.runtime.metal_device', return_value='Metal'), \
             patch.object(runtime, '_start', side_effect=[CoderError('allocation failed'), runtime]) as start, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(runtime.__enter__(), runtime)
        self.assertEqual(start.call_args_list[0].args, (Path('/engine'), 'Metal', 99))
        self.assertEqual(start.call_args_list[1].args, (Path('/engine'), None, 0))

    def test_unsupported_device_loads_cpu_without_retry(self):
        runtime = Runtime(load_config(), self.base/'model')
        with patch('gi_coder.runtime.discover_engine', return_value=Path('/engine')), \
             patch('gi_coder.runtime.wants_metal', return_value=True), \
             patch('gi_coder.runtime.metal_device', return_value=None), \
             patch.object(runtime, '_start', return_value=runtime) as start, \
             contextlib.redirect_stdout(io.StringIO()):
            runtime.__enter__()
        start.assert_called_once_with(Path('/engine'), None, 0)

    def test_actual_backend_needs_confirmed_offloading(self):
        for logs, expected in [('llama_model_load: offloaded 29/29 layers to GPU\n', 'Metal'),
                               ('llama_model_load: offloaded 0/29 layers to GPU\n', 'CPU'), ('', 'CPU')]:
            runtime = Runtime(load_config(), self.base/'model')
            def popen(*args, **kwargs):
                kwargs['stdout'].write(logs.encode())
                kwargs['stdout'].flush()
                child = Mock()
                child.poll.return_value = None
                return child
            with patch('gi_coder.runtime.subprocess.Popen', side_effect=popen) as create, \
                 patch.object(runtime, 'request', return_value={'status': 'ok'}), \
                 contextlib.redirect_stdout(io.StringIO()) as printed:
                runtime._start(Path('/engine'), 'Metal', 12)
                self.assertEqual(runtime.backend, expected)
                self.assertIn('Inference: Local ' + expected, printed.getvalue())
                command = create.call_args.args[0]
                self.assertEqual(command[command.index('--gpu-layers')+1], '12')
                self.assertEqual(command[command.index('--device')+1], 'Metal')
                self.assertEqual(command[command.index('--n-predict')+1], str(load_config()['context_size'] - 64))
                runtime.close()

    def build_mocks(self):
        build = self.root/'third_party/llama.cpp/build'
        build.mkdir(parents=True)
        return build

    def test_legacy_cpu_build_migrates_once_then_reuses_fallback(self):
        build = self.build_mocks()
        with patch('gi_coder.setup.wants_metal', return_value=True), \
             patch('gi_coder.setup.metal_device', return_value=None), \
             patch('gi_coder.setup.discover_engine', return_value=build/'bin/llama-server'), \
             patch('gi_coder.setup.subprocess.run', return_value=Mock(stdout=LLAMA_COMMIT, stderr='')), \
             patch('gi_coder.setup.subprocess.check_output', return_value=LLAMA_COMMIT), \
             patch('gi_coder.setup.shutil.which', return_value='/tool'), \
             patch('gi_coder.setup.prepare_source'), patch('gi_coder.setup.run') as run, \
             contextlib.redirect_stdout(io.StringIO()):
            ensure_engine(load_config(), root=self.root)
            commands = [[str(value) for value in call.args[0]] for call in run.call_args_list]
            self.assertTrue(any('-DGGML_METAL=ON' in command for command in commands))
            self.assertTrue(any('-DGGML_METAL_EMBED_LIBRARY=ON' in command for command in commands))
            run.reset_mock()
            ensure_engine(load_config(), root=self.root)
            run.assert_not_called()

    def test_metal_build_failure_reconfigures_cpu(self):
        build = self.build_mocks()
        failed = False
        def run(command, **kwargs):
            nonlocal failed
            if '-DGGML_METAL=ON' in command:
                failed = True
                raise CoderError('missing Metal compiler')
        with patch('gi_coder.setup.wants_metal', return_value=True), \
             patch('gi_coder.setup.metal_device', return_value=None), \
             patch('gi_coder.setup.discover_engine', return_value=build/'bin/llama-server'), \
             patch('gi_coder.setup.subprocess.run', return_value=Mock(stdout=LLAMA_COMMIT, stderr='')), \
             patch('gi_coder.setup.subprocess.check_output', return_value=LLAMA_COMMIT), \
             patch('gi_coder.setup.shutil.which', return_value='/tool'), \
             patch('gi_coder.setup.prepare_source'), patch('gi_coder.setup.run', side_effect=run) as commands, \
             contextlib.redirect_stdout(io.StringIO()):
            ensure_engine(load_config(), root=self.root)
        self.assertTrue(failed)
        self.assertTrue(any('-DGGML_METAL=OFF' in call.args[0] for call in commands.call_args_list))

    def test_text_always_assessed_revised_and_timed(self):
        self.runtime.complete.side_effect = [(json.dumps(assessment('text')), {'seconds': 999}), ('Hello!', {'seconds': 999})]
        with patch('gi_coder.pipeline.time.monotonic', side_effect=[10, 11, 12.25, 13, 13.8, 15]), \
             contextlib.redirect_stdout(io.StringIO()) as printed:
            result = process_request(self.runtime, 'Hi', root=self.root)
        self.assertEqual(result['timings']['assessment'], 1.25)
        self.assertAlmostEqual(result['timings']['generation'], .8)
        self.assertEqual(result['timings']['total'], 5)
        self.assertIn('Assessment: 1.25s\nGeneration: 0.80s\nTotal: 5.00s', printed.getvalue())
        calls = self.runtime.complete.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn('schema', calls[0].kwargs)
        generation_input = json.loads(calls[1].args[1])
        self.assertEqual(generation_input['original_request'], 'Hi')
        self.assertEqual(generation_input['assessment']['revised_prompt'], assessment('text')['revised_prompt'])
        self.assertFalse((self.base/'installation/SliderGI_Output').exists())

    def test_default_output_is_installation_sibling_and_reused(self):
        self.runtime.complete.return_value = (json.dumps(assessment()), {})
        self.runtime.generate.return_value = ('```rust\nfn main() {}\n```', {})
        caller = self.base/'unrelated caller'
        caller.mkdir()
        with patch('pathlib.Path.cwd', return_value=caller), contextlib.redirect_stdout(io.StringIO()) as printed:
            first = process_request(self.runtime, 'greeting', root=self.root)
            with self.assertRaises(CoderError):
                process_request(self.runtime, 'greeting', root=self.root)
            again = process_request(self.runtime, 'greeting', root=self.root, overwrite=True)
        self.assertEqual(first['path'], self.root.parent/'SliderGI_Output/greeting.rs')
        self.assertEqual(first['path'], again['path'])
        self.assertIn('Assessment:', printed.getvalue())
        self.assertFalse((caller/'greeting.rs').exists())
        self.assertFalse((self.root/'greeting.rs').exists())

    def test_metadata_defaults_to_same_sibling_and_override_still_works(self):
        self.runtime.complete.return_value = (json.dumps(assessment(language='json', filename='data.json', extension='.json')), {})
        self.runtime.generate.return_value = ('```json\n{}\n```', {})
        with contextlib.redirect_stdout(io.StringIO()):
            result = process_request(self.runtime, 'JSON', root=self.root)
            override = process_request(self.runtime, 'JSON', output_dir=self.base/'explicit', root=self.root)
        self.assertTrue((result['path'].parent/'data.json.gi-coder.json').exists())
        self.assertEqual(override['path'].parent, self.base/'explicit')

    def test_assessment_token_budget_is_distinct_and_elapsed_time_is_local(self):
        runtime = Runtime(load_config(), self.base/'model')
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}], 'usage': {'seconds': 999}}
        with patch.object(runtime, 'request', return_value=response) as request, \
             patch('gi_coder.runtime.time.monotonic', side_effect=[1, 2, 4, 6]):
            _, stats = runtime.complete('assess', 'Hi', schema={})
            assessment_payload = request.call_args.args[1]
            runtime.generate('Write Python', 'python')
            code_payload = request.call_args.args[1]
        self.assertGreater(assessment_payload['max_tokens'], 384)
        self.assertLess(assessment_payload['max_tokens'], load_config()['context_size'])
        self.assertEqual(assessment_payload['temperature'], 0)
        self.assertEqual(code_payload['max_tokens'], 768)
        self.assertEqual(stats['seconds'], 1)
        self.assertFalse(assessment_payload['cache_prompt'])

    def test_compact_text_assessment_preserves_understanding_and_revision(self):
        original = assessment('text')
        compact = {key: original[key] for key in TEXT_FIELDS}
        self.runtime.complete.return_value = (json.dumps(compact), {})
        normalized, _ = assess(self.runtime, 'Hi')
        self.assertEqual(normalized, original)
        self.assertLess(len(json.dumps(compact)), len(json.dumps(original)))
        self.runtime.complete.assert_called_once()

    def test_new_config_fields_are_validated(self):
        directory = self.root/'config'
        directory.mkdir()
        (directory/'settings.json').write_bytes((ROOT/'config/settings.json').read_bytes())
        for data in ({'backend': 'cuda'}, {'gpu_layers': -1}, {'gpu_layers': True},
                     {'assessment_max_tokens': 0}, {'assessment_max_tokens': 4096}):
            (directory/'local.json').write_text(json.dumps(data))
            with self.subTest(data=data), self.assertRaises(CoderError):
                load_config(self.root)
