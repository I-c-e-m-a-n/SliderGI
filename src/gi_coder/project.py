"""Bounded, single-file Python planning and function-by-function integration."""
import ast
import json
import keyword
import re
import tempfile
import textwrap
import time
from pathlib import Path

from .assessment import no_duplicates, string, validate_schema
from .config import CoderError
from .generation import extract_source, write_source
from .verification import dependency_status, parse_signature, verify_source


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def array(item, limit=32):
    return {'type': 'array', 'items': item, 'maxItems': limit}


PLAN_SCHEMA = obj({
    'architecture': string(1000),
    'requirements': array(obj({'id': string(20), 'text': string(1200), 'components': array(string(100))})),
    'imports': array(string(256)),
    'dependencies': array(obj({'module': string(80), 'package': string(80)})),
    'variables': array(obj({'name': string(80), 'value': string(1000)})),
    'classes': array(obj({'name': string(80), 'bases': array(string(100)), 'fields': array(string(80))})),
    'components': array(obj({'name': string(100), 'signature': string(400), 'description': string(1600),
                             'depends_on': array(string(100)), 'uses': array(string(100)), 'requirements': array(string(20))})),
    'entrypoint': string(80)})
REVIEW_SCHEMA = obj({'verdict': {'type': 'string', 'enum': ['pass', 'fail']},
                     'evidence': string(2000), 'evidence_snippets': array(string(1000)), 'issues': array(string(500)),
                     'missing_requirements': array(string(500))})


def decode(raw, schema):
    try:
        if len(raw) > 128000:
            raise CoderError('Project JSON exceeds resource limit')
        data = json.loads(raw, object_pairs_hook=no_duplicates)
    except ValueError as exc:
        raise CoderError('Invalid project JSON') from exc
    validate_schema(data, schema)
    return data


def verify_evidence(review, source):
    """Require concrete executable AST snippets, not invented lines or comments."""
    if not review['evidence_snippets']:
        raise CoderError('Requirement review lacks source evidence')
    nodes = (ast.Call, ast.Assign, ast.AugAssign, ast.Return, ast.If, ast.While, ast.For, ast.Compare)
    segments = {ast.get_source_segment(source, node) for node in ast.walk(ast.parse(source)) if isinstance(node, nodes)}
    for snippet in review['evidence_snippets']:
        if snippet not in segments:
            raise CoderError('Requirement review cited nonexistent or non-executable source evidence')


def identifier(name):
    if not name.isidentifier() or keyword.iskeyword(name):
        raise CoderError(f'Invalid Python identifier in plan: {name}')


def validate_plan(plan, config):
    try:
        return _validate_plan(plan, config)
    except (SyntaxError, ValueError, TypeError, KeyError) as exc:
        raise CoderError(f'Invalid implementation plan: {exc}') from exc


