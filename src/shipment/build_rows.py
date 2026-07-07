from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import hashlib
import re

from .models import (
    CustomsRow,
    CustomsWorkbookData,
    IssueRow,
    PurchaseBatch,
    PurchaseSplitRow,
    RawCustomsData,
    ShipmentItem,
    SkuInfo,
)

PENDING = "待确认"


def build_customs_workbook_data(raw_data: RawCustomsData) -> CustomsWorkbookData:
    issues: list[IssueRow] = []
    customs_rows: list[CustomsRow] = []
    purchase_split_rows: list[PurchaseSplitRow] = []
    batches_by_item = _group_batches(raw_data.purchase_batches)

    for item in raw_data.shipment_items:
        sku_info = raw_data.sku_infos.get(item.sku, SkuInfo(sku=item.sku))
        item_batches = _match_batches(item, batches_by_item)
        _collect_sku_issues(item, sku_info, issues)

        if item_batches:
            _append_batch_rows(item, sku_info, item_batches, customs_rows, purchase_split_rows, issues)
            continue

        if item.purchase_unit_price is None:
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "采购单价", "发货单列表未返回 fba_stock_cost"))
        if not item.purchase_entity:
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "采购主体", "未匹配采购单/采购方资料：请检查 purchase_sn、采购单 purchaser_id、采购方列表 name"))
        if not item.supplier:
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "供应商", "未匹配采购单资料"))
        if not item.domestic_source:
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "境内货源地", "未匹配供应商地址"))

        customs_rows.append(_build_row_from_item(item, sku_info, item.quantity))

    _sort_and_zero_duplicate_box_metrics(customs_rows)
    return CustomsWorkbookData(customs_rows=customs_rows, issue_rows=issues, purchase_split_rows=purchase_split_rows)


def _append_batch_rows(
    item: ShipmentItem,
    sku_info: SkuInfo,
    item_batches: list[PurchaseBatch],
    customs_rows: list[CustomsRow],
    purchase_split_rows: list[PurchaseSplitRow],
    issues: list[IssueRow],
) -> None:
    normalized_batches = _normalize_batches_for_main_row(item, item_batches)
    normalized_quantity = sum((batch.quantity for batch in normalized_batches), Decimal("0"))
    if normalized_quantity != item.quantity:
        issues.append(
            IssueRow(
                item.shipment_no,
                item.box_no,
                item.sku,
                "发货数量",
                f"采购批次数量合计 {normalized_quantity} 与发货数量 {item.quantity} 不一致",
            )
        )

    for batch in item_batches:
        if batch.quantity_missing:
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "采购拆分数量", "发货单详情 purchase_items 未提供数量"))
        if not (batch.purchase_order_no or batch.purchase_sn):
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "采购单号", "发货单详情 purchase_items 未提供采购单号"))
        if not (batch.purchase_entity or item.purchase_entity):
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "采购主体", "采购单/采购方资料未匹配：请检查 purchase_sn、采购单 purchaser_id、采购方列表 name"))
        if not (batch.supplier or item.supplier):
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "供应商", "采购单资料未匹配"))
        if not (batch.domestic_source or item.domestic_source):
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "境内货源地", "供应商地址未匹配"))
        if batch.purchase_unit_price is None:
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "采购单价", "采购批次未提供采购单价"))
        purchase_split_rows.append(
            PurchaseSplitRow(
                shipment_no=item.shipment_no,
                box_no=item.box_no,
                sku=item.sku,
                purchase_order_no=batch.purchase_order_no or batch.purchase_sn,
                batch_no=batch.batch_no,
                supplier=batch.supplier,
                purchase_entity=batch.purchase_entity,
                quantity=batch.quantity,
                purchase_unit_price=batch.purchase_unit_price if batch.purchase_unit_price is not None else PENDING,
            )
        )
    customs_rows.append(_build_row_from_batches(item, sku_info, normalized_batches))


def _group_batches(batches: list[PurchaseBatch]) -> dict[tuple[str, str, str], list[PurchaseBatch]]:
    grouped: dict[tuple[str, str, str], list[PurchaseBatch]] = defaultdict(list)
    for batch in batches:
        grouped[(batch.shipment_no, batch.sku, batch.box_no or "")].append(batch)
    return grouped


