# ruff: noqa: E501
"""``cad_guide(topic=drawing)``: how to make a production drawing -- sheets, title block, items and how they behave.

Kept out of ``guide.py`` because the item reference grows with every drawing phase: a new item type is one more entry in
``ITEM_LINES`` here, one in the contract's ``drawing_items`` and one in the worker's registry."""

from collections.abc import Collection

SHEET_RULES = (
    "drawing(action=create, template=A4|A3|A2|A1|A0|Letter, angle=first|third, title?, author?, date?, material?, "
    "scale?, language?, drawing_number?, revision?, general_tolerances?, company?, title_block?, sheet?, body?, "
    "isometric?, views?, projection?, symmetry?, fresh?) builds a sheet "
    "(an existing sheet of that name is rebuilt keeping its items, answers and reviewer; fresh=true starts over): "
    "front, top and right views (first angle by "
    "default) plus an isometric one, the overall width / height / depth dimensions and the title block (ISO 7200, in "
    "project language by default; language=es|en changes). Projection is the engineer's choice; no paper stated: "
    "it is chosen from the part (the reply says). Title "
    "defaults to the document name, date to today, material to "
    "the document's (it follows doc set_material while you leave it empty), "
    "general_tolerances to ISO 2768-mK. scale is a ratio (1:2, 2:1, 0.5); omitted, the largest standard one that fits. "
    "It returns only when every view has finished computing: never start a step on a view yourself while one is "
    "still computing (TechDraw does that in the background and a step on a half-computed view can crash the CAD).",
    "Several sheets: a new sheet= name makes another sheet of the same document (body=<body id> draws that body "
    "alone: one part per sheet; template and scale are per sheet). Without sheet= every action works on the first "
    "sheet. ONE DRAWING = ONE SHEET = ONE FILE: if the drawing does not fit, use a bigger paper (template=); several "
    "drawings are independent files. export(action=pdf|dxf, sheet=, path=) writes THAT sheet to ONE file (with several "
    "sheets and no sheet= it is refused and lists them; give each its own path). drawing(action=list) shows "
    "every sheet, its fields, its items and the texts it prints, each item with what it does now; "
    "drawing(action=remove_sheet, sheet=) takes a sheet away (it is the ONLY way: remove takes item ids and never the sheet).",
    "Look at it before writing it: drawing(action=capture) returns the sheet as an image painted from the SAME "
    "scene the PDF is written from (what you see, what the reviewer sees and what the engineer opens are one thing), "
    "native TechDraw objects a run_native script added included (a section view, its dimensions and hatching); the "
    "reply names the views and those objects. region=[x0,y0,x1,y1] (mm on the sheet from its lower left corner) "
    "zooms in: use it to read a dimension or tell a line type before you say it is right. A headless CAD (no "
    "window) cannot paint native objects: the reply then lists them as NOT DRAWN and you must say so, never call "
    "the sheet fine without them (drawing review lists them as native_unread, a warning: it does not read them). "
    "export(action=pdf) checks the file it wrote (one page of the sheet's size, every operator well formed, ink in "
    "every view) and a damaged PDF is an error and is not saved: a native object with a colour outside 0..1 is "
    "normalised before the export, any other cause you fix at its source. "
    "Write it with export(action=pdf|dxf) with no face/flat -- only a FINISHED "
    "drawing is written (cad_guide(topic=drawing:done)) -- (with face= or flat= the DXF is the cutting "
    "outline instead, not the sheet). The DXF is R12 (opens in any CAD), text in the Windows code page, layers by "
    "role (CONTORNO, OCULTAS, COTAS, TEXTO, ROTULO, EJES, SOMBREADO, ROSCAS) with the title block in it: PDF and "
    "DXF show "
    "the same. Line weights follow ISO 128: the visible outline is thick (0.5 mm), everything else thin (dimension, "
    "extension and leader lines, centre lines, thread arcs, hidden lines 0,25 mm; hatching 0,18 mm) and every "
    "dimension or note is written 3,5 mm high; the DXF (R12, no lineweight) carries the weight in the layer colour "
    "for a standard pen table: colour 7 = 0,50 mm (CONTORNO, ROTULO), 3 = 0,25 mm (COTAS, TEXTO, ROSCAS), 1 = 0,25 mm "
    "(EJES), 8 = 0,25 mm (OCULTAS), 9 = 0,18 mm (SOMBREADO).",
    "The sheet is part of the document (doc(action=save) keeps it) and its views show the part as it is: after "
    "changing the part, build the sheet again. The part must have volume.",
)

PROJECTION_LINE = (
    "PROJECTION (an option of create; the engineer's choice): projection=techdraw (the default, FreeCAD's own) makes "
    "the views as TechDraw view objects (FreeCAD's own); on a big assembly (hundreds of "
    "bodies) FreeCAD takes minutes to hours per view. projection=hlr projects the views with TechDraw.projectEx (what "
    "section, detail and exploded views already use): same sheet, same numbers, same items, in PDF / PNG / DXF, "
    "in seconds to minutes; the views in the FreeCAD window are then drawings of that projection (symbols, not "
    "TechDraw view objects: they cannot be edited there). NEVER choose hlr "
    "yourself and never switch silently. Only when a create on a large assembly is slow (the progress lines of the "
    "call show it) OFFER it to the engineer, say what it costs (the views are not TechDraw's own) and wait for the "
    "answer. "
    "The mode is kept in the sheet (add, update, save, reopen and export keep it); to change it call create again "
    "(the sheet is rebuilt and keeps its items). drawing(action=list) shows which mode a sheet uses."
)