def _validate_plan(plan, config):
    if not 1 <= len(plan['components']) <= config.get('max_components', 16):
        raise CoderError('Plan exceeds max_components or has no components')
    if not 1 <= len(plan['requirements']) <= config.get('max_requirements', 16):
        raise CoderError('Plan exceeds max_requirements or has no requirements')
    names = [item['name'] for item in plan['components']]
    if len(set(names)) != len(names):
        raise CoderError('Duplicate planned components')
    reqs = [item['id'] for item in plan['requirements']]
    if len(set(reqs)) != len(reqs):
        raise CoderError('Duplicate requirements')
    classes = {cls['name']: cls for cls in plan['classes']}
    if len(classes) != len(plan['classes']):
        raise CoderError('Duplicate classes')
    declared = set(classes)
    for cls in plan['classes']:
        identifier(cls['name'])
        for field in cls['fields']:
            identifier(field)
        for base in cls['bases']:
            node = ast.parse(base, mode='eval').body
            if not isinstance(node, (ast.Name, ast.Attribute)) or any(not isinstance(n, (ast.Name, ast.Attribute, ast.Load)) for n in ast.walk(node)):
                raise CoderError('Class bases must be names or attributes')
            if isinstance(node, ast.Name) and node.id in classes and node.id not in list(classes)[:list(classes).index(cls['name'])]:
                raise CoderError('Classes must be ordered before their derived classes')
    for item in plan['variables']:
        identifier(item['name'])
        if item['name'] in declared:
            raise CoderError('Duplicate module symbol')
        declared.add(item['name'])
        try:
            ast.literal_eval(item['value'])
        except (SyntaxError, ValueError) as exc:
            raise CoderError('Shared initializers must be literals; initialize runtime resources in main/__init__') from exc
    for line in plan['imports']:
        tree = ast.parse(line)
        if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.Import, ast.ImportFrom)):
            raise CoderError('Plan imports must contain only import statements')
        node = tree.body[0]
        if isinstance(node, ast.ImportFrom) and (node.level or any(a.name == '*' for a in node.names)):
            raise CoderError('Relative and wildcard imports are unsupported in single-file plans')
        for alias in node.names:
            name = alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
            if name in declared:
                raise CoderError(f'Import collides with another symbol: {name}')
            declared.add(name)
        # Normalize spelling for comparison against actual source.
        plan['imports'][plan['imports'].index(line)] = ast.unparse(node)
    roots = set()
    for line in plan['imports']:
        node = ast.parse(line).body[0]
        if isinstance(node, ast.Import):
            roots.update(a.name.split('.')[0] for a in node.names)
        else:
            roots.add(node.module.split('.')[0])
    import sys
    external = roots - sys.stdlib_module_names - {'__future__'}
    if external != {d['module'] for d in plan['dependencies']}:
        raise CoderError('External imports and dependency manifest disagree')
    for dependency in plan['dependencies']:
        identifier(dependency['module'])
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', dependency['package']):
            raise CoderError('Unsafe dependency package name')
    global_symbols = set(declared)
    for component in plan['components']:
        if not set(component['uses']) <= global_symbols:
            raise CoderError('Component uses undeclared shared symbols/imports')
        parts = component['name'].split('.')
        if len(parts) > 2:
            raise CoderError('Nested classes/functions are not supported')
        for part in parts:
            identifier(part)
        node = parse_signature(component['signature'])
        if node.name != parts[-1]:
            raise CoderError('Component name and signature disagree')
        if len(parts) == 2:
            if parts[0] not in classes or not node.args.args or node.args.args[0].arg != 'self':
                raise CoderError('Methods need a declared class and self parameter')
            constructor = parts[0] + '.__init__'
            if component['name'] != constructor and constructor in names and constructor not in component['depends_on']:
                component['depends_on'].append(constructor)
        elif component['name'] in declared:
            raise CoderError('Duplicate module symbol')
        else:
            declared.add(component['name'])
        if not set(component['requirements']) <= set(reqs) or not component['requirements']:
            raise CoderError('Component has invalid/missing requirement links')
        if not set(component['depends_on']) <= set(names) or component['name'] in component['depends_on']:
            raise CoderError('Invalid component dependency')
    for req in plan['requirements']:
        if not req['components'] or not set(req['components']) <= set(names):
            raise CoderError('Requirement lacks planned implementation')
    if plan['entrypoint'] not in names or '.' in plan['entrypoint']:
        raise CoderError('Application needs a top-level entrypoint')
    entry = parse_signature(next(c['signature'] for c in plan['components'] if c['name'] == plan['entrypoint']))
    if entry.args.args or entry.args.posonlyargs or entry.args.kwonlyargs or isinstance(entry, ast.AsyncFunctionDef):
        raise CoderError('Entrypoint must be a synchronous zero-argument function')
    ordered, remaining = [], list(plan['components'])
    while remaining:
        ready = [c for c in remaining if set(c['depends_on']) <= {x['name'] for x in ordered}]
        if not ready:
            raise CoderError('Cyclic component dependencies')
        for component in ready:
            remaining.remove(component)
            ordered.append(component)
    plan['components'] = ordered
    return plan


def assemble(plan, sources, final=False):
    sections = ['from __future__ import annotations', *[i for i in plan['imports'] if i != 'from __future__ import annotations']]
    sections.extend(item['name'] + ' = ' + item['value'] for item in plan['variables'])
    for cls in plan['classes']:
        methods = [sources[c['name']] for c in plan['components'] if c['name'].startswith(cls['name'] + '.') and c['name'] in sources]
        sections.append('class ' + cls['name'] + ('(' + ', '.join(cls['bases']) + ')' if cls['bases'] else '') + ':\n' +
                        textwrap.indent('\n\n'.join(methods) if methods else 'pass', '    '))
    sections.extend(sources[c['name']] for c in plan['components'] if '.' not in c['name'] and c['name'] in sources)
    if final:
        sections.append('if __name__ == "__main__":\n    ' + plan['entrypoint'] + '()')
    return '\n\n'.join(sections) + '\n'


def component_source(raw, component):
    source = extract_source(raw, 'python')
    tree = ast.parse(source)
    if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise CoderError('Component must contain exactly one function/method, without imports or extra definitions')
    if tree.body[0].decorator_list:
        raise CoderError('Decorated components are unsupported')
    if any(isinstance(n, (ast.Import, ast.ImportFrom, ast.ClassDef)) for n in ast.walk(tree.body[0])):
        raise CoderError('Component imports/classes must be declared in the central plan')
    if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(tree.body[0]) if n is not tree.body[0]):
        raise CoderError('Nested function generation is unsupported')
    if any(isinstance(n, ast.Call) and (isinstance(n.func, ast.Name) and n.func.id == '__import__'
           or isinstance(n.func, ast.Attribute) and n.func.attr == 'import_module') for n in ast.walk(tree.body[0])):
        raise CoderError('Dynamic imports cannot be checked against the manifest')
    if tree.body[0].name != component['name'].split('.')[-1]:
        raise CoderError('Generated wrong component')
    return source