def _match_batches(item: ShipmentItem, grouped: dict[tuple[str, str, str], list[PurchaseBatch]]) -> list[PurchaseBatch]:
    exact = grouped.get((item.shipment_no, item.sku, item.box_no or ""), [])
    if exact:
        return exact
    return grouped.get((item.shipment_no, item.sku, ""), [])


def _collect_sku_issues(item: ShipmentItem, sku_info: SkuInfo, issues: list[IssueRow]) -> None:
    checks = [
        ("中文报关名", sku_info.customs_name_cn),
        ("单位", sku_info.unit),
        ("品名", item.product_name or sku_info.product_name),
        ("外箱尺寸", item.outer_box_size or sku_info.outer_box_size),
    ]
    for field_name, value in checks:
        if not value:
            issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, field_name, "SKU资料缺失"))
    if item.total_gross_weight is None and sku_info.gross_weight is None:
        issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "单品毛重", "SKU资料缺失"))
    if item.total_gross_weight is None and sku_info.net_weight is None:
        issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "单品净重", "SKU资料缺失"))
    if not item.box_no:
        issues.append(IssueRow(item.shipment_no, item.box_no, item.sku, "箱号", "发货/装箱资料缺失"))


def _build_row_from_item(item: ShipmentItem, sku_info: SkuInfo, quantity: Decimal) -> CustomsRow:
    return _build_row(
        item=item,
        sku_info=sku_info,
        quantity=quantity,
        purchase_entity=item.purchase_entity,
        supplier=item.supplier or PENDING,
        domestic_source=item.domestic_source or PENDING,
        purchase_unit_price=item.purchase_unit_price if item.purchase_unit_price is not None else PENDING,
    )


def _build_row_from_batch(item: ShipmentItem, sku_info: SkuInfo, batch: PurchaseBatch, quantity: Decimal) -> CustomsRow:
    return _build_row(
        item=item,
        sku_info=sku_info,
        quantity=quantity,
        purchase_entity=batch.purchase_entity or item.purchase_entity,
        supplier=batch.supplier or item.supplier or PENDING,
        domestic_source=batch.domestic_source or item.domestic_source or PENDING,
        purchase_unit_price=batch.purchase_unit_price if batch.purchase_unit_price is not None else item.purchase_unit_price or PENDING,
    )


def _build_row_from_batches(item: ShipmentItem, sku_info: SkuInfo, batches: list[PurchaseBatch]) -> CustomsRow:
    return _build_row(
        item=item,
        sku_info=sku_info,
        quantity=item.quantity,
        purchase_entity=_combined_text(batch.purchase_entity for batch in batches) or item.purchase_entity,
        supplier=_combined_text(batch.supplier for batch in batches) or item.supplier or PENDING,
        domestic_source=_combined_text(batch.domestic_source for batch in batches) or item.domestic_source or PENDING,
        purchase_unit_price=_combined_price(batches, item),
    )


def _normalize_batches_for_main_row(item: ShipmentItem, batches: list[PurchaseBatch]) -> list[PurchaseBatch]:
    grouped: dict[tuple[str, str, str, str, Decimal | None], PurchaseBatch] = {}
    for batch in batches:
        key = (
            batch.purchase_order_no or batch.purchase_sn,
            batch.purchase_entity,
            batch.supplier,
            batch.domestic_source,
            batch.purchase_unit_price,
        )
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = batch
            continue
        grouped[key] = PurchaseBatch(
            shipment_no=existing.shipment_no,
            sku=existing.sku,
            box_no=existing.box_no,
            quantity=existing.quantity + batch.quantity,
            purchase_entity=existing.purchase_entity,
            supplier=existing.supplier,
            domestic_source=existing.domestic_source,
            purchase_order_no=existing.purchase_order_no,
            purchase_sn=existing.purchase_sn,
            batch_no=existing.batch_no,
            purchase_unit_price=existing.purchase_unit_price,
            quantity_missing=existing.quantity_missing or batch.quantity_missing,
        )

    normalized = list(grouped.values())
    if len(batches) > 1 and len(normalized) == 1 and normalized[0].quantity != item.quantity:
        batch = normalized[0]
        normalized = [
            PurchaseBatch(
                shipment_no=batch.shipment_no,
                sku=batch.sku,
                box_no=batch.box_no,
                quantity=item.quantity,
                purchase_entity=batch.purchase_entity,
                supplier=batch.supplier,
                domestic_source=batch.domestic_source,
                purchase_order_no=batch.purchase_order_no,
                purchase_sn=batch.purchase_sn,
                batch_no=batch.batch_no,
                purchase_unit_price=batch.purchase_unit_price,
                quantity_missing=batch.quantity_missing,
            )
        ]
    return normalized