UPDATE_LINE = (
    "After changing the part, drawing(action=update) lays the views out again and keeps every item (dimensions "
    "re-measure the part every time the sheet is drawn, even without update)."
)

WINDOW_LINE = (
    "THE SHEET IS BUILT LIVE IN THE FREECAD WINDOW, like the 3D model: the engineer watches the TechDraw sheet while you "
    "work and can stop you or suggest, so build it in SMALL STEPS -- one feature or one group per drawing(action=add) "
    "call (the dowel holes, then the clearance holes, then the datums, then a frame, then the notes), never thirty "
    "items at once -- and each item appears in the window as soon as it is added. What is native and what is not: the "
    "views (projection=techdraw), the overall dimensions and the title block / revision texts are TechDraw's own "
    "objects; every item you add (callouts, dimensions, datum triangles, GD&T frames, finish symbols, tables, notes, "
    "sections, thread arcs) is a TechDraw SYMBOL object made from the same drawing the PDF is written from, at the "
    "same place, so the window and the PDF/DXF show the same thing. The window is for watching: edit the sheet only "
    "through drawing(...); a change made by hand in the window is put back by the next add, remove or export."
)

ITEM_INTRO = (
    'ITEMS: drawing(action=add, items=[{...}, ...]) puts items on a sheet; each is {"type": ..., "id"?: ..., ...} '
    '(the id defaults to a letter + number: use it to remove one: drawing(action=remove, items=["h2"]) takes THAT '
    "item and its objects off, never the sheet). Every value "
    "a dimension shows is MEASURED on the model when the sheet is drawn, never taken from the item: you say WHAT to "
    "measure (a hole, a face, a point of the part), not the number. The reply is short: the items of this call and "
    "what each did -- ok, warning "
    "(drawn, with something to know), error (cannot be drawn now) or refused (not stored). Dimension lines stack "
    "outward in the order of the items; offset_mm (distance from the edge of the view) and side move one. Callouts "
    "look for free room around the dimensions, away from each other and inside the frame, never over the title "
    "block; with side or offset_mm a callout goes exactly there and, if that place is taken or outside the frame, the "
    "item's warning says so (datum, gdt and finish too: on a flat face side only tells which way the mark would go "
    "and the warning says when it cannot). The engineer sees each one in the window; fix a placement with offset_mm "
    "/ side, or remove and add again, instead of rebuilding the sheet. A face id the part no longer has (an edit regenerated "
    "it) makes its item an error, never an old number: add it again with the new id. "
    "Positions are world mm; at_mm is a SHEET position in mm from its bottom-left corner (y up). view is front, "
    'top or right (or the label of a section, "A-A", for dim, hole_dim, note and line).',
)

