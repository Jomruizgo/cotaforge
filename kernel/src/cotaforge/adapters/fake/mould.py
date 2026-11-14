"""``FakeAdapter.draft_analysis``: the draft of the analytic faces of the fake, exact.

The fake knows planes (with a normal) and cylinders (with an axis). A plane's lean from the pull direction is exact; a
cylinder is a full wall round its axis, so some line of it always runs parallel to the pull (zero draft), or the whole
face does when the axis is the pull. The fake has no ray casting: ``shadow_tested`` is false and no undercut is
reported (a real CAD does that test).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from cotaforge.contract import (
    BodyHandle,
    DraftAnalysisRequest,
    DraftAnalysisResult,
    FaceDraft,
    FaceHandle,
    classify_draft,
)
from cotaforge.contract.enums import SurfaceType

if TYPE_CHECKING:
    from cotaforge.adapters.fake.adapter import FakeAdapter

METHOD = (
    "planes: exact from the face normal; cylinders: a full wall always has a line parallel to the pull, so 0 degrees; "
    "no shadow test in this adapter"
)


def draft_analysis(adapter: FakeAdapter, request: DraftAnalysisRequest) -> DraftAnalysisResult:
    doc = adapter._doc(request)  # noqa: SLF001 - same package family
    wanted = request.body.id if request.body is not None else None
    length = math.sqrt(sum(c * c for c in request.pull))
    pull = tuple(c / length for c in request.pull)
    faces: list[FaceDraft] = []
    for face in doc.faces.values():
        if face.body not in doc.bodies or (wanted is not None and face.body != wanted):
            continue
        if face.surface is SurfaceType.plane and face.normal is not None:
            along = abs(sum(n * p for n, p in zip(face.normal, pull, strict=True)))
            draft, exact = math.degrees(math.asin(min(1.0, along))), True
        elif face.surface is SurfaceType.cylinder:
            draft, exact = 0.0, False
        else:
            continue
        faces.append(
            FaceDraft(
                face=FaceHandle(id=face.id),
                body=BodyHandle(id=face.body),
                surface=face.surface.value,
                area_mm2=face.area_mm2,
                draft_deg=draft,
                samples=1,
                exact=exact,
                status=classify_draft(draft, False, request.min_draft_deg),
            )
        )
    return DraftAnalysisResult(faces=tuple(faces), method=METHOD, shadow_tested=False)
