"""Static risk scan of a ``run_native`` fragment (pure: ``ast`` and the standard library only).

THIS IS AN ADVISORY HEURISTIC, NOT A SANDBOX. It reads the source text and flags the patterns a person reviewing the
fragment would want to look at before it runs: modules that reach the operating system or the network, dynamic code,
and writes (``open``, ``pathlib``, ``saveAs``, exports) whose target is not provably inside the workspace. Anything
Python can do dynamically (``getattr(__builtins__, ...)``, a string built at run time, code in a file the fragment
loads, a module that re-exports ``os``) escapes it, and a fragment that scores clean is NOT thereby safe. The caller
(the ``PreToolUse`` hook) only turns findings into a permission *question*; it never blocks and never replaces the
permission rules or the operating-system protections around the CAD process.

Findings carry the 1-based line in the fragment and a short reason. A fragment that does not parse yields no findings:
the worker reports the syntax error itself.
"""

import ast
import posixpath
import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

# Modules whose mere import gives the fragment the operating system, the network or native code.
RISKY_MODULES: dict[str, str] = {
    "os": "reaches the file system and processes",
    "subprocess": "runs other programs",
    "shutil": "copies, moves and deletes files",
    "socket": "opens network connections",
    "urllib": "reaches the network",
    "http": "reaches the network",
    "requests": "reaches the network",
    "ctypes": "calls arbitrary native code",
}
# Attribute calls on ``os`` that run programs or remove/rename files (``os.path.*`` is not among them).
_OS_DANGEROUS_PREFIXES = ("system", "popen", "exec", "spawn", "startfile", "remove", "unlink", "rmdir", "removedirs")
_OS_DANGEROUS_EXACT = frozenset({"rename", "renames", "replace", "kill", "killpg", "fork", "chmod", "chown"})
_DYNAMIC_CODE_CALLS = frozenset({"eval", "exec", "compile"})
_PATH_CONSTRUCTORS = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath", "PurePosixPath", "PureWindowsPath"})
_PATH_WRITE_METHODS = frozenset(
    {"write_text", "write_bytes", "touch", "mkdir", "unlink", "rmdir", "rename", "replace", "symlink_to", "hardlink_to"}
)
SAVE_METHODS = frozenset({"saveAs", "saveCopy", "saveDocument"})
"""The calls that save a document in FreeCAD; an adapter adds its CAD's own (``Adapter.save_methods``)."""
_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")


@dataclass(frozen=True, slots=True)
class Risk:
    """One finding: the 1-based ``line`` in the fragment, a ``kind`` for grouping and a short ``reason``."""

    line: int
    kind: str
    reason: str

    def format(self) -> str:
        return f"line {self.line}: {self.reason}"


def scan_risks(code: str, workspace: Path | None, save_methods: Collection[str] = ()) -> tuple[Risk, ...]:
    """Findings for ``code``, ordered by line. ``workspace`` is the directory writes are allowed in (``None``: no
    write is provably inside it, so every write is flagged). ``save_methods`` are the calls that save a document in
    the CAD, beyond :data:`SAVE_METHODS`."""
    try:
        tree = ast.parse(code, filename="<run_native>")
    except (SyntaxError, ValueError, RecursionError):
        return ()
    scanner = _Scanner(str(workspace) if workspace is not None else None, SAVE_METHODS | frozenset(save_methods))
    scanner.visit(tree)
    seen: set[tuple[int, str]] = set()
    unique: list[Risk] = []
    for risk in sorted(scanner.risks, key=lambda r: (r.line, r.kind, r.reason)):
        key = (risk.line, risk.reason)
        if key not in seen:
            seen.add(key)
            unique.append(risk)
    return tuple(unique)


# --- paths -------------------------------------------------------------------------------------------------------


def _normalise(path: str) -> str:
    """Slash-separated, ``..``-collapsed, drive letter lowercased (Windows paths are case-insensitive)."""
    text = path.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", text):
        return (text[:2] + posixpath.normpath(text[2:] or ".")).lower()
    return posixpath.normpath(text if text else ".")


def _is_absolute(path: str) -> bool:
    return bool(_ABSOLUTE_RE.match(path))


