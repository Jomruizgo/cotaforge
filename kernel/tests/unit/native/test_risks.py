"""The static risk scan: what it flags (with line and reason) and, as important, what it must leave alone."""

from pathlib import Path

import pytest

from cotaforge.native import Risk, scan_risks

WORKSPACE = Path("/work/design")


def kinds(code: str, workspace: Path | None = WORKSPACE) -> list[tuple[int, str]]:
    return [(risk.line, risk.kind) for risk in scan_risks(code, workspace)]


def reasons(code: str, workspace: Path | None = WORKSPACE) -> list[str]:
    return [risk.reason for risk in scan_risks(code, workspace)]


# --- imports and module calls ------------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["os", "subprocess", "shutil", "socket", "urllib", "http", "requests", "ctypes"])
def test_importing_a_risky_module_is_flagged_with_its_line(module: str) -> None:
    risks = scan_risks(f"x = 1\nimport {module}\n", WORKSPACE)
    assert [(r.line, r.kind) for r in risks] == [(2, "import")]
    assert module in risks[0].reason


def test_dotted_and_from_imports_of_risky_modules_are_flagged() -> None:
    code = "import urllib.request\nfrom http.client import HTTPConnection\nfrom os import path\n"
    assert kinds(code) == [(1, "import"), (2, "import"), (3, "import")]


def test_os_system_and_popen_calls_are_flagged_even_through_an_alias() -> None:
    code = "import os as o\no.system('rm -rf /')\nfrom os import popen\npopen('ls')\n"
    found = kinds(code)
    assert (2, "call") in found
    assert (4, "call") in found


def test_subprocess_call_is_flagged_as_a_call_too() -> None:
    assert (2, "call") in kinds("import subprocess\nsubprocess.run(['ls'])\n")


def test_os_path_calls_are_not_flagged_as_calls() -> None:
    found = kinds("import os\np = os.path.join('a', 'b')\n")
    assert found == [(1, "import")]  # the import itself, no call finding


def test_importlib_is_flagged_as_dynamic_import() -> None:
    assert kinds("import importlib\n") == [(1, "import")]


def test_dunder_import_is_flagged() -> None:
    assert kinds("m = __import__('os')\n") == [(1, "dynamic-import")]


# --- dynamic code ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("fn", ["eval", "exec", "compile"])
def test_dynamic_code_of_a_non_literal_string_is_flagged(fn: str) -> None:
    extra = ", 'x', 'exec'" if fn == "compile" else ""
    assert kinds(f"s = input_text\n{fn}(s{extra})\n") == [(2, "dynamic-code")]


def test_eval_of_a_literal_string_is_not_flagged() -> None:
    assert kinds("eval('1 + 1')\n") == []


# --- open() ---------------------------------------------------------------------------------------------


def test_open_for_writing_outside_the_workspace_is_flagged() -> None:
    risks = scan_risks("f = open('/etc/passwd', 'w')\n", WORKSPACE)
    assert [(r.line, r.kind) for r in risks] == [(1, "file-write")]
    assert "/etc/passwd" in risks[0].reason


@pytest.mark.parametrize("mode", ["w", "a", "x", "r+", "wb", "ab"])
def test_every_writing_mode_is_flagged_outside_the_workspace(mode: str) -> None:
    assert kinds(f"open('/tmp/x', {mode!r})\n") == [(1, "file-write")]


def test_open_for_writing_inside_the_workspace_is_not_flagged() -> None:
    assert kinds("open('/work/design/out/part.txt', 'w')\n") == []


def test_dotdot_escaping_the_workspace_is_flagged() -> None:
    assert kinds("open('/work/design/../secret.txt', 'w')\n") == [(1, "file-write")]


def test_prefix_sibling_of_the_workspace_is_outside() -> None:
    assert kinds("open('/work/design-evil/x', 'w')\n") == [(1, "file-write")]


def test_open_for_reading_is_never_flagged() -> None:
    assert kinds("open('/etc/hosts')\nopen('/work/design/a.txt', 'r')\nopen('/etc/hosts', mode='rb')\n") == []


def test_open_with_unresolvable_target_is_flagged() -> None:
    risks = scan_risks("name = get_name()\nopen(name, 'w')\n", WORKSPACE)
    assert [(r.line, r.kind) for r in risks] == [(2, "file-write")]
    assert "statically" in risks[0].reason


def test_open_with_relative_target_is_flagged_because_the_cad_cwd_is_not_the_workspace() -> None:
    assert kinds("open('out.txt', 'w')\n") == [(1, "file-write")]


def test_open_with_unknown_mode_and_target_outside_is_flagged() -> None:
    assert kinds("open('/tmp/x', mode)\n") == [(1, "file-write")]


def test_path_built_from_constants_is_resolved() -> None:
    code = (
        "from pathlib import Path\nbase = Path('/work/design')\nopen(base / 'a.txt', 'w')\nopen('/tmp/' + 'b', 'w')\n"
    )
    assert kinds(code) == [(4, "file-write")]


