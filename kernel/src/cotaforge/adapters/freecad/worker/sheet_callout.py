# ruff: noqa: E501
"""The own text of a hole callout and what the model says about it (no FreeCAD import).

``hole_dim`` writes the callout of a hole from the hole as measured. When the engineer's text is wanted instead (the thread
with its class, ``M45x1,5 - 6H``) the geometry stays measured: this module reads the text and compares every number it
states with the hole -- the diameter, the depth, the thread it names against the thread the hole was made with or, for a
plain hole, against the tap drill of that thread -- and says, in words, each one the measured geometry does not confirm.
The callout is still drawn as written (it is the engineer's to write); the item says what contradicts and the review holds
the sheet until the text or the model changes.
"""

import re

from sheet_context import Context, HoleInfo
from sheet_thread import TAP_DRILL_TOL_MM, thread_table

NUMBER_TOL_MM = 0.06  # a callout prints two decimals at most
_THREAD = re.compile(r"\bM(\d+(?:[.,]\d+)?)(?:\s*[x×X]\s*(\d+(?:[.,]\d+)?))?")
_CLASS = re.compile(r"\b\d{1,2}[A-Za-z]{1,2}\b|\b[A-Za-z]{1,2}\d{1,2}\b")  # 6H, 6g, H7, js6
_MULTIPLIER = re.compile(r"(?<![\d.,])(\d+)\s*[x×X]\s*(?=[ØØ⌀⌴↧\d]|$)|(?<![\d.,])(\d+)\s*[x×X]$")
_SIGNED = re.compile(r"[+\-±]\s*\d+(?:[.,]\d+)?")  # a deviation: +0,025 -0,010 ±0,1
_BRACKETED = re.compile(r"\([^)]*\)")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _value(text: str) -> float:
    return float(text.replace(",", "."))


def stated_numbers(text: str) -> tuple[list[tuple[float, float | None]], list[int], list[float]]:
    """``(threads, counts, numbers)`` a callout text states: the thread designations (major, pitch or ``None``), the
    quantities (``4x``) and the other plain numbers (diameters, depths). Tolerance classes, deviations and bracketed
    remarks state nothing about the geometry and are left out."""
    rest = _BRACKETED.sub(" ", text)
    threads: list[tuple[float, float | None]] = []
    for found in _THREAD.finditer(rest):
        threads.append((_value(found.group(1)), _value(found.group(2)) if found.group(2) else None))
    rest = _THREAD.sub(" ", rest)
    counts = [int(m.group(1) or m.group(2)) for m in _MULTIPLIER.finditer(rest)]
    rest = _MULTIPLIER.sub(" ", rest)
    rest = _CLASS.sub(" ", rest)
    rest = _SIGNED.sub(" ", rest)
    return threads, counts, [_value(n) for n in _NUMBER.findall(rest)]


def _shown(ctx: Context, value: float) -> str:
    return ctx.num(round(value, 2))


def _size(ctx: Context, designation: str) -> str:
    return designation.replace(".", ",") if ctx.comma else designation


def problems(ctx: Context, text: str, stack: list[HoleInfo], count: int = 1) -> list[str]:
    """What the text states that the measured hole (``stack``: the holes on one axis, a counterbore is two) does not
    confirm, one sentence each, in the sheet's language. Empty when everything it says agrees with the geometry."""
    hole = stack[0]
    found: list[str] = []
    threads, counts, numbers = stated_numbers(text)
    allowed = {hole.diameter, *(h.diameter for h in stack), *(h.depth for h in stack)}
    if hole.thread is not None and hole.thread[1] is not None:
        allowed.add(float(hole.thread[1]))
    for major, pitch in threads:
        size = f"M{major:g}" + (f"x{pitch:g}" if pitch is not None else "")
        row = thread_table(size)
        named = _size(ctx, size)
        if row is None:
            found.append(
                ctx.say(
                    f"el texto cita {named}, que no está en la tabla de roscas ISO 261: no se puede confirmar con el agujero de Ø{_shown(ctx, hole.diameter)}",
                    f"the text names {named}, which is not in the ISO 261 thread table: it cannot be confirmed against the Ø{_shown(ctx, hole.diameter)} hole",
                )
            )
            continue
        designation, tap_drill = row
        shown = _size(ctx, designation)
        if hole.thread is not None:
            made = thread_table(hole.thread[0])
            if made is not None and made[0] != designation:
                found.append(
                    ctx.say(
                        f"el texto dice {shown} y el agujero se hizo como {_size(ctx, made[0])}",
                        f"the text says {shown} and the hole was made as {_size(ctx, made[0])}",
                    )
                )
        elif abs(hole.diameter - tap_drill) > TAP_DRILL_TOL_MM:
            found.append(
                ctx.say(
                    f"el texto dice {shown}, cuyo taladro de rosca es Ø{_shown(ctx, tap_drill)}, y el agujero mide Ø{_shown(ctx, hole.diameter)}",
                    f"the text says {shown}, whose tap drill is Ø{_shown(ctx, tap_drill)}, and the hole measures Ø{_shown(ctx, hole.diameter)}",
                )
            )
        allowed.update({tap_drill, major, *([pitch] if pitch is not None else [])})
    for stated in counts:
        if stated != count:
            found.append(
                ctx.say(
                    f"el texto dice {stated}x y la llamada cubre {count} agujero(s) iguales",
                    f"the text says {stated}x and the callout covers {count} equal hole(s)",
                )
            )
    for number in numbers:
        if not any(abs(number - known) <= NUMBER_TOL_MM for known in allowed):
            measured = ", ".join(
                [f"Ø{_shown(ctx, hole.diameter)}"]
                + ([f"profundidad {_shown(ctx, hole.depth)}"] if ctx.comma else [f"depth {_shown(ctx, hole.depth)}"])
            )
            found.append(
                ctx.say(
                    f"el texto dice {_shown(ctx, number)} y ninguna medida del agujero lo es (mide {measured})",
                    f"the text says {_shown(ctx, number)} and no measure of the hole is that (it measures {measured})",
                )
            )
    return found
