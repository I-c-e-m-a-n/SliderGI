"""Non-executing Python structure, symbol and integration checks."""
import ast
import builtins
import hashlib
import importlib.metadata
import symtable

from .config import CoderError


def signature(node):
    return ('async def ' if isinstance(node, ast.AsyncFunctionDef) else 'def ') + node.name + '(' + ast.unparse(node.args) + ')' + (
        ' -> ' + ast.unparse(node.returns) if node.returns else '')


def parse_signature(text):
    try:
        tree = ast.parse(text + ':\n    pass\n')
        node = tree.body[0]
        if len(tree.body) != 1 or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.decorator_list:
            raise ValueError('expected one undecorated function signature')
        # Defaults are evaluated during module assembly; permit literal defaults only.
        for value in [*node.args.defaults, *[d for d in node.args.kw_defaults if d is not None]]:
            ast.literal_eval(value)
        return node
    except (SyntaxError, ValueError, TypeError) as exc:
        raise CoderError(f'Invalid component signature: {text}') from exc


def inventory(source):
    try:
        tree = ast.parse(source)
        compile(tree, '<generated>', 'exec')  # Compile only; never execute generated code.
    except (SyntaxError, ValueError) as exc:
        raise CoderError(f'Integrated Python syntax error: {exc}') from exc
    functions, classes, variables, nodes = {}, {}, {}, {}
    imports = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            pass
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in nodes:
                raise CoderError(f'Duplicate definition: {node.name}')
            nodes[node.name] = node
            functions[node.name] = signature(node)
        elif isinstance(node, ast.ClassDef):
            if node.name in classes:
                raise CoderError(f'Duplicate class: {node.name}')
            classes[node.name] = {'bases': [ast.unparse(base) for base in node.bases], 'fields': []}
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name + '.' + child.name
                    if name in nodes:
                        raise CoderError(f'Duplicate method: {name}')
                    nodes[name] = child
                    functions[name] = signature(child)
                    for item in ast.walk(child):
                        if isinstance(item, ast.Attribute) and isinstance(item.ctx, ast.Store) and isinstance(item.value, ast.Name) and item.value.id == 'self':
                            classes[node.name]['fields'].append(item.attr)
            classes[node.name]['fields'] = sorted(set(classes[node.name]['fields']))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    variables[target.id] = ast.unparse(node.value) if node.value else None
    return {'functions': functions, 'classes': classes, 'variables': variables, 'imports': imports}, nodes, tree


