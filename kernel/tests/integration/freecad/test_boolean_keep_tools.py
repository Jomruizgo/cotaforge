"""Boolean subtract with ``keep_tools`` (fish-mouth coping) against the real FreeCAD worker.

No integration test on real FreeCAD covered ``keep_tools`` before this file (unit coverage only:
``tests/unit/fake/test_booleans.py::test_boolean_subtract_keep_tools_preserves_the_tool_body``). This
mirrors that fake test's intent -- the kept tool body must survive as a live, visible, queryable body
with its own faces intact (not consumed as ``Part::Cut``'s hidden ``Tool``) -- but deliberately uses a
bore that is fully BURIED inside the block (does not touch any of the block's own outer faces) instead
of a flush through-hole. Fake and real bookkeeping
can diverge on exact face create/modify classification for degenerate/flush cases, and the fake test's
bore happens to be flush with the block's top and bottom faces -- so its exact created/modified counts
are not assumed to carry over to real OCC topology without separately verifying them. The buried-cavity
geometry below sidesteps that ambiguity entirely while still exercising the property that matters for
``keep_tools``: the tool body's identity, visibility and geometry survive the cut independent of the
target's own face bookkeeping.

Ported from a real FreeCAD 1.1.3 run on Windows, where it passed (23/23 freecad integration tests green).
"""

import math

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    Axis,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    CylinderRequest,
    DocumentKind,
    ErrorCode,
    FaceRequest,
    MassRequest,
    NewDocumentRequest,
)

pytestmark = pytest.mark.freecad

Z = (0.0, 0.0, 1.0)
BOX_SIZE = (100.0, 60.0, 20.0)
BOX_VOLUME = BOX_SIZE[0] * BOX_SIZE[1] * BOX_SIZE[2]  # 120000
BORE_RADIUS = 15.0
BORE_HEIGHT = 10.0  # strictly less than the box height (20): the bore never reaches z=0 or z=20
BORE_VOLUME = math.pi * BORE_RADIUS**2 * BORE_HEIGHT


def _block_and_buried_bore(adapter: FreeCADAdapter, name: str) -> tuple:
    adapter.new_document(NewDocumentRequest(operation_id=f"{name}.new_document", kind=DocumentKind.part, name=name))
    box = adapter.box(BoxRequest(operation_id=f"{name}.box", size=BOX_SIZE))
    # centred in z (5..15) so the cavity touches none of the block's own 6 outer faces
    cyl = adapter.cylinder(
        CylinderRequest(
            operation_id=f"{name}.cyl",
            diameter_mm=2 * BORE_RADIUS,
            height_mm=BORE_HEIGHT,
            axis=Axis(origin=(50.0, 30.0, 5.0), direction=Z),
        )
    )
    return box, cyl


def test_boolean_subtract_keep_tools_preserves_the_tool_body_on_real_freecad(adapter: FreeCADAdapter) -> None:
    box, cyl = _block_and_buried_bore(adapter, "keep_tools")
    result = adapter.boolean(
        BooleanRequest(
            operation_id="keep_tools.sub",
            kind=BooleanKind.subtract,
            target=box.created_bodies[0],
            tools=cyl.created_bodies,
            keep_tools=True,
        )
    )
    assert result.rebuild_ok
    assert result.feature is not None
    # a buried cavity touches none of the block's original 6 faces: they are all untouched, and the
    # cavity's own faces (the tool copy's shape) are new to the target -- never modified
    assert result.modified_faces == ()
    assert len(result.created_faces) >= 1

    # document-wide volume is conserved: what the target lost, the now-separate tool body still has
    doc_mass = adapter.mass_properties(MassRequest(operation_id="keep_tools.doc_mass"))
    assert doc_mass.volume_mm3 == pytest.approx(BOX_VOLUME, rel=1e-6)

    # the target on its own lost exactly the bore volume
    target_mass = adapter.mass_properties(
        MassRequest(operation_id="keep_tools.target_mass", body=box.created_bodies[0])
    )
    assert target_mass.volume_mm3 == pytest.approx(BOX_VOLUME - BORE_VOLUME, rel=1e-6)

    # the tool survives as its own live, queryable, un-consumed body (no invalid_reference)
    tool_mass = adapter.mass_properties(MassRequest(operation_id="keep_tools.tool_mass", body=cyl.created_bodies[0]))
    assert tool_mass.volume_mm3 == pytest.approx(BORE_VOLUME, rel=1e-6)

    # every one of the tool's own original faces is still resolvable (non-stale) and still owned by the
    # tool body -- never reassigned or invalidated by the Cut
    for face in cyl.created_faces:
        info = adapter.face_info(FaceRequest(operation_id="keep_tools.probe_tool_face", face=face))
        assert info.body == cyl.created_bodies[0]

    # a fully buried cavity does not change the block's outward envelope
    assert adapter.bounding_box(MassRequest(operation_id="keep_tools.bbox")) == BoundingBox(
        min=(0.0, 0.0, 0.0), max=BOX_SIZE
    )


def test_boolean_subtract_without_keep_tools_makes_the_tool_body_unqueryable(adapter: FreeCADAdapter) -> None:
    """Contrast case: the default (``keep_tools=False``) consumes the tool body -- querying it afterwards
    is ``invalid_reference``, same as the fake (``tests/unit/fake/test_booleans.py::
    test_boolean_subtract_contained_cylinder``)."""
    box, cyl = _block_and_buried_bore(adapter, "no_keep_tools")
    result = adapter.boolean(
        BooleanRequest(
            operation_id="no_keep_tools.sub",
            kind=BooleanKind.subtract,
            target=box.created_bodies[0],
            tools=cyl.created_bodies,
        )
    )
    assert result.rebuild_ok
    with pytest.raises(AdapterError) as excinfo:
        adapter.mass_properties(MassRequest(operation_id="no_keep_tools.tool_mass", body=cyl.created_bodies[0]))
    assert excinfo.value.info.code is ErrorCode.invalid_reference

    target_mass = adapter.mass_properties(
        MassRequest(operation_id="no_keep_tools.target_mass", body=box.created_bodies[0])
    )
    assert target_mass.volume_mm3 == pytest.approx(BOX_VOLUME - BORE_VOLUME, rel=1e-6)
