"""Workbook sheets as rows of cells, with the standard library (D13 §5.1 T6).

An `.xlsx` is a zip of XML: the workbook names its sheets, each sheet holds rows
of cells, and text cells point into one shared string table. The T6 adapters
(Ei's household file, VREG's tariff sheet) read exactly that and nothing more -
no formulas, no styles - so `core/` carries no third-party dependency (D-0053).
"""

from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from .base import QualityError

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["number", "sheet", "sheet_names"]

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _open(workbook: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(workbook))
    except zipfile.BadZipFile as err:
        msg = f"not a workbook: {err}"
        raise QualityError(msg) from err


def _targets(archive: zipfile.ZipFile) -> dict[str, str]:
    """Return each sheet's name → its XML part."""
    try:
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError) as err:
        msg = f"not a workbook: {err}"
        raise QualityError(msg) from err
    parts = {rel.get("Id"): str(rel.get("Target")) for rel in rels}
    sheets = book.find("m:sheets", _NS)
    found: dict[str, str] = {}
    for entry in () if sheets is None else sheets:
        target = parts.get(entry.get(_REL), "")
        found[str(entry.get("name"))] = "xl/" + target.lstrip("/").removeprefix("xl/")
    return found


def number(text: str) -> Decimal:
    """Return a number cell as the figure it was typed as.

    Excel stores `49.4036563` as the double `49.403656300000002`, and a sum it
    computed as `52.286399999999993`; rounded to nine decimals - more than any
    tariff sheet prints - the shortest representation is the figure published.
    """
    try:
        return Decimal(repr(round(float(text), 9)))
    except ValueError as err:
        msg = f"not a number: {text!r}"
        raise QualityError(msg) from err


def sheet_names(workbook: bytes) -> list[str]:
    """Return the workbook's sheet names in order."""
    with _open(workbook) as archive:
        return list(_targets(archive))


def sheet(workbook: bytes, name: str | None = None) -> list[dict[str, str]]:
    """Return one sheet (the first where `name` is `None`) as rows of column → text."""
    with _open(workbook) as archive:
        targets = _targets(archive)
        part = targets.get(name) if name is not None else next(iter(targets.values()), None)
        if part is None:
            msg = f"the workbook has no sheet {name!r}"
            raise QualityError(msg)
        try:
            strings = _strings(archive)
            root = ET.fromstring(archive.read(part))
        except (KeyError, ET.ParseError) as err:
            msg = f"unreadable sheet {name!r}: {err}"
            raise QualityError(msg) from err
    data = root.find("m:sheetData", _NS)
    return [] if data is None else [_cells(row, strings) for row in data]


def _strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(t.text or "" for t in item.iter(f"{{{_NS['m']}}}t")) for item in root]


def _cells(row: ET.Element, strings: Sequence[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for cell in row:
        column = re.sub(r"\d", "", cell.get("r", ""))
        value = cell.find("m:v", _NS)
        if value is None or value.text is None:
            continue
        found[column] = strings[int(value.text)] if cell.get("t") == "s" else value.text
    return found
