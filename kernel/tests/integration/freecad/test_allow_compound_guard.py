"""``PartDesign::Body.AllowCompound`` defaults to ``False`` on
1.0.2 and to ``True`` on 1.1.3. With it ``True``, a boss whose profile does not touch the body keeps
*two* solids instead of the single solid PartDesign silently drops it to on 1.0.2 -- the naive
"volume grew -> success" check used to report success instead of ``geometry_failed`` for that case.

This is the adapter-level half of the guard (``ops_common._check_volume_change`` also checking the
solid count): it runs the disjoint-boss scenario through the full ``FreeCADAdapter`` contract on whichever
FreeCAD root the test session is pointed at, so the fix is proven end-to-end and not just against the
worker's own dispatch table (``tests/freecad_worker/check_errors.py::
check_boss_disjoint_with_allow_compound_true_is_geometry_failed`` covers 1.0.2 with ``AllowCompound`` set
explicitly, since the client protocol has no RPC to flip that flag from here)."""

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    CircleEntity,
    DocumentKind,
    ErrorCode,
    ExtrudeRequest,
    NewDocumentRequest,
    PlaneName,
    RectEntity,
    SketchRequest,
)

pytestmark = pytest.mark.freecad


def test_disjoint_boss_is_geometry_failed_regardless_of_the_allow_compound_default(
    adapter: FreeCADAdapter,
) -> None:
    adapter.new_document(
        NewDocumentRequest(operation_id="disjoint_boss.new_document", kind=DocumentKind.part, name="disjoint_boss")
    )
    sketch = adapter.sketch(
        SketchRequest(
            operation_id="disjoint_boss.sketch",
            on=PlaneName.XY,
            entities=(RectEntity(name="outline", center=(50, 30), width=100, height=60),),
        )
    )
    adapter.extrude(ExtrudeRequest(operation_id="disjoint_boss.base", sketch=sketch.sketch, depth_mm=8.0))
    # far away from the plate footprint (0..100, 0..60): PartDesign cannot merge this into the body
    boss_sketch = adapter.sketch(
        SketchRequest(
            operation_id="disjoint_boss.boss_sketch",
            on=PlaneName.XY,
            entities=(CircleEntity(name="boss", center=(500, 500), diameter=8),),
        )
    )
    with pytest.raises(AdapterError) as excinfo:
        adapter.extrude(ExtrudeRequest(operation_id="disjoint_boss.boss", sketch=boss_sketch.sketch, depth_mm=4.0))
    assert excinfo.value.info.code is ErrorCode.geometry_failed
    assert "no material" in excinfo.value.info.message
