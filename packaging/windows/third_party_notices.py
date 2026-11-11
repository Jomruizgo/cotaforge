"""Writes THIRD_PARTY_NOTICES.md: what the Windows installer bundles besides cotaforge, with each license.

Reads the ``*.dist-info`` folders of a site-packages directory, so the list is exactly what ``pip install`` put there:

    python -I third_party_notices.py --site-packages <stage>/python/Lib/site-packages --out <stage>/THIRD_PARTY_NOTICES.md

``build.ps1`` runs it on the staged Python on every build. The copy committed at the root of the repository is the same
output for the current dependencies (``pip install --target`` with ``--platform win_amd64``, see CONTRIBUTING.md).
The full license texts travel with the packages (``<package>.dist-info/licenses/``) and with Python (``python/LICENSE.txt``).
"""

import argparse
import re
import sys
from email.parser import Parser
from pathlib import Path

OWN = {"cotaforge-kernel", "cotaforge-mcp"}

HEADER = """# Third-party notices

cotaforge is released under the MIT License (see `LICENSE`). The Windows installer also bundles the software below,
each under its own license. The full text of every license is installed with it: Python's in `python\\LICENSE.txt`, and
each package's in its `<package>.dist-info` folder under `python\\Lib\\site-packages`. FreeCAD and Claude Code are NOT
bundled: they are installed separately by the user and keep their own licenses.

## Python runtime

| Component | License | Source |
|---|---|---|
| CPython 3.12 (relocatable build by python-build-standalone, with Tcl/Tk) | Python Software Foundation License 2.0; the libraries it bundles (OpenSSL, SQLite, zlib, bzip2, expat, libffi, xz, Tcl/Tk and others) keep theirs, all collected in `python\\LICENSE.txt` | <https://github.com/astral-sh/python-build-standalone> |

## Python packages

| Package | Version | License | Project |
|---|---|---|---|
"""


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def license_of(meta: dict[str, list[str]]) -> str:
    expression = " ".join(meta.get("License-Expression", [])).strip()
    if expression:
        return expression
    classifiers = [c.split("::")[-1].strip() for c in meta.get("Classifier", []) if c.startswith("License ::")]
    classifiers = [c for c in classifiers if c != "OSI Approved"]
    if classifiers:
        return "; ".join(classifiers)
    text = " ".join(meta.get("License", [])).strip()
    first = text.splitlines()[0] if text else ""
    return first[:60] if first and len(text) < 100 else "see the license file"


def project_of(meta: dict[str, list[str]]) -> str:
    for url in meta.get("Project-URL", []):
        label, _, target = url.partition(",")
        if label.strip().lower() in {"homepage", "source", "repository", "source code"}:
            return target.strip()
    return meta.get("Home-page", [""])[0]


def read_meta(dist_info: Path) -> dict[str, list[str]]:
    message = Parser().parsestr((dist_info / "METADATA").read_text(encoding="utf-8", errors="replace"))
    meta: dict[str, list[str]] = {}
    for key, value in message.items():
        meta.setdefault(key, []).append(str(value))
    return meta


def rows(site_packages: Path) -> list[tuple[str, str, str, str]]:
    found = []
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        if not (dist_info / "METADATA").is_file():
            continue
        meta = read_meta(dist_info)
        name = meta.get("Name", [dist_info.name.split("-")[0]])[0]
        if normalise(name) in OWN:
            continue
        found.append((name, meta.get("Version", ["?"])[0], license_of(meta), project_of(meta)))
    return sorted(found, key=lambda row: row[0].lower())


def render(site_packages: Path) -> str:
    lines = [
        f"| {name} | {version} | {lic.replace('|', '/')} | {('<' + url + '>') if url else ''} |"
        for name, version, lic, url in rows(site_packages)
    ]
    return HEADER + "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-packages", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.site_packages.is_dir():
        print(f"not a folder: {args.site_packages}", file=sys.stderr)
        return 2
    text = render(args.site_packages)
    if text.count("\n| ") < 5:
        print("fewer than 3 packages found: wrong folder?", file=sys.stderr)
        return 2
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
