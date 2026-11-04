"""Actual compiled llama.cpp failure handling; no model weights needed."""
from pathlib import Path
import tempfile
import unittest
from gi_coder.config import CoderError, load_config
from gi_coder.runtime import Runtime, discover_engine


class EngineFailureTest(unittest.TestCase):
    def test_actual_runtime_rejects_invalid_model_and_cleans_up(self):
        config = load_config()
        try:
            discover_engine(config)
        except CoderError:
            self.skipTest('Build llama.cpp to exercise actual engine failure handling')
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)/'invalid.gguf'
            model.write_bytes(b'not a model')
            runtime = Runtime(dict(config, load_timeout=20), model)
            with self.assertRaisesRegex(CoderError, 'model loading failed'):
                with runtime:
                    self.fail('Invalid model must not become ready')
            self.assertIsNotNone(runtime.process.poll())
            self.assertTrue(runtime.log.closed)
