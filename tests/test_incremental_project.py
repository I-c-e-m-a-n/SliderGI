import ast
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gi_coder.config import CoderError
from gi_coder.pipeline import process_request
from gi_coder.project import PLAN_SCHEMA, REVIEW_SCHEMA, assemble, validate_plan, generate_project
from gi_coder.verification import verify_source
from clock_project_fixture import REQUEST, SOURCES, plan, PASS
from test_pipeline import assessment


class IncrementalProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'SliderGI'
        self.root.mkdir()
        self.runtime = Mock()
        self.runtime.config = {'max_components': 16, 'max_requirements': 16, 'project_timeout': 60}
        self.calls = []
        self.plan = plan()
        self.sources = copy.deepcopy(SOURCES)
        self.review = copy.deepcopy(PASS)
        self.semantic_clock_check = False
        def respond(system, prompt, schema=None, **kwargs):
            context = json.loads(prompt)
            self.calls.append((schema, context))
            if schema == PLAN_SCHEMA:
                return json.dumps(self.plan), {}
            if schema == REVIEW_SCHEMA:
                if self.semantic_clock_check:
                    joined = '\n'.join(context['source'].values())
                    omissions = []
                    if 'pygame.K_SPACE' not in joined:
                        omissions.append('analog/digital switching')
                    if 'pygame.K_c' not in joined:
                        omissions.append('colour changes')
                    if 'range(0)' in joined:
                        omissions.append('hour notches')
                    if omissions:
                        return json.dumps({**PASS, 'verdict': 'fail', 'issues': omissions}), {}
                return json.dumps(self.review), {}
            if schema is None:
                manifest = json.loads(next((self.root/'.gi-coder/projects').glob('*/manifest.json')).read_text())
                completed = [n for n, status in manifest['progress'].items() if status == 'implemented']
                if completed:
                    checkpoint = next((self.root/'.gi-coder/projects').glob('*/source.py')).read_text()
                    self.assertEqual(verify_source(checkpoint)['source_sha256'], manifest['verification']['source_sha256'])
                return '```python\n' + self.sources[context['component']['name']] + '```', {}
            data = assessment(language='python', filename='clock.py', extension='.py')
            data.update(complexity='complex', constraints=['Do not use tkinter', 'Animated hands', 'Switch displays', 'Change colours'])
            return json.dumps(data), {}
        self.runtime.complete.side_effect = respond

    def run_clock(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return process_request(self.runtime, REQUEST, root=self.root)

    def test_clock_is_incrementally_integrated_and_manifest_matches_actual_source(self):
        result = self.run_clock()
        path = result['path']
        source = path.read_text()
        manifest = json.loads(path.with_name(path.name+'.gi-coder.json').read_text())
        checked = verify_source(source, manifest['plan'])
        self.assertEqual(checked['source_sha256'], manifest['verification']['source_sha256'])
        self.assertEqual(set(manifest['progress'].values()), {'verified'})
        self.assertEqual(len(manifest['reviews']), 4)
        steps = [ctx['component']['name'] for schema, ctx in self.calls if schema is None]
        self.assertLess(steps.index('hand_angles'), steps.index('draw_analog'))
        self.assertEqual(steps[-1], 'main')
        for schema, ctx in self.calls:
            if schema is None:
                self.assertNotIn('source', ctx)
                self.assertNotIn('original_request', ctx)
                self.assertEqual(set(ctx['dependency_signatures']), set(ctx['component']['depends_on']))
                self.assertEqual({v['name'] for v in ctx['shared_variables']},
                                 set(ctx['component']['uses']) & {'WIDTH', 'HEIGHT', 'COLOURS'})
        self.assertIn('while state[\'running\']', source)
        self.assertIn('pygame.K_SPACE', source)
        self.assertIn('pygame.K_c', source)
        self.assertIn('timer.tick(60)', source)
        self.assertEqual(manifest['verification']['inventory']['imports'],
                         ['from __future__ import annotations', 'import math', 'import time', 'import pygame'])
        self.assertEqual(path.parent, self.root.parent/'SliderGI_Output')
        self.runtime.generate.assert_not_called()

    def test_compile_only_is_insufficient_disconnected_clock_helper_is_rejected(self):
        self.sources['main'] = self.sources['main'].replace('draw_analog(surface, now, colour)', 'surface.fill(colour)')
        with self.assertRaisesRegex(CoderError, 'Disconnected'):
            self.run_clock()
        self.assertFalse((self.root.parent/'SliderGI_Output/clock.py').exists())
        manifest = json.loads(next((self.root/'.gi-coder/projects').glob('*/manifest.json')).read_text())
        self.assertEqual(manifest['status'], 'failed')

    def test_semantic_review_detects_connected_but_missing_switch_or_colour_behavior(self):
        self.semantic_clock_check = True
        for omitted, broken in [('Space switching', SOURCES['handle_event'].replace('pygame.K_SPACE', '999')),
                                ('C colour changes', SOURCES['handle_event'].replace('pygame.K_c', '998'))]:
            self.sources['handle_event'] = broken
            with self.subTest(feature=omitted), self.assertRaisesRegex(CoderError, 'Requirement verification failed'):
                self.run_clock()
        self.assertFalse((self.root.parent/'SliderGI_Output/clock.py').exists())

    def test_nonexistent_review_evidence_and_forbidden_import_fail(self):
        self.review['evidence_snippets'] = ['this is an invented implementation']
        with self.assertRaisesRegex(CoderError, 'nonexistent'):
            self.run_clock()
        self.review = copy.deepcopy(PASS)
        self.plan['imports'].append('import tkinter')
        with self.assertRaisesRegex(CoderError, 'Forbidden import'):
            self.run_clock()

    def test_handwritten_clock_fixture_control_and_time_math_not_model_execution(self):
        import math, types
        # Execute only our handwritten unit-test functions, never a model response.
        fake = types.SimpleNamespace(QUIT=1, KEYDOWN=2, K_SPACE=3, K_c=4)
        namespace = {'pygame': fake, 'COLOURS': [(1,), (2,), (3,)], 'math': math,
                     'time': types.SimpleNamespace(localtime=lambda _: types.SimpleNamespace(tm_sec=15, tm_min=30, tm_hour=18))}
        exec(compile(SOURCES['handle_event'] + '\n' + SOURCES['hand_angles'], '<handwritten-test-fixture>', 'exec'), namespace)
        state = {'running': True, 'digital': False, 'colour': 0}
        namespace['handle_event'](types.SimpleNamespace(type=2, key=3), state)
        self.assertTrue(state['digital'])
        for _ in range(4):
            namespace['handle_event'](types.SimpleNamespace(type=2, key=4), state)
        self.assertEqual(state['colour'], 1)
        angles = namespace['hand_angles'](1000.5)
        self.assertAlmostEqual(angles[2], 15.5 * math.pi / 30)
        self.assertAlmostEqual(angles[1], (30 + 15.5 / 60) * math.pi / 30)
        self.assertAlmostEqual(angles[0], (6 + (30 + 15.5 / 60) / 60) * math.pi / 6)

    def test_signature_failure_does_not_advance_manifest_or_publish(self):
        self.sources['handle_event'] = 'def handle_event(event):\n    return event\n'
        with self.assertRaisesRegex(CoderError, 'Signature mismatch'):
            self.run_clock()
        manifest = json.loads(next((self.root/'.gi-coder/projects').glob('*/manifest.json')).read_text())
        self.assertEqual(manifest['progress']['handle_event'], 'pending')
        self.assertEqual(manifest['status'], 'failed')
        self.assertFalse((self.root.parent/'SliderGI_Output/clock.py').exists())

    def test_missing_import_and_undeclared_dependency_are_rejected(self):
        self.sources['hand_angles'] = self.sources['hand_angles'].replace('math.pi', 'missing_math.pi')
        with self.assertRaisesRegex(CoderError, 'Undefined global/import'):
            self.run_clock()

    def test_disconnected_shared_state_is_rejected_even_when_code_compiles(self):
        self.plan['variables'].append({'name': 'unused_colour_state', 'value': '0'})
        with self.assertRaisesRegex(CoderError, 'Disconnected shared variables'):
            self.run_clock()

    def test_cycles_limits_and_uncovered_requirements_fail_before_generation(self):
        for mutate in (lambda p: p['components'][-1]['depends_on'].append('main'),
                       lambda p: p['requirements'][0].update(components=[])):
            data = plan()
            mutate(data)
            with self.assertRaises(CoderError):
                validate_plan(data, {})
        with self.assertRaisesRegex(CoderError, 'max_components'):
            validate_plan(plan(), {'max_components': 2})

    def test_elapsed_project_budget_prevents_any_generation(self):
        with patch('gi_coder.project.time.monotonic', side_effect=[0, 100]), self.assertRaisesRegex(CoderError, 'budget'):
            generate_project(self.runtime, REQUEST, {}, self.root, {'project_timeout': 60})
        self.runtime.complete.assert_not_called()
        manifest = json.loads(next((self.root/'.gi-coder/projects').glob('*/manifest.json')).read_text())
        self.assertEqual(manifest['status'], 'failed')

    def test_undeclared_function_coupling_is_rejected(self):
        self.sources['draw_analog'] = self.sources['draw_analog'].replace('    centre =', '    draw_digital(surface, now, colour)\n    centre =')
        with self.assertRaisesRegex(CoderError, 'Undeclared component dependencies'):
            self.run_clock()

    def test_classes_methods_are_generated_individually_and_checked(self):
        data = {'architecture': 'Small counter class', 'imports': [], 'dependencies': [], 'variables': [],
            'classes': [{'name': 'Counter', 'bases': [], 'fields': ['value']}], 'entrypoint': 'main',
            'requirements': [{'id': 'R1', 'text': 'Increment counter', 'components': ['main', 'Counter.increment']}],
            'components': [
                {'name': 'Counter.__init__', 'signature': 'def __init__(self)', 'description': 'Initialize value', 'depends_on': [], 'uses': [], 'requirements': ['R1']},
                {'name': 'Counter.increment', 'signature': 'def increment(self)', 'description': 'Increment value', 'depends_on': [], 'uses': [], 'requirements': ['R1']},
                {'name': 'main', 'signature': 'def main()', 'description': 'Create and increment', 'depends_on': ['Counter.__init__', 'Counter.increment'], 'uses': ['Counter'], 'requirements': ['R1']}]}
        data = validate_plan(data, {})
        sources = {'Counter.__init__': 'def __init__(self):\n    self.value = 0\n',
                   'Counter.increment': 'def increment(self):\n    self.value += 1\n',
                   'main': 'def main():\n    counter = Counter()\n    counter.increment()\n'}
        result = verify_source(assemble(data, sources, final=True), data)
        self.assertEqual(result['inventory']['classes']['Counter']['fields'], ['value'])
        self.assertIn('Counter.increment', result['references']['main'])

    def test_component_validation_failure_is_repaired_once_then_checked(self):
        original = self.runtime.complete.side_effect
        attempts = []
        def respond(system, prompt, schema=None, **kwargs):
            context = json.loads(prompt)
            if schema is None and context['component']['name'] == 'hand_angles':
                attempts.append(context)
                if len(attempts) == 1:
                    return 'def hand_angles(now):\n    return missing_math.pi\n', {}
            return original(system, prompt, schema=schema, **kwargs)
        self.runtime.complete.side_effect = respond
        result = self.run_clock()
        self.assertEqual(len(attempts), 2)
        self.assertIn('Undefined global/import', attempts[1]['validation_error'])
        self.assertEqual(result['metrics']['model_calls'], 9)  # assessment, plan, 6 components, one review
        self.assertTrue(result['metrics']['successful_completion'])

    def test_clock_review_slices_are_deduplicated(self):
        result = self.run_clock()
        self.assertEqual(sum(schema == REVIEW_SCHEMA for schema, _ in self.calls), 1)
        self.assertEqual(result['metrics']['model_calls'], 8)

    def test_final_disconnected_entrypoint_is_repaired_with_contracts_preserved(self):
        original = self.runtime.complete.side_effect
        attempts = []
        def respond(system, prompt, schema=None, **kwargs):
            context = json.loads(prompt)
            if schema is None and context['component']['name'] == 'main':
                attempts.append(context)
                if len(attempts) == 1:
                    broken = SOURCES['main'].replace('draw_analog(surface, now, colour)', 'surface.fill(colour)')
                    return broken, {}
            return original(system, prompt, schema=schema, **kwargs)
        self.runtime.complete.side_effect = respond
        result = self.run_clock()
        self.assertEqual(len(attempts), 2)
        self.assertIn('Disconnected', attempts[1]['validation_error'])
        self.assertTrue(result['metrics']['successful_completion'])