ITEM_LINES = (
    "hole_dim: the callout of holes with a leader on the view that shows them as circles. at=[x,y,z] (any point of the "
    'hole\'s axis, 0.05 mm; or a list of points) | face="face:N" (hole faces, inspect(action=holes) lists them) | '
    "all=true; view?, side? (where the callout goes), offset_mm?, centre_marks? (default true), dowel? (true: a "
    "registration / dowel hole: ◎ leads the callout and a ring marks each hole). Equal holes share ONE "
    'callout with the count ("4× Ø8 pasante", "Ø6 ↧ 10", "M6 ↧ 12"; ↧ is depth, ⌴ counterbore); a counterbore is two lines. The '
    "thread and its depth come from the hole op that made the hole; a hole made any other way (imported, patterned) "
    "is described by its geometry; only when its diameter is a tap drill (Ø5 for M6, Ø6,8 for M8...) the reply and "
    'the review say "rosca no declarada" (info): add a note if it is threaded -- never said of clearance or dowel '
    "holes. The round "
    "ends of a slot and half holes are not holes here: dimension them with dim (radius, distance between end faces). "
    'text? ("M45x1,5 - 6H"): your own wording of the callout of ONE hole (or group); the geometry stays measured and the '
    "text is checked against it: a thread that is not the one the hole was made with (or, for a plain hole, whose tap "
    "drill is not its diameter), a count, a diameter or a depth the hole does not have is said in the item's reply "
    "(warning: contradicts the model) and held by the review (callout_contradicts) until the text or the model "
    'changes; tolerance classes (6H, H7) and deviations are not checked. thread? ("M45x1.5"): the thread of a bore '
    "no hole op made (revolved, imported): refused unless its tap drill is the measured diameter. Fine threads "
    '(M45x1.5, ISO 261) and coarse ones up to M68 are in the table the hole op uses. view="A-A" puts it on a section '
    "view: a diameter dimension across the bore (at the major diameter for a thread), the thread lines and the centre "
    "line.",
    'dim: kind=distance, from={ref}, to={ref}, along?=x|y|z, view?, side?, offset_mm?, arrows?=auto|inside|outside. A ref is {"at":[x,y,z]} (a '
    "point "
    "of the part: a vertex or a point on an edge or face; or the axis of a hole, so two hole refs are centre to "
    'centre), {"face":"face:N"} (a plane: its position) or {"edge":"edge:N"} (a straight edge, or a circle: its '
    "centre). along defaults to the normal of a face ref, else the one axis the refs are apart along. "
    'kind=radius, face=<cylindrical face> ("R5"); kind=angle, faces=[a, b] (two planar faces seen edge-on in a view); '
    'kind=chamfer, face=<flat chamfer face> (leg × angle, "3 × 33,7°"). view="A-A" (a section view, once its '
    "section item is on the sheet; the items are drawn after it whatever their order) takes kind=distance (a face is a "
    "line of the cut: the extension line starts at its end), kind=diameter (a shaft or boss seen from the side) and "
    "kind=angle; a section has no TechDraw view, so its dimensions are the sheet's own drawing like a callout's.",
    "datum_dims: where the holes are, counted from two datum edges of one view (default top). Every sheet with holes "
    "already carries the item loc (auto=true), see drawing:quality. mode=ordinate (default: "
    "baseline dimensions from the datum edge to each hole centre, one line per distinct position, TechDraw's own "
    "dimensions: FreeCAD has no ordinate dimension) or table (numbered holes and a table of X, Y and the callout). "
    'x_from / y_from: a face id (a plane seen edge-on, perpendicular to that direction) or "min" / "max" (the '
    'silhouette; default min = lower-left corner); holes? ("all" or points), x_side? (below|above), y_side? '
    "(left|right), at_mm? (the table's top-left corner), view?, offset_mm?.",
    'TOLERANCES (fields of hole_dim and dim, not a separate item): fit="H7" | "g6" | "H7/g6" (ISO 286, IT5 to '
    "IT11, up to 500 mm; the nominal size is the MEASURED one), tol=0.1 (symmetric) | [upper, lower] in mm, "
    "show=symbol|deviations|both|limits (fit: both by default, 'Ø12 H7 (+0.018/0)'; tol: deviations, '20 +0.1/-0.05' "
    "stacked on linear dimensions; limits: 'Ø12.018 / 12.000'). dim kind=diameter, face=<cylinder that is NOT a hole "
    "wall> gives 'Ø24 g6 (-0.007/-0.02)' (a hole is toleranced with hole_dim fit=). A threaded hole takes no fit. "
    "A fit prints its class and its limits and nothing else: never write a note that asks the shop to verify a "
    "tolerance or a table. A dimension with no tolerance of its own reports in its answer the sheet's general ISO "
    "2768 tolerance (field general_tolerances).",
    "datum: letter (A-Z without I, O, Q) and ONE of face=<plane seen edge-on> (the triangle rests on its outline) or "
    "on=<id of a hole_dim or dim kind=diameter> (AXIS of a cylinder, ISO 5459: the triangle rests on the ledge of "
    "that dimension's callout, in line with it, and the letter hangs below; a cylinder is NOT given by face=). One "
    "letter, one datum. gdt (ISO 1101): char=flatness|straightness|circularity|cylindricity|parallelism|"
    "perpendicularity|angularity|position|concentricity|coaxiality|symmetry|runout|total_runout|profile_line|"
    "profile_surface, value (mm, the engineer's requirement), diameter? (Ø zone), modifier? (M|L), datums? (up to 3 "
    "letters, ALL must be datum items of the sheet: if not, error and review flags datum_undefined), and ONE of "
    "face=<face> (leader with arrow) or on=<id of a hole_dim or dim with leader> (the frame hangs from that "
    "callout: this is how an AXIS is toleranced; position, concentricity, coaxiality, symmetry and anything with "
    "diameter require it). Form tolerances take no datums; orientation, location and runout do require them. "
    "position and profile need the feature LOCATED from the datums by a dimension (datum_dims or dim distance), "
    "otherwise review flags gdt_not_located; theoretically exact dimensions (boxed) do not exist yet: the normal "
    "dimension is used. A geometric tolerance is ONLY a gdt frame on the feature or hung from its dimension, with "
    "the datum items it cites: never the text of a note ('perpendicularidad 0,02 A', '⊥ Ø0,1 A'; review flags "
    "gdt_as_note), and a datum no frame cites is flagged too (datum_unused). Tolerate the FINAL feature (the bore "
    "of H7, not the pre-machined one) and cite the datums in the order the part is located on.",
    "overall on a part of revolution (a body whose total size is the diameter of an outer cylinder) gives Ø once, "
    "in the view where the axis lies sideways; review flags duplicate_dimension if a dim repeats an overall one.",
    "finish (ISO 1302 / 21920): ra (um), machining=required (triangle, default) | forbidden (circle) | any, "
    "face=<face>, view?, side? -> symbol with 'Ra 1.6' (standing on the outline if the face looks up, otherwise "
    "with a leader). general=true, ra: the sheet's general symbol 'Ra 3.2 (✓)' near the title block. Threads (ISO "
    "6410): a hole with a thread declared by the hole operation carries ONLY the thin 3/4 arc at the major diameter "
    "in the view where it shows as a circle (THREADS layer), with no item.",
    "note: text (numbered in a NOTAS block at the bottom left; at_mm puts it elsewhere; the sheet's own notes -- "
    'thickness -- is numbered in the same block); view="A-A" writes it, unnumbered, under that section '
    "view (else beside it). "
    'revision: index ("A"), description, date?, by? -- a row of the revision table of the title block (the last 5 '
    "show) and the sheet's revision field. overall: the three overall dimensions (made by create; remove it and add "
    "it back to change it).",
    "view_lines: hidden edges (dashed, layer OCULTAS) and centre lines (layer EJES) in the standard views. hidden? "
    '(true = front and right, false, or a list like ["front","top"]), centres? (default true: a cross on every hole '
    "or whole cylinder seen as a circle and a centre line along every one seen from the side). Add it to every part "
    "with holes or pins: without "
    "it the views show no hidden edges and no axes. It takes the place of the callout's own centre marks (never two).",
    'section: a cut view. holes="all" (or the points on the axes of the holes the plane goes through, hole_dim style) '
    "cuts through the axes of the holes the parent view shows as circles (they must lie on one line there), "
    'holes="axis" through the axis of revolution of the part (a sleeve, a shaft), or '
    "at=[x,y,z] "
    "(a point of the plane) + normal=[x,y,z] (the direction the viewer looks; the plane must be seen edge-on in the "
    "parent view); view? (the parent, where the cutting line goes: default the first that fits), letter? (default "
    "the next "
    "free one: A, B...), flip? (look from the other side), at_mm? (centre of the cut view on the sheet), hatch? "
    '({"body 2": {"turn": 90, "spacing": 1.5}}: the angle, as a turn from the standard hatch, and the spacing, as a factor, of a '
    "body of the cut; the pattern is the material's and cannot be set). It draws the "
    'cutting-plane line in the parent (thick ends, thin chain, arrows and letters, ISO 128-40), the cut view as "A-A" '
    "with the cut faces hatched at 45 degrees (layer SOMBREADO), no hidden lines, and takes the first free place: "
    "in the "
    "direction of the arrows when there is room, else beside or below. Full sections only. The cut view is a view of "
    'the sheet: it is named by its label ("A-A") and carries items (hole_dim, dim, note, line with view="A-A"); it also '
    "draws, by itself, the centre line of every bore and round boss the plane runs along and the ISO 6410 thread lines "
    "(thin, at the major diameter, inside the hatched wall) of a threaded bore a hole op made. Section through a "
    'revolved part: holes="all" works when the part has a bore; one bore is cut through its axis. Several holes '
    "that are not on one line cannot be cut by one plane (refused, says so): name the ones to cut with holes=[...].",
    "line: an axis or a line in an ISO 128 line type, drawn by the sheet (TechDraw's own cosmetic lines cannot be a chain "
    "line: see cad_guide(topic=traps)). axis=<cylindrical face> (a shaft, boss or bore: its centre line along its whole "
    "length plus 3 mm, in every standard view that sees it from the side and a centre cross where it is seen end-on) "
    "or from={ref} + to={ref} (two places of the part, as in dim: the line between them); style? chain (long-dash "
    "dotted, default for an axis) | dashed | continuous (thin, default between two places); view? (front, top, right "
    "or a section label), extend_mm?.",
    "detail: an enlarged circle of a view. at=[x,y,z] (centre, a model point), radius_mm, view? (default front), "
    "scale? (the enlargement: 5 = 5:1; default the ISO 5455 one that fills about 50 mm), letter?, at_mm?. The circle "
    'and its letter go in the parent, the view is clipped exactly at the circle and labelled "B (5:1)". Hidden lines '
    "appear in it when view_lines drew them in the parent.",
    'aux_view: the part seen square-on to an inclined flat face. face="face:N" (a plane that is edge-on in front, top '
    "or right: its normal lies in that view's plane), view?, letter?, at_mm?. A view arrow with its letter goes in the "
    "parent; the auxiliary view shows the part only around that face (a partial view, margin 4 mm) and is placed in "
    "the "
    "free room nearest to the direction of sight.",
    "CONJUNTOS: drawing(action=create) en un documento de ensamblaje dibuja todos los componentes en su pose (sin "
    "lineas ocultas), rotulo 'Plano de conjunto' y deja sitio a la lista de piezas. parts_list: rows? (cambios de "
    "TEXTO por posicion: [{component:'comp:3', name?, reference?, material?, remarks?}]); las posiciones y cantidades "
    "se calculan (un componente = un archivo de pieza; las instancias iguales son UNA fila con su cantidad); nombre, "
    "n. de plano, material salen de la pieza, y una pieza importada muestra su archivo de origen y 'comprado'. Va "
    "sobre el rotulo (ISO 7573), cabecera abajo. balloons (ISO 6433): view? (front|top|right|isometric|exploded; por "
    "defecto exploded si la hoja la tiene, si no front), side?; un globo por posicion, la guia acaba en un punto sobre "
    "una cara visible de UNA instancia; un componente que la vista no muestra se informa (warning) y no se dibuja: "
    "elige otra vista. exploded: view? (isometric por defecto|front|top|right), mode? radial (cada componente se "
    "aleja del base) | axis (+ axis=[x,y,z]), factor? (0-5, def. 0.6), base? (componente que no se mueve; def. el "
    "mayor), moves? [{component, by:[dx,dy,dz]}] sustituye el desplazamiento de ese componente (tu sabes en que "
    "direccion se monta), trails? (lineas finas a lo largo del desplazamiento, solo en el hueco; se omiten las que "
    "cruzan otra pieza; def. true), at_mm?. Solo dibuja: el ensamblaje no cambia. Si la hoja lleva exploded, create "
    "con isometric=false evita la isometrica repetida. Nombre en la lista = el que diste a la pieza (new_document / "
    "import name=).",
    "REVIEW: drawing(action=review, sheet?) checks the sheet without drawing anything and lists findings (warning = a "
    "likely drawing error, info = confirm), each with the item and feature ids: holes without a callout or a position, "
    "cuts (slots, pockets) with no size or no position, a distance dimensioned twice or a chain that closes on the "
    "overall size or duplicates the ordinates, threaded holes without callout, holes of a tap-drill diameter that no hole op made (info: thread not declared), "
    "datums a GD&T frame cites that no datum item defines, positions of the parts list without balloon (and balloons "
    "without a row), components no view shows, items that cannot be drawn (stale face ids, points not on "
    "the part), leaders that cross or cut the outline twice, dimensions inside an outline, holes located in one direction only, notes that point at an empty title block field, views that overlap each other or the title block, items or text outside the frame or over the title "
    "block, and window_differs (the FreeCAD window does not show what the PDF shows). Run it before export and fix "
    "what it names; it is not a substitute for looking at the sheet. It also says who reviews the sheet and where that "
    "stands (cad_guide(topic=drawing:done)), which export(pdf|dxf) of a sheet requires.",
)

