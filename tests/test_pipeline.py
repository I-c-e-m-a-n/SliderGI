"""Deterministic assessment/routing tests; these completions are synthetic."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch

from gi_coder.assessment import assess, SCHEMA
from gi_coder.config import CoderError, load_config
from gi_coder.pipeline import process_request, skeleton_header
from gi_coder.runtime import Runtime, RuntimeFailure
from gi_coder.cli import interactive


def assessment(kind='code', language='rust', filename='greeting.rs', extension='.rs'):
    return {'output_type': kind, 'objective': 'Produce a greeting', 'constraints': [],
            'output_format': 'source_code' if kind == 'code' else 'plain_text',
            'language': language if kind == 'code' else None,
            'filename': filename if kind == 'code' else None,
            'extension': extension if kind == 'code' else None, 'complexity': 'simple',
            'revised_prompt': 'Produce a complete greeting as requested.',
            'functions': [{'name': 'main', 'inputs': 'none', 'behaviour': 'print greeting',
                           'variables': 'none', 'outputs': 'greeting on stdout'}] if kind == 'code' else []}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = Mock()
        self.runtime.complete.return_value = (json.dumps(assessment()), {})
        self.runtime.generate.return_value = ('```rust\nfn main() { println!("hello"); }\n```', {})

    def process(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return process_request(self.runtime, *args, root=self.root,
                                   output_dir=self.root/'output', **kwargs)

    def test_rust_automatic_filename_and_two_calls_same_runtime(self):
        result = self.process('Make a greeting in an appropriate language')
        self.assertEqual(result['path'], self.root/'output/greeting.rs')
        self.assertIn('// GI-Coder function skeleton:', result['path'].read_text())
        self.assertIn('outputs=greeting on stdout', result['path'].read_text())
        self.assertEqual(self.runtime.complete.call_count, 1)
        self.assertEqual(self.runtime.generate.call_count, 1)
        self.assertEqual(list((self.root/'.gi-coder/tmp').iterdir()), [])
        manifest = json.loads(result['path'].with_name('greeting.rs.gi-coder.json').read_text())
        import hashlib
        self.assertEqual(manifest['verification']['source_sha256'], hashlib.sha256(result['path'].read_bytes()).hexdigest())
        self.assertTrue(next((self.root/'.gi-coder/projects').glob('*/source.rs')).is_file())
        self.assertEqual(self.runtime.complete.call_args.kwargs['schema'], SCHEMA)

    def test_text_routes_to_terminal_without_source_files(self):
        self.runtime.complete.side_effect = [(json.dumps(assessment('text')), {}), ('Hello there', {})]
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            result = process_request(self.runtime, 'What is a compiler?', root=self.root)
        self.assertEqual(result['type'], 'text')
        self.assertIn('Hello there', printed.getvalue())
        self.runtime.generate.assert_not_called()
        self.assertFalse((self.root/'output').exists())
        self.assertEqual(list((self.root/'.gi-coder/tmp').iterdir()), [])

    def test_explicit_options_win_over_inferred_preferences(self):
        self.runtime.complete.side_effect = [self.runtime.complete.return_value, (json.dumps({
            'verdict': 'pass', 'evidence': 'Prints greeting', 'evidence_snippets': ['print("hi")'],
            'issues': [], 'missing_requirements': []}), {})]
        self.runtime.generate.return_value = ('```python\nprint("hi")\n```', {})
        result = self.process('Give a greeting', language='python', filename='my_greeting.py')
        self.assertEqual(result['path'].name, 'my_greeting.py')
        self.assertEqual(result['assessment']['language'], 'python')
        with self.assertRaises(CoderError):
            self.process('x', language='python', filename='x.rs')

    def test_unknown_language_extension_and_sidecar(self):
        self.runtime.complete.return_value = (json.dumps(assessment(language='zig', filename='hello.zig', extension='.zig')), {})
        self.runtime.generate.return_value = ('```zig\npub fn main() void {}\n```', {})
        result = self.process('Write Zig greeting', language='zig')
        sidecar = result['path'].with_name('hello.zig.gi-coder.json')
        self.assertEqual(json.loads(sidecar.read_text())['functions'][0]['name'], 'main')

    def test_json_sidecar_and_syntax(self):
        self.runtime.complete.return_value = (json.dumps(assessment(language='json', filename='data.json', extension='.json')), {})
        self.runtime.generate.return_value = ('```json\n{"hello": "world"}\n```', {})
        result = self.process('Write JSON')
        self.assertEqual(json.loads(result['path'].read_text()), {'hello': 'world'})
        self.assertTrue((result['path'].parent/'data.json.gi-coder.json').exists())

    def test_invalid_assessments_fail_closed_and_cleanup(self):
        invalid = ["{}", '```json\n{}\n```', '{"output_type":"text","output_type":"code"}']
        for field, value in [('filename', '../escape.rs'), ('extension', '.exe/evil'),
                             ('complexity', 'impossible'), ('functions', []), ('objective', 3)]:
            data = assessment()
            data[field] = value
            invalid.append(json.dumps(data))
        for text in invalid:
            self.runtime.complete.return_value = (text, {})
            with self.subTest(text=text), self.assertRaises(CoderError):
                self.process('x')
            self.assertEqual(list((self.root/'.gi-coder/tmp').iterdir()), [])
        self.runtime.generate.assert_not_called()

    def test_overwrite_protection_precedes_generation(self):
        path = self.root/'output/greeting.rs'
        path.parent.mkdir()
        path.write_text('original')
        with self.assertRaises(CoderError):
            self.process('greeting')
        self.runtime.generate.assert_not_called()
        self.assertEqual(path.read_text(), 'original')

    def test_sidecar_needs_independent_confirmation(self):
        self.runtime.complete.return_value = (json.dumps(assessment(language='json', filename='data.json', extension='.json')), {})
        directory = self.root/'output'
        directory.mkdir()
        source, sidecar = directory/'data.json', directory/'data.json.gi-coder.json'
        source.write_text('original source')
        sidecar.write_text('original metadata')
        confirmations = []
        def confirm(path):
            confirmations.append(path)
            return path == source
        with self.assertRaises(CoderError):
            self.process('JSON', confirm=confirm)
        self.assertEqual(confirmations, [source, sidecar])
        self.assertEqual(sidecar.read_text(), 'original metadata')
        self.runtime.generate.assert_not_called()

    def test_cleanup_on_cancellation(self):
        self.runtime.generate.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.process('greeting')
        self.assertEqual(list((self.root/'.gi-coder/tmp').iterdir()), [])

    def test_private_assessment_and_revised_prompt_exist_only_during_request(self):
        def generation(*args, **kwargs):
            files = list((self.root/'.gi-coder/tmp').glob('request-*/*'))
            self.assertEqual({p.name for p in files}, {'assessment.json', 'revised-prompt.json'})
            for path in files:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                json.loads(path.read_text())
            return '```rust\nfn main() {}\n```', {}
        self.runtime.generate.side_effect = generation
        self.process('make a greeting')
        self.assertEqual(list((self.root/'.gi-coder/tmp').iterdir()), [])

    def test_transport_failure_stops_runtime(self):
        runtime = Runtime(load_config(), self.root/'unused')
        runtime.process = Mock()
        runtime.process.poll.return_value = None
        with patch.object(runtime, 'request', side_effect=OSError('lost connection')):
            with self.assertRaises(RuntimeFailure):
                runtime.complete('system', 'request')
        runtime.process.terminate.assert_called_once()

    def test_interactive_exits_on_runtime_failure(self):
        entered = MagicMock()
        with patch('gi_coder.cli.Runtime', return_value=entered), \
             patch('gi_coder.cli.prepared', return_value=self.root/'model'), \
             patch('gi_coder.cli.process_request', side_effect=RuntimeFailure('stopped')), \
             patch('builtins.input', return_value='request') as read, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeFailure):
                interactive(load_config())
        read.assert_called_once()
        entered.__enter__.return_value.close.assert_called_once()

    def test_explicit_model_switch_replaces_then_closes_resident_runtime(self):
        first, second = MagicMock(), MagicMock()
        with patch('gi_coder.cli.Runtime', side_effect=[first, second]) as factory, \
             patch('gi_coder.cli.prepared', return_value=self.root/'model'), \
             patch('gi_coder.cli.switch_model') as save, \
             patch('builtins.input', side_effect=['/model 3b', '/exit']), \
             contextlib.redirect_stdout(io.StringIO()):
            interactive(load_config())
        self.assertEqual(factory.call_count, 2)
        first.__enter__.return_value.close.assert_called_once()
        second.__enter__.return_value.close.assert_called_once()
        save.assert_called_once_with('3b')

    def test_header_preserves_shebang_and_blocks_comment_injection(self):
        function = dict(assessment()['functions'][0], behaviour='hi\nraise Exception()')
        source, _ = skeleton_header('#!/usr/bin/env python3\n# coding: utf-8\nprint("hi")\n', 'python', [function])
        self.assertTrue(source.startswith('#!/usr/bin/env python3\n# coding: utf-8\n# GI-Coder'))
        self.assertNotIn('\nraise Exception()', source)
        compile(source, 'test.py', 'exec')

    def test_request_payloads_have_no_history_or_prompt_cache(self):
        runtime = Runtime(load_config(), self.root/'unused.gguf')
        payloads = []
        def response(route, data, **kwargs):
            payloads.append(data)
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]}
        with patch.object(runtime, 'request', side_effect=response):
            runtime.complete('assessment system', 'request-one-secret', schema=SCHEMA)
            runtime.complete('generation system', 'request-one-revised')
            runtime.complete('assessment system', 'request-two')
        self.assertNotIn('request-one', json.dumps(payloads[2]))
        for payload in payloads:
            self.assertFalse(payload['cache_prompt'])
            self.assertEqual(len(payload['messages']), 2)
            self.assertEqual(payload['id_slot'], 0)

    def test_interactive_reuses_one_entered_runtime_until_exit(self):
        entered = MagicMock()
        with patch('gi_coder.cli.Runtime', return_value=entered) as factory, \
             patch('gi_coder.cli.prepared', return_value=self.root/'model'), \
             patch('gi_coder.cli.process_request') as pipeline, \
             patch('builtins.input', side_effect=['first request', 'second request', '/quit']), \
             contextlib.redirect_stdout(io.StringIO()):
            interactive(load_config())
        factory.assert_called_once()
        entered.__enter__.assert_called_once()
        active = entered.__enter__.return_value
        self.assertEqual([call.args[0] for call in pipeline.call_args_list], [active, active])
        active.close.assert_called_once()

    def test_interactive_cleanup_on_eof_interrupt_and_runtime_failure(self):
        for failure in (EOFError(), KeyboardInterrupt()):
            entered = MagicMock()
            with patch('gi_coder.cli.Runtime', return_value=entered), \
                 patch('gi_coder.cli.prepared', return_value=self.root/'model'), \
                 patch('builtins.input', side_effect=failure), contextlib.redirect_stdout(io.StringIO()):
                if isinstance(failure, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        interactive(load_config())
                else:
                    interactive(load_config())
            entered.__enter__.return_value.close.assert_called_once()
