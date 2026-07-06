from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


def decimal_or_zero(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


@dataclass(frozen=True)
class ShipmentItem:
    shipment_date: str
    shipment_no: str
    sku: str
    quantity: Decimal
    seller_name: str = ""
    product_name: str = ""
    updated_at: str = ""
    box_no: str = ""
    box_count: Decimal | str = Decimal("1")
    pieces: Decimal = Decimal("1")
    logistics_provider: str = ""
    logistics_channel: str = ""
    transport_method: str = ""
    logistics_center_code: str = ""
    volume: Decimal | None = None
    total_gross_weight: Decimal | None = None
    outer_box_size: str = ""
    purchase_unit_price: Decimal | None = None
    purchase_entity: str = ""
    supplier: str = ""
    domestic_source: str = ""
    source: str = "amazon"


@dataclass(frozen=True)
class SkuInfo:
    sku: str
    product_name: str = ""
    customs_name_cn: str = ""
    customs_name_en: str = ""
    unit: str = ""
    package_type: str = ""
    gross_weight: Decimal | None = None
    net_weight: Decimal | None = None
    outer_box_size: str = ""
    box_length_cm: Decimal | None = None
    box_width_cm: Decimal | None = None
    box_height_cm: Decimal | None = None


@dataclass(frozen=True)
class PurchaseBatch:
    shipment_no: str
    sku: str
    quantity: Decimal
    purchase_entity: str = ""
    supplier: str = ""
    domestic_source: str = ""
    purchase_order_no: str = ""
    purchase_sn: str = ""
    batch_no: str = ""
    purchase_unit_price: Decimal | None = None
    box_no: str = ""
    quantity_missing: bool = False


@dataclass
class RawCustomsData:
    shipment_items: list[ShipmentItem] = field(default_factory=list)
    sku_infos: dict[str, SkuInfo] = field(default_factory=dict)
    purchase_batches: list[PurchaseBatch] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CustomsRow:
    id: str
    shipment_date: str
    shipment_day: str
    shipment_no: str
    seller_name: str
    purchase_entity: str
    supplier: str
    domestic_source: str
    sku: str
    pieces: Decimal
    product_name: str
    customs_name_cn: str
    customs_name_en: str
    unit: str
    shipment_quantity: Decimal
    purchase_unit_price: Decimal | str
    updated_at: str
    trade_term: str
    payment_method_name: str
    currency: str
    logistics_provider: str
    logistics_channel: str
    transport_method: str
    logistics_center_code: str
    package_type: str
    box_no: str
    box_count: Decimal | str
    total_gross_weight: Decimal | str
    total_net_weight: Decimal | str
    outer_box_size: str
    volume: Decimal | str
    source: str = ""


@dataclass(frozen=True)
class IssueRow:
    shipment_no: str
    box_no: str
    sku: str
    field_name: str
    issue: str


@dataclass(frozen=True)
class PurchaseSplitRow:
    shipment_no: str
    box_no: str
    sku: str
    purchase_order_no: str
    batch_no: str
    supplier: str
    purchase_entity: str
    quantity: Decimal
    purchase_unit_price: Decimal | str


@dataclass
class CustomsWorkbookData:
    customs_rows: list[CustomsRow]
    issue_rows: list[IssueRow]
    purchase_split_rows: list[PurchaseSplitRow]
