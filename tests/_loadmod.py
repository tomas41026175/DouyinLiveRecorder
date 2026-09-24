"""Load a src/ module standalone by file path, bypassing src/__init__.py.

Why: src/__init__.py pulls in the full recorder stack (network clients,
config, etc.) for main.py's use -- fine for the running app, but it means a
plain `from src import X` would drag in unrelated heavy dependencies just to
test a handful of pure functions in X. Most of the web-side pure-logic
modules (library.py, danmaku_subtitle.py, danmaku_store.py, danmaku_capture.py,
health.py, marks_store.py) have no relative imports of their own (verified:
no `from . import ...`), so loading them directly by path is safe and keeps
this test suite fast and dependency-free.

A few modules (e.g. discord_bot.py) DO have a relative import of their own
(`from . import common`) to reuse the shared JSON-settings helpers instead of
duplicating them -- `common.py` itself has zero relative imports, so this is
still safe to resolve, it just needs the `src` package name to exist in
sys.modules for `.` to mean something. `_ensure_stub_src_package()` registers
a lightweight stand-in for the `src` package (just its `__path__`, no actual
code executed) the first time it's needed, so `from . import common` resolves
to the real src/common.py loaded fresh as `src.common` -- WITHOUT ever
executing the real, heavy `src/__init__.py`.
"""
import importlib.util
import os
import sys
import types

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")


def _ensure_stub_src_package():
    if "src" in sys.modules:
        return
    stub = types.ModuleType("src")
    stub.__path__ = [_SRC_DIR]
    sys.modules["src"] = stub


def load(module_name: str):
    _ensure_stub_src_package()
    path = os.path.join(_SRC_DIR, module_name + ".py")
    spec = importlib.util.spec_from_file_location("src." + module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # lets the module's own relative imports (and re-loads) find it
    spec.loader.exec_module(mod)
    return mod
