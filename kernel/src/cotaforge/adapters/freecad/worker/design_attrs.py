"""The material and the process route of the design, kept INSIDE the FreeCAD document.

* material: a dynamic string property ``CotaforgeMaterial`` on every live body (visible in the property editor, and the
  place an engineer or a native fragment would change it) AND the document metadata key ``cotaforge_design.material``
  (the only place a document without bodies yet can hold it);
* route: the metadata key ``cotaforge_design.route`` (declared process ids joined by commas).

``Document.Meta`` creates no object and is saved with the file; the keys do not start with ``cotaforge.`` because the
save rewrites that namespace with the stamp (``stamp.merged``).

Reading is strict on purpose: the distinct non-empty values of the metadata and of every body are joined by `` / ``, so
a document that disagrees with itself (one body re-labelled by hand) can never pass a material check."""

import json
from typing import Any

import registry as reg

MATERIAL_PROPERTY = "CotaforgeMaterial"
META_MATERIAL = "cotaforge_design.material"
META_ROUTE = "cotaforge_design.route"
META_ROLES = "cotaforge_design.roles"
SEPARATOR = " / "


def _body_objects(doc: reg.DocumentRec) -> list[Any]:
    objects = []
    for body in doc.live_bodies():
        obj = doc.fc_doc.getObject(body.result_obj)
        if obj is not None:
            objects.append(obj)
    return objects


def read(doc: reg.DocumentRec) -> dict[str, Any]:
    meta = dict(doc.fc_doc.Meta)
    values: list[str] = []
    candidates = [str(meta.get(META_MATERIAL, "") or "")]
    for obj in _body_objects(doc):
        if MATERIAL_PROPERTY in obj.PropertiesList:
            candidates.append(str(getattr(obj, MATERIAL_PROPERTY) or ""))
    for value in candidates:
        value = value.strip()
        if value and value not in values:
            values.append(value)
    route = [part.strip() for part in str(meta.get(META_ROUTE, "") or "").split(",") if part.strip()]
    return {"material": SEPARATOR.join(values) if values else None, "route": route, "roles": _read_roles(meta)}


def _read_roles(meta: dict[str, Any]) -> dict[str, str]:
    """The roles of bodies and components, by the name the caller gave them (a JSON object in the metadata)."""
    try:
        loaded = json.loads(str(meta.get(META_ROLES, "") or "{}"))
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}


def _set_body_material(obj: Any, material: str) -> None:
    if MATERIAL_PROPERTY not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", MATERIAL_PROPERTY, "Cotaforge", "Material of the part")
    setattr(obj, MATERIAL_PROPERTY, material)


def write(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    meta = dict(doc.fc_doc.Meta)
    material = params.get("material")
    if material is not None:
        material = str(material).strip()
        if material:
            meta[META_MATERIAL] = material
        else:
            meta.pop(META_MATERIAL, None)
        for obj in _body_objects(doc):
            if material or MATERIAL_PROPERTY in obj.PropertiesList:
                _set_body_material(obj, material)
    route = params.get("route")
    if route is not None:
        steps = [str(step).strip() for step in route if str(step).strip()]
        if steps:
            meta[META_ROUTE] = ",".join(steps)
        else:
            meta.pop(META_ROUTE, None)
    roles = params.get("roles")
    if roles is not None:
        if roles:
            meta[META_ROLES] = json.dumps({str(k): str(v) for k, v in roles.items()}, sort_keys=True)
        else:
            meta.pop(META_ROLES, None)
    doc.fc_doc.Meta = meta
    return read(doc)