DONE_LINES = (
    "A DRAWING IS FINISHED when somebody other than the agent that drew it has looked at the sheet and every point they "
    "and the CAD raised is closed (the harness forces the review, it does not judge the drawing). "
    "export(action=pdf|dxf) of a sheet is REFUSED until then, and the refusal says which case the sheet is in, what to "
    "do next and what is missing. One drawing = one sheet = one file (a bigger paper if it does not fit; several drawings "
    "are independent files). There is no draft PDF: the draft is the TechDraw sheet of the document. The DXF of a face cut "
    "(face=) or a flat pattern (flat=) is not a drawing and is not asked.",
    "WHO REVIEWS is the project's setting drawing_review, kept in brief.yaml: memory(action=brief, drawing_review=<mode>"
    "[:<wait min>], text=<his words>). The default is engineer_or_fabricator; any other mode is the ENGINEER's decision "
    "(quote his words in text). engineer_or_fabricator: the engineer chooses between reviewing the sheet himself and the "
    "manufacturer agent, and if he does not choose within drawing_review_wait_min (default 15) the manufacturer counts | "
    "fabricator: the manufacturer agent | engineer: the engineer, no time limit | own: you ask yourself (below) | "
    "none: nobody (the blocking findings of the CAD are not asked either).",
    "THE FLOW of engineer_or_fabricator. (1) drawing(action=request_review, sheet): records the time and tells you to ask "
    "the engineer ONE question: «¿Revisas tú la hoja X o la revisa el agente fabricante?», and to start a background "
    "timer right after: Bash `sleep <wait min x 60>` with run_in_background=true (PowerShell: Start-Sleep -Seconds N in the "
    "background); Claude Code wakes you when it ends. Then keep working on other things: NEVER end the turn to wait. "
    "(2) «yo»: drawing(action=review_choice, sheet, by=engineer); no time limit; he tells you his remarks in the chat "
    "(or «sin observaciones») and you record them with drawing(action=review_report, sheet, by=engineer, items=[...]), "
    "zero items allowed; what he selects on the sheet you read with view(action=selection) and put in `where`. (3) "
    "«manufacturer», or no answer in time: review_choice by=fabricator (if he said it) and launch the agent `fabricator` "
    "for that sheet. If the timer wakes you and he already answered, ignore it (the server keeps the clock). The choice "
    "belongs to the SHEET: once made "
    "(or the manufacturer assumed) it stays through the changes of the sheet; asking again only says who reviews.",
    "THE FABRICATOR AGENT is a machinist / welder / assembler who gets ONLY the drawing (the sheet image, the texts it "
    "prints, the title block: drawing read and capture), never the model. It records its report with drawing(action="
    "review_report, sheet, by=fabricator, items=[{kind: missing|ambiguous|costly|other, where, text, fix}]): can it make "
    "and assemble the part from this alone; what is missing; what is ambiguous; what is hard or costly as drawn. Each "
    "item gets an id (fab1.1, eng2.3...). EVERY item of the latest report must be answered before the export: FIX the "
    "sheet and answer drawing(action=answer, id, text='corregido: <what you changed>'), or text='no aplica: <why it is not "
    "a problem>'. If a manufacturer reads it wrong, another will: fix it or add a note. The report stays valid through "
    "the fixes it asked for. A NEW review only if the engineer asks or you decide the sheet changed beyond the remarks: "
    "launch the reviewer again (or ask the engineer); the newest report supersedes. The server cannot prove WHO recorded a "
    "report (any agent can call the tool): record only the report you got, never one of your own.",
    "FINDINGS, found by the CAD on the real geometry and sheet, hold the export in every mode but none and are checked "
    "again on every export: FIX one (it is gone on the next review) or ANSWER it. A measurement (hole_not_located, a thread "
    "with no callout, an item that cannot be drawn...) cannot be answered 'no aplica' on your word: only the engineer's "
    "order closes it (by=<him>, reason=<his words>). One that stands for a missing requirement is listed under its row.",
    "REQUIREMENTS: what the standards ask a sheet to carry is a table, not prose: drawing(action=requirements) (also in "
    "create and review) lists the rows that apply to THIS part (thread, fits, tolerance, title block, texture, edges, treatments, "
    "locations, welds, parts list...), each added automatically, met, or a question, with its standard. What the model or "
    "the intent file already says the HARNESS adds (items rq_...: callouts with the declared thread and fit, centre lines, "
    "a note in the designer's words): move them, remove one only with reason=. Ask what needs a decision BEFORE drawing it "
    "(the designer through the channel or the engineer, one question at a time, with the proposed answer). The export waits until each "
    "row is met or answered ('met: where on the sheet' | 'no aplica: why'); one the harness measured (a thread exists, a "
    "fit is declared, the intent file says so) is dismissed only on the engineer's order (by=, reason=).",
    "ANSWER: drawing(action=answer, sheet?, id=<id from review>, text='applies: <how, in this part>' | 'no aplica: <why>'), "
    "one id per call, at least three words after the colon. The answers are stored in the sheet and the ENGINEER READS "
    "THEM: a 'no aplica' with no real reason is a false statement to him. What the shop must read goes in a note item.",
    "IN own you ask yourself: (1) the QUESTIONS OF JUDGEMENT no memory principle covers (q.callouts, q.line_weights; "
    "q.assembly where it applies) and (2) the MEMORY ENTRIES the export calls for: read each with memory(action=get, id) "
    "BEFORE answering it (answering one also closes it at memory(review)). In the other modes the reviewer uses the entries "
    "as his checklist.",
    "APPROVAL: 'Aprobó' in the title block is the engineer's and only he fills it: drawing(action=approve, sheet=, by="
    "<him>, reason=<his words, quoted>), only on his explicit instruction. Any later change of the "
    "sheet (an item, a view, update) empties it again: he approved that drawing, not the next one.",
)

