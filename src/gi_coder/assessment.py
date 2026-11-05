"""Schema-constrained Qwen assessment; model output is always untrusted data."""
import json
import os
import sys
import uuid
from pathlib import Path
from .config import ROOT, CoderError
from .languages import normalize, extension_for, filename_language, LANGUAGES, requested_language
from .generation import output_path


def string(limit):
    return {'type': 'string', 'minLength': 1, 'maxLength': limit}


FUNCTION = {'type': 'object', 'additionalProperties': False,
            'properties': {k: string(160) for k in ('name', 'inputs', 'behaviour', 'variables', 'outputs')},
            'required': ['name', 'inputs', 'behaviour', 'variables', 'outputs']}
CANONICAL_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'output_type': {'type': 'string', 'enum': ['text', 'code']},
    'objective': string(240),
    'constraints': {'type': 'array', 'items': string(320), 'maxItems': 24},
    'output_format': {'type': 'string', 'enum': ['plain_text', 'source_code']},
    'language': {'type': ['string', 'null'], 'maxLength': 40},
    'filename': {'type': ['string', 'null'], 'maxLength': 200},
    'extension': {'type': ['string', 'null'], 'maxLength': 22},
    'complexity': {'type': 'string', 'enum': ['simple', 'moderate', 'complex']},
    'revised_prompt': string(4000),
    'functions': {'type': 'array', 'items': FUNCTION, 'maxItems': 24}},
    'required': ['output_type', 'objective', 'constraints', 'output_format', 'language',
                 'filename', 'extension', 'complexity', 'revised_prompt', 'functions']}
TEXT_FIELDS = ('output_type', 'objective', 'constraints', 'complexity', 'revised_prompt')
TEXT_SCHEMA = {'type': 'object', 'additionalProperties': False,
               'properties': {key: CANONICAL_SCHEMA['properties'][key] for key in TEXT_FIELDS},
               'required': list(TEXT_FIELDS)}
TEXT_SCHEMA['properties']['output_type'] = {'type': 'string', 'enum': ['text']}
CODE_SCHEMA = {**CANONICAL_SCHEMA, 'properties': {
    **CANONICAL_SCHEMA['properties'], 'output_type': {'type': 'string', 'enum': ['code']}}}
# Text still gets a real Qwen assessment and revision; omit only predictable
# code-only null fields on the wire, then restore the canonical internal shape.
SCHEMA = {'anyOf': [TEXT_SCHEMA, CODE_SCHEMA]}


def validate_schema(value, schema, location='assessment'):
    """Validate the small schema subset above independently of llama.cpp grammar."""
    kinds = {'object': dict, 'array': list, 'string': str, 'null': type(None)}
    allowed = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
    if not any(type(value) is kinds[t] for t in allowed):
        raise CoderError(f'{location}: invalid type')
    if value is None:
        return
    if 'enum' in schema and value not in schema['enum']:
        raise CoderError(f'{location}: invalid value')
    if isinstance(value, dict):
        if not set(schema['required']) <= set(value) or not set(value) <= set(schema['properties']):
            raise CoderError(f'{location}: missing fields {sorted(set(schema["required"]) - set(value))}; '
                             f'unknown fields {sorted(set(value) - set(schema["properties"]))}')
        for key, item in value.items():
            validate_schema(item, schema['properties'][key], location + '.' + key)
    elif isinstance(value, list):
        if len(value) > schema['maxItems']:
            raise CoderError(f'{location}: too many items')
        for index, item in enumerate(value):
            validate_schema(item, schema['items'], f'{location}[{index}]')
    elif isinstance(value, str):
        if (not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 10000)
                or '\x00' in value or (schema.get('minLength', 0) and not value.strip())):
            raise CoderError(f'{location}: invalid string length/content')


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CoderError(f'Duplicate JSON field: {key!r}')
        result[key] = value
    return result


