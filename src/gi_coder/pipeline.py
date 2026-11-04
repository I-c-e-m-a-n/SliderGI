"""One request, two stateless calls on one resident runtime; no execution/tools."""
import json
import os
from pathlib import Path
import tempfile
import time

from .assessment import assess
from .config import ROOT, CoderError
from .generation import extract_source, output_path, write_source
from .languages import SPECS


def skeleton_header(source, language, functions):
    prefix = SPECS.get(language, {}).get('comment')
    if not prefix:
        return source, {'functions': functions}
    lines = [prefix + ' GI-Coder function skeleton:']
    for function in functions:
        # Model text must never escape the comment line or inject source.
        summary = '; '.join(key + '=' + ' '.join(value.split()) for key, value in function.items())
        lines.append(prefix + ' ' + summary)
    original = source.splitlines(keepends=True)
    position = 1 if original and original[0].startswith('#!') else 0
    if language == 'ruby':
        # Ruby magic comments (encoding/frozen_string_literal) belong at the top.
        while position < len(original) and original[position].lstrip().startswith('#'):
            position += 1
    # Preserve Python's first/second-line encoding declaration position.
    if language == 'python':
        import re
        for index, line in enumerate(original[:2]):
            if re.match(r'^\s*#.*coding[:=]', line):
                position = index + 1
    original[position:position] = [line + '\n' for line in lines]
    result = ''.join(original)
    if language == 'python':
        result = extract_source(result, language)
    return result, None


def process_request(runtime, prompt, language=None, filename=None, output_dir=None,
                    overwrite=False, confirm=None, root=ROOT):
    started = time.monotonic()
    result = _process_request(runtime, prompt, language, filename, output_dir, overwrite, confirm, root)
    timings = {'assessment': result['statistics']['assessment']['seconds'],
               'generation': result['statistics']['generation']['seconds'],
               'total': time.monotonic() - started}
    result['timings'] = timings
    print(f'\nAssessment: {timings["assessment"]:.2f}s\nGeneration: {timings["generation"]:.2f}s\n'
          f'Total: {timings["total"]:.2f}s')
    return result


def _process_request(runtime, prompt, language, filename, output_dir, overwrite, confirm, root):
    if not prompt.strip():
        raise CoderError('Request cannot be empty')
    temporary = root / '.gi-coder/tmp'
    temporary.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Request-private files have owner-only permissions; finally cleans errors,
    # cancellation and success. No prompts/assessments enter subsequent requests.
    with tempfile.TemporaryDirectory(prefix='request-', dir=temporary) as work:
        print('Assessing request...', flush=True)
        started = time.monotonic()
        assessment, assessment_stats = assess(runtime, prompt, language, filename)
        assessment_stats = {**assessment_stats, 'seconds': time.monotonic() - started}
        revised = json.dumps({'original_request': prompt, 'assessment': assessment},
                             ensure_ascii=False, separators=(',', ':'))
        for name, content in (('assessment.json', json.dumps(assessment, ensure_ascii=False, indent=2)),
                              ('revised-prompt.json', revised)):
            path = Path(work) / name
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(content)
        if assessment['output_type'] == 'text':
            started = time.monotonic()
            answer, stats = runtime.complete(
                'Answer naturally in ordinary English using the revised prompt and original request. '
                'Explicit user instructions take priority. Do not produce a source file.', revised)
            stats = {**stats, 'seconds': time.monotonic() - started}
            print(answer)
            return {'type': 'text', 'text': answer, 'assessment': assessment,
                    'statistics': {'assessment': assessment_stats, 'generation': stats}}
        directory = output_dir if output_dir is not None else root.parent / 'SliderGI_Output'
        path = output_path(assessment['language'], assessment['filename'], directory,
                           extension=assessment['extension'])
        sidecar = path.with_name(path.name + '.gi-coder.json') if not SPECS.get(
            assessment['language'], {}).get('comment') else None
        destinations = [path] + ([sidecar] if sidecar else [])
        # macOS often uses case-insensitive filesystems; filesystem existence and
        # O_EXCL enforce collisions without guessing filesystem case behavior.
        permissions = {target: overwrite for target in destinations}
        for target in destinations:
            if (target.exists() or target.is_symlink()) and not overwrite:
                if confirm is None or not confirm(target):
                    raise CoderError(f'Refusing to overwrite {target}; use --overwrite explicitly')
                permissions[target] = True
        print('Generating...', flush=True)
        started = time.monotonic()
        code, stats = runtime.generate(revised, assessment['language'])
        stats = {**stats, 'seconds': time.monotonic() - started}
        source = extract_source(code, assessment['language'])
        if assessment['language'] == 'json':
            try:
                json.loads(source)
            except ValueError as exc:
                raise CoderError(f'Generated JSON is invalid: {exc}; no file written') from exc
        source, metadata = skeleton_header(source, assessment['language'], assessment['functions'])
        # Stage sidecar first so a metadata collision cannot leave an unaccompanied
        # source file. On failure roll back only a sidecar newly created by us.
        created_sidecar = False
        if sidecar:
            existed = sidecar.exists() or sidecar.is_symlink()
            write_source(sidecar, json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', permissions[sidecar])
            created_sidecar = not existed
        try:
            write_source(path, source, permissions[path])
        except BaseException:
            if created_sidecar:
                sidecar.unlink(missing_ok=True)
            raise
        print(f'File created: {path}\nLanguage: {assessment["language"]}\nStatus: Complete')
        if sidecar:
            print(f'Function metadata: {sidecar}')
        return {'type': 'code', 'path': path, 'assessment': assessment,
                'statistics': {'assessment': assessment_stats, 'generation': stats}}
