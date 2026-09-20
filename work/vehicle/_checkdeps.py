#!/usr/bin/env python3
"""Verify the vehicle tools need nothing but the standard library.

No venv should be required at the car: a virtualenv is one more thing to
forget to activate while sitting in the driver's seat, and these tools
deliberately use only stdlib + SocketCAN (which lives in the kernel, not in
a Python package).

This script confirms that claim mechanically rather than by inspection, and
it must be run with the SAME interpreter that will run the tools.

Usage:  python3 _checkdeps.py *.py
"""
import ast
import importlib.util
import pathlib
import sys

# Modules that genuinely are third-party and must NOT appear.
FORBIDDEN = {"can", "isotp", "canmatrix", "cantools", "numpy", "scipy",
             "pandas", "serial"}


def imports_of(path):
    names = set()
    for node in ast.walk(ast.parse(pathlib.Path(path).read_text(), path)):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def main():
    files = sys.argv[1:] or [str(p) for p in pathlib.Path(".").glob("*.py")]
    stdlib = getattr(sys, "stdlib_module_names", None)
    all_names = set()
    for f in files:
        all_names |= imports_of(f)

    print(f"interpreter: {sys.executable}")
    print(f"version    : {sys.version.split()[0]}")
    print(f"in a venv  : {sys.prefix != sys.base_prefix}")
    print()

    ok = True
    for name in sorted(all_names):
        if name in FORBIDDEN:
            print(f"  FAIL  {name:<14} third-party -- would need a venv")
            ok = False
            continue
        is_std = (name in stdlib) if stdlib else False
        found = importlib.util.find_spec(name) is not None
        if is_std and found:
            print(f"  ok    {name:<14} stdlib")
        elif found:
            print(f"  WARN  {name:<14} importable but NOT stdlib")
            ok = False
        else:
            print(f"  FAIL  {name:<14} not importable")
            ok = False

    # SocketCAN is a kernel feature, not a package -- check it directly.
    import socket
    for attr in ("AF_CAN", "SOCK_RAW", "CAN_RAW"):
        has = hasattr(socket, attr)
        print(f"  {'ok  ' if has else 'FAIL'}  socket.{attr:<8} "
              f"{'present' if has else 'MISSING'}")
        ok = ok and has

    print()
    print("DEPENDENCY CHECK:", "PASS -- no venv needed" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