def assess(runtime, prompt, language=None, filename=None, root=ROOT):
    """Retain opt-in diagnostics even when parsing/validation never completes."""
    debug = (os.environ.get('GI_CODER_DEBUG_ASSESSMENT') == '1' or
             runtime.config.get('debug_assessment') is True)
    record = {'raw_assessment': None, 'validation_error': None, 'status': 'failed'}
    def capture(raw):
        record['raw_assessment'] = raw
    try:
        result = _assess(runtime, prompt, language, filename, capture)
        record.update(status='validated', normalized_assessment=result[0])
        return result
    except CoderError as exc:
        record['validation_error'] = str(exc)
        raise
    finally:
        if debug:
            try:
                directory = root / '.gi-coder/debug'
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = directory / ('assessment-' + uuid.uuid4().hex + '.json')
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(record, stream, ensure_ascii=True, indent=2)
                print(f'Assessment debug log: {path}', file=sys.stderr)
            except OSError as exc:
                print(f'Could not save assessment debug log: {exc}', file=sys.stderr)


def _assess(runtime, prompt, language, filename, capture):
    explicit_language = normalize(language) if language else requested_language(prompt)
    inferred = filename_language(filename) if filename else None
    if explicit_language and inferred and explicit_language != inferred:
        raise CoderError('Filename extension conflicts with requested language')
    system = (
        'Assess and revise; do not answer. Return concise schema JSON. Preserve explicit instructions; '
        'CLI language/filename require code and override guesses. Choose text/plain_text or code/source_code. '
        'Text: emit only output_type, objective, constraints, complexity, revised_prompt. '
        'Code: emit all schema fields; choose language, descriptive basename/extension, '
        'and sufficient function/module summaries (name, inputs, behaviour, variables, outputs). '
        'State objective, constraints, complexity and a precise revised_prompt. For greetings keep fields '
        'and revision minimal; preserve complex constraints. Mark interactive/multi-feature applications '
        'moderate or complex; simple is for small self-contained snippets. No extra features, files or tools.')
    user = json.dumps({'request': prompt, 'explicit_language': explicit_language, 'explicit_filename': filename},
                      ensure_ascii=False, separators=(',', ':'))
    raw, stats = runtime.complete(system, user, schema=SCHEMA)
    capture(raw)
    try:
        if len(raw) > 128000:
            raise CoderError('Assessment exceeds size limit')
        data = json.loads(raw, object_pairs_hook=no_duplicates)
    except ValueError as exc:
        raise CoderError('Qwen assessment is not valid JSON; no output written') from exc
    if isinstance(data, dict) and data.get('output_type') == 'text' and set(data) == set(TEXT_FIELDS):
        validate_schema(data, TEXT_SCHEMA)
        data.update(output_format='plain_text', language=None, filename=None, extension=None, functions=[])
    validate_schema(data, CANONICAL_SCHEMA)
    if data['output_type'] == 'text':
        if language or filename:
            raise CoderError('Assessment ignored explicit code options; retry the request')
        if (data['output_format'] != 'plain_text' or data['functions'] or
                any(data[k] is not None for k in ('language', 'filename', 'extension'))):
            raise CoderError('Inconsistent text assessment')
    else:
        if data['output_format'] != 'source_code' or not data['functions']:
            raise CoderError('Code assessment needs source format and function/module summary')
        # CLI overrides are authoritative; inferred fields may never replace them.
        data['language'] = explicit_language or inferred or normalize(data['language'])
        data['filename'] = filename or data['filename']
        # Known CLI overrides determine extension even if Qwen guessed something else.
        override = explicit_language or inferred
        proposed = data['extension']
        if filename:
            proposed = Path(filename).suffix or (None if data['language'] in LANGUAGES else proposed)
        elif override and data['language'] in LANGUAGES:
            proposed = None
        data['extension'] = extension_for(data['language'], proposed)
        if not isinstance(data['filename'], str):
            raise CoderError('Code assessment requires a filename')
        path = output_path(data['language'], data['filename'], extension=data['extension'])
        data['filename'] = path.name
    return data, stats
