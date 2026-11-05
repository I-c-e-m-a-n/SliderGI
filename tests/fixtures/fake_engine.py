#!/usr/bin/env python3
"""Synthetic local subprocess for lifecycle/protocol tests; never real inference."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import ast
import os
import sys


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond({'status': 'ok'})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/apply-template':
            self.respond({'prompt': json.dumps(data['messages'])})
            return
        if self.path == '/tokenize':
            self.respond({'tokens': [0] * (len(data['content'].encode()) // 3 + 1)})
            return
        if 'response_format' in data:
            schema = data['response_format']['json_schema']['schema']
            if 'verdict' in schema.get('properties', {}):
                source = json.loads(data['messages'][1]['content'])['source']
                snippet = next(ast.get_source_segment(source, n) for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call))
                self.respond({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                    'verdict': 'pass', 'evidence': 'Synthetic protocol fixture only.', 'evidence_snippets': [snippet],
                    'issues': [], 'missing_requirements': []})}}],
                    'usage': {'engine_pid': os.getpid()}})
                return
            request = json.loads(data['messages'][1]['content'])
            text = 'explain' in request['request'].lower()
            content = json.dumps({
                'output_type': 'text' if text else 'code', 'objective': 'Respond to request',
                'constraints': [], 'output_format': 'plain_text' if text else 'source_code',
                'language': None if text else 'python', 'filename': None if text else 'greeting.py',
                'extension': None if text else '.py', 'complexity': 'simple',
                'revised_prompt': request['request'], 'functions': [] if text else [
                    {'name': 'module', 'inputs': 'none', 'behaviour': 'print hello',
                     'variables': 'none', 'outputs': 'hello'}]})
        elif 'ordinary English' in data['messages'][0]['content']:
            content = 'A compiler translates source code.'
        else:
            content = '```python\nprint("hello")\n```'
        self.respond({'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
                      'usage': {'engine_pid': os.getpid()}})

    def respond(self, body):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    port = int(sys.argv[sys.argv.index('--port') + 1])
    HTTPServer(('127.0.0.1', port), Handler).serve_forever()
