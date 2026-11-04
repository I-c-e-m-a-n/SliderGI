"""Protocol tests against a local fake HTTP server, not Qwen inference."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading
import unittest

from gi_coder.config import load_config, CoderError
from gi_coder.runtime import Runtime


class ProtocolTest(unittest.TestCase):
    def test_chat_payload_auth_and_statistics(self):
        captured = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured['route'] = self.path
                captured['auth'] = self.headers['Authorization']
                captured['data'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '```python\nx=1\n```'}}],
                                   'usage': {'completion_tokens': 7}}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            runtime = Runtime(load_config(), Path('unused.gguf'))
            runtime.url = f'http://127.0.0.1:{server.server_port}'
            code, stats = runtime.generate('Print one in Python', 'python')
            self.assertIn('x=1', code)
            self.assertEqual(stats['completion_tokens'], 7)
            self.assertEqual(captured['route'], '/v1/chat/completions')
            self.assertEqual(captured['auth'], 'Bearer ' + runtime.key)
            self.assertEqual(captured['data']['messages'][1]['content'], 'Print one in Python')
            self.assertEqual(captured['data']['messages'][0]['role'], 'system')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
