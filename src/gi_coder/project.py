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
from .verification import dependency_status, parse_signature, signature, verify_source


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
# Empty structural sections are optional; normalization supplies bounded empty lists.
PLAN_SCHEMA['required'] = [key for key in PLAN_SCHEMA['required']
                           if key not in ('imports', 'dependencies', 'variables', 'classes')]
REVIEW_SCHEMA = obj({'verdict': {'type': 'string', 'enum': ['pass', 'fail']},
                     'evidence': string(2000), 'evidence_snippets': array(string(1000)), 'issues': array(string(500)),
                     'missing_requirements': array(string(500))})


def decode(raw, schema, location='project'):
    try:
        if len(raw) > 128000:
            raise CoderError('Project JSON exceeds resource limit')
        data = json.loads(raw, object_pairs_hook=no_duplicates)
    except json.JSONDecodeError as exc:
        raise CoderError(f'{location}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}') from exc
    if schema is PLAN_SCHEMA:
        data = normalize_plan(data)
    validate_schema(data, schema, location)
    return data


class RequirementReviewError(CoderError):
    """A valid reviewer response reports a source requirement defect."""


class ReviewEvidenceError(CoderError):
    """A reviewer response failed its contract; this is not a source defect."""


def verify_evidence(review, source):
    """Compare executable ASTs so whitespace/quote differences need no inference."""
    if not review['evidence_snippets']:
        raise ReviewEvidenceError('Requirement review lacks source evidence')
    kinds = (ast.Call, ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return,
             ast.If, ast.While, ast.For, ast.Compare, ast.Raise)
    segments = {ast.dump(node) for node in ast.walk(ast.parse(source)) if isinstance(node, kinds)}
    for snippet in review['evidence_snippets']:
        try:
            tree = ast.parse(textwrap.dedent(snippet).strip())
            nodes = [n.value if isinstance(n, ast.Expr) else n for n in tree.body]
        except SyntaxError:
            nodes = []
        if not nodes or any(ast.dump(n) not in segments for n in nodes):
            raise ReviewEvidenceError('Requirement review cited nonexistent or non-executable source evidence')


def review_source(call, context, source):
    """At most one targeted evidence correction. Never rewrite valid code for it."""
    previous = None
    for attempt in range(2):
        raw = call('Review the supplied Python source without executing it. Check actual behavior and '
                   'all original requirements, not comments or syntax alone. Report missing behavior in issues '
                   'or missing_requirements. Copy executable source statements or expressions into evidence_snippets. '
                   'When review_error is supplied, correct only the review evidence, preserving honest findings.',
                   context, REVIEW_SCHEMA)
        try:
            review = decode(raw, REVIEW_SCHEMA)
            verify_evidence(review, source)
            return review
        except CoderError as exc:
            if attempt or raw == previous:
                raise ReviewEvidenceError('Review-evidence validation failed: ' + str(exc)) from exc
            previous = raw
            context = {**context, 'rejected_review': raw, 'review_error': str(exc)}
    raise AssertionError('unreachable')


def normalize_plan(plan):
    """Only unambiguous spelling repairs; never evaluate expressions or drop fields."""
    if not isinstance(plan, dict):
        return plan
    import copy
    plan = copy.deepcopy(plan)
    for key in ('imports', 'dependencies', 'variables', 'classes'):
        plan.setdefault(key, [])
    if isinstance(plan.get('imports'), list):
        for i, line in enumerate(plan['imports']):
            if isinstance(line, str):
                line = line.strip()
                if re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', line):
                    line = 'import ' + line
                plan['imports'][i] = line
    if isinstance(plan.get('components'), list):
        for component in plan['components']:
            if isinstance(component, dict) and isinstance(component.get('signature'), str):
                try:
                    component['signature'] = signature(parse_signature(component['signature']))
                except CoderError:
                    pass  # Preserve invalid values for field-specific validation diagnostics.
    return plan