QUALITY_LINES = (
    "WHAT THE SHEET MUST CARRY is not in this guide: drawing(action=requirements) lists the rows of the table that apply "
    "to THIS part, each met, missing or needing an answer, with the question to ask (create and review print it; the "
    "export waits for it). This guide is HOW it is drawn.",
    "THE QUALITY FLOOR (every sheet, without asking): reference drawings set a floor, not a style. Paper "
    "(template=A4|A3|A2|A1|A0|Letter), projection (angle=first|third: the symbol in the title block, the views "
    "placed the other way round) and the title block are the engineer's choice; what a machinist can make the part "
    "from is not.",
    "TITLE BLOCK: the template is a TechDraw template kept in the document (FreeCAD edits the same fields). "
    'create or update(title_block={"document_type": ..., "legal_owner_2": ..., "fixed_note_1": ...}) sets '
    "any field by name (document_type, drawing_number, fixed_note_1..3: the standing statements beside the "
    "projection symbol); scale, sheet number and the revision rows are the sheet's own. A logo is not supported.",
    "LOCATION OF THE HOLES: a sheet whose part has holes carries the item loc (datum_dims with auto=true) from "
    "create on. A number on a sheet must have a function: loc never counts from the corner of the part's box. In each "
    "direction of the view it counts from a reference the sheet HAS: (1) the datums you declared (the first datum items "
    "whose face is seen edge-on across x and across y: baseline dimensions stacked outside the part 7 mm apart, nearest "
    "first, one per distinct value, a centre line joining the holes that share it), else (2) the part's plane of "
    "symmetry across that direction, ONLY when the intent declares the part symmetric (symmetry=true on create / "
    "update; never from the shape): a hole on it has no dimension at all, the holes either side are ONE dimension "
    "between each pair, and the plane is drawn as a centre line. With neither it draws nothing "
    "for that direction and says so (loc is a warning: 'no reference along x ...'; the review says hole_not_located / "
    "hole_partly_located with the same words): ASK THE ENGINEER which face or edge the positions are counted from "
    "(do not pick one for him), declare it with a datum item (face) and the location follows. Holes you locate "
    "yourself are left to you. drawing(action=remove, "
    'items=["loc"]) takes the default off for good (the review then asks for the positions).',
    "REPEATED DIMENSIONS: the same size in two views (ISO 129: the length 50 on the front view and again on a section) "
    "is flagged dimension_repeated (info, it does not hold the export): keep the one in the view that shows it best.",
    "WHERE A DIMENSION STANDS: on the side of the feature it measures, where its extension lines cross the least of the "
    "part. review says dimension_side (no hold) with the side (side=). On a "
    "short dimension whose text and two arrows do not fit the arrows go outside: dim arrows=auto|inside|outside "
    "(arrows_inside_short). A text over another dimension's text, line, arrow or extension line is a collision, accepted "
    "only when no free place exists (the finding names the free side) and then answered as a deliberate choice "
    "(no aplica: why).",
    "A VIEW THAT ONLY CARRIES A CUTTING LINE goes when another view that stays sees the plane edge-on and can carry it "
    "(a turned part: the line on the circular view, the section carries diameters and lengths); so does a view whose "
    "dimensions other views all repeat. review says view_redundant (no hold) with the view to drop.",
    "THE SCHEME OF EVERY DIMENSION tells the shop what is critical (from a datum: that distance; a chain: each link; "
    "between two features: their relation): chosen by what mates or locates there, stated, and asked when the intent file "
    "does not say.",
    "ASSEMBLY OR WELDED SHEET: a sub-part with its own sheet gets here only the dimensions that place it and what is done "
    "after joining (a bore reamed after welding, with its note); its form dimensions stay on its own sheet. A feature "
    "machined after welding is drawn on the welded sheet; the part sheet shows it as before welding (with the allowance).",
    "A SECTION WITH SEVERAL BODIES: tell the bodies apart by hatch ANGLE (45 against 135 degrees) and/or SPACING, never by "
    "PATTERN: it is the material's. The harness alternates the angle of touching bodies (review: hatch_same_as_neighbour, "
    "hatch_pattern_differs).",
    "CALLOUTS: equal holes share one callout (4× ...). Its leader is short, goes to the hole and the side that is "
    "clean, never crosses another leader, cuts the outline once, and its text lies outside the part, off the "
    "dimension lines; review says leader_crossing / leader_crosses_part when it cannot. Dowel or registration holes: "
    "hole_dim fit=H7 dowel=true -> ◎ 4× Ø6 H7 (+0.012/0) through and a ring round each hole; review asks "
    "(dowel_unmarked: no hold) for a through hole of 25 mm or less with a hole fit of grade 8 or finer.",
    "NOTES: numbered in the NOTAS block, the thickness first ('19 de espesor': the sheet writes it when it has ONE "
    "standard view), yours in the order added. A note is for fabrication and general remarks, never for a geometric "
    "tolerance (that is a gdt frame). Part-specific fabrication notes go there too (which ones the part needs: the requirements).",
    "VIEWS OF A REVOLVED PART: without views, a body of revolution about x, y or z gets, with a bore, the view along its "
    "axis and a section A-A through it (item section holes=axis), solid the one view that shows its profile; the reply "
    "says it chose, and views=[front, top, right, iso] gives all four. Any other part gets the four. A rebuild without "
    "views keeps the sheet's.",
    'ONE VIEW: a plate or flat part whose holes go through its thickness needs one view: create(views=["top"]) (or '
    "remove_view front on a sheet that has one other standard view). review says single_view_enough (no hold: the memory principle drawing-sufficient-views covers it) when the "
    "other views add nothing. The sheet then gives width and height on that view and the thickness as a note.",
)

