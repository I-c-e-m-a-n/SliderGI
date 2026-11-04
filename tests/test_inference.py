"""Opt-in REAL Qwen test. Never substituted with a mocked completion."""
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('GI_CODER_INFERENCE') == '1',
                     'Real inference opt-in: set GI_CODER_INFERENCE=1 after setup')
class QwenSmokeTest(unittest.TestCase):
    def test_real_qwen_writes_valid_python_in_caller_directory(self):
        with tempfile.TemporaryDirectory(prefix='gi coder smoke ') as directory:
            result = subprocess.run([sys.executable, '-m', 'gi_coder.cli', 'generate',
                'Write a complete Python program that defines add(a, b) returning their sum, '
                'and prints add(2, 3) inside an if __name__ == "__main__" guard.',
                '--language', 'python', '--filename', 'smoke.py', '--offline',
                '--model', os.environ.get('GI_CODER_TEST_MODEL', '7b')],
                cwd=directory, text=True, capture_output=True, timeout=2400)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            path = Path(directory)/'smoke.py'
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 20)
            py_compile.compile(str(path), doraise=True)
            print('REAL QWEN OUTPUT:\n' + path.read_text(encoding='utf-8'))
            print(result.stdout)


if __name__ == '__main__':
    unittest.main()