def test_fstring_of_known_values_is_resolved() -> None:
    code = "d = '/work/design'\nopen(f'{d}/a.txt', 'w')\nopen(f'{unknown}/a.txt', 'w')\n"
    assert kinds(code) == [(3, "file-write")]


def test_os_path_join_is_resolved() -> None:
    code = "import os\nopen(os.path.join('/work/design', 'a.txt'), 'w')\n"
    assert kinds(code) == [(1, "import")]


def test_without_a_workspace_every_write_is_flagged() -> None:
    assert kinds("open('/work/design/a.txt', 'w')\n", workspace=None) == [(1, "file-write")]


# --- pathlib and FreeCAD saves --------------------------------------------------------------------------------


def test_pathlib_write_methods_outside_are_flagged() -> None:
    code = (
        "from pathlib import Path\nPath('/tmp/x.txt').write_text('hi')\nPath('/work/design/ok.txt').write_text('hi')\n"
    )
    assert kinds(code) == [(2, "file-write")]


def test_pathlib_variable_writes_are_resolved_through_the_assignment() -> None:
    code = "from pathlib import Path\np = Path('/tmp/q')\np.write_bytes(b'x')\n"
    assert kinds(code) == [(3, "file-write")]


def test_pathlib_unresolvable_receiver_is_flagged() -> None:
    code = "from pathlib import Path\np = Path(some_name)\np.unlink()\n"
    assert kinds(code) == [(3, "file-write")]


def test_pathlib_open_in_write_mode_outside_is_flagged_and_read_is_not() -> None:
    code = "from pathlib import Path\nPath('/tmp/a').open('w')\nPath('/tmp/a').open()\n"
    assert kinds(code) == [(2, "file-write")]


def test_save_as_outside_the_workspace_is_flagged_and_inside_is_not() -> None:
    code = "doc.saveAs('/home/u/other.FCStd')\ndoc.saveAs('/work/design/a.FCStd')\n"
    assert kinds(code) == [(1, "file-write")]


def test_save_document_and_save_copy_are_checked() -> None:
    code = "App.saveDocument('/tmp/x.FCStd')\ndoc.saveCopy('/tmp/y.FCStd')\n"
    assert kinds(code) == [(1, "file-write"), (2, "file-write")]


def test_plain_save_and_recompute_are_not_flagged() -> None:
    assert kinds("doc.save()\ndoc.recompute()\n") == []


def test_export_target_is_the_second_argument() -> None:
    code = "Part.export([obj], '/tmp/x.step')\nPart.export([obj], '/work/design/x.step')\n"
    assert kinds(code) == [(1, "file-write")]


# --- the false-positive guard ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "import math\nr = math.sqrt(2)\n",
        "from FreeCAD import Vector\nv = Vector(1, 2, 3)\n",
        "import FreeCAD as App\nimport Part\nb = Part.makeBox(1, 2, 3)\n",
        "import json, re, itertools\nprint(json.dumps({'a': 1}))\n",
        "from collections import namedtuple\n",
        "for i in range(3):\n    print(i)\n",
        "text = open('/work/design/readme.txt').read()\n",
        "def f(x):\n    return [y * 2 for y in x]\n",
        "doc.recompute()\n",
        "s = 'os.system is only a string here'\n",
        "# import os\n",
        "",
    ],
)
def test_harmless_code_has_no_findings(code: str) -> None:
    assert scan_risks(code, WORKSPACE) == ()


def test_a_syntax_error_yields_no_findings() -> None:
    assert scan_risks("import os\ndef broken(:\n", WORKSPACE) == ()


def test_findings_are_ordered_by_line_and_deduplicated() -> None:
    risks = scan_risks("import os, os\nimport socket\n", WORKSPACE)
    assert [r.line for r in risks] == [1, 2]
    assert len({(r.line, r.reason) for r in risks}) == len(risks)


def test_risk_formats_as_one_line_with_its_line_number() -> None:
    assert Risk(3, "import", "imports 'os'").format() == "line 3: imports 'os'"


def test_windows_paths_are_compared_case_insensitively_and_with_either_slash() -> None:
    workspace = Path("C:/Work/Design")
    assert kinds("open('c:\\\\work\\\\design\\\\a.txt', 'w')\n", workspace) == []
    assert kinds("open('D:\\\\work\\\\design\\\\a.txt', 'w')\n", workspace) == [(1, "file-write")]


def test_an_adapter_adds_its_own_save_calls_and_freecads_stay() -> None:
    code = "model.Extension.SaveAs3('/tmp/a.part', 0, 1)\ndoc.saveAs('/tmp/b.FCStd')\n"
    assert [(r.line, r.kind) for r in scan_risks(code, WORKSPACE)] == [(2, "file-write")]
    risks = scan_risks(code, WORKSPACE, ("SaveAs3", "SaveAs4"))
    assert [(r.line, r.kind) for r in risks] == [(1, "file-write"), (2, "file-write")]
    inside = "model.Extension.SaveAs4('/work/design/a.part', 0, 1)\n"
    assert scan_risks(inside, WORKSPACE, ("SaveAs3", "SaveAs4")) == ()
