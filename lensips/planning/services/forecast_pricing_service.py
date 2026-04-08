from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import frappe
from frappe.utils import flt, getdate


@dataclass
class ForecastPrice:
	price_list: str | None = None
	price_list_rate: float = 0.0
	currency: str | None = None
	valid_from: date | None = None
	valid_upto: date | None = None
	uom: str | None = None


def get_customer_default_price_list(customer: str | None) -> str | None:
	if not customer:
		return None

	return frappe.db.get_value("Customer", customer, "default_price_list")


def get_effective_item_price(
	item_code: str | None,
	customer: str | None = None,
	price_list: str | None = None,
	period_start: date | str | None = None,
	period_end: date | str | None = None,
	uom: str | None = None,
) -> ForecastPrice:
	"""Return the item price that is valid for the requested forecast period."""

	if not item_code:
		return ForecastPrice()

	price_list = price_list or get_customer_default_price_list(customer)
	if not price_list:
		return ForecastPrice()

	period_start = getdate(period_start) if period_start else None
	period_end = getdate(period_end) if period_end else period_start

	selected = _get_matching_price_row(item_code, price_list, period_start, period_end, uom)
	if not selected and uom:
		selected = _get_matching_price_row(item_code, price_list, period_start, period_end, None)

	if not selected:
		return ForecastPrice(price_list=price_list)

	return ForecastPrice(
		price_list=price_list,
		price_list_rate=flt(selected.price_list_rate),
		currency=selected.currency,
		valid_from=getdate(selected.valid_from) if selected.valid_from else None,
		valid_upto=getdate(selected.valid_upto) if selected.valid_upto else None,
		uom=selected.uom,
	)


def _get_matching_price_row(item_code, price_list, period_start, period_end, uom):
	filters = {
		"item_code": item_code,
		"price_list": price_list,
	}
	if uom:
		filters["uom"] = uom

	rows = frappe.get_all(
		"Item Price",
		filters=filters,
		fields=["name", "price_list_rate", "currency", "valid_from", "valid_upto", "uom"],
		order_by="valid_from desc, creation desc",
	)

	for row in rows:
		if _row_overlaps_period(row, period_start, period_end):
			return row

	return rows[0] if rows else None


def _row_overlaps_period(row, period_start, period_end):
	period_start = getdate(period_start) if period_start else None
	period_end = getdate(period_end) if period_end else period_start

	if period_start and period_end:
		if row.valid_from and getdate(row.valid_from) > period_end:
			return False
		if row.valid_upto and getdate(row.valid_upto) < period_start:
			return False

	return True
