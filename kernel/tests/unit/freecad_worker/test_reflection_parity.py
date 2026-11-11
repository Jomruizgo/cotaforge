"""The FreeCAD worker re-implements the reflection math (it imports nothing from ``cotaforge``); this keeps the copy
equal to ``verification.formulas``, the one the expectations are computed with."""

import importlib.util
import random
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from cotaforge.verification import formulas as f

WORKER_GEOMETRY = (
    Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker" / "geometry.py"
)

Vec3 = tuple[float, float, float]


def _load_worker_geometry() -> ModuleType:
    """Import the worker's ``geometry.py`` with ``FreeCAD`` and ``Part`` stubbed: only its pure math is exercised."""
    saved = {name: sys.modules.get(name) for name in ("FreeCAD", "Part")}
    sys.modules["FreeCAD"] = MagicMock()
    sys.modules["Part"] = MagicMock()
    try:
        spec = importlib.util.spec_from_file_location("freecad_worker_geometry_under_test", WORKER_GEOMETRY)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


@pytest.fixture(scope="module")
def worker() -> ModuleType:
    return _load_worker_geometry()


def _cases() -> list[tuple[Vec3, Vec3, Vec3]]:
    rng = random.Random(7)

    def vec(scale: float) -> Vec3:
        return (rng.uniform(-scale, scale), rng.uniform(-scale, scale), rng.uniform(-scale, scale))

    fixed: list[tuple[Vec3, Vec3, Vec3]] = [
        ((30.0, 5.0, 2.0), (20.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
        ((1.0, 2.0, 3.0), (0.0, 0.0, 8.0), (0.0, 0.0, -2.0)),
    ]
    return fixed + [(vec(100.0), vec(100.0), vec(3.0)) for _ in range(50)]


def test_the_worker_reflects_points_exactly_like_the_verification_formulas(worker: ModuleType) -> None:
    for point, origin, normal in _cases():
        expected = f.reflect_point(point, origin, normal)
        assert worker.reflect_point(point, origin, normal) == pytest.approx(expected, abs=1e-12)


def test_the_worker_reflects_directions_exactly_like_the_verification_formulas(worker: ModuleType) -> None:
    for direction, _, normal in _cases():
        expected = f.reflect_direction(direction, normal)
        assert worker.reflect_direction(direction, normal) == pytest.approx(expected, abs=1e-12)
