"""User interface; orchestration is separate from inference and file delivery."""
import argparse
import json
import sys

from . import __version__
from .config import ROOT, CoderError, load_config, switch_model
from .generation import choose_language, extract_source, output_path, write_source
from .models import ensure_model
from .runtime import Runtime, RuntimeFailure
from .pipeline import process_request
from .session import session_signals, SessionTerminated
from .setup import ensure_engine


def parser():
    result = argparse.ArgumentParser(prog="gi-coder", description="Generate source files with local Qwen / llama.cpp")
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command")
    gen = commands.add_parser("generate", help="Generate one source file")
    gen.add_argument("prompt", nargs="+", help="Coding instruction (quote it in your shell)")
    gen.add_argument("--language", "-l")
    gen.add_argument("--filename", "-f")
    gen.add_argument("--output-dir", "-o")
    gen.add_argument("--overwrite", action="store_true")
    gen.add_argument("--model", choices=("7b", "3b"), help="Override model for this request")
    gen.add_argument("--offline", action="store_true", help="Never download or build")
    commands.add_parser("config", help="Show effective configuration")
    model = commands.add_parser("model", help="Select and download a model")
    model.add_argument("name", choices=("7b", "3b"))
    commands.add_parser("setup", help="Build runtime and download selected model")
    verify = commands.add_parser("verify", help="Full model hash check and actual model load")
    verify.add_argument("--offline", action="store_true")
    return result


def deliver(runtime, prompt, language, path, overwrite=False):
    if path.exists() and not overwrite:
        raise CoderError(f"Refusing to overwrite {path}; use --overwrite explicitly")
    print("Generating...", flush=True)
    text, stats = runtime.generate(prompt, language)
    source = extract_source(text, language)
    write_source(path, source, overwrite)
    print(f"File created: {path}\nLanguage: {language}\nStatus: Complete\n"
          f"Statistics: {json.dumps(stats)}\nBytes: {len(source.encode('utf-8'))}")
    return path


def prepared(config, offline=False):
    ensure_engine(config, offline=offline)
    return ensure_model(config, offline=offline)


def interactive(config):
    print(f"GI-Coder v{__version__}\nModel: Qwen2.5-Coder-{config['model'].upper()}")
    runtime = None
    try:
        runtime = Runtime(config, prepared(config)).__enter__()
        print("Status: Ready\n/help for commands; enter a request on one line.")
        while True:
            try:
                prompt = input("GI-Coder> ").strip()
            except EOFError:
                break
            if not prompt:
                continue
            if prompt in ("/exit", "/quit"):
                break
            try:
                if prompt == "/help":
                    print("/config | /model 7b | /model 3b | /help | /exit\n"
                          "For filename/output-directory control use one-shot generate --help.\n"
                          "For multiline requests: enter /multi, then finish with a single '.' line.")
                elif prompt == "/config":
                    print(json.dumps(config, indent=2))
                elif prompt.startswith("/model "):
                    name = prompt.split(maxsplit=1)[1]
                    if name not in ("7b", "3b"):
                        raise CoderError("Choose /model 7b or /model 3b")
                    candidate = dict(config, model=name, model_path=None)
                    model = prepared(candidate)
                    # Free the old model before loading another on a 10 GB laptop.
                    runtime.close()
                    runtime = None
                    replacement = Runtime(candidate, model).__enter__()
                    runtime = replacement
                    switch_model(name)
                    config = candidate
                    print(f"Model: {name}; Status: Ready")
                else:
                    if prompt == "/multi":
                        lines = []
                        while (line := input("... ")) != ".":
                            lines.append(line)
                        prompt = "\n".join(lines)
                    process_request(runtime, prompt, confirm=lambda path:
                        input(f"Overwrite {path}? Type yes: ").strip() == 'yes')
            except RuntimeFailure:
                raise
            except CoderError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                if runtime is None:
                    raise CoderError("Model switch failed; restart GI-Coder to reload the previous model")
    finally:
        if runtime:
            runtime.close()


def execute(argv=None):
    args = parser().parse_args(argv)
    try:
        config = load_config()
        if args.command == "config":
            print(json.dumps(config, indent=2))
        elif args.command == "model":
            candidate = dict(config, model=args.name, model_path=None)
            prepared(candidate)
            switch_model(args.name)
            print(f"Selected {args.name}")
        elif args.command == "setup":
            prepared(config)
            print("Setup complete. Launch GI-Coder or run verify to test model loading.")
        elif args.command == "verify":
            ensure_engine(config, offline=args.offline)
            model = ensure_model(config, offline=args.offline, full_verify=True)
            with Runtime(config, model):
                print("SHA-256 verified; llama.cpp successfully loaded the model. Ready.")
        elif args.command == "generate":
            if args.model:
                config.update(model=args.model, model_path=None)
            prompt = " ".join(args.prompt).strip()
            if not prompt:
                raise CoderError("Coding request cannot be empty")
            with Runtime(config, prepared(config, args.offline)) as runtime:
                process_request(runtime, prompt, args.language, args.filename, args.output_dir, args.overwrite)
        else:
            interactive(config)
        return 0
    except (CoderError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled", file=sys.stderr)
        return 130


def main(argv=None):
    try:
        with session_signals():
            return execute(argv)
    except SessionTerminated as exc:
        return 128 + exc.signum


if __name__ == "__main__":
    sys.exit(main())