def _inside(workspace: str | None, target: str) -> bool:
    if workspace is None or not _is_absolute(target) or not _is_absolute(workspace):
        return False
    root, leaf = _normalise(workspace), _normalise(target)
    return leaf == root or leaf.startswith(root.rstrip("/") + "/")


class _Scanner(ast.NodeVisitor):
    def __init__(self, workspace: str | None, save_methods: frozenset[str] = SAVE_METHODS) -> None:
        self.workspace = workspace
        self.save_methods = save_methods
        self.risks: list[Risk] = []
        self.aliases: dict[str, str] = {}  # local name -> dotted origin ("o" -> "os", "system" -> "os.system")
        self.env: dict[str, str] = {}  # local name -> statically known string value
        self.pathvars: set[str] = set()  # local names known to hold a path object, resolvable or not

    # --- bookkeeping -------------------------------------------------------------------------------------------

    def _add(self, node: ast.AST, kind: str, reason: str) -> None:
        self.risks.append(Risk(getattr(node, "lineno", 1), kind, reason))

    def _origin(self, node: ast.AST) -> str | None:
        """Dotted origin of a Name/Attribute chain through the import aliases (``o.system`` -> ``os.system``)."""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        root = self.aliases.get(node.id, node.id)
        return ".".join([root, *reversed(parts)])

    # --- statements --------------------------------------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            self.aliases[alias.asname or top] = alias.name if alias.asname else top
            if top in RISKY_MODULES:
                self._add(node, "import", f"imports '{alias.name}' ({RISKY_MODULES[top]})")
            elif top == "importlib":
                self._add(node, "import", "imports 'importlib' (loads modules dynamically)")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        top = module.split(".")[0]
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = f"{module}.{alias.name}" if module else alias.name
        if top in RISKY_MODULES:
            self._add(node, "import", f"imports from '{module}' ({RISKY_MODULES[top]})")
        elif top == "importlib":
            self._add(node, "import", "imports from 'importlib' (loads modules dynamically)")
        elif module == "" and any(a.name in RISKY_MODULES for a in node.names):
            self._add(node, "import", "relative import of a risky module name")

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)  # inner calls first, then record what the name now holds
        value = self._string(node.value)
        is_path = self._is_path_expr(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if value is None:
                    self.env.pop(target.id, None)
                else:
                    self.env[target.id] = value
                if is_path:
                    self.pathvars.add(target.id)
                else:
                    self.pathvars.discard(target.id)

    # --- calls -------------------------------------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        self._check_call(node)
        self.generic_visit(node)

    def _check_call(self, node: ast.Call) -> None:
        func = node.func
        origin = self._origin(func)
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name == "__import__":
            self._add(node, "dynamic-import", "__import__(...) loads a module chosen at run time")
            return
        if name in _DYNAMIC_CODE_CALLS and (origin in (name, f"builtins.{name}")):
            first = node.args[0] if node.args else None
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                self._add(node, "dynamic-code", f"{name}(...) of a string that is not a literal")
            return
        if origin is not None and origin.split(".")[0] in RISKY_MODULES:
            self._check_risky_module_call(node, origin)
        if origin == "open" or origin == "io.open" or origin == "builtins.open":
            self._check_open(node, node.args[0] if node.args else _keyword(node, "file"), node.args[1:], "open")
            return
        if isinstance(func, ast.Attribute):
            self._check_attribute_call(node, func)

    def _check_risky_module_call(self, node: ast.Call, origin: str) -> None:
        head, _, rest = origin.partition(".")
        if head == "os":
            attr = rest.split(".")[0]
            if rest.startswith("path."):
                return
            if attr.startswith(_OS_DANGEROUS_PREFIXES) or attr in _OS_DANGEROUS_EXACT:
                self._add(node, "call", f"calls {origin}(...) (runs programs or removes/renames files)")
            return
        self._add(node, "call", f"calls {origin}(...) ({RISKY_MODULES[head]})")

    def _check_attribute_call(self, node: ast.Call, func: ast.Attribute) -> None:
        method = func.attr
        if method in self.save_methods:
            index = 0
            target = node.args[index] if len(node.args) > index else _keyword(node, "filename", "path", "name")
            self._check_write_target(node, target, f"{method}(...)")
        elif method == "export":
            target = node.args[1] if len(node.args) > 1 else _keyword(node, "filename", "path", "name")
            self._check_write_target(node, target, "export(...)")
        elif method == "open" and self._looks_like_path(func.value):
            self._check_open(node, func.value, node.args, "Path.open")
        elif method in _PATH_WRITE_METHODS and self._looks_like_path(func.value):
            self._check_write_target(node, func.value, f"Path.{method}(...)")

    def _looks_like_path(self, node: ast.AST) -> bool:
        """A receiver that is statically a path (or a call/name that plausibly is one)."""
        return self._string(node) is not None or self._is_path_expr(node)

    def _is_path_expr(self, node: ast.AST) -> bool:
        """Whether ``node`` builds a ``pathlib`` path, even one whose value is not statically known."""
        if isinstance(node, ast.Name):
            return node.id in self.pathvars
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._is_path_expr(node.left)
        if isinstance(node, ast.Call):
            origin = self._origin(node.func) or ""
            if origin.split(".")[-1] in _PATH_CONSTRUCTORS or origin.endswith(("Path.home", "Path.cwd")):
                return True
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("resolve", "absolute", "joinpath", "expanduser"):
                return self._is_path_expr(func.value)
        return False

    def _check_open(self, node: ast.Call, path: ast.AST | None, rest: list[ast.expr], label: str) -> None:
        mode_node = rest[0] if rest else _keyword(node, "mode")
        mode = "r"
        resolvable = True
        if mode_node is not None:
            if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
                mode = mode_node.value
            else:
                resolvable = False
        if resolvable and not any(c in mode for c in "wax+"):
            return  # read-only: not flagged (see module docstring)
        what = f"{label}(..., {mode!r})" if resolvable else f"{label}(...) (mode not statically known)"
        self._check_write_target(node, path, what)

    def _check_write_target(self, node: ast.AST, target: ast.AST | None, what: str) -> None:
        value = self._string(target) if target is not None else None
        if value is None:
            self._add(node, "file-write", f"{what} writes to a path that cannot be determined statically")
        elif not _is_absolute(value):
            self._add(
                node,
                "file-write",
                f"{what} writes to relative path {value!r} (relative to the CAD process, not to the workspace)",
            )
        elif not _inside(self.workspace, value):
            where = "outside the workspace" if self.workspace is not None else "with no workspace configured"
            self._add(node, "file-write", f"{what} writes to {value!r} {where}")

    # --- static string evaluation --------------------------------------------------------------------------------

    def _string(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for item in node.values:
                text = (
                    item.value
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                    else self._string(item.value)
                    if isinstance(item, ast.FormattedValue)
                    else None
                )
                if text is None:
                    return None
                parts.append(text)
            return "".join(parts)
        if isinstance(node, ast.BinOp):
            left, right = self._string(node.left), self._string(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Div):
                return _join(left, right)
            return None
        if isinstance(node, ast.Call):
            return self._string_of_call(node)
        return None

    def _string_of_call(self, node: ast.Call) -> str | None:
        func = node.func
        origin = self._origin(func) or ""
        last = origin.split(".")[-1]
        if last in _PATH_CONSTRUCTORS or origin in ("os.path.join", "posixpath.join", "ntpath.join") or origin == "str":
            parts = [self._string(arg) for arg in node.args]
            if not parts or any(p is None for p in parts) or node.keywords:
                return None
            result = parts[0] or ""
            for part in parts[1:]:
                result = _join(result, part or "")
            return result
        if isinstance(func, ast.Attribute) and func.attr in ("resolve", "absolute", "joinpath"):
            base = self._string(func.value)
            if base is None:
                return None
            if func.attr == "joinpath":
                extra = [self._string(arg) for arg in node.args]
                if any(e is None for e in extra):
                    return None
                for part in extra:
                    base = _join(base, part or "")
            return base
        return None


def _keyword(node: ast.Call, *names: str) -> ast.expr | None:
    for kw in node.keywords:
        if kw.arg in names:
            return kw.value
    return None


def _join(left: str, right: str) -> str:
    if _is_absolute(right) or not left:
        return right
    return left.rstrip("/\\") + "/" + right