WELD_LINE = (
    "weld: a part welded from several bodies says how they join (ISO 2553 symbol). between=[face of one body, face of the "
    "other] (the faces that meet), kind? (fillet), size_mm (leg; size_kind=throat for the throat), sides? (arrow | other | "
    "both), all_around?, field?, length_mm + pitch_mm (intermittent), view?, side?, offset_mm?. A CAD with no weld symbol "
    "refuses it and says so: put the weld in a note. ONE SYMBOL PER JOINT: a symbol on both sides of the reference line "
    "(sides=both) already means both sides of the joint; a second weld item on the same joint is a duplicate "
    "(weld_duplicate). A welded sheet carries a parts list (the weldment cut list) and a balloon per body, numbered as "
    "the list: review says weld_list_missing, item_without_balloon and balloon_not_in_list."
)

KEYWAY_LINE = (
    "keyway_dims (no fields): the dimensions of the keyways the walls of the part show -- width, depth as the standards give "
    "it (d - t1 on a shaft, d + t2 in a hub), length, position -- as dim items <id>_w, _t, _l, _p. The harness adds them "
    "itself (rq_keyway); only the width's fit class is asked."
)

SPANISH_LINE = (
    "The engineer reads Spanish: notes, revision rows and the title block fields go in Spanish (the title block "
    "labels and the callouts are Spanish when language=es), numbers use the decimal comma on the sheet."
)

