"""Extensible mappings; unknown languages use validated model-proposed extensions."""
import json
import re
from pathlib import Path
from .config import ROOT, CoderError

SPECS = json.loads((ROOT / 'config/languages.json').read_text(encoding='utf-8'))
LANGUAGES = {name: data['extension'] for name, data in SPECS.items()}
ALIASES = {'py': 'python', 'js': 'javascript', 'ts': 'typescript', 'c++': 'cpp', 'cxx': 'cpp',
           'c#': 'csharp', 'cs': 'csharp', 'sh': 'shell', 'rb': 'ruby', 'rs': 'rust', 'yml': 'yaml'}


def normalize(value):
    if not isinstance(value, str):
        raise CoderError('Invalid language name')
    value = value.lower().strip()
    if re.fullmatch(r'python(?:[ -]?[23](?:\.\d+)*(?:\.x)?)?', value):
        return 'python'
    if value.startswith('.'):
        mapped = filename_language('source' + value)
        if mapped:
            return mapped
    if not re.fullmatch(r'[a-z][a-z0-9_+# .-]{0,39}', value):
        raise CoderError('Invalid language name')
    return ALIASES.get(value, value)


def extension_for(language, proposed=None):
    language = normalize(language)
    known = LANGUAGES.get(language)
    if proposed is not None and not isinstance(proposed, str):
        raise CoderError('A safe source extension is required for this language')
    if isinstance(proposed, str):
        proposed = proposed.strip().lower()
        if proposed and not proposed.startswith('.'):
            proposed = '.' + proposed
    extension = proposed or known
    if not isinstance(extension, str) or not re.fullmatch(r'\.[a-zA-Z0-9]{1,10}(?:\.[a-zA-Z0-9]{1,10})?', extension):
        raise CoderError('A safe source extension is required for this language')
    extension = extension.lower()
    if known and extension not in [known, *SPECS[language].get('extensions', [])]:
        raise CoderError('Filename extension conflicts with language')
    return extension


def requested_language(prompt):
    """Recognize explicit code-language instructions, not incidental mentions.

    Assessment still decides output and rewrites every request. Ambiguous or
    unsupported prose is left to Qwen; CLI options remain authoritative.
    """
    names = sorted(set(LANGUAGES) | set(ALIASES), key=len, reverse=True)
    token = r'(?:python(?:[ -]?[23](?:\.\d+)*(?:\.x)?)?|' + '|'.join(map(re.escape, names)) + r')'
    verb = r'\b(?:generate|write|create|build|implement|make|develop)\s+'
    patterns = (
        verb + r'(?:an?\s+)?(' + token + r')\s+(?:program|script|code|application|app|function|module)\b',
        verb + r'[^.!?\n]{0,160}?\bin\s+(' + token + r')(?![\w+#.])',
    )
    matches = set()
    for pattern in patterns:
        for match in re.finditer(pattern, prompt, re.I):
            prefix = prompt[max(0, match.start() - 32):match.start()]
            if not re.search(r"(?:\bnot|\bnever|\bdon't)\s*$", prefix, re.I):
                matches.add(normalize(match.group(1)))
    return matches.pop() if len(matches) == 1 else None


def filename_language(filename):
    suffix = Path(filename).suffix.lower()
    return next((name for name, ext in LANGUAGES.items()
                 if suffix in [ext, *SPECS[name].get('extensions', [])]), None)
