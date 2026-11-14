"""``worker/draft_math.py`` in isolation: the shadow test and the classification (no FreeCAD, no numpy)."""

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

import pytest

from cotaforge.contract import DEFAULT_MIN_DRAFT_DEG, DRAFT_ZERO_DEG, classify_draft

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("worker_draft_math", WORKER_DIR / "draft_math.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dm = _load()


def _box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> tuple[list[Any], list[Any]]:
    """The 12 triangles of an axis-aligned box."""
    v = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    return v, tris


def _merged(*boxes: tuple[list[Any], list[Any]]) -> tuple[list[Any], list[Any]]:
    verts: list[Any] = []
    tris: list[Any] = []
    for v, t in boxes:
        base = len(verts)
        verts.extend(v)
        tris.extend((a + base, b + base, c + base) for a, b, c in t)
    return verts, tris


def test_the_mirror_constants_and_classification_equal_the_contract() -> None:
    assert dm.DRAFT_ZERO_DEG == DRAFT_ZERO_DEG
    for draft in (0.0, 0.005, 0.01, 0.011, 0.5, 0.9995, 0.998, 1.0, 1.5, 45.0):
        for shadowed in (False, True):
            assert dm.classify(draft, shadowed, DEFAULT_MIN_DRAFT_DEG) == classify_draft(
                draft, shadowed, DEFAULT_MIN_DRAFT_DEG
            )


def test_the_lean_of_a_face_from_the_pull() -> None:
    assert dm.lean_deg((1, 0, 0), (0, 0, 1)) == pytest.approx(0.0)
    assert dm.lean_deg((0, 0, -1), (0, 0, 1)) == pytest.approx(90.0)
    tilt = (math.sin(math.radians(3)), 0.0, math.cos(math.radians(3)))
    tilted = (math.cos(math.radians(3)), 0.0, math.sin(math.radians(3)))
    assert dm.lean_deg(tilted, (0, 0, 1)) == pytest.approx(3.0)
    assert dm.lean_deg(tilt, (0, 0, 1)) == pytest.approx(87.0)


def test_a_face_looking_along_the_pull_is_released_that_way_and_the_opposite_one_the_other_way() -> None:
    assert dm.release_sense((0, 0, 1), (0, 0, 1)) == 1
    assert dm.release_sense((0, 0, -1), (0, 0, 1)) == -1
    assert dm.release_sense((1, 0, 0), (0, 0, 1)) == 1  # a wall counts for the first half


def test_a_ray_is_blocked_by_a_body_above_it_and_free_beside_it() -> None:
    caster = dm.ShadowCaster(*_merged(_box(0, 0, 10, 10, 10, 12)), (0, 0, 1))
    assert caster.blocked((5, 5, 0), 1) is True
    assert caster.blocked((5, 5, 0), -1) is False  # the other way it leaves
    assert caster.blocked((20, 5, 0), 1) is False
    assert caster.blocked((5, 5, 13), 1) is False  # the body is behind the start


def test_an_overhang_shadows_the_face_under_it_but_not_the_face_beside_it() -> None:
    # a post 0..10 in x with a ledge sticking out to x=20 at z 10..12: the post's side wall under the ledge is undercut
    post = _box(0, 0, 0, 10, 10, 10)
    ledge = _box(0, 0, 10, 20, 10, 12)
    caster = dm.ShadowCaster(*_merged(post, ledge), (0, 0, 1))
    under_ledge = ((10.0, 5.0, 5.0), (1.0, 0.0, 0.0))
    away = ((0.0, 5.0, 5.0), (-1.0, 0.0, 0.0))
    assert dm.shadowed_share(caster, [under_ledge], 0.05) == 1.0
    assert dm.shadowed_share(caster, [away], 0.05) == 0.0


def test_the_share_is_the_fraction_of_blocked_points() -> None:
    ledge = _box(0, 0, 10, 20, 10, 12)
    caster = dm.ShadowCaster(*ledge, (0, 0, 1))
    samples = [((5.0, 5.0, 0.0), (0.0, 0.0, 1.0)), ((30.0, 5.0, 0.0), (0.0, 0.0, 1.0))]
    assert dm.shadowed_share(caster, samples, 0.05) == pytest.approx(0.5)


def test_an_oblique_pull_projects_the_triangles_along_it() -> None:
    pull = dm.unit((1.0, 0.0, 1.0))
    caster = dm.ShadowCaster(*_box(10, 0, 8, 12, 10, 12), pull)
    assert caster.blocked((0.0, 5.0, 0.0), 1) is True  # the diagonal ray from the origin runs into the block
    assert caster.blocked((0.0, 5.0, 8.0), 1) is False  # it passes above


def test_a_model_with_no_triangles_blocks_nothing() -> None:
    caster = dm.ShadowCaster([], [], (0, 0, 1))
    assert caster.blocked((0, 0, 0), 1) is False
    assert dm.shadowed_share(caster, [], 0.05) == 0.0
