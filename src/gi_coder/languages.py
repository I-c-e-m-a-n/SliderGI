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
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_+# .-]{0,39}', value):
        raise CoderError('Invalid language name')
    value = value.lower().strip()
    return ALIASES.get(value, value)


def extension_for(language, proposed=None):
    language = normalize(language)
    known = LANGUAGES.get(language)
    extension = proposed or known
    if not isinstance(extension, str) or not re.fullmatch(r'\.[a-zA-Z0-9]{1,10}(?:\.[a-zA-Z0-9]{1,10})?', extension):
        raise CoderError('A safe source extension is required for this language')
    extension = extension.lower()
    if known and extension not in [known, *SPECS[language].get('extensions', [])]:
        raise CoderError('Filename extension conflicts with language')
    return extension


def filename_language(filename):
    suffix = Path(filename).suffix.lower()
    return next((name for name, ext in LANGUAGES.items()
                 if suffix in [ext, *SPECS[name].get('extensions', [])]), None)