def generate_project(runtime, prompt, assessment, root, max_config):
    """One flat plan, at most N components and R reviews; no repair recursion."""
    directory = root / '.gi-coder/projects'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = Path(tempfile.mkdtemp(prefix='project-', dir=directory))
    manifest = {'version': 1, 'original_request': prompt, 'assessment': assessment, 'status': 'planning',
                'plan': None, 'progress': {}, 'verification': None, 'reviews': [], 'calls': []}
    def save():
        write_source(workspace/'manifest.json', json.dumps(manifest, ensure_ascii=True, indent=2) + '\n', True)
    save()
    deadline = time.monotonic() + max_config.get('project_timeout', 1800)
    calls = 0
    def call(system, context, schema=None):
        nonlocal calls
        calls += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0 or calls > 2 + max_config.get('max_components', 16) + max_config.get('max_requirements', 16):
            raise CoderError('Project time/call budget exhausted')
        started = time.monotonic()
        raw, stats = runtime.complete(system, json.dumps(context, separators=(',', ':')), schema=schema, timeout=remaining)
        manifest['calls'].append({'number': calls, 'seconds': time.monotonic() - started, 'usage': stats})
        save()
        if time.monotonic() > deadline:
            raise CoderError('Project time budget exhausted')
        return raw
    try:
        raw = call('Plan ONE Python application, no code yet. Return schema JSON. Cover every original requirement, '
                   'including controls and forbidden libraries. Declare imports, dependency packages, literal shared values, '
                   'classes with self fields, exact def signatures (no colon), direct component dependencies and requirement IDs. '
                   'For each component list uses (only needed imported names/shared variables/classes) and direct depends_on. '
                   'Use a zero-argument main entrypoint. Qualified Class.method components are generated individually. '
                   'No decorators, nested functions, recursive task planning, execution or extra files. Keep the flat plan within '
                   f'{max_config.get("max_components", 16)} components and {max_config.get("max_requirements", 16)} requirements.',
                   {'original_request': prompt, 'assessment': assessment}, PLAN_SCHEMA)
        plan = validate_plan(decode(raw, PLAN_SCHEMA), max_config)
        manifest.update(plan=plan, status='implementing', dependencies=dependency_status(plan['dependencies']),
                        progress={c['name']: 'pending' for c in plan['components']})
        save()
        sources = {}
        for component in plan['components']:
            print(f'Implementing {component["name"]}...', flush=True)
            used = set(component['uses'])
            relevant_imports = []
            for line in plan['imports']:
                node = ast.parse(line).body[0]
                aliases = {a.asname or (a.name.split('.')[0] if isinstance(node, ast.Import) else a.name) for a in node.names}
                if aliases & used:
                    relevant_imports.append(line)
            context = {'component': component, 'imports': relevant_imports,
                       'shared_variables': [v for v in plan['variables'] if v['name'] in used],
                       'classes': [c for c in plan['classes'] if c['name'] in used or component['name'].startswith(c['name'] + '.')],
                       'dependency_signatures': {c['name']: c['signature'] for c in plan['components'] if c['name'] in component['depends_on']},
                       'requirements': [r for r in plan['requirements'] if r['id'] in component['requirements']]}
            raw = call('Implement only this exact Python function or method in one fenced Python block. '
                       'Preserve its signature, use declared imports/shared state and dependency signatures. '
                       'Do not add imports, classes, helper functions, decorators, placeholders or entrypoint calls.', context)
            sources[component['name']] = component_source(raw, component)
            source = assemble(plan, sources)
            if len(source.encode()) > 128 * 1024:
                raise CoderError('Single-file source exceeds 128 KiB safeguard')
            manifest['verification'] = verify_source(source, plan, completed=sources, final=False)
            write_source(workspace/'source.py', source, True)
            manifest['progress'][component['name']] = 'implemented'
            save()
        source = assemble(plan, sources, final=True)
        manifest['verification'] = verify_source(source, plan)
        forbidden = re.findall(r"(?:do not|don't|never)\s+(?:use|import)\s+([A-Za-z_]\w*)|without\s+([A-Za-z_]\w*)", prompt, re.I)
        imported = ' '.join(manifest['verification']['inventory']['imports']).lower()
        for pair in forbidden:
            module = next(filter(None, pair)).lower()
            if re.search(r'\b' + re.escape(module) + r'\b', imported):
                raise CoderError(f'Forbidden import from original request: {module}')
        write_source(workspace/'source.py', source, True)
        manifest['status'] = 'reviewing'
        save()
        # Review each requirement using only its integrated reachable component
        # slice. All requirement texts remain visible so omissions can be flagged.
        graph = manifest['verification']['references']
        for req in plan['requirements']:
            relevant, pending = set(), list(req['components'])
            while pending:
                name = pending.pop()
                if name not in relevant:
                    relevant.add(name)
                    pending.extend(graph.get(name, []))
            # Include callers so a reviewer can see whether drawing/updating is
            # actually connected to the event loop, not just an isolated helper.
            changed = True
            while changed:
                parents = {name for name, refs in graph.items() if set(refs) & relevant}
                changed = not parents <= relevant
                relevant.update(parents)
            review = decode(call('Review this integrated code slice without executing it. Check the target requirement, '
                'actual behavior/data flow and event-loop integration, not comments or mere syntax. Compare the full original '
                'request against the requirement list; report omissions in missing_requirements. Fail on missing/disconnected '
                'behavior, forbidden libraries, placeholders or insufficient evidence. Cite exact executable AST statements '
                'or expressions copied verbatim from the supplied source in evidence_snippets.',
                {'original_request': prompt, 'all_requirements': plan['requirements'], 'target': req,
                 'imports': plan['imports'], 'variables': plan['variables'], 'classes': plan['classes'],
                 'references': graph, 'source': {name: sources[name] for name in sorted(relevant)}}, REVIEW_SCHEMA), REVIEW_SCHEMA)
            manifest['reviews'].append({'requirement': req['id'], **review})
            save()
            if review['verdict'] != 'pass' or review['issues'] or review['missing_requirements']:
                raise CoderError(f'Requirement verification failed: {req["id"]}: {review["evidence"]}')
            verify_evidence(review, '\n'.join(sources[name] for name in sorted(relevant)))
        manifest.update(status='verified', progress={name: 'verified' for name in sources})
        manifest['verification']['coverage'] = 'model_reviewed_static_evidence_not_runtime_proof'
        save()
        return source, manifest, workspace
    except BaseException as exc:
        manifest.update(status='failed', error=str(exc) or type(exc).__name__)
        save()
        raise


