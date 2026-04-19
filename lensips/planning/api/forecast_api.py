from __future__ import annotations

import frappe

from lensips.planning.services.forecast_export_service import create_sales_forecast


@frappe.whitelist()
def create_sales_forecast_from_report(data, filters):
	data = frappe.parse_json(data) or []
	filters = frappe.parse_json(filters) or {}

	result = create_sales_forecast(data=data, filters=filters)

	return {
		"forecast_name": result["forecast_name"],
		"forecast_names": result.get("forecast_names") or ([] if not result.get("forecast_name") else [result["forecast_name"]]),
		"results": result.get("results") or [],
		"total_items": result["total_items"],
		"total_entries": result.get("total_entries", 0),
		"message": result["message"],
	}
