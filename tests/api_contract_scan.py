"""
AST scanner backing tests/test_api_contract.py.

Regex over a fixed window of source lines is not good enough here: it catches
`return jsonify(...)` statements belonging to a *neighbouring helper* and reports
them against whichever route happened to be declared above, and it cannot match a
multi-line dict containing nested braces. Both produce false positives, and a
conformance gate that cries wolf gets switched off.

So this parses the module and walks the actual decorated function body, which
gives exact facts: for each `/api/*` route, every `return` it can execute, with
the real HTTP status attached.

Pure and importable — no Flask, no network, no repo state beyond the source.
"""

import ast
import os


def _decorator_routes(fn):
    """Rules from @bp.route("/…") / @app.route("/…") decorators on `fn`."""
    rules = []
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        target = dec.func
        if not (isinstance(target, ast.Attribute) and target.attr == "route"):
            continue
        if not (isinstance(target.value, ast.Name) and target.value.id in ("bp", "app")):
            continue
        if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
            rules.append(dec.args[0].value)
    return rules


def _jsonify_arg(node):
    """(is_jsonify, dict_node_or_None) for `node`.

    A `jsonify(payload)` whose argument is a variable rather than a literal dict
    is OPAQUE: the envelope is decided somewhere else, so the contract cannot be
    verified at the return site. That is reported, not ignored — an unverifiable
    response shape is exactly the thing this gate exists to prevent, and silently
    passing 17 routes because they happened to use a variable would have made the
    whole test theatre.
    """
    if not isinstance(node, ast.Call):
        return False, None
    fn = node.func
    name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
    if name != "jsonify" or not node.args:
        return False, None
    arg = node.args[0]
    return True, (arg if isinstance(arg, ast.Dict) else None)


def _dict_lookup(d):
    """{literal-str-key: value-node} for a Dict node."""
    out = {}
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            out[k.value] = v
    return out


def _const(node):
    return node.value if isinstance(node, ast.Constant) else None


def returns_of(fn):
    """Every `return` in `fn` (excluding nested defs) as a fact dict.

    {"line", "status", "opaque", "has_ok", "ok_value", "has_error", "keys"}
    `status` is the literal HTTP status, or 200 when the return is a bare value.
    """
    facts = []
    nested = {n for d in ast.walk(fn) if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))
              and d is not fn for n in ast.walk(d)}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return) or node in nested or node.value is None:
            continue
        value, status = node.value, 200
        if isinstance(value, ast.Tuple) and len(value.elts) >= 2:
            maybe = _const(value.elts[1])
            if isinstance(maybe, int):
                status = maybe
            value = value.elts[0]
        is_jsonify, d = _jsonify_arg(value)
        if not is_jsonify:
            continue
        if d is None:                      # jsonify(<variable>) — shape not visible here
            facts.append({
                "line": node.lineno, "status": status, "opaque": True,
                "has_ok": False, "ok_value": None, "has_error": False, "keys": [],
            })
            continue
        items = _dict_lookup(d)
        facts.append({
            "line": node.lineno,
            "status": status,
            "opaque": False,
            "has_ok": "ok" in items,
            "ok_value": _const(items["ok"]) if "ok" in items else None,
            "has_error": "error" in items,
            "keys": sorted(items),
        })
    return facts


def scan_file(path):
    """[(rule, relpath, funcname, lineno, [return-facts])] for one module."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        rules = [r for r in _decorator_routes(node) if r.startswith("/api/")]
        if not rules:
            continue
        facts = returns_of(node)
        for rule in rules:
            out.append((rule, path, node.name, node.lineno, facts))
    return out


def scan_tree(root, subdirs=("server", "services", "maps")):
    results = []
    for sub in subdirs:
        base = os.path.join(root, sub)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    results.extend(scan_file(os.path.join(dirpath, fn)))
    return results
