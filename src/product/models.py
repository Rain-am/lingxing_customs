from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProductListRow:
    product_id: str
    sku: str
    update_time: str = ""
    batch_status: str = ""


@dataclass(frozen=True)
class ProductRow:
    sku: str
    product_name: str = ""
    material_cn: str = ""
    unit: str = ""
    customs_name_cn: str = ""
    customs_code: str = ""
    update_time: str = ""
    is_enabled: int = 0


@dataclass
class ProductLoadStats:
    product_list_raw_rows: int = 0
    product_list_rows: int = 0
    products_without_id: int = 0
    enabled_products: int = 0
    skipped_not_enabled: int = 0
    detail_request_count: int = 0
    detail_missing: int = 0
    empty_status_rows: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)


ProductPreviewRow = ProductRow
