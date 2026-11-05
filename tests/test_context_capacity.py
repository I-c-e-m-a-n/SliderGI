import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from gi_coder.config import CoderError, load_config
from gi_coder.runtime import Runtime


class ContextCapacityTest(unittest.TestCase):
    def runtime(self, count, cap=None):
        runtime = Runtime(dict(load_config(), assessment_max_tokens=cap), Path('unused'))
        runtime.process = Mock()
        runtime.process.poll.return_value = None
        payloads = []
        def response(route, data=None, **kwargs):
            if route == '/apply-template':
                return {'prompt': 'actual Qwen chat template fixture'}
            if route == '/tokenize':
                return {'tokens': [0] * count}
            payloads.append(data)
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}
        runtime.request = Mock(side_effect=response)
        return runtime, payloads

    def test_assessment_uses_available_context_not_384_and_honors_explicit_limit(self):
        for cap, expected in [(None, 4096-1000-64), (1200, 1200)]:
            runtime, payloads = self.runtime(1000, cap)
            runtime.complete('assess', 'detailed request', schema={})
            self.assertEqual(payloads[0]['max_tokens'], expected)
            self.assertEqual(runtime.request.call_args_list[0].args[0], '/apply-template')
            self.assertFalse(payloads[0]['cache_prompt'])
            runtime.process.terminate.assert_not_called()

    def test_too_large_prompt_fails_before_inference_without_reloading_model(self):
        runtime, payloads = self.runtime(4096)
        with self.assertRaisesRegex(CoderError, 'context'):
            runtime.complete('assess', 'large request', schema={})
        self.assertEqual(payloads, [])
        runtime.process.terminate.assert_not_called()