def _combined_text(values) -> str:
    distinct: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        text = str(value)
        if text not in distinct:
            distinct.append(text)
    return " / ".join(distinct)


def _combined_price(batches: list[PurchaseBatch], item: ShipmentItem) -> Decimal | str:
    prices: list[Decimal] = []
    for batch in batches:
        if batch.purchase_unit_price is not None and batch.purchase_unit_price not in prices:
            prices.append(batch.purchase_unit_price)
    if len(prices) == 1:
        return prices[0]
    if item.purchase_unit_price is not None:
        return item.purchase_unit_price
    return PENDING


def _build_row(
    item: ShipmentItem,
    sku_info: SkuInfo,
    quantity: Decimal,
    purchase_entity: str,
    supplier: str,
    domestic_source: str,
    purchase_unit_price: Decimal | str,
) -> CustomsRow:
    ratio = _quantity_ratio(item, quantity)
    box_no = _display_box_no(item.box_no)
    box_count = item.box_count if item.source == "overseas" else (_box_count_from_box_no(box_no) or item.box_count)
    total_gross_weight = _row_gross_weight(item, sku_info, quantity, ratio)
    total_net_weight = _row_net_weight(item, sku_info, quantity, ratio, total_gross_weight, box_count)
    volume = _row_volume(item, sku_info, ratio)
    product_name = item.product_name or sku_info.product_name
    unit = sku_info.unit or _unit_from_product_name(product_name)
    pieces = _pieces_from_product_name(product_name, unit, item.pieces)

    return CustomsRow(
        id=_row_id(item.shipment_no, item.sku, box_no),
        shipment_date=_shipment_month(item.shipment_date),
        shipment_day=_shipment_day(item.shipment_date),
        shipment_no=item.shipment_no,
        seller_name=item.seller_name,
        purchase_entity=purchase_entity,
        supplier=supplier,
        domestic_source=domestic_source,
        sku=item.sku,
        pieces=pieces,
        product_name=product_name,
        customs_name_cn=sku_info.customs_name_cn or PENDING,
        customs_name_en=sku_info.customs_name_en,
        unit=unit or PENDING,
        shipment_quantity=quantity * pieces,
        purchase_unit_price=purchase_unit_price,
        updated_at=item.updated_at,
        trade_term="FOB",
        payment_method_name="t/t",
        currency="美元",
        logistics_provider=item.logistics_provider,
        logistics_channel=item.logistics_channel,
        transport_method=item.transport_method,
        logistics_center_code=item.logistics_center_code,
        logistics_center_region=item.logistics_center_region,
        package_type="cnts",
        box_no=box_no,
        box_count=box_count,
        total_gross_weight=total_gross_weight,
        total_net_weight=total_net_weight,
        outer_box_size=item.outer_box_size or sku_info.outer_box_size or PENDING,
        volume=volume,
        source=item.source,
    )


def _calculate_volume(sku_info: SkuInfo, box_count: Decimal) -> Decimal | str:
    if sku_info.box_length_cm is None or sku_info.box_width_cm is None or sku_info.box_height_cm is None:
        return PENDING
    return (sku_info.box_length_cm * sku_info.box_width_cm * sku_info.box_height_cm * box_count) / Decimal("1000000")


def _shipment_month(value: str) -> str:
    if len(value) >= 7 and value[4:5] == "-":
        return value[:7]
    return value


def _shipment_day(value: str) -> str:
    if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        return value[:10]
    return value


