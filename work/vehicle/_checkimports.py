#!/usr/bin/env python3
"""Static check: every global name a module uses must be resolvable.

Catches the class of bug where a function that --selftest never exercises
(e.g. the live-CAN transmit path) references a module that was never
imported.  The self-tests deliberately avoid touching hardware, so such a
NameError only surfaces at the vehicle -- exactly where it is most costly.
"""
import ast
import builtins
import sys


def check(path):
    tree = ast.parse(open(path).read(), path)

    defined = set(dir(builtins))
    defined.update(["__file__", "__name__", "__doc__", "__spec__",
                    "__loader__", "__package__"])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                defined.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                defined.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            pass
        elif isinstance(node, ast.Global):
            defined.update(node.names)

    missing = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in defined:
                missing.setdefault(node.id, node.lineno)
    return missing


def main():
    files = sys.argv[1:]
    bad = False
    for f in files:
        m = check(f)
        if m:
            bad = True
            for name, line in sorted(m.items(), key=lambda kv: kv[1]):
                print(f"  FAIL  {f}:{line}  undefined name '{name}'")
        else:
            print(f"  PASS  {f}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