def identifier(name, field='plan'):
    if not name.isidentifier() or keyword.iskeyword(name):
        raise CoderError(f'{field}: invalid Python identifier {name!r}')


def validate_plan(plan, config):
    try:
        return _validate_plan(normalize_plan(plan), config)
    except (SyntaxError, ValueError, TypeError, KeyError) as exc:
        raise CoderError(f'Invalid implementation plan: {exc}') from exc


def _validate_plan(plan, config):
    if not 1 <= len(plan['components']) <= config.get('max_components', 16):
        raise CoderError(f'plan.components: expected 1..{config.get("max_components", 16)} items (max_components), got {len(plan["components"])}')
    if not 1 <= len(plan['requirements']) <= config.get('max_requirements', 16):
        raise CoderError(f'plan.requirements: expected 1..{config.get("max_requirements", 16)} items (max_requirements), got {len(plan["requirements"])}')
    names = [item['name'] for item in plan['components']]
    if len(set(names)) != len(names):
        raise CoderError(f'plan.components[].name: duplicate names in {names!r}')
    reqs = [item['id'] for item in plan['requirements']]
    if len(set(reqs)) != len(reqs):
        raise CoderError(f'plan.requirements[].id: duplicate IDs in {reqs!r}')
    classes = {cls['name']: cls for cls in plan['classes']}
    if len(classes) != len(plan['classes']):
        raise CoderError(f'plan.classes[].name: duplicate class names in {[c["name"] for c in plan["classes"]]!r}')
    declared = set(classes)
    for ci, cls in enumerate(plan['classes']):
        identifier(cls['name'], f'plan.classes[{ci}].name')
        for fi, field in enumerate(cls['fields']):
            identifier(field, f'plan.classes[{ci}].fields[{fi}]')
        for bi, base in enumerate(cls['bases']):
            try:
                node = ast.parse(base, mode='eval').body
            except SyntaxError as exc:
                raise CoderError(f'plan.classes[{ci}].bases[{bi}] = {base!r}: invalid expression') from exc
            if not isinstance(node, (ast.Name, ast.Attribute)) or any(not isinstance(n, (ast.Name, ast.Attribute, ast.Load)) for n in ast.walk(node)):
                raise CoderError(f'plan.classes[{ci}].bases[{bi}] = {base!r}: class bases must be names or attributes')
            if isinstance(node, ast.Name) and node.id in classes and node.id not in list(classes)[:list(classes).index(cls['name'])]:
                raise CoderError(f'plan.classes[{ci}].bases[{bi}] = {base!r}: base class must precede derived class')
    for vi, item in enumerate(plan['variables']):
        identifier(item['name'], f'plan.variables[{vi}].name')
        if item['name'] in declared:
            raise CoderError(f'plan.variables[{vi}].name = {item["name"]!r}: duplicate module symbol')
        declared.add(item['name'])
        try:
            ast.literal_eval(item['value'])
        except (SyntaxError, ValueError) as exc:
            raise CoderError(f'plan.variables[{vi}].value ({item["name"]}) = {item["value"]!r}: '
                'Shared initializers must be literals; precompute constant values or initialize runtime '
                'resources/calculations inside main/__init__ and pass them as parameters or self fields') from exc
    for ii, line in enumerate(plan['imports']):
        try:
            tree = ast.parse(line)
        except SyntaxError as exc:
            raise CoderError(f'plan.imports[{ii}] = {line!r}: invalid import syntax') from exc
        if len(tree.body) != 1 or not isinstance(tree.body[0], (ast.Import, ast.ImportFrom)):
            raise CoderError(f'plan.imports[{ii}] = {line!r}: must contain only one import statement')
        node = tree.body[0]
        if isinstance(node, ast.ImportFrom) and (node.level or any(a.name == '*' for a in node.names)):
            raise CoderError(f'plan.imports[{ii}] = {line!r}: relative/wildcard imports are unsupported')
        for alias in node.names:
            name = alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
            if name in declared:
                raise CoderError(f'plan.imports[{ii}] = {line!r}: import collides with symbol {name!r}')
            declared.add(name)
        # Normalize spelling for comparison against actual source.
        plan['imports'][ii] = ast.unparse(node)
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
        raise CoderError(f'plan.dependencies[].module: expected {sorted(external)!r} from imports, '
                         f'got {[d["module"] for d in plan["dependencies"]]!r}')
    for di, dependency in enumerate(plan['dependencies']):
        identifier(dependency['module'], f'plan.dependencies[{di}].module')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', dependency['package']):
            raise CoderError(f'plan.dependencies[{di}].package = {dependency["package"]!r}: unsafe package name')
    global_symbols = set(declared)
    for ci, component in enumerate(plan['components']):
        if not set(component['uses']) <= global_symbols:
            raise CoderError(f'plan.components[{ci}].uses = {component["uses"]!r}: undeclared shared symbols/imports')
        parts = component['name'].split('.')
        if len(parts) > 2:
            raise CoderError(f'plan.components[{ci}].name = {component["name"]!r}: nested classes/functions unsupported')
        for part in parts:
            identifier(part, f'plan.components[{ci}].name')
        try:
            node = parse_signature(component['signature'])
        except CoderError as exc:
            raise CoderError(f'plan.components[{ci}].signature = {component["signature"]!r}: {exc}') from exc
        if node.name != parts[-1]:
            raise CoderError(f'plan.components[{ci}].signature = {component["signature"]!r}: disagrees with name {component["name"]!r}')
        if len(parts) == 2:
            if parts[0] not in classes or not node.args.args or node.args.args[0].arg != 'self':
                raise CoderError(f'plan.components[{ci}].signature = {component["signature"]!r}: method needs declared class/self')
            constructor = parts[0] + '.__init__'
            if component['name'] != constructor and constructor in names and constructor not in component['depends_on']:
                component['depends_on'].append(constructor)
        elif component['name'] in declared:
            raise CoderError(f'plan.components[{ci}].name = {component["name"]!r}: duplicate module symbol')
        else:
            declared.add(component['name'])
        if not set(component['requirements']) <= set(reqs) or not component['requirements']:
            raise CoderError(f'plan.components[{ci}].requirements = {component["requirements"]!r}: invalid/missing links')
        if not set(component['depends_on']) <= set(names) or component['name'] in component['depends_on']:
            raise CoderError(f'plan.components[{ci}].depends_on = {component["depends_on"]!r}: invalid component dependency')
    for ri, req in enumerate(plan['requirements']):
        if not req['components'] or not set(req['components']) <= set(names):
            raise CoderError(f'plan.requirements[{ri}].components = {req["components"]!r}: no valid implementation links')
    if plan['entrypoint'] not in names or '.' in plan['entrypoint']:
        raise CoderError(f'plan.entrypoint = {plan["entrypoint"]!r}: must name a top-level component')
    entry = parse_signature(next(c['signature'] for c in plan['components'] if c['name'] == plan['entrypoint']))
    if entry.args.args or entry.args.posonlyargs or entry.args.kwonlyargs or isinstance(entry, ast.AsyncFunctionDef):
        raise CoderError(f'plan.entrypoint = {plan["entrypoint"]!r}: signature {ast.unparse(entry)!r} must be synchronous/zero-argument')
    ordered, remaining = [], list(plan['components'])
    while remaining:
        ready = [c for c in remaining if set(c['depends_on']) <= {x['name'] for x in ordered}]
        if not ready:
            raise CoderError(f'plan.components[].depends_on: cyclic dependencies among {[c["name"] for c in remaining]!r}')
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
    """Flat planning, bounded component repair, and non-executing verification."""
    directory = root / '.gi-coder/projects'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = Path(tempfile.mkdtemp(prefix='project-', dir=directory))
    manifest = {'version': 1, 'workspace': str(workspace), 'original_request': prompt, 'assessment': assessment, 'status': 'planning',
                'plan': None, 'progress': {}, 'verification': None, 'reviews': [], 'calls': [],
                'planning_attempts': [], 'timings': {'planning': 0.0, 'retry': 0.0,
                                                    'generation': 0.0, 'verification': 0.0}}
    def save():
        manifest['metrics'] = call_metrics(manifest['calls'], manifest['status'] == 'verified')
        write_source(workspace/'manifest.json', json.dumps(manifest, ensure_ascii=True, indent=2) + '\n', True)
    save()
    deadline = time.monotonic() + max_config.get('project_timeout', 1800)
    calls = 0
    def call(system, context, schema=None, phase='generation', raw_plan_path=None):
        nonlocal calls
        calls += 1
        remaining = deadline - time.monotonic()
        if remaining <= 0 or calls > 5 + 3 * max_config.get('max_components', 16) + 2 * max_config.get('max_requirements', 16):
            raise CoderError('Project time/call budget exhausted')
        started = time.monotonic()
        row = {'number': calls, 'phase': phase, 'usage': {}, 'status': 'failed'}
        try:
            raw, stats = runtime.complete(system, json.dumps(context, separators=(',', ':')), schema=schema, timeout=remaining)
            # Save verbatim before decoding, normalizing, validation or timeout checks.
            if raw_plan_path:
                write_source(workspace/raw_plan_path, raw, True)
            row.update(usage=stats, status='completed')
        except BaseException as exc:
            partial = getattr(exc, 'raw_response', None)
            if raw_plan_path and isinstance(partial, str):
                write_source(workspace/raw_plan_path, partial, True)
                row['usage'] = getattr(exc, 'response_usage', {})
            row['error'] = str(exc) or type(exc).__name__
            raise
        finally:
            row['seconds'] = time.monotonic() - started
            manifest['calls'].append(row)
            manifest['timings'][phase] += row['seconds']
            save()
        if time.monotonic() > deadline:
            raise CoderError('Project time budget exhausted')
        return raw
    try:
        system = ('Plan one Python file as compact schema JSON, no code. Original requirements take priority. '
            'Use the fewest necessary components; brief descriptions and requirement IDs, not repeated prose. '
            'variables.value strings contain Python literals only: 640, False, None, quoted strings, literal containers. '
            'No calls, names or arithmetic there: put pygame.display.set_mode(...), pygame.time.Clock(), fonts '
            'and calculations inside main/__init__; pass live state as parameters or self fields. '
            'Use exact def signatures without colons, a zero-argument main, direct depends_on, and uses for needed '
            'imports/shared data/classes. No decorators, nested functions or extra files. '
            f'Maximum {max_config.get("max_components", 16)} components and {max_config.get("max_requirements", 16)} requirements.')
        context = {'original_request': prompt, 'revised_prompt': assessment.get('revised_prompt', '')}
        rejected_fingerprints, rejected_errors = set(), set()
        for attempt in range(1, 4):
            entry = {'attempt': attempt, 'call_number': calls + 1,
                     'raw_plan': f'plan-attempt-{attempt}.raw.json', 'status': 'pending'}
            manifest['planning_attempts'].append(entry)
            save()
            print('Planning...' if attempt == 1 else f'Correcting plan ({attempt - 1}/2)...', flush=True)
            try:
                raw = call(system if attempt == 1 else (
                    'Correct the rejected plan using validation_error. Return the complete corrected compact schema JSON. '
                    'Keep original requirements. Keep literal constants; remove runtime resources from variables (not None placeholders), '
                    'initialize resources/calculations inside main/__init__, '
                    'pass them explicitly, and update signatures, uses and dependencies. No safety bypass or extra scope.'),
                    context, PLAN_SCHEMA, phase='planning' if attempt == 1 else 'retry', raw_plan_path=entry['raw_plan'])
            except BaseException as exc:
                entry.update(status='inference_failed', error=str(exc) or type(exc).__name__)
                raise  # Transport, token/time budget and cancellation errors are not validation retries.
            finally:
                if manifest['calls'] and manifest['calls'][-1]['number'] == entry['call_number']:
                    entry.update(seconds=manifest['calls'][-1]['seconds'], usage=manifest['calls'][-1]['usage'])
                save()
            try:
                plan = validate_plan(decode(raw, PLAN_SCHEMA, 'plan'), max_config)
            except CoderError as exc:
                entry.update(status='rejected', validation_error=str(exc))
                save()
                fingerprint = plan_fingerprint(raw)
                if fingerprint in rejected_fingerprints or str(exc) in rejected_errors:
                    raise CoderError(f'Repeated plan failure after {attempt} attempts: {exc}') from exc
                rejected_fingerprints.add(fingerprint)
                rejected_errors.add(str(exc))
                if attempt == 3:
                    raise CoderError(f'Plan validation failed after 3 attempts: {exc}') from exc
                try:
                    rejected = json.loads(raw, object_pairs_hook=no_duplicates)
                except (ValueError, CoderError):
                    rejected = raw
                context = {'original_request': prompt, 'rejected_plan': rejected, 'validation_error': str(exc)}
            else:
                entry['status'] = 'accepted'
                save()
                break
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
            seen = set()
            for attempt in range(3):
                raw = call('Implement only this exact Python function or method in one fenced Python block. '
                           'Preserve its signature, use declared imports/shared state and dependency signatures. '
                           'Do not add imports, classes, helper functions, decorators, placeholders or entrypoint calls. '
                           'If validation_error is present, correct that defect without changing the contract.', context,
                           phase='generation' if attempt == 0 else 'retry')
                try:
                    candidate = component_source(raw, component)
                    trial = {**sources, component['name']: candidate}
                    source = assemble(plan, trial)
                    if len(source.encode()) > 128 * 1024:
                        raise CoderError('Single-file source exceeds 128 KiB safeguard')
                    checked = verify_source(source, plan, completed=trial, final=False)
                except CoderError as exc:
                    fingerprint = raw.strip()
                    if fingerprint in seen or attempt == 2:
                        raise CoderError(f'Component correction exhausted: {exc}') from exc
                    seen.add(fingerprint)
                    context = {**context, 'rejected_source': raw, 'validation_error': str(exc)}
                    continue
                sources = trial
                manifest['verification'] = checked
                break
            write_source(workspace/'source.py', source, True)
            manifest['progress'][component['name']] = 'implemented'
            save()
        source = assemble(plan, sources, final=True)
        verify_constraints(source, prompt)
        final_seen = set()
        for attempt in range(3):
            try:
                manifest['verification'] = verify_source(source, plan)
                break
            except CoderError as exc:
                if source in final_seen or attempt == 2:
                    raise CoderError(f'Final integration correction exhausted: {exc}') from exc
                final_seen.add(source)
                # Integration defects usually concern wiring. Keep all component
                # contracts fixed and repair the caller named in the diagnostic,
                # falling back to the entrypoint for disconnected behavior.
                candidates = [] if str(exc).startswith('Disconnected') else [
                    c for c in plan['components'] if c['name'] in str(exc)]
                component = next((c for c in candidates if c['name'] == plan['entrypoint']),
                                 candidates[0] if candidates else next(c for c in plan['components'] if c['name'] == plan['entrypoint']))
                raw = call('Correct only this Python component using validation_error. Return one function. '
                           'Preserve the exact signature and declared imports, uses and dependency contracts. '
                           'Wire required behavior into the existing program; no extra scope or execution.',
                           {'component': component, 'rejected_source': sources[component['name']],
                            'validation_error': str(exc), 'original_request': prompt,
                            'dependency_signatures': {c['name']: c['signature'] for c in plan['components']
                                                      if c['name'] in component['depends_on']}}, phase='retry')
                sources[component['name']] = component_source(raw, component)
                source = assemble(plan, sources, final=True)
                if len(source.encode()) > 128 * 1024:
                    raise CoderError('Single-file source exceeds 128 KiB safeguard')
        write_source(workspace/'source.py', source, True)
        manifest['status'] = 'reviewing'
        save()
        # Review each requirement using only its integrated reachable component
        # slice. All requirement texts remain visible so omissions can be flagged.
        graph = manifest['verification']['references']
        reviewed_slices = {}
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
            slice_key = tuple(sorted(relevant))
            if slice_key in reviewed_slices:
                manifest['reviews'].append({'requirement': req['id'], 'reused': True, **reviewed_slices[slice_key]})
                continue
            review = review_source(
                lambda system, context, schema: call(system, context, schema, phase='verification'),
                {'original_request': prompt, 'all_requirements': plan['requirements'], 'target': req,
                 'covered_requirements': [r for r in plan['requirements'] if set(r['components']) <= relevant],
                 'review_scope': 'Check covered_requirements against this slice. Flag original requirements absent from all_requirements; other slices are reviewed separately',
                 'imports': plan['imports'], 'variables': plan['variables'], 'classes': plan['classes'],
                 'references': graph, 'source': {name: sources[name] for name in sorted(relevant)}},
                '\n'.join(sources[name] for name in sorted(relevant)))
            reviewed_slices[slice_key] = review
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
        import sys
        print(f'Project workspace retained: {workspace}\nPlanning: {manifest["timings"]["planning"]:.2f}s; '
              f'plan retries: {manifest["timings"]["retry"]:.2f}s; generation: {manifest["timings"]["generation"]:.2f}s; '
              f'verification: {manifest["timings"]["verification"]:.2f}s', file=sys.stderr)
        raise