def references(nodes, classes):
    """Resolve direct calls/references, self methods and locals constructed as classes."""
    graph = {}
    for name, node in nodes.items():
        owner = name.split('.')[0] if '.' in name else None
        types = {'self': owner} if owner else {}
        body = ast.Module(body=node.body, type_ignores=[])
        local = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
        local.update(n.id for n in ast.walk(body) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        for item in ast.walk(body):
            if isinstance(item, ast.Assign) and isinstance(item.value, ast.Call) and isinstance(item.value.func, ast.Name) and item.value.func.id in classes:
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        types[target.id] = item.value.func.id
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if isinstance(arg.annotation, ast.Name) and arg.annotation.id in classes:
                types[arg.arg] = arg.annotation.id
        refs = set()
        for item in ast.walk(body):
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
                if item.id in local:
                    continue
                if item.id in nodes:
                    refs.add(item.id)
                elif item.id in classes and item.id + '.__init__' in nodes:
                    refs.add(item.id + '.__init__')
            elif isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id in types:
                target = types[item.value.id] + '.' + item.attr
                if target in nodes:
                    refs.add(target)
                elif item.value.id == 'self' and item.attr not in classes[owner]['fields']:
                    raise CoderError(f'Unknown class member {target}')
        graph[name] = sorted(refs - {name})
    return graph


def verify_source(source, plan=None, completed=None, final=True):
    index, nodes, tree = inventory(source)
    if plan is None:
        return {'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'inventory': index,
                'coverage': 'not_reviewed', 'level': 'syntax_and_structure'}
    specs = {component['name']: component for component in plan['components']}
    completed = set(specs if completed is None else completed)
    if set(nodes) != completed:
        raise CoderError('Integrated source definitions differ from completed manifest components')
    for name, node in nodes.items():
        expected = parse_signature(specs[name]['signature'])
        if (type(node) is not type(expected) or ast.dump(node.args) != ast.dump(expected.args)
                or ast.dump(node.returns or ast.Constant(None)) != ast.dump(expected.returns or ast.Constant(None))):
            raise CoderError(f'Signature mismatch: {name}')
        body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
        if not body or all(isinstance(n, ast.Pass) or isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and n.value.value is Ellipsis for n in body):
            raise CoderError(f'Unimplemented component: {name}')
        if any(isinstance(n, ast.Name) and n.id == 'NotImplementedError' for n in ast.walk(node)):
            raise CoderError(f'Placeholder implementation: {name}')
    expected_imports = {'from __future__ import annotations', *plan['imports']}
    if set(index['imports']) != expected_imports:
        raise CoderError('Imports differ from the implementation plan')
    if index['variables'] != {v['name']: ast.unparse(ast.parse(v['value'], mode='eval').body) for v in plan['variables']}:
        raise CoderError('Shared variables differ from the implementation plan')
    if set(index['classes']) != {c['name'] for c in plan['classes']}:
        raise CoderError('Classes differ from the implementation plan')
    graph = references(nodes, index['classes'])
    module_symbols = set(index['variables']) | set(index['classes'])
    for line in index['imports']:
        node = ast.parse(line).body[0]
        module_symbols.update(a.asname or (a.name.split('.')[0] if isinstance(node, ast.Import) else a.name) for a in node.names)
    for name, refs in graph.items():
        undeclared = set(refs) - set(specs[name]['depends_on'])
        if undeclared:
            raise CoderError(f'Undeclared component dependencies in {name}: {sorted(undeclared)}')
        loads = {n.id for n in ast.walk(ast.Module(body=nodes[name].body, type_ignores=[]))
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        locals_ = {a.arg for a in [*nodes[name].args.args, *nodes[name].args.posonlyargs, *nodes[name].args.kwonlyargs]}
        locals_.update(n.id for n in ast.walk(nodes[name]) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        globals_ = {value for n in ast.walk(nodes[name]) if isinstance(n, ast.Global) for value in n.names}
        loads = (loads - (locals_ - globals_)) | globals_
        if not (loads & module_symbols) <= set(specs[name]['uses']):
            raise CoderError(f'Undeclared shared-state/import use in {name}')
    if final:
        declared = set(index['variables']) | set(index['classes']) | {name for name in nodes if '.' not in name}
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                declared.update(alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name) for alias in node.names)
        declared.update(dir(builtins))
        declared.update(('__name__', '__file__'))
        def check(table):
            for symbol in table.get_symbols():
                if symbol.is_referenced() and symbol.is_global() and symbol.get_name() not in declared:
                    raise CoderError(f'Undefined global/import: {symbol.get_name()}')
            for child in table.get_children():
                check(child)
        check(symtable.symtable(source, '<generated>', 'exec'))
        reachable, pending = set(), [plan['entrypoint']]
        while pending:
            name = pending.pop()
            if name not in reachable:
                reachable.add(name)
                pending.extend(graph.get(name, []))
        if set(nodes) - reachable:
            raise CoderError(f'Disconnected functionality: {sorted(set(nodes) - reachable)}')
        used_names = {n.id for name in reachable for n in ast.walk(ast.Module(body=nodes[name].body, type_ignores=[]))
                      if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        if set(index['variables']) - used_names:
            raise CoderError('Disconnected shared variables: ' + ', '.join(sorted(set(index['variables']) - used_names)))
        used_classes = used_names & set(index['classes'])
        for _ in index['classes']:
            used_classes.update(base for name in list(used_classes) for base in index['classes'][name]['bases'] if base in index['classes'])
        if set(index['classes']) - used_classes:
            raise CoderError('Disconnected classes: ' + ', '.join(sorted(set(index['classes']) - used_classes)))
        for cls in plan['classes']:
            expected_bases = [ast.unparse(ast.parse(b, mode='eval').body) for b in cls['bases']]
            if index['classes'][cls['name']]['bases'] != expected_bases:
                raise CoderError(f'Class bases differ from manifest: {cls["name"]}')
            if set(index['classes'][cls['name']]['fields']) != set(cls['fields']):
                raise CoderError(f'Class fields differ from manifest: {cls["name"]}')
        for requirement in plan['requirements']:
            if not set(requirement['components']) <= reachable:
                raise CoderError(f'Requirement has no integrated implementation: {requirement["id"]}')
    return {'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'inventory': index,
            'references': graph, 'level': 'static_integration', 'coverage': 'pending_semantic_review'}


def dependency_status(dependencies):
    result = []
    for dependency in dependencies:
        try:
            version = importlib.metadata.version(dependency['package'])
            result.append({**dependency, 'installed_version': version})
        except importlib.metadata.PackageNotFoundError:
            result.append({**dependency, 'installed_version': None})
    return result
