"""Source extraction and safe file delivery. Generated programs are never executed."""
import ast
import os
import re
import tempfile
from pathlib import Path

from .config import CoderError
from .languages import LANGUAGES, ALIASES, normalize, extension_for


def choose_language(prompt, language=None, filename=None):
    """Legacy library helper only; the CLI pipeline uses Qwen assessment."""
    explicit = ALIASES.get((language or "").lower(), (language or "").lower())
    ext = Path(filename).suffix.lower() if filename else ""
    inferred = next((lang for lang, suffix in LANGUAGES.items() if suffix == ext), None)
    if explicit:
        explicit = normalize(explicit)
    if ext and inferred is None:
        raise CoderError(f"Unsupported source file extension: {ext}")
    if explicit and inferred and explicit != inferred:
        raise CoderError("Filename extension conflicts with requested language")
    if explicit or inferred:
        return explicit or inferred
    matches = set()
    for word, lang in {**{k: k for k in LANGUAGES}, **ALIASES}.items():
        if re.search(r"(?<![\w+#])" + re.escape(word) + r"(?![\w+#])", prompt, re.I):
            matches.add(lang)
    if len(matches) != 1:
        raise CoderError("Specify --language or --filename to select one language")
    return matches.pop()


def extract_source(text, language):
    text = text.replace("\r\n", "\n").strip()
    fences = re.findall(r"^```([^\n`]*)\n(.*?)^```[ \t]*$", text, re.M | re.S)
    if "```" in text:
        if len(fences) != 1 or text.count("```") != 2:
            raise CoderError("Expected one complete source-code block; no file written")
        tag, source = fences[0]
        if tag.strip() and ALIASES.get(tag.strip().lower(), tag.strip().lower()) != language:
            raise CoderError("Model returned a different language; no file written")
    else:
        if language != "python":
            raise CoderError("Expected a fenced source block to separate code from commentary")
        source = text
    source = source.strip("\n") + "\n"
    if not source.strip() or "\x00" in source:
        raise CoderError("Empty or invalid source returned; no file written")
    if language == "python":
        try:
            ast.parse(source)
        except SyntaxError as exc:
            raise CoderError(f"Generated Python is not syntactically valid: {exc}; no file written") from exc
    return source


def output_path(language, filename=None, output_dir=None, cwd=None, extension=None):
    extension = extension_for(language, extension)
    cwd = Path.cwd() if cwd is None else Path(cwd)
    directory = Path(output_dir).expanduser() if output_dir else cwd
    if not directory.is_absolute():
        directory = cwd / directory
    name = filename or "generated" + extension
    if (not isinstance(name, str) or len(name.encode('utf-8')) > 200 or name.startswith(('.', '-'))
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or Path(name).name != name or "/" in name or "\\" in name or ":" in name):
        raise CoderError("--filename must be a basename; use --output-dir for directories")
    if not Path(name).suffix:
        name += extension
    if not name.lower().endswith(extension):
        raise CoderError("Filename extension conflicts with language")
    # Resolve only the directory: do not follow an existing destination symlink.
    return directory.resolve() / name


def write_source(path, source, overwrite=False):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite:
            # O_EXCL refuses existing files and symlinks atomically.
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(source)
        else:
            # Replace the link itself, never a symlink's target; preserve old file if writing fails.
            fd, temp = tempfile.mkstemp(prefix=".gi-coder-", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(source)
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
    except FileExistsError as exc:
        raise CoderError(f"Refusing to overwrite {path}; use --overwrite explicitly") from exc
    except OSError as exc:
        raise CoderError(f"Could not write {path}: {exc}") from exc
    return path