def single_manifest(runtime, prompt, assessment, source):
    """Keep simple requests one-shot; one bounded review checks their semantics."""
    manifest = {'version': 1, 'original_request': prompt, 'assessment': assessment,
                'status': 'verified', 'plan': {'architecture': 'single-pass source',
                    'requirements': [{'id': 'R1', 'text': prompt}], 'functions': assessment['functions']},
                'progress': {'source': 'implemented'}, 'reviews': []}
    if assessment['language'] == 'python':
        manifest['verification'] = verify_source(source)
        raw, _ = runtime.complete('Review the Python program without executing it. Compare actual implementation '
            'against every original requirement/constraint. Fail missing or disconnected behavior, undefined names, '
            'invalid imports/signatures and placeholders. Give concrete evidence; syntax alone is insufficient. '
            'Copy exact executable source statements/expressions verbatim into evidence_snippets.',
            json.dumps({'original_request': prompt, 'assessment': assessment, 'source': source}, separators=(',', ':')),
            schema=REVIEW_SCHEMA)
        review = decode(raw, REVIEW_SCHEMA)
        manifest['reviews'].append(review)
        if review['verdict'] != 'pass' or review['issues'] or review['missing_requirements']:
            raise CoderError('Source requirement review failed: ' + review['evidence'])
        verify_evidence(review, source)
        manifest['verification']['coverage'] = 'model_reviewed_not_runtime_proof'
        manifest['plan']['actual_structure'] = manifest['verification']['inventory']
        index = manifest['verification']['inventory']
        manifest['plan'].update(imports=index['imports'], variables=index['variables'], classes=index['classes'],
                                signatures=index['functions'])
        import sys
        roots = set()
        for line in index['imports']:
            node = ast.parse(line).body[0]
            if isinstance(node, ast.Import):
                roots.update(a.name.split('.')[0] for a in node.names)
            elif node.module:
                roots.add(node.module.split('.')[0])
        # Package names can differ from import names; no installation is attempted.
        manifest['dependencies'] = dependency_status([{'module': name, 'package': name}
            for name in sorted(roots - sys.stdlib_module_names - {'__future__'})])
        manifest['progress']['source'] = 'verified'
    else:
        import hashlib
        manifest['status'] = 'framing_verified'
        manifest['verification'] = {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                                    'coverage': 'unverified', 'level': 'framing_only_non_python'}
    return manifest
