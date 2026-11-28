"""Undefined-name detection with the stdlib's symtable.

Python resolves a name that a function never assigns as an IMPLICIT
GLOBAL, so `reconcile_pending(ex, t)` inside `main` compiles cleanly and
raises only when the line runs. Three NameErrors in one piece of work
hid exactly there, two of them behind a green test suite. The check is
therefore: for every referenced global, does the name exist at module
level or in builtins?
"""
import builtins, symtable, sys


def undefined(path):
    src = open(path, encoding="utf-8").read()
    top = symtable.symtable(src, path, "exec")
    module_names = {s.get_name() for s in top.get_symbols()}
    bi = set(dir(builtins))
    out = []

    def walk(tab, stack, enclosing):
        names = {s.get_name() for s in tab.get_symbols()
                 if s.is_local() or s.is_parameter() or s.is_imported()}
        for s in tab.get_symbols():
            n = s.get_name()
            if not s.is_referenced():
                continue
            if n in names or n in enclosing or n in module_names or n in bi:
                continue
            if s.is_free():
                continue                 # closed over an enclosing scope
            out.append(("->".join(stack + [tab.get_name()]), n,
                        tab.get_lineno()))
        for ch in tab.get_children():
            walk(ch, stack + [tab.get_name()], enclosing | names)
    walk(top, [], set())
    return out
