from __future__ import annotations

import os
import re
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from src.shipment.models import RawCustomsData


YACHANG_ENTITY = "义乌市雅畅进出口有限公司"
DIEXIANG_ENTITY = "杭州蝶象科技有限公司"


def apply_seller_department_mapping(raw_data: RawCustomsData, path: str | None = None) -> tuple[int, int, str | None]:
    mapping_path = _mapping_path(path)
    if not mapping_path.exists():
        _clear_purchase_entities(raw_data)
        return 0, 0, f"seller department file not found: {mapping_path}"

    mapping = load_seller_department_mapping(mapping_path)
    exact_entities: dict[tuple[str, str, str], str] = {}
    sku_entities: dict[tuple[str, str], str] = {}
    updated_items = []
    applied_items = 0

    for item in raw_data.shipment_items:
        purchase_entity = purchase_entity_for_seller(item.seller_name, mapping)
        if item.seller_name:
            applied_items += 1
        exact_entities[(item.shipment_no, item.sku, item.box_no or "")] = purchase_entity
        sku_entities[(item.shipment_no, item.sku)] = purchase_entity
        updated_items.append(replace(item, purchase_entity=purchase_entity))

    updated_batches = []
    for batch in raw_data.purchase_batches:
        key = (batch.shipment_no, batch.sku, batch.box_no or "")
        fallback_key = (batch.shipment_no, batch.sku)
        purchase_entity = exact_entities.get(key, sku_entities.get(fallback_key, ""))
        updated_batches.append(replace(batch, purchase_entity=purchase_entity))

    raw_data.shipment_items = updated_items
    raw_data.purchase_batches = updated_batches
    return len(mapping), applied_items, None


def _clear_purchase_entities(raw_data: RawCustomsData) -> None:
    raw_data.shipment_items = [replace(item, purchase_entity="") for item in raw_data.shipment_items]
    raw_data.purchase_batches = [replace(batch, purchase_entity="") for batch in raw_data.purchase_batches]


def load_seller_department_mapping(path: Path) -> dict[str, str]:
    rows = _read_xlsx_rows(path, preferred_sheet_name="店铺-运营")
    headers = [str(value or "").strip() for value in (rows[0] if rows else [])]
    shop_index = _header_index(headers, "店铺")
    department_index = _header_index(headers, "业务部门")
    if shop_index is None or department_index is None:
        raise RuntimeError("seller department Excel must contain headers: 店铺, 业务部门")

    mapping: dict[str, str] = {}
    for row in rows[1:]:
        shop = _cell_text(_row_value(row, shop_index))
        if not shop:
            continue
        mapping[shop] = _cell_text(_row_value(row, department_index))
    return mapping


def purchase_entity_for_seller(seller_name: str, department_by_seller: dict[str, str]) -> str:
    if not seller_name:
        return ""
    department = department_by_seller.get(seller_name)
    if department is None or department == "":
        return ""
    if department == "业务二部":
        return YACHANG_ENTITY
    return DIEXIANG_ENTITY


def _mapping_path(path: str | None) -> Path:
    configured = path or os.getenv("SHIPMENT_SELLER_DEPARTMENT_FILE", "data/shop-operations.xlsx")
    return Path(configured)


def _header_index(headers: list[str], name: str) -> int | None:
    try:
        return headers.index(name)
    except ValueError:
        return None


def _row_value(row: tuple[Any, ...], index: int) -> Any:
    return row[index] if len(row) > index else None


def _cell_text(value: Any) -> str:
    return str(value or "").strip()


def _read_xlsx_rows(path: Path, preferred_sheet_name: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_path = _sheet_path(archive, preferred_sheet_name)
        root = ElementTree.fromstring(archive.read(sheet_path))

    rows: list[list[str]] = []
    for row_element in root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
        row_values: list[str] = []
        for cell in row_element.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
            ref = cell.attrib.get("r", "")
            column_index = _column_index(ref)
            while len(row_values) <= column_index:
                row_values.append("")
            row_values[column_index] = _cell_value(cell, shared_strings)
        rows.append(row_values)
    return rows


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
        texts = [node.text or "" for node in item.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
        strings.append("".join(texts))
    return strings


def _sheet_path(archive: zipfile.ZipFile, preferred_sheet_name: str) -> str:
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationships = {
        rel.attrib.get("Id"): rel.attrib.get("Target", "")
        for rel in rels.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    }
    first_target = ""
    for sheet in workbook.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"):
        rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = relationships.get(rel_id, "")
        if not first_target:
            first_target = target
        if sheet.attrib.get("name") == preferred_sheet_name:
            return _normalize_sheet_target(target)
    return _normalize_sheet_target(first_target or "worksheets/sheet1.xml")


def _normalize_sheet_target(target: str) -> str:
    target = target.lstrip("/")
    return target if target.startswith("xl/") else f"xl/{target}"


def _cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        texts = [
            node.text or ""
            for node in cell.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
        ]
        return "".join(texts)

    value = cell.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
    text = value.text if value is not None and value.text is not None else ""
    if cell_type == "s" and text:
        index = int(text)
        return shared_strings[index] if index < len(shared_strings) else ""
    return text


def _column_index(ref: str) -> int:
    match = re.match(r"([A-Z]+)", ref or "A")
    letters = match.group(1) if match else "A"
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1
