"""Owned localhost llama.cpp child. No remote endpoint or user API credentials."""
import json
import atexit
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import ROOT, CoderError, installation_path

LLAMA_COMMIT = "74d4f5b041ad837153b0e90fc864b8290e01d8d5"


class RuntimeFailure(CoderError):
    """The owned inference process is unusable and has been stopped."""


def discover_engine(config, root=ROOT):
    if config.get("engine_path"):
        paths = [installation_path(config["engine_path"], root)]
    else:
        build = root / "third_party/llama.cpp/build/bin"
        paths = [build / "llama-server", build / "Release/llama-server.exe", build / "llama-server.exe"]
    for path in paths:
        if path.is_file():
            return path.resolve()
    raise CoderError("llama-server not found. Run scripts/setup.py; Windows needs Git, CMake "
                     "and Visual Studio 2022 Build Tools with Desktop development with C++.")


class Runtime:
    def __init__(self, config, model, root=ROOT):
        self.config, self.model, self.root = config, Path(model), root
        self.process = None
        self.log = None
        self.url = None
        self.key = secrets.token_hex(24)  # Local process authentication, never a service API key.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, route, data=None, timeout=10):
        raw = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(self.url + route, data=raw, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        with self.http.open(req, timeout=timeout) as response:
            return json.load(response)

    def __enter__(self):
        engine = discover_engine(self.config, self.root)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.log = tempfile.TemporaryFile(mode="w+b")
        command = [str(engine), "--model", str(self.model), "--host", "127.0.0.1",
                   "--port", str(port), "--api-key", self.key, "--jinja", "--no-context-shift",
                   "--ctx-size", str(self.config["context_size"]),
                   "--threads", str(self.config["threads"]), "--gpu-layers", "0",
                   "--parallel", "1", "--batch-size", "128", "--ubatch-size", "128",
                   "--n-predict", str(self.config["max_tokens"])]
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            stdout=self.log, stderr=self.log, start_new_session=True)
            atexit.register(self.close)
            deadline = time.monotonic() + self.config["load_timeout"]
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise CoderError("llama.cpp model loading failed: " + self.log_tail())
                try:
                    if self.request("/health", timeout=2).get("status") == "ok":
                        return self
                except (OSError, ValueError):
                    pass
                time.sleep(.2)
            raise CoderError("Model loading timed out; close other apps, try 3b or increase load_timeout. "
                             + self.log_tail())
        except BaseException:
            self.close()
            raise

    def log_tail(self):
        self.log.seek(0, 2)
        size = self.log.tell()
        self.log.seek(max(0, size - 2500))
        return self.log.read().decode("utf-8", errors="replace").replace(self.key, "<local-auth>")

    def generate(self, prompt, language):
        system = (f"You are a precise coding assistant. Produce one complete, runnable {language} source file. "
                  "Return exactly one Markdown code block containing only the full source code. "
                  "No explanations, no placeholders, no multiple files. Do not execute anything.")
        return self.complete(system, prompt)

    def complete(self, system, prompt, schema=None):
        """Stateless chat: fresh messages and no KV/prompt reuse on every call."""
        if self.process is not None and self.process.poll() is not None:
            self.close()
            raise RuntimeFailure('Local inference process exited; restart GI-Coder')
        payload = {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "max_tokens": self.config["max_tokens"], "temperature": self.config["temperature"],
            "stream": False, "cache_prompt": False, "id_slot": 0}
        if schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "request_assessment", "strict": True, "schema": schema}}
        started = time.monotonic()
        try:
            result = self.request("/v1/chat/completions", payload, timeout=self.config["generation_timeout"])
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise CoderError("Generation did not finish (possibly output-token limit). "
                                 "No file written. Increase max_tokens/context_size or simplify the request.")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise CoderError("Qwen returned empty source code; no file written")
            return content, {"seconds": round(time.monotonic() - started, 2), **result.get("usage", {})}
        except urllib.error.HTTPError as exc:
            detail = exc.read(4000).decode("utf-8", errors="replace")
            if exc.code >= 500:
                self.close()
                raise RuntimeFailure(f'llama.cpp runtime failure ({exc.code}): {detail}') from exc
            raise CoderError(f"llama.cpp rejected the request ({exc.code}): {detail}") from exc
        except (OSError, KeyError, ValueError, IndexError, TypeError) as exc:
            self.close()
            raise RuntimeFailure(f"Local inference failed: {exc}. Restart GI-Coder.") from exc

    def close(self):
        atexit.unregister(self.close)
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log is not None:
            self.log.close()

    def __exit__(self, *args):
        self.close()