def single_manifest(runtime, prompt, assessment, source, call=None):
    """Keep simple requests one-shot; one bounded review checks their semantics."""
    manifest = {'version': 1, 'original_request': prompt, 'assessment': assessment,
                'status': 'verified', 'plan': {'architecture': 'single-pass source',
                    'requirements': [{'id': 'R1', 'text': prompt}], 'functions': assessment['functions']},
                'progress': {'source': 'implemented'}, 'reviews': []}
    if assessment['language'] == 'python':
        manifest['verification'] = verify_source(source)
        if call is None:
            call = lambda system, context, schema: runtime.complete(
                system, json.dumps(context, separators=(',', ':')), schema=schema)[0]
        review = review_source(call, {'original_request': prompt, 'assessment': assessment, 'source': source}, source)
        manifest['reviews'].append(review)
        if review['verdict'] != 'pass' or review['issues'] or review['missing_requirements']:
            raise RequirementReviewError('Source requirement review failed: ' + review['evidence'])
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


def plan_fingerprint(raw):
    try:
        return json.dumps(normalize_plan(json.loads(raw, object_pairs_hook=no_duplicates)), sort_keys=True)
    except (ValueError, CoderError):
        return raw.strip()


def generate_single(runtime, prompt, assessment, revised, root, config):
    """Two repairs at most; transport failures and invalid reviews never repair code."""
    directory = root / '.gi-coder/projects'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = Path(tempfile.mkdtemp(prefix='project-', dir=directory))
    record = {'version': 1, 'workspace': str(workspace), 'original_request': prompt,
              'assessment': assessment, 'status': 'generating', 'calls': [], 'attempts': []}
    deadline = time.monotonic() + config.get('project_timeout', 1800)
    seen = set()
    def save():
        write_source(workspace/'manifest.json', json.dumps(record, indent=2) + '\n', True)
    def invoke(phase, operation):
        if time.monotonic() >= deadline:
            raise CoderError('Project time budget exhausted')
        row = {'number': len(record['calls']) + 1, 'phase': phase, 'status': 'failed', 'usage': {}}
        started = time.monotonic()
        try:
            raw, stats = operation()
            row.update(status='completed', usage=stats)
            if time.monotonic() > deadline:
                raise CoderError('Project time budget exhausted')
            return raw
        finally:
            row['seconds'] = time.monotonic() - started
            record['calls'].append(row)
            save()
    def review_call(system, context, schema):
        return invoke('verification', lambda: runtime.complete(system, json.dumps(context, separators=(',', ':')),
                      schema=schema, timeout=max(0.001, deadline-time.monotonic())))
    save()
    try:
        correction = None
        for attempt in range(3):
            if correction is None:
                raw = invoke('generation', lambda: runtime.generate(
                    revised, assessment['language'], timeout=max(0.001, deadline-time.monotonic())))
            else:
                raw = invoke('retry', lambda: runtime.complete(
                    'Correct this source using the validation_error and original requirements. Return one complete '
                    'fenced source file. No execution, tools, extra files or safety bypass.',
                    json.dumps(correction, separators=(',', ':')), timeout=max(0.001, deadline-time.monotonic())))
            write_source(workspace/f'source-attempt-{attempt+1}.raw.txt', raw, True)
            row = {'attempt': attempt+1, 'status': 'rejected'}
            record['attempts'].append(row)
            repairable = True
            try:
                if len(raw.encode()) > 128 * 1024:
                    raise CoderError('Single-file source exceeds 128 KiB safeguard')
                source = extract_source(raw, assessment['language'])
                if assessment['language'] == 'python':
                    verify_constraints(source, prompt)
                    verify_source(source)
                repairable = False
                manifest = single_manifest(runtime, prompt, assessment, source, call=review_call)
            except ReviewEvidenceError:
                raise  # Reviewer errors cannot justify changing generated code.
            except CoderError as exc:
                if not repairable and not isinstance(exc, RequirementReviewError):
                    raise
                row['validation_error'] = str(exc)
                save()
                # Non-Python framing keeps its original fail-closed behavior.
                if assessment['language'] != 'python' or raw.strip() in seen or attempt == 2:
                    raise CoderError(f'Source correction exhausted: {exc}') from exc
                seen.add(raw.strip())
                correction = {'original_request': prompt, 'rejected_source': raw, 'validation_error': str(exc)}
                continue
            row['status'] = 'accepted'
            record.update(manifest)
            record['metrics'] = call_metrics(record['calls'], success=True)
            save()
            stats = dict(record['calls'][0]['usage'])
            stats.update(record['metrics'])
            return source, record, workspace, stats
    except BaseException as exc:
        record.update(status='failed', error=str(exc) or type(exc).__name__)
        record['metrics'] = call_metrics(record['calls'], success=False)
        save()
        raise


def call_metrics(calls, success):
    """Measured values only; absent token counts remain explicitly unknown."""
    metrics = {'model_calls': len(calls), 'inference_seconds': sum(c['seconds'] for c in calls),
               'successful_completion': success}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        values = [c['usage'].get(key) for c in calls]
        metrics[key] = sum(values) if all(type(v) is int for v in values) else None
    return metrics


def verify_constraints(source, prompt):
    """Deterministic checks for explicit import restrictions; no generated execution."""
    import sys
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(a.name.split('.')[0].lower() for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split('.')[0].lower())
    forbidden = re.findall(r"(?:do not|don't|never)\s+(?:use|import)\s+([A-Za-z_]\w*)|without\s+([A-Za-z_]\w*)", prompt, re.I)
    for pair in forbidden:
        module = next(filter(None, pair)).lower()
        if module in roots:
            raise CoderError(f'Forbidden import from original request: {module}')
    if re.search(r'(?:without|no)\s+(?:any\s+)?(?:external|third.party)\s+(?:libraries|dependencies|packages)', prompt, re.I):
        external = roots - sys.stdlib_module_names - {'__future__'}
        if external:
            raise CoderError(f'External imports forbidden by original request: {sorted(external)}')