VIEW_LINE = (
    "Display only (visible CAD window): view(action=style) sets transparency / colour / hide-show of bodies, "
    "view(action=section) a section plane, view(action=set_view) the orientation (front, top, right, rear, left, "
    "bottom, iso, dimetric, trimetric) and the fit of the whole model or of some bodies."
)


POINTING_3D = (
    "POINTING (visible CAD window): the engineer SELECTS what he means and you READ it with view(action=selection) "
    "(a face, an edge: their ids, usable in every tool), instead of both of you describing 'which one'. ASK HIM TO POINT "
    "when naming it would take a sentence ('the hole near the left edge', 'that face'): 'select it in the window and tell "
    "me'. To SHOW him something, view(action=show, ids=[...]) (note, clear=true takes it off); it is display only and "
    "changes nothing."
)
POINTING_SHEET = (
    "On a drawing sheet he selects in the sheet window -- a dimension, a callout, a symbol, a view, a line or a circle of "
    "a view -- and view(action=selection) says what it is: the ITEM (id, type, what it prints, what it measures), the "
    "VIEW, and the model faces / edges / holes it corresponds to, by the ids every tool takes (the hole circle of the "
    "top view comes back as the hole: diameter, axis, wall face; a dimension as its item and the two holes it "
    "measures). A face or edge he selects in 3D lists the sheet items that refer to it ('on the sheet: d3 dim 70'): "
    "that answers 'which dimensions refer to this face?'. Lines of section and detail views are not mapped to the "
    "model.",
    "SHOW on the sheet: view(action=show, ids=[...]) takes an item id (d3), a view (view:top) or a face / edge; items and "
    "views are painted on the sheet window (it opens the sheet if it is not shown); on=sheet also shows on the sheet what "
    "faces / edges are (the items that refer to them and the lines of the views that draw them; a step id stands for the faces it made: a hole's wall), on=3d shows in 3D the "
    "faces an item refers to, on=both does both; sheet= picks the sheet. The paint goes away with clear=true or with the "
    "next change of the sheet; nothing of the document changes.",
)


