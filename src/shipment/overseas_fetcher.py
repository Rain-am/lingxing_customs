from __future__ import annotations

import os
import json
import re
from datetime import datetime
from decimal import Decimal
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.common.lingxing_client import LingxingClient, LingxingClientError
from src.shipment.build_rows import _format_box_no
from src.shipment.fetcher import (
    LingxingApiDataSource,
    _as_list,
    _date_text,
    _extract_rows,
    _first,
    _first_matching_any,
    _optional_decimal,
)
from src.shipment.models import PurchaseBatch, RawCustomsData, ShipmentItem, SkuInfo, decimal_or_zero


class OverseasWarehouseApiDataSource:
    def __init__(self, client: LingxingClient | None = None, refresh_cache: bool = False) -> None:
        self.client = client or LingxingClient()
        self.inbound_list_endpoint = os.getenv("LINGXING_OVERSEAS_INBOUND_LIST_ENDPOINT", "/erp/sc/routing/owms/inbound/listInbound")
        self.stock_order_detail_endpoint = os.getenv(
            "LINGXING_OVERSEAS_STOCK_ORDER_DETAIL_ENDPOINT",
            "/basicOpen/overSeaWarehouse/stockOrder/detail",
        )
        self.awd_inbound_plan_detail_endpoint = os.getenv(
            "LINGXING_AWD_INBOUND_PLAN_DETAIL_ENDPOINT",
            "/amzStaServer/openapi/awd/inbound-plan/detail",
        )
        self.packing_data_endpoint = os.getenv("LINGXING_OVERSEAS_PACKING_DATA_ENDPOINT", "/erp/sc/routing/owms/inbound/getPackingData")
        self.supplier_list_endpoint = os.getenv("LINGXING_SUPPLIER_LIST_ENDPOINT", "/erp/sc/data/local_inventory/supplier")
        self.common_source = LingxingApiDataSource(client=self.client, refresh_cache=refresh_cache)

    def load(self, shipment_time: str | None = None) -> RawCustomsData:
        self._validate_config()
        headers = self._fetch_headers(shipment_time)
        shipment_items: list[ShipmentItem] = []
        purchase_batches: list[PurchaseBatch] = []
        sku_infos: dict[str, SkuInfo] = {}
        field_debug_rows: list[dict[str, Any]] = []

        for header in headers:
            detail = self._fetch_detail(header)
            detail_data = detail.get("data", detail)
            if shipment_time and _date_text(_first(detail_data, header, "real_delivery_time")) != shipment_time:
                continue
            packing_data = self._fetch_packing_data(header, detail_data)
            awd_center_codes = self._fetch_awd_center_codes(detail_data)
            items, batches, debug_rows = _map_overseas_detail(header, detail_data, packing_data, awd_center_codes)
            shipment_items.extend(items)
            purchase_batches.extend(batches)
            field_debug_rows.extend(debug_rows)
            for item in items:
                if item.sku and item.sku not in sku_infos:
                    sku_infos[item.sku] = SkuInfo(sku=item.sku)
        sku_infos.update(self.common_source._fetch_sku_infos(set(sku_infos)))
        purchase_batches = self._enrich_purchase_batches(purchase_batches)
        shipment_items = _enrich_items_from_batches(shipment_items, purchase_batches)
        return RawCustomsData(
            shipment_items=shipment_items,
            sku_infos=sku_infos,
            purchase_batches=purchase_batches,
            metadata={"shipment_source": "overseas", "overseas_field_debug_rows": field_debug_rows},
        )

    def _fetch_headers(self, shipment_time: str | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        length = min(_client_page_size(self.client), 1000)
        while True:
            payload: dict[str, Any] = {"offset": offset, "length": length}
            if shipment_time:
                payload.update(
                    {
                        "start_date": shipment_time,
                        "end_date": shipment_time,
                        "search_field_time": "real_delivery_time",
                    }
                )
            data = self.client.post(self.inbound_list_endpoint, payload)
            items = _extract_rows(data)
            rows.extend(items)
            if len(items) < length:
                return rows
            offset += length
            if offset > 20000:
                return rows

    def _fetch_detail(self, header: dict[str, Any]) -> dict[str, Any]:
        for request_body in _detail_request_bodies(header):
            try:
                return self.client.post(self.stock_order_detail_endpoint, request_body)
            except LingxingClientError as exc:
                if _is_parameter_error(exc):
                    continue
                raise
        return {"data": header}

    def _fetch_packing_data(self, header: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
        for request_body in _packing_request_bodies(header, detail):
            try:
                return self.client.post(self.packing_data_endpoint, request_body)
            except LingxingClientError as exc:
                if _is_parameter_error(exc):
                    continue
                return {}
        return {}

    def _fetch_awd_center_codes(self, detail: dict[str, Any]) -> dict[str, str]:
        awd_ids = _awd_shipment_ids(detail)
        center_codes: dict[str, str] = {}
        for awd_id in awd_ids:
            for request_body in _awd_request_bodies(awd_id):
                try:
                    data = self.client.post(self.awd_inbound_plan_detail_endpoint, request_body)
                except LingxingClientError as exc:
                    if _is_parameter_error(exc):
                        continue
                    break
                center_codes.update(_awd_center_codes(data))
                if awd_id in center_codes:
                    break
        return center_codes

    def _fetch_supplier_infos(self) -> dict[str, dict[str, str]]:
        return self.common_source._fetch_supplier_infos()

    def _enrich_purchase_batches(self, batches: list[PurchaseBatch]) -> list[PurchaseBatch]:
        if not batches:
            return []
        purchase_orders = self.common_source._fetch_purchase_orders({batch.purchase_sn or batch.purchase_order_no for batch in batches})
        supplier_infos = self._fetch_supplier_infos()
        enriched: list[PurchaseBatch] = []
        order_keys = ("purchase_sn", "purchase_order_no", "po_no", "order_sn", "custom_order_sn", "alibaba_order_sn")
        for batch in batches:
            purchase_sn = batch.purchase_sn or batch.purchase_order_no
            purchase_order = purchase_orders.get(purchase_sn, {})
            if not purchase_order and purchase_sn:
                purchase_order = _first_matching_any(purchase_orders.values(), order_keys, purchase_sn) or {}
            supplier_key = str(_first(purchase_order, {}, "supplier_name", "supplier", "supplierName") or batch.supplier)
            supplier_info = supplier_infos.get(supplier_key, {})
            enriched.append(
                replace(
                    batch,
                    supplier=str(supplier_info.get("account_name") or supplier_key),
                    domestic_source=str(supplier_info.get("url") or batch.domestic_source),
                    purchase_order_no=str(_first(purchase_order, {}, "purchase_sn", "purchase_order_no", "po_no") or purchase_sn),
                    purchase_sn=purchase_sn,
                )
            )
        return enriched

    def _fetch_offset_rows(self, endpoint: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        length = _client_page_size(self.client)
        while True:
            data = self.client.post(endpoint, {"offset": offset, "length": length})
            items = _extract_rows(data)
            rows.extend(items)
            if len(items) < length:
                return rows
            offset += length

    def _validate_config(self) -> None:
        missing = [
            name
            for name, value in (
                ("LINGXING_OVERSEAS_INBOUND_LIST_ENDPOINT", self.inbound_list_endpoint),
                ("LINGXING_OVERSEAS_STOCK_ORDER_DETAIL_ENDPOINT", self.stock_order_detail_endpoint),
                ("LINGXING_AWD_INBOUND_PLAN_DETAIL_ENDPOINT", self.awd_inbound_plan_detail_endpoint),
                ("LINGXING_OVERSEAS_PACKING_DATA_ENDPOINT", self.packing_data_endpoint),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Real Lingxing overseas endpoints are not configured: " + ", ".join(missing))


def _map_overseas_detail(
    header: dict[str, Any],
    detail: dict[str, Any],
    packing_data: dict[str, Any],
    awd_center_codes: dict[str, str],
) -> tuple[list[ShipmentItem], list[PurchaseBatch], list[dict[str, Any]]]:
    products = _product_rows(detail) or _product_rows(header)
    header_products = _products_by_sku(header)
    box_info = _packing_box_info(packing_data)
    shipment_no = str(_first(detail, header, "overseas_order_no", "order_no", "orderNo", "inbound_no", "inboundNo", "id") or "")
    shipment_date = _date_text(_first(detail, header, "real_delivery_time"))
    updated_at = _date_time_text(_first(header, detail, "update_time", "updated_at"))
    items: list[ShipmentItem] = []
    batches: list[PurchaseBatch] = []
    debug_rows: list[dict[str, Any]] = []

    for product in products:
        sku = str(_first(product, {}, "sku", "seller_sku", "local_sku") or "")
        product = _merge_product(product, header_products.get(sku, {}))
        product_box_info, box_info_matched = _box_info_for_product(box_info, sku)
        box_no, box_no_source = _box_no_with_source(product_box_info, header, box_info_matched)
        box_count = ""
        total_gross_weight, gross_weight_source = _box_gross_weight_with_source(product_box_info)
        total_box_volume = _optional_decimal(_first(product_box_info, {}, "total_box_volume", "totalBoxVolume", "volume", "cbm"))
        awd_shipment_id = str(_first(product, {}, "awd_shipment_id", "awdShipmentId", "shipmentId") or "")
        quantity, quantity_source = _product_quantity(product, detail)
        transport_method, transport_source = _overseas_transport_method_with_source(
            header,
            detail,
            str(_first(detail, header, "logistics_way_name", "logisticsWayName") or ""),
        )
        item = ShipmentItem(
            shipment_date=shipment_date,
            shipment_no=shipment_no,
            sku=sku,
            quantity=quantity,
            seller_name=_seller_name(product),
            product_name=str(_first(product, {}, "product_name", "productName", "name") or ""),
            updated_at=updated_at,
            box_no=box_no,
            box_count=box_count,
            logistics_provider=_logistics_provider(str(_first(detail, header, "logistics_name", "logisticsName") or "")),
            logistics_channel=str(_first(detail, header, "logistics_way_name", "logisticsWayName") or ""),
            transport_method=transport_method,
            logistics_center_code=awd_center_codes.get(awd_shipment_id, "") or _product_center_code(product, detail, header),
            volume=total_box_volume,
            total_gross_weight=total_gross_weight,
            outer_box_size=_outer_box_size(product_box_info),
            purchase_unit_price=_first_batch_price(product),
            supplier=_combined_batch_text(product, "supplier_names", "supplier_name", "supplier"),
            source="overseas",
        )
        items.append(item)
        batches.extend(_map_overseas_purchase_batches(item, product))
        debug_rows.append(
            {
                "shipment_no": shipment_no,
                "sku": sku,
                "quantity": str(quantity),
                "quantity_source": quantity_source,
                "box_no": box_no,
                "box_no_source": box_no_source,
                "gross_weight": str(total_gross_weight or ""),
                "gross_weight_source": gross_weight_source,
                "transport_method": transport_method,
                "transport_method_source": transport_source,
            }
        )
    return items, batches, debug_rows


def _enrich_items_from_batches(items: list[ShipmentItem], batches: list[PurchaseBatch]) -> list[ShipmentItem]:
    batches_by_item: dict[tuple[str, str, str], list[PurchaseBatch]] = {}
    for batch in batches:
        batches_by_item.setdefault((batch.shipment_no, batch.sku, batch.box_no or ""), []).append(batch)

    enriched: list[ShipmentItem] = []
    for item in items:
        item_batches = batches_by_item.get((item.shipment_no, item.sku, item.box_no or ""), [])
        if not item_batches:
            enriched.append(item)
            continue
        enriched.append(
            replace(
                item,
                supplier=_combined_nonempty(batch.supplier for batch in item_batches) or item.supplier,
                domestic_source=_combined_nonempty(batch.domestic_source for batch in item_batches) or item.domestic_source,
                purchase_unit_price=item_batches[0].purchase_unit_price or item.purchase_unit_price,
            )
        )
    return enriched


def _combined_nonempty(values) -> str:
    distinct: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in distinct:
            distinct.append(text)
    return " / ".join(distinct)


def _product_rows(detail: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _as_list(detail.get("products") or detail.get("product_list") or detail.get("items"))
    return [row for row in rows if isinstance(row, dict)]


def _products_by_sku(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for product in _product_rows(payload):
        sku = str(_first(product, {}, "sku", "seller_sku", "local_sku") or "")
        if sku and sku not in result:
            result[sku] = product
    return result


def _merge_product(detail_product: dict[str, Any], header_product: dict[str, Any]) -> dict[str, Any]:
    if not header_product:
        return detail_product
    merged = dict(header_product)
    merged.update({key: value for key, value in detail_product.items() if value not in (None, "", [])})
    if not merged.get("batch_record_list") and header_product.get("batch_record_list"):
        merged["batch_record_list"] = header_product["batch_record_list"]
    if not merged.get("batchRecordList") and header_product.get("batchRecordList"):
        merged["batchRecordList"] = header_product["batchRecordList"]
    return merged


def _seller_name(product: dict[str, Any]) -> str:
    sellers = _as_list(product.get("seller_arr") or product.get("sellerArr"))
    for seller in sellers:
        if isinstance(seller, dict):
            value = seller.get("seller_name") or seller.get("sellerName") or seller.get("sname")
            if value not in (None, ""):
                return str(value)
    return str(_first(product, {}, "seller_name", "sellerName", "sname") or "")


def _product_quantity(product: dict[str, Any], detail: dict[str, Any]) -> tuple[Decimal, str]:
    product_value = _first(product, {}, "stock_num", "stockNum", "package_num", "packageNum", "quantity", "qty", "num")
    if product_value not in (None, ""):
        return decimal_or_zero(product_value), "products.stock_num/package_num/quantity"
    total = detail.get("total")
    if isinstance(total, dict):
        total_stock_num = _first(total, {}, "stock_num", "stockNum")
        if total_stock_num not in (None, ""):
            return decimal_or_zero(total_stock_num), "total.stock_num"
    return Decimal("0"), "missing"


def _overseas_transport_method_with_source(header: dict[str, Any], detail: dict[str, Any], logistics_channel: str = "") -> tuple[str, str]:
    detail_transport = _transport_method_from_detail(detail)
    if detail_transport:
        return detail_transport, "detail.logisticsInfo.head_logistics_tracking_info.transport_type_name"
    header_transport = _transport_method_from_header(header)
    if header_transport:
        return header_transport, "listInbound.head_logistics_list.tracking_list.transport_type"
    channel_transport = _normalize_transport(logistics_channel)
    if channel_transport:
        return channel_transport, "logistics_way_name"
    return "", "missing"


def _transport_method_from_detail(detail: dict[str, Any]) -> str:
    logistics_info = detail.get("logisticsInfo") or detail.get("logistics_info") or {}
    tracking_rows = []
    if isinstance(logistics_info, dict):
        tracking_rows = _as_list(logistics_info.get("head_logistics_tracking_info") or logistics_info.get("headLogisticsTrackingInfo"))
    candidates: list[str] = []
    for tracking in tracking_rows:
        if not isinstance(tracking, dict):
            continue
        normalized = _normalize_transport(str(tracking.get("transport_type_name") or tracking.get("transportTypeName") or ""))
        if normalized:
            candidates.append(normalized)
    for candidate in candidates:
        if candidate == "\u6d77\u8fd0":
            return candidate
    if candidates:
        return candidates[0]
    return ""


def _transport_method_from_header(header: dict[str, Any]) -> str:
    logistics_rows = _as_list(header.get("head_logistics_list") or header.get("headLogisticsList"))
    candidates: list[str] = []
    for logistics in logistics_rows:
        if not isinstance(logistics, dict):
            continue
        tracking_rows = _as_list(
            logistics.get("tracking_list")
            or logistics.get("trackingList")
            or logistics.get("track_list")
            or logistics.get("trackList")
        )
        for tracking in tracking_rows:
            if not isinstance(tracking, dict):
                continue
            transport = _transport_type_value_name(tracking.get("transport_type") or tracking.get("transportType"))
            if transport:
                candidates.append(transport)
    for candidate in candidates:
        if candidate == "\u6d77\u8fd0":
            return candidate
    return candidates[0] if candidates else ""


def _transport_type_value_name(value: Any) -> str:
    text = str(value or "").strip()
    if text == "1":
        return "\u5feb\u9012"
    if text == "2":
        return "\u6d77\u8fd0"
    if text == "3":
        return "\u7a7a\u8fd0"
    if text == "4":
        return "\u5176\u4ed6"
    return ""


def _normalize_transport(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lower_text = text.lower()
    if "\u6d77" in text or "\u7f8e\u68ee" in text or "sea" in lower_text or "ocean" in lower_text:
        return "\u6d77\u8fd0"
    if "\u9646" in text or "land" in lower_text or "truck" in lower_text:
        return "\u9646\u8fd0"
    if "\u7a7a" in text or "air" in lower_text:
        return "\u7a7a\u8fd0"
    return text


def _logistics_provider(value: str) -> str:
    text = str(value or "").strip()
    match = re.search(r"[（(]([^()（）]+)[）)]", text)
    if match:
        return match.group(1).strip()
    return text


def _product_center_code(product: dict[str, Any], detail: dict[str, Any], header: dict[str, Any]) -> str:
    return str(
        _first(product, detail, "warehouseReferenceId", "warehouse_reference_id", "logistics_center_code", "center_id", "warehouse_code")
        or _first(header, {}, "warehouseReferenceId", "warehouse_reference_id", "logistics_center_code", "center_id", "warehouse_code")
        or ""
    )


def _total_package_num(detail: dict[str, Any]) -> Any:
    total = detail.get("total")
    if isinstance(total, dict):
        return _first(total, {}, "package_num", "packageNum")
    return None


def _map_overseas_purchase_batches(item: ShipmentItem, product: dict[str, Any]) -> list[PurchaseBatch]:
    rows = _as_list(product.get("batch_record_list") or product.get("batchRecordList"))
    batches: list[PurchaseBatch] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        purchase_sns = _batch_purchase_sns(row)
        supplier = _combined_batch_text({"batch_record_list": [row]}, "supplier_names", "supplier_name", "supplier")
        price = _optional_decimal(_first(row, {}, "unit_storage_cost", "unitStorageCost", "unit_purchase_price", "purchase_unit_price"))
        quantity = decimal_or_zero(_first(row, {}, "outbound_num", "quantity", "qty", "num") or item.quantity)
        if not purchase_sns:
            purchase_sns = [""]
        for index, purchase_sn in enumerate(purchase_sns):
            batches.append(
                PurchaseBatch(
                    shipment_no=item.shipment_no,
                    sku=item.sku,
                    box_no=item.box_no,
                    quantity=quantity if len(purchase_sns) == 1 else quantity / Decimal(len(purchase_sns)),
                    supplier=supplier,
                    purchase_order_no=str(purchase_sn),
                    purchase_sn=str(purchase_sn),
                    purchase_unit_price=price or item.purchase_unit_price,
                )
            )
    if not batches and item.supplier:
        batches.append(
            PurchaseBatch(
                shipment_no=item.shipment_no,
                sku=item.sku,
                box_no=item.box_no,
                quantity=item.quantity,
                supplier=item.supplier,
                purchase_unit_price=item.purchase_unit_price,
            )
        )
    return batches


def _batch_purchase_sns(row: dict[str, Any]) -> list[str]:
    values = _as_list(
        row.get("purchase_order_sns")
        or row.get("custom_purchase_order_sns")
        or row.get("purchase_sns")
        or row.get("purchaseSn")
        or row.get("purchase_sn")
    )
    distinct: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in distinct:
            distinct.append(text)
    return distinct


def _first_batch_price(product: dict[str, Any]) -> Decimal | None:
    for row in _as_list(product.get("batch_record_list") or product.get("batchRecordList")):
        if not isinstance(row, dict):
            continue
        price = _optional_decimal(_first(row, {}, "unit_storage_cost", "unitStorageCost", "unit_purchase_price", "purchase_unit_price"))
        if price is not None:
            return price
    return None


def _combined_batch_text(product: dict[str, Any], *keys: str) -> str:
    distinct: list[str] = []
    for row in _as_list(product.get("batch_record_list") or product.get("batchRecordList")):
        if not isinstance(row, dict):
            continue
        for key in keys:
            for value in _as_list(row.get(key)):
                text = str(value or "").strip()
                if text and text not in distinct:
                    distinct.append(text)
    return " / ".join(distinct)


def _packing_box_info(packing_data: dict[str, Any]) -> list[dict[str, Any]]:
    payload = packing_data.get("data", packing_data)
    if not isinstance(payload, dict):
        return []
    box_data = payload.get("box_data") or payload.get("boxData") or payload
    rows: list[dict[str, Any]] = []
    for content in _as_list(box_data.get("box_content") if isinstance(box_data, dict) else None):
        if not isinstance(content, dict):
            continue
        box_info = content.get("boxInfo") or content.get("box_info") or {}
        box_list = content.get("box_list") or content.get("boxList") or []
        merged = dict(content)
        if isinstance(box_info, dict):
            merged.update(box_info)
        root_box_count = _first(payload, {}, "box_count", "boxCount")
        if root_box_count not in (None, "") and "box_count" not in merged:
            merged["box_count"] = root_box_count
        if isinstance(box_list, list):
            merged["box_list"] = box_list
        rows.append(merged)
    if rows:
        return rows
    box_list = payload.get("box_list") or payload.get("boxList") or []
    if isinstance(box_list, list):
        return [{"box_list": box_list, "box_count": payload.get("box_count") or payload.get("boxCount")}]
    return []


def _box_info_for_product(box_infos: list[dict[str, Any]], sku: str) -> tuple[dict[str, Any], bool]:
    for box_info in box_infos:
        if _box_info_contains_sku(box_info, sku):
            return box_info, True
    return (box_infos[0], False) if box_infos else ({}, False)


def _box_info_contains_sku(box_info: dict[str, Any], sku: str) -> bool:
    if not sku:
        return False
    for value in _walk_values(box_info):
        if str(value or "") == sku:
            return True
    return False


def _walk_values(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)
    else:
        yield value


def _box_no(box_info: dict[str, Any]) -> str:
    values: list[str] = []
    for box in _as_list(box_info.get("box_list") or box_info.get("boxList")):
        if not isinstance(box, dict):
            continue
        value = _first(box, {}, "box_no", "boxNo", "carton_no", "case_no")
        if value not in (None, ""):
            values.append(str(value))
    if values:
        return "\n".join(values)
    return str(_first(box_info, {}, "box_no", "boxNo", "carton_no", "case_no") or "")


def _box_no_with_source(box_info: dict[str, Any], header: dict[str, Any], box_info_matched: bool) -> tuple[str, str]:
    packing_box_no = _format_box_no(_box_no(box_info))
    if packing_box_no and box_info_matched:
        return packing_box_no, "packing.box_list.box_no"
    header_box_no = _box_no_from_header_tracking(header)
    if header_box_no:
        return header_box_no, "listInbound.head_logistics_list.tracking_list.order_type_code=3"
    if packing_box_no:
        return packing_box_no, "packing.box_list.box_no_unmatched"
    return "", "missing"


def _box_no_from_header_tracking(header: dict[str, Any]) -> str:
    values: list[str] = []
    for logistics in _as_list(header.get("head_logistics_list") or header.get("headLogisticsList")):
        if not isinstance(logistics, dict):
            continue
        tracking_rows = _as_list(
            logistics.get("tracking_list")
            or logistics.get("trackingList")
            or logistics.get("track_list")
            or logistics.get("trackList")
        )
        for tracking in tracking_rows:
            if not isinstance(tracking, dict):
                continue
            if str(tracking.get("order_type_code") or tracking.get("orderTypeCode") or "").strip() != "3":
                continue
            value = _first(
                tracking,
                {},
                "box_no",
                "boxNo",
                "carton_no",
                "cartonNo",
                "case_no",
                "caseNo",
                "tracking_number",
                "trackingNumber",
                "order_no",
                "orderNo",
                "number",
                "no",
            )
            if value not in (None, ""):
                values.extend(str(part) for part in _as_list(value))
    return _format_box_no("\n".join(values)) if values else ""


def _box_gross_weight_with_source(box_info: dict[str, Any]) -> tuple[Decimal | None, str]:
    weights: list[Decimal] = []
    for box in _as_list(box_info.get("box_list") or box_info.get("boxList")):
        if not isinstance(box, dict):
            continue
        weight = _optional_decimal(_first(box, {}, "weight", "box_weight", "boxWeight", "total_box_weight", "totalBoxWeight"))
        if weight is not None:
            weights.append(weight)
    if weights:
        return sum(weights, Decimal("0")), "packing.box_list.weight"
    fallback = _optional_decimal(_first(box_info, {}, "total_box_weight", "totalBoxWeight", "box_weight", "boxWeight", "weight"))
    if fallback is not None:
        return fallback, "packing.boxInfo.total_box_weight/weight"
    return None, "missing"


def _outer_box_size(box_info: dict[str, Any]) -> str:
    box = {}
    box_list = _as_list(box_info.get("box_list") or box_info.get("boxList"))
    if box_list and isinstance(box_list[0], dict):
        box = box_list[0]
    length = _first(box, box_info, "length", "box_length", "boxLength")
    width = _first(box, box_info, "width", "box_width", "boxWidth")
    height = _first(box, box_info, "height", "box_height", "boxHeight")
    if length in (None, "") or width in (None, "") or height in (None, ""):
        return ""
    return f"{length}*{width}*{height}"


def _awd_shipment_ids(detail: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for product in _product_rows(detail):
        value = _first(product, {}, "awd_shipment_id", "awdShipmentId", "shipmentId")
        if value not in (None, "") and str(value) not in ids:
            ids.append(str(value))
    return ids


def _awd_center_codes(data: dict[str, Any]) -> dict[str, str]:
    payload = data.get("data", data)
    rows = []
    if isinstance(payload, dict):
        rows = _as_list(payload.get("awdShipmentVOS") or payload.get("awd_shipment_vos") or payload.get("list"))
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        shipment_id = str(row.get("shipmentId") or row.get("shipment_id") or "")
        center_code = str(row.get("warehouseReferenceId") or row.get("warehouse_reference_id") or "")
        if shipment_id and center_code:
            result[shipment_id] = center_code
    return result


def _detail_request_bodies(header: dict[str, Any]) -> list[dict[str, Any]]:
    return _candidate_request_bodies(
        header,
        ("overseas_order_no", "order_no", "orderNo", "inbound_no", "inboundNo", "id"),
        ("overseas_order_no", "order_no", "orderNo", "inbound_no", "inboundNo", "id"),
    )


def _packing_request_bodies(header: dict[str, Any], detail: dict[str, Any]) -> list[dict[str, Any]]:
    values = dict(header, **detail)
    return _candidate_request_bodies(
        values,
        ("overseas_order_no", "order_no", "orderNo", "inbound_no", "inboundNo", "id"),
        ("overseas_order_no", "order_no", "orderNo", "inbound_no", "inboundNo", "id"),
    )


def _awd_request_bodies(awd_id: str) -> list[dict[str, Any]]:
    bodies = []
    for key in ("shipmentId", "shipment_id", "awd_shipment_id", "awdShipmentId", "id", "shipment_id_list", "shipmentIdList"):
        if key in ("shipment_id_list", "shipmentIdList"):
            bodies.append({key: [awd_id]})
            continue
        bodies.append({key: awd_id})
    return bodies


def _candidate_request_bodies(source: dict[str, Any], value_keys: tuple[str, ...], request_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    values: list[Any] = []
    for key in value_keys:
        value = source.get(key)
        if value not in (None, "") and value not in values:
            values.append(value)
    bodies: list[dict[str, Any]] = []
    for value in values:
        for key in request_keys:
            body = {key: value}
            if body not in bodies:
                bodies.append(body)
    return bodies


def _date_time_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if len(text) >= 19 and text[4:5] == "-" and text[7:8] == "-":
        return text[:19]
    if not text.isdigit():
        return text
    timestamp = int(text)
    if len(text) >= 13:
        timestamp = timestamp // 1000
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return text


def write_overseas_field_debug(raw_data: RawCustomsData) -> None:
    debug_dir = os.getenv("LINGXING_DEBUG_DIR", "")
    if not debug_dir:
        return
    rows = raw_data.metadata.get("overseas_field_debug_rows")
    if not isinstance(rows, list) or not rows:
        return
    enriched_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku") or "")
        sku_info = raw_data.sku_infos.get(sku)
        customs_name_cn = sku_info.customs_name_cn if sku_info else ""
        enriched = dict(row)
        enriched["customs_name_cn"] = customs_name_cn
        enriched["customs_name_cn_source"] = "customs_product/productInfo" if customs_name_cn else "missing"
        enriched_rows.append(enriched)
    path = Path(debug_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "overseas_field_sources.json").write_text(
        json.dumps(enriched_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _client_page_size(client: Any) -> int:
    config = getattr(client, "config", None)
    page_size = getattr(config, "page_size", 100)
    try:
        value = int(page_size)
    except (TypeError, ValueError):
        return 100
    return value if value > 0 else 100


def _is_parameter_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "参数" in text or "parameter" in text or "code': 102" in text or '"code": 102' in text
