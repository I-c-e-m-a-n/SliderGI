"""Stateless adaptive requests on one resident runtime; no generated execution."""
import json
import os
from pathlib import Path
import tempfile
import time

from .assessment import assess
from .config import ROOT, CoderError
from .generation import extract_source, output_path, write_source
from .languages import SPECS
from .project import generate_project, generate_single, call_metrics
from .verification import verify_source


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
    if 'project_timings' in result:
        timings['implementation'] = timings['generation']
        timings.update(result['project_timings'])
    lines = [f'Assessment: {timings["assessment"]:.2f}s']
    if 'project_timings' in result:
        lines.extend([f'Planning: {timings["planning"]:.2f}s', f'Plan retries: {timings["retry"]:.2f}s'])
    lines.append(f'Generation: {timings["generation"]:.2f}s')
    if 'project_timings' in result:
        lines.append(f'Verification: {timings["verification"]:.2f}s')
    lines.append(f'Total: {timings["total"]:.2f}s')
    print('\n' + '\n'.join(lines))
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
        request_started = started
        assessment, assessment_stats = assess(runtime, prompt, language, filename, root=root)
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
        sidecar = path.with_name(path.name + '.gi-coder.json')
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
        workspace = None
        config = runtime.config if isinstance(runtime.config, dict) else {}
        incremental = assessment['language'] == 'python' and needs_decomposition(assessment, prompt)
        if incremental:
            source, manifest, workspace = generate_project(runtime, prompt, assessment, root, config)
            stats = {}
        else:
            source, manifest, workspace, stats = generate_single(runtime, prompt, assessment, revised, root, config)
        if assessment['language'] == 'json':
            try:
                json.loads(source)
            except ValueError as exc:
                raise CoderError(f'Generated JSON is invalid: {exc}; no file written') from exc
        source, metadata = skeleton_header(source, assessment['language'], assessment['functions'])
        if incremental:
            manifest['verification'] = {**verify_source(source, manifest['plan']),
                'coverage': 'model_reviewed_static_evidence_not_runtime_proof'}
        elif assessment['language'] == 'python':
            # Comments do not change static findings, but the delivered hash must match.
            manifest['verification'] = {**verify_source(source),
                'coverage': manifest['verification']['coverage']}
        import hashlib
        manifest['verification']['source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
        stats = {**stats, 'seconds': time.monotonic() - started}
        manifest['metrics'] = call_metrics([{'seconds': assessment_stats['seconds'], 'usage': assessment_stats},
            *manifest.get('calls', [])], success=True)
        manifest['assessment_statistics'] = assessment_stats
        manifest['metrics']['request_seconds_before_delivery'] = time.monotonic() - request_started
        manifest['functions'] = assessment['functions']  # Retain V1 metadata compatibility.
        manifest['output_path'] = str(path)
        # Stage sidecar first so a metadata collision cannot leave an unaccompanied
        # source file. On failure roll back only a sidecar newly created by us.
        created_sidecar = False
        backup = None
        if sidecar:
            existed = sidecar.exists() or sidecar.is_symlink()
            if existed and permissions[sidecar]:
                fd, name = tempfile.mkstemp(prefix='.gi-coder-manifest-backup-', dir=sidecar.parent)
                os.close(fd)
                backup = Path(name)
                os.replace(sidecar, backup)
            try:
                write_source(sidecar, json.dumps(manifest, ensure_ascii=True, indent=2) + '\n', permissions[sidecar])
            except BaseException:
                if backup:
                    os.replace(backup, sidecar)
                raise
            created_sidecar = not existed
        try:
            write_source(path, source, permissions[path])
        except BaseException:
            if backup:
                os.replace(backup, sidecar)
            elif created_sidecar:
                sidecar.unlink(missing_ok=True)
            raise
        if backup:
            backup.unlink()
        print(f'File created: {path}\nLanguage: {assessment["language"]}\nStatus: Complete')
        if sidecar:
            print(f'Project manifest: {sidecar}')
        if workspace:
            write_source(workspace/('source' + assessment['extension']), source, True)
            write_source(workspace/'manifest.json', json.dumps(manifest, ensure_ascii=True, indent=2), True)
            missing = [d['package'] for d in manifest.get('dependencies', []) if d['installed_version'] is None]
            if missing:
                print('Dependencies not installed (not installed automatically): ' + ', '.join(missing))
        result = {'type': 'code', 'path': path, 'assessment': assessment, 'metrics': manifest['metrics'],
                  'statistics': {'assessment': assessment_stats, 'generation': stats}}
        if incremental:
            result['project_timings'] = dict(manifest['timings'])
        return result


def needs_decomposition(assessment, prompt):
    """Function count alone does not make a small calculator a complex project."""
    import re
    return (assessment['complexity'] == 'complex' or len(assessment['functions']) >= 5
            or assessment['complexity'] == 'moderate' and bool(re.search(
                r'\b(?:pygame|GUI|event.loop)\b', prompt, re.I)))
