"""The classification of holes the harness decides in ONE place (``cotaforge/conventions/classify.py``, standard library
only), loaded here by path: the worker is flat modules on their own ``sys.path`` and imports nothing from ``cotaforge``.

If the file is not where it is expected (a worker copied out of the package) the loader says so with an error the first
time a class is asked for; nothing at import time can fail."""

import importlib.util
from pathlib import Path
from types import ModuleType

_module: ModuleType | None = None


def _classify_module() -> ModuleType:
    global _module  # noqa: PLW0603
    if _module is None:
        path = Path(__file__).resolve().parents[3] / "conventions" / "classify.py"
        spec = importlib.util.spec_from_file_location("cotaforge_hole_classify", path)
        if spec is None or spec.loader is None:
            msg = f"the hole classification is not at {path}"
            raise ImportError(msg)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _module = module
    return _module


def classify(*, threaded: bool, fit: str | None, max_precision_grade: int | None = None) -> str:
    module = _classify_module()
    grade = module.DEFAULT_MAX_PRECISION_GRADE if max_precision_grade is None else max_precision_grade
    return str(module.classify(threaded=threaded, fit=fit, max_precision_grade=grade))


def default_max_precision_grade() -> int:
    return int(_classify_module().DEFAULT_MAX_PRECISION_GRADE)


def fit_grade(fit: str | None) -> int | None:
    return _classify_module().fit_grade(fit)