def _row_id(shipment_no: str, sku: str, box_no: str) -> str:
    source = "|".join((shipment_no or "", sku or "", box_no or ""))
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def _display_box_no(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parts = [part.strip() for part in re.split(r"[\r\n,;/，；]+", text) if part.strip()]
    return ",".join(parts) if len(parts) > 1 else text


def _format_box_no(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", text):
        return text

    parts = [part for part in re.split(r"[\s,;/，；]+", text) if part]
    if not parts:
        return text

    numbers: list[int] = []
    for part in parts:
        match = re.search(r"(\d+)$", part)
        if not match:
            return text
        numbers.append(int(match.group(1)))

    return _compress_box_numbers(numbers)


def _compress_box_numbers(numbers: list[int]) -> str:
    if not numbers:
        return ""
    distinct = sorted(set(numbers))
    ranges: list[str] = []
    start = previous = distinct[0]
    for number in distinct[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(_format_number_range(start, previous))
        start = previous = number
    ranges.append(_format_number_range(start, previous))
    return ",".join(ranges)


def _format_number_range(start: int, end: int) -> str:
    if start == end:
        return str(start)
    return f"{start}-{end}"


def _box_count_from_box_no(box_no: str) -> Decimal | None:
    text = str(box_no or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", text):
        parts = [part for part in re.split(r"[\s,;/，；]+", text) if part]
        return Decimal(len(parts)) if len(parts) > 1 else None

    count = 0
    for part in text.split(","):
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if end < start:
                return None
            count += end - start + 1
        else:
            count += 1
    return Decimal(count)


def _quantity_ratio(item: ShipmentItem, quantity: Decimal) -> Decimal:
    if item.quantity == 0:
        return Decimal("1")
    return quantity / item.quantity


def _row_gross_weight(item: ShipmentItem, sku_info: SkuInfo, quantity: Decimal, ratio: Decimal) -> Decimal | str:
    if item.total_gross_weight is not None:
        return item.total_gross_weight
    if sku_info.gross_weight is not None:
        return sku_info.gross_weight * quantity
    return PENDING


def _row_net_weight(
    item: ShipmentItem,
    sku_info: SkuInfo,
    quantity: Decimal,
    ratio: Decimal,
    total_gross_weight: Decimal | str,
    box_count: Decimal | str,
) -> Decimal | str:
    if isinstance(total_gross_weight, Decimal):
        return total_gross_weight - box_count if isinstance(box_count, Decimal) else total_gross_weight
    if sku_info.net_weight is not None:
        return sku_info.net_weight * quantity
    return PENDING


def _row_volume(item: ShipmentItem, sku_info: SkuInfo, ratio: Decimal) -> Decimal | str:
    if item.volume is not None:
        return item.volume
    if not isinstance(item.box_count, Decimal):
        return PENDING
    return _calculate_volume(sku_info, item.box_count)


def _pieces_from_product_name(product_name: str, unit: str, fallback: Decimal) -> Decimal:
    text = product_name or ""
    units = [unit] if unit else []
    units.extend(_KNOWN_UNITS)
    for candidate_unit in units:
        if not candidate_unit:
            continue
        match = re.search(rf"(\d+(?:\.\d+)?)\s*{re.escape(candidate_unit)}", text)
        if match:
            return Decimal(match.group(1))
    return fallback


_KNOWN_UNITS = ["件", "个", "条", "套", "只", "双", "片", "包", "pcs", "PCS"]


def _unit_from_product_name(product_name: str) -> str:
    text = product_name or ""
    for candidate_unit in _KNOWN_UNITS:
        if re.search(rf"\d+(?:\.\d+)?\s*{re.escape(candidate_unit)}", text):
            return candidate_unit
    return ""


def _sort_and_zero_duplicate_box_metrics(rows: list[CustomsRow]) -> None:
    rows.sort(key=lambda row: (row.shipment_no, row.box_no, str(row.sku)))
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.shipment_no, row.box_no)
        if row.source == "overseas":
            continue
        if not row.box_no or key not in seen:
            if row.box_no:
                seen.add(key)
            continue
        row.box_count = Decimal("0")
        row.total_gross_weight = Decimal("0")
        row.total_net_weight = Decimal("0")
        row.outer_box_size = "0"
        row.volume = Decimal("0")
