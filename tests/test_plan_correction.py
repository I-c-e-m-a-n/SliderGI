"""Clock-planning regressions with synthetic Qwen responses; no GUI execution."""
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
from gi_coder.project import PLAN_SCHEMA, REVIEW_SCHEMA, validate_plan
from gi_coder.runtime import RuntimeFailure
from clock_project_fixture import plan, SOURCES, PASS
from test_pipeline import assessment

REQUEST = ('Build a clock system in Python. Do not use tkinter. It should be able to switch '
           'between analog and digital, and switch between colours and have a screen. Build using pygame.')


class PlanCorrectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'SliderGI'
        self.root.mkdir()
        broken = plan()
        broken['variables'].append({'name': 'clock', 'value': 'pygame.time.Clock()'})
        self.rejected = broken
        self.replies = ['\n' + json.dumps(broken, indent=2) + '\n', json.dumps(plan())]
        self.plan_calls, self.component_calls = [], []
        self.clock = 0.0
        self.runtime = Mock()
        self.runtime.config = {'max_components': 16, 'max_requirements': 16, 'project_timeout': 1800}
        def respond(system, prompt, schema=None, **kwargs):
            data = json.loads(prompt)
            if schema == PLAN_SCHEMA:
                index = len(self.plan_calls)
                self.plan_calls.append((system, data))
                self.clock += 159.79334933299106 if index == 0 else 12.5
                reply = self.replies[min(index, len(self.replies)-1)]
                if isinstance(reply, BaseException):
                    raise reply
                return reply, {'completion_tokens': 787 if index == 0 else 500}
            if schema == REVIEW_SCHEMA:
                self.clock += .25
                return json.dumps(PASS), {}
            if schema is None:
                self.component_calls.append(data)
                self.clock += 2
                return '```python\n'+SOURCES[data['component']['name']]+'```', {}
            data = assessment(language='python', filename='clock_system.py', extension='.py')
            data.update(complexity='moderate', revised_prompt='Build a Pygame clock with analog/digital and colour controls; no tkinter.')
            return json.dumps(data), {}
        self.runtime.complete.side_effect = respond

    def run_clock(self):
        with patch('gi_coder.project.time.monotonic', side_effect=lambda: self.clock), \
             contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            result = process_request(self.runtime, REQUEST, root=self.root)
        return result, output.getvalue()

    def workspace(self):
        return next((self.root/'.gi-coder/projects').glob('project-*'))

    def manifest(self):
        return json.loads((self.workspace()/'manifest.json').read_text())

    def test_invalid_resource_is_saved_and_corrected_with_precise_feedback(self):
        result, output = self.run_clock()
        record = self.manifest()
        self.assertEqual([r['status'] for r in record['planning_attempts']], ['rejected', 'accepted'])
        self.assertEqual((self.workspace()/'plan-attempt-1.raw.json').read_text(), self.replies[0])
        self.assertEqual((self.workspace()/'plan-attempt-2.raw.json').read_text(), self.replies[1])
        feedback = self.plan_calls[1][1]
        self.assertEqual(feedback['rejected_plan'], self.rejected)
        self.assertIn('plan.variables[3].value (clock)', feedback['validation_error'])
        self.assertIn("'pygame.time.Clock()'", feedback['validation_error'])
        self.assertEqual(feedback['original_request'], REQUEST)
        self.assertNotIn('assessment', self.plan_calls[0][1])
        self.assertEqual(set(self.plan_calls[0][1]), {'original_request', 'revised_prompt'})
        self.assertEqual(record['plan']['variables'], plan()['variables'])
        source = result['path'].read_text()
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                ast.literal_eval(node.value)  # No module-level resources slipped through correction.
        self.assertIn('timer = pygame.time.Clock()', source)
        self.assertEqual((self.workspace()/'plan-attempt-1.raw.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.workspace().stat().st_mode & 0o777, 0o700)
        self.assertIn('Planning: 159.79s', output)
        self.assertIn('Plan retries: 12.50s', output)
        self.assertIn('Generation: 10.00s', output)
        self.assertAlmostEqual(result['timings']['planning'], 159.79334933299106)
        self.assertEqual(result['timings']['retry'], 12.5)
        self.assertEqual(record['timings']['generation'], 10)
        self.assertEqual(record['timings']['verification'], 1)
        self.assertEqual(record['calls'][0]['usage']['completion_tokens'], 787)
        for phase in record['timings']:
            self.assertAlmostEqual(record['timings'][phase], sum(row['seconds'] for row in record['calls'] if row['phase'] == phase))

    def test_two_retries_maximum_never_starts_implementation_on_invalid_plan(self):
        self.replies = [self.replies[0]]
        with self.assertRaisesRegex(CoderError, 'after 3 attempts'):
            self.run_clock()
        record = self.manifest()
        self.assertEqual(len(self.plan_calls), 3)
        self.assertEqual(self.component_calls, [])
        self.assertEqual(len(list(self.workspace().glob('*.raw.json'))), 3)
        self.assertEqual(record['status'], 'failed')
        self.assertIsNone(record['plan'])
        self.assertTrue(all(r['status'] == 'rejected' for r in record['planning_attempts']))
        self.assertFalse((self.root.parent/'SliderGI_Output/clock_system.py').exists())
        self.assertFalse((self.workspace()/'source.py').exists())

    def test_second_correction_uses_latest_error_and_fits_exact_call_budget(self):
        another = copy.deepcopy(self.rejected)
        another['variables'][-1] = {'name': 'font', 'value': 'pygame.font.Font(None, 64)'}
        self.replies = [self.replies[0], json.dumps(another), json.dumps(plan())]
        self.runtime.config.update(max_components=5, max_requirements=4)
        self.run_clock()
        self.assertEqual(len(self.plan_calls), 3)
        self.assertEqual(self.plan_calls[2][1]['rejected_plan'], another)
        self.assertIn('(font)', self.plan_calls[2][1]['validation_error'])
        self.assertEqual(len(self.manifest()['calls']), 3 + 5 + 4)
        self.assertEqual(self.manifest()['timings']['retry'], 25)
        self.assertEqual(self.manifest()['planning_attempts'][0]['seconds'], 159.79334933299106)

    def test_runtime_failure_and_interrupt_are_propagated_without_retry(self):
        initial = self.root
        for error in (RuntimeFailure('engine exited'), KeyboardInterrupt()):
            self.root = initial/type(error).__name__
            self.root.mkdir()
            self.plan_calls = []
            self.replies = [error]
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.run_clock()
            self.assertEqual(len(self.plan_calls), 1)
            self.assertEqual(self.manifest()['status'], 'failed')
            self.assertEqual(self.manifest()['timings']['retry'], 0)

    def test_malformed_json_is_retained_and_retry_receives_verbatim_raw(self):
        self.replies[0] = '{"variables":'
        self.run_clock()
        self.assertEqual((self.workspace()/'plan-attempt-1.raw.json').read_text(), self.replies[0])
        self.assertEqual(self.plan_calls[1][1]['rejected_plan'], self.replies[0])
        self.assertIn('plan: invalid JSON at line 1, column', self.plan_calls[1][1]['validation_error'])

    def test_resource_failures_and_cancellation_do_not_trigger_validation_retries(self):
        error = CoderError('Generation did not finish')
        error.raw_response = '{"architecture":"partial'
        error.response_usage = {'completion_tokens': 100}
        self.replies = [error]
        with self.assertRaisesRegex(CoderError, 'did not finish'):
            self.run_clock()
        self.assertEqual(len(self.plan_calls), 1)
        self.assertEqual((self.workspace()/'plan-attempt-1.raw.json').read_text(), error.raw_response)
        self.assertEqual(self.manifest()['planning_attempts'][0]['status'], 'inference_failed')
        self.assertEqual(self.manifest()['calls'][0]['usage']['completion_tokens'], 100)
        self.assertEqual(self.manifest()['timings']['retry'], 0)

    def test_literal_safety_rejects_calls_names_and_expressions_with_field_details(self):
        for expression in ('pygame.display.set_mode((640, 480))', 'pygame.time.Clock()',
                           'pygame.font.Font(None, 64)', 'time.time()', 'WIDTH // 2', 'WIDTH'):
            data = plan()
            data['variables'].append({'name': 'resource', 'value': expression})
            with self.subTest(expression=expression), self.assertRaises(CoderError) as caught:
                validate_plan(data, {})
            self.assertIn('plan.variables[3].value (resource)', str(caught.exception))
            self.assertIn(repr(expression), str(caught.exception))
        for literal in ('None', 'False', '(320, 240)', '[1, 2, 3]', "{'mode': 'analog'}"):
            data = plan()
            data['variables'].append({'name': 'optional_state', 'value': literal})
            validate_plan(data, {})

    def test_schema_and_signature_errors_identify_exact_field(self):
        data = plan()
        data['components'][0]['signature'] = 'def main(window=pygame.display.set_mode((1, 1)))'
        with self.assertRaises(CoderError) as caught:
            validate_plan(data, {})
        self.assertIn('plan.components[0].signature', str(caught.exception))
        self.replies[0] = json.dumps({**plan(), 'variables': [{'name': 'screen', 'value': 42}]})
        self.run_clock()
        self.assertIn('plan.variables[0].value', self.plan_calls[1][1]['validation_error'])