def pointing_text(sheets: bool) -> str:
    """The guide for pointing (``cad_guide(topic=pointing)``): selection and display, in 3D and on the drawing sheets."""
    lines = [POINTING_3D, *(POINTING_SHEET if sheets else ())]
    return "\n".join(f"- {line}" for line in lines)


NATIVE_LINE = (
    "THE SHEET IS A TECHDRAW DRAWING: what TechDraw has an object for is that object, so the engineer opens the page in "
    "FreeCAD and edits it (text, position, tolerance) and the PDF is TechDraw's own export of the window. Native: "
    "hole_dim (a diameter dimension with the whole callout as its text: 4x O6 H7, counterbore, depth, thread), "
    "dim distance / radius / angle / diameter (FreeCAD MEASURES them: if its number is not the model's, the item is "
    "drawn as a symbol and review says native_fallback), datum_dims (baseline dimensions from the datum edges), "
    "note (one annotation per line), overall, the views, and the hidden lines of view_lines. A SYMBOL (a drawing of "
    "ours inside TechDraw, not editable as dimensions) is what TechDraw has no object for: datum, gdt, finish, "
    "centre marks and lines, thread arcs, chamfer callouts, section / detail / aux_view / exploded, parts list, "
    "hole table. Every object has CotaforgeItem (the item id) and CotaforgeSheet. The PDF of a visible CAD is "
    "TechDraw's; a headless CAD (no window to paint) writes it with our own writer from the same sheet, and "
    "export says which; the DXF is always our R12 writer (TechDraw's own DXF has no title block, symbols or notes)."
)

VIEWS_LINE = (
    "VIEWS: create(views=[front, top, iso]) makes only those (the front one, or ONE other standard view: views=[top] "
    "is a plate seen from above in a single view; right is the left-hand view of first angle, the right-hand one of "
    "third). A revolved part gets fewer (drawing:quality). "
    "drawing(action=remove_view, views=[right]) / add_view re-lays the sheet out over the room it frees at "
    "the largest standard scale at which everything on the sheet still fits (the result says the scale before and "
    "after). drawing(action=review) flags a non-iso view with no dimension, callout or balloon "
    "(view_adds_nothing): dimension/drop it. Placement is automatic: items that would lie on one another or off the sheet are "
    "moved apart; review lists what still collides, by item id (collision)."
)

TOPIC_ITEMS = "drawing:items"
TOPIC_MARKS = "drawing:marks"
TOPIC_VIEWS = "drawing:views"
TOPIC_ASSEMBLY = "drawing:assembly"
TOPIC_OUTPUT = "drawing:output"
TOPIC_QUALITY = "drawing:quality"
TOPIC_DONE = "drawing:done"
SUBTOPICS = (
    TOPIC_ITEMS,
    TOPIC_MARKS,
    TOPIC_VIEWS,
    TOPIC_ASSEMBLY,
    TOPIC_OUTPUT,
    TOPIC_QUALITY,
    TOPIC_DONE,
)
INDEX_LINE = (
    "MORE (the item reference is split: each reply is read in full): cad_guide(topic=drawing:items) holes, "
    "dimensions, position dimensions, tolerances | drawing:marks datums, GD&T frames, finish, notes, hidden / centre "
    "lines | drawing:views section, detail, auxiliary view, review | drawing:assembly parts list, balloons, exploded | "
    "drawing:output how export(action=pdf|dxf) writes the sheet, DXF layers, line weights, projection=hlr | "
    "drawing:quality the quality floor: location of the holes, callouts, dowels, notes, title block, one view | "
    "drawing:done WHEN A DRAWING MAY BE EXPORTED: requirements, who reviews it, answer, approve. Look at the "
    "sheet (drawing capture) before you write it."
)


def drawing_text(tools: Collection[str], part: str = "") -> str:
    """The guide for the drawing tools this CAD has: the sheet (``part`` empty) or one group of its items."""
    if part:
        groups = {
            TOPIC_ITEMS: [ITEM_INTRO[0], *ITEM_LINES[0:4]],
            TOPIC_MARKS: [*ITEM_LINES[4:9], WELD_LINE, KEYWAY_LINE],
            TOPIC_VIEWS: [*ITEM_LINES[9:12], ITEM_LINES[13]],
            TOPIC_ASSEMBLY: [ITEM_LINES[12]],
            TOPIC_OUTPUT: [SHEET_RULES[2], PROJECTION_LINE, VIEW_LINE],
            TOPIC_QUALITY: QUALITY_LINES,
            TOPIC_DONE: list(DONE_LINES),
        }
        return "\n".join(f"- {line}" for line in groups[part])
    lines = [SHEET_RULES[0]]
    if "annotate_drawing" in tools:
        lines += [SHEET_RULES[1], UPDATE_LINE, WINDOW_LINE, NATIVE_LINE, VIEWS_LINE, SPANISH_LINE, INDEX_LINE]
    else:
        lines += [SHEET_RULES[2], SHEET_RULES[3], PROJECTION_LINE, VIEW_LINE]
    return "\n".join(f"- {line}" for line in lines)
