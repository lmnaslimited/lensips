from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from lensips.planning.services.forecast_pricing_service import (
	get_customer_default_price_list,
	get_effective_item_price,
)


def create_sales_forecast(data, filters):
	parent_meta, child_meta, detail_meta, _child_doctype = get_forecast_meta()
	clean_filters = normalize_filters(filters)
	start_date = compute_forecast_start_date(clean_filters["to_date"], clean_filters["periodicity"])
	frequency = get_frequency(clean_filters["periodicity"])
	demand_number = cint(clean_filters["forecast_periods"])
	clean_rows = clean_report_rows(data, start_date)

	if not clean_rows:
		frappe.throw(_("No future forecast rows with positive forecast quantity were found to export."))

	item_codes = sorted({row["item_code"] for row in clean_rows})
	item_details = get_item_details(item_codes)
	grouped_rows = group_forecast_rows(clean_rows)
	parent_warehouse = resolve_parent_warehouse(clean_filters, grouped_rows)
	forecast_doc = get_existing_forecast(start_date, clean_filters["company"], parent_warehouse)
	existing_items = get_existing_item_map(forecast_doc) if forecast_doc else {}
	detail_rows = build_detail_rows(data, clean_filters, detail_meta)

	if forecast_doc and forecast_doc.docstatus == 1:
		frappe.throw(
			_(
				"Sales Forecast {0} is already submitted for company {1}, warehouse {2}, and start date {3}. "
				"Please cancel/amend it or choose a different export window."
			).format(frappe.bold(forecast_doc.name), clean_filters["company"], parent_warehouse, start_date)
		)

	selected_items = build_selected_items(item_codes, item_details, child_meta)
	child_rows = build_child_rows(
		grouped_rows=grouped_rows,
		item_details=item_details,
		existing_items=existing_items,
		child_meta=child_meta,
	)

	if not child_rows:
		frappe.throw(_("No exportable forecast rows remained after applying lock rules."))

	if forecast_doc:
		forecast_doc.set("selected_items", [])
		forecast_doc.set("items", [])
		if detail_meta:
			forecast_doc.set("forecast_entries", [])
	else:
		forecast_doc = frappe.new_doc(parent_meta.name)

	apply_parent_values(
		doc=forecast_doc,
		parent_meta=parent_meta,
		company=clean_filters["company"],
		parent_warehouse=parent_warehouse,
		start_date=start_date,
		frequency=frequency,
		demand_number=demand_number,
	)

	for row in selected_items:
		forecast_doc.append("selected_items", row)

	for row in child_rows:
		forecast_doc.append("items", row)

	if detail_meta:
		for row in detail_rows:
			forecast_doc.append("forecast_entries", row)

	if forecast_doc.is_new():
		forecast_doc.insert()
		action = "created"
	else:
		forecast_doc.save()
		action = "updated"

	return {
		"forecast_name": forecast_doc.name,
		"total_items": len({row["item_code"] for row in child_rows}),
		"total_entries": len(detail_rows),
		"message": _("Sales Forecast {0} successfully {1}.").format(forecast_doc.name, action),
	}


def get_forecast_meta():
	parent_meta = frappe.get_meta("Sales Forecast")
	items_field = parent_meta.get_field("items")
	selected_items_field = parent_meta.get_field("selected_items")
	detail_field = parent_meta.get_field("forecast_entries")

	if not items_field or not items_field.options:
		frappe.throw(_("Sales Forecast child table configuration is missing."))

	if not selected_items_field or not selected_items_field.options:
		frappe.throw(_("Sales Forecast selected_items configuration is missing."))

	child_doctype = items_field.options
	child_meta = frappe.get_meta(child_doctype)
	detail_meta = frappe.get_meta(detail_field.options) if detail_field and detail_field.options else None
	return parent_meta, child_meta, detail_meta, child_doctype


def normalize_filters(filters):
	filters = frappe._dict(filters or {})
	company = (filters.get("company") or "").strip()
	periodicity = (filters.get("periodicity") or "Monthly").strip()
	to_date = getdate(filters.get("to_date"))
	forecast_periods = cint(filters.get("forecast_periods"))

	if not company:
		frappe.throw(_("Company is required to export Sales Forecast."))
	if periodicity not in {"Weekly", "Monthly"}:
		frappe.throw(_("Sales Forecast export supports only Weekly and Monthly periodicity."))
	if not to_date:
		frappe.throw(_("To Date is required to compute the Sales Forecast start date."))
	if forecast_periods <= 0:
		frappe.throw(_("Forecast Periods must be greater than zero."))

	return frappe._dict(
		company=company,
		warehouse=(filters.get("warehouse") or "").strip(),
		periodicity=periodicity,
		to_date=to_date,
		forecast_periods=forecast_periods,
	)


def compute_forecast_start_date(to_date, periodicity):
	period_start = normalize_to_period(to_date, periodicity)
	return next_period(period_start, periodicity)


def get_frequency(periodicity):
	return "Weekly" if periodicity == "Weekly" else "Monthly"


def clean_report_rows(rows, start_date):
	clean_rows = []
	for row in rows or []:
		row = frappe._dict(row or {})
		item_code = (row.get("item_code") or "").strip()
		forecast_qty = flt(row.get("forecast_qty"))
		period = getdate(row.get("period"))

		if not item_code or forecast_qty <= 0 or not period or period < start_date:
			continue

		clean_rows.append(
			frappe._dict(
				item_code=item_code,
				period=period,
				forecast_qty=forecast_qty,
				forecast_value=flt(row.get("forecast_value")),
				warehouse=(row.get("warehouse") or "").strip(),
				is_locked=cint(row.get("is_locked")),
				customer=(row.get("customer") or "").strip() or None,
				item_group=(row.get("item_group") or "").strip() or None,
				sales_group=(row.get("sales_group") or "").strip() or None,
				group_key=(row.get("group_key") or "").strip() or None,
				item_name=(row.get("item_name") or "").strip() or None,
				period_label=(row.get("period_label") or "").strip() or None,
				price_list=(row.get("price_list") or "").strip() or None,
				price_list_rate=flt(row.get("price_list_rate")),
			)
		)

	return clean_rows


def group_forecast_rows(rows):
	grouped = defaultdict(lambda: {"forecast_qty": 0.0, "forecast_value": 0.0, "is_locked": 0})
	for row in rows or []:
		row = frappe._dict(row or {})
		key = (row.item_code, row.period, row.warehouse)
		grouped[key]["forecast_qty"] += flt(row.forecast_qty)
		grouped[key]["forecast_value"] += flt(row.forecast_value)
		grouped[key]["is_locked"] = max(grouped[key]["is_locked"], cint(row.is_locked))

	return grouped


def get_item_details(item_codes):
	if not item_codes:
		return {}

	items = frappe.get_all(
		"Item",
		filters={"name": ("in", item_codes)},
		fields=["name", "item_name", "stock_uom", "sales_uom"],
		limit_page_length=len(item_codes),
	)

	item_details = {}
	for item in items:
		item_details[item.name] = {
			"item_name": item.item_name,
			"uom": item.sales_uom or item.stock_uom,
		}

	missing_items = sorted(set(item_codes) - set(item_details))
	if missing_items:
		frappe.throw(_("These items do not exist in ERPNext: {0}").format(", ".join(missing_items)))

	return item_details


def resolve_parent_warehouse(filters, grouped_rows):
	if filters.warehouse:
		return filters.warehouse

	warehouses = sorted({warehouse for _, _, warehouse in grouped_rows if warehouse})
	if len(warehouses) == 1:
		return warehouses[0]

	frappe.throw(
		_(
			"Warehouse is required for Sales Forecast export. Please select a warehouse filter in the report."
		)
	)


def get_existing_forecast(start_date, company, parent_warehouse):
	existing_name = frappe.db.get_value(
		"Sales Forecast",
		{
			"from_date": start_date,
			"company": company,
			"parent_warehouse": parent_warehouse,
			"docstatus": ("!=", 2),
		},
		"name",
		order_by="modified desc",
	)
	return frappe.get_doc("Sales Forecast", existing_name) if existing_name else None


def get_existing_item_map(forecast_doc):
	item_map = {}
	for row in forecast_doc.get("items") or []:
		key = (row.item_code, getdate(row.delivery_date), row.warehouse or "")
		item_map[key] = row
	return item_map


def build_selected_items(item_codes, item_details, child_meta):
	selected_items = []
	required_defaults = get_required_child_defaults(child_meta)

	for item_code in item_codes:
		item = item_details[item_code]
		row = dict(required_defaults)
		row.update(
			{
				"item_code": item_code,
				"item_name": item.get("item_name"),
				"uom": item.get("uom"),
			}
		)
		selected_items.append(row)

	return selected_items


def build_detail_rows(rows, filters, detail_meta):
	if not detail_meta:
		return []

	detail_rows = []
	for row in rows or []:
		row = frappe._dict(row or {})
		period = getdate(row.get("period"))
		if not period:
			continue

		detail_rows.append(
			sanitize_child_row(
				{
					"company": filters["company"],
					"customer": row.get("customer"),
					"sales_group": row.get("sales_group"),
					"group_key": row.get("group_key"),
					"item_code": row.get("item_code"),
					"item_name": row.get("item_name"),
					"item_group": row.get("item_group"),
					"warehouse": row.get("warehouse"),
					"period": period,
					"period_label": row.get("period_label"),
					"actual_qty": flt(row.get("actual_qty")),
					"actual_value": flt(row.get("actual_value")),
					"forecast_qty": flt(row.get("forecast_qty")),
					"forecast_value": flt(row.get("forecast_value")),
					"adjust_qty": flt(row.get("adjust_qty")),
					"adjust_value": flt(row.get("adjust_value")),
					"demand_qty": flt(row.get("demand_qty")),
					"demand_value": flt(row.get("demand_value")),
					"price_list": row.get("price_list"),
					"price_list_rate": flt(row.get("price_list_rate")),
					"is_locked": cint(row.get("is_locked")),
				},
				detail_meta,
			)
		)

	return detail_rows


def build_child_rows(grouped_rows, item_details, existing_items, child_meta):
	child_rows = []
	has_adjust_qty = bool(child_meta.get_field("adjust_qty"))
	has_demand_qty = bool(child_meta.get_field("demand_qty"))

	for key in sorted(grouped_rows, key=lambda x: (x[1], x[0], x[2] or "")):
		item_code, delivery_date, warehouse = key
		row_data = grouped_rows[key]
		existing_row = existing_items.get(key)
		customer = row_data.get("customer")
		item_price = get_effective_item_price(
			item_code=item_code,
			customer=customer,
			price_list=get_customer_default_price_list(customer),
			period_start=delivery_date,
			period_end=delivery_date,
			uom=item_details[item_code].get("uom"),
		)
		price_rate = flt(item_price.price_list_rate)
		forecast_qty = rounded_quantity(row_data["forecast_qty"])
		forecast_value = rounded_value(forecast_qty * price_rate)
		adjust_qty = flt(getattr(existing_row, "adjust_qty", 0)) if existing_row else 0
		adjust_value = rounded_value(adjust_qty * price_rate)
		demand_qty = rounded_quantity(forecast_qty + adjust_qty)
		demand_value = rounded_value(demand_qty * price_rate)

		if row_data["is_locked"] and existing_row:
			saved_row = sanitize_child_row(existing_row, child_meta)
			saved_row.update(
				{
					"forecast_value": forecast_value,
					"adjust_value": adjust_value,
					"demand_value": demand_value,
				}
			)
			child_rows.append(saved_row)
			continue

		if row_data["is_locked"] and not existing_row:
			continue

		item = item_details[item_code]
		child_row = {
			"item_code": item_code,
			"item_name": item.get("item_name"),
			"uom": item.get("uom"),
			"delivery_date": delivery_date,
			"forecast_qty": forecast_qty,
			"forecast_value": forecast_value,
			"warehouse": warehouse or None,
		}

		if has_adjust_qty:
			child_row["adjust_qty"] = adjust_qty
			child_row["adjust_value"] = adjust_value

		if has_demand_qty:
			child_row["demand_qty"] = demand_qty
			child_row["demand_value"] = demand_value

		child_rows.append(child_row)

	return child_rows


def apply_parent_values(doc, parent_meta, company, parent_warehouse, start_date, frequency, demand_number):
	set_if_present(doc, parent_meta, "company", company)
	set_if_present(doc, parent_meta, "parent_warehouse", parent_warehouse)
	set_if_present(doc, parent_meta, "posting_date", nowdate())
	set_if_present(doc, parent_meta, "from_date", start_date)
	set_if_present(doc, parent_meta, "frequency", frequency)
	set_if_present(doc, parent_meta, "demand_number", demand_number)
	if parent_meta.get_field("status"):
		doc.status = "Planned"


def set_if_present(doc, meta, fieldname, value):
	if meta.get_field(fieldname):
		doc.set(fieldname, value)


def sanitize_child_row(row, child_meta):
	allowed_fields = {
		field.fieldname
		for field in child_meta.fields
		if getattr(field, "fieldname", None)
	}
	allowed_fields.update({"doctype"})

	row_dict = row.as_dict(no_nulls=False) if hasattr(row, "as_dict") else dict(row)
	return {key: value for key, value in row_dict.items() if key in allowed_fields}


def get_required_child_defaults(child_meta):
	defaults = {}
	for field in child_meta.fields:
		fieldname = getattr(field, "fieldname", None)
		if not fieldname or not getattr(field, "reqd", 0):
			continue
		if field.default not in (None, ""):
			defaults[fieldname] = field.default
			continue
		if field.fieldtype in {"Float", "Currency", "Percent", "Int"}:
			defaults[fieldname] = 0
		elif field.fieldtype == "Check":
			defaults[fieldname] = 0

	return defaults


def normalize_to_period(value, periodicity):
	value = getdate(value)
	if periodicity == "Weekly":
		return value - timedelta(days=value.weekday())
	return date(value.year, value.month, 1)


def next_period(period_start, periodicity):
	if periodicity == "Weekly":
		return period_start + timedelta(days=7)
	month = period_start.month + 1
	year = period_start.year
	if month == 13:
		month = 1
		year += 1
	return date(year, month, 1)


def rounded_quantity(value):
	return int(round(flt(value)))


def rounded_value(value):
	return round(flt(value), 2)


# New export implementation overrides the legacy single-document flow above.


def create_sales_forecast(data, filters):
	parent_meta, child_meta, detail_meta, _child_doctype = get_forecast_meta()
	clean_filters = normalize_filters(filters)
	start_date = compute_forecast_start_date(clean_filters["to_date"], clean_filters["periodicity"])
	frequency = get_frequency(clean_filters["periodicity"])
	demand_number = cint(clean_filters["forecast_periods"])
	clean_rows = clean_report_rows(data, start_date)

	if not clean_rows:
		frappe.throw(_("No future forecast rows with positive forecast quantity were found to export."))

	results = []
	rows_by_warehouse = group_rows_by_warehouse(clean_rows)

	for warehouse, warehouse_rows in rows_by_warehouse.items():
		item_codes = sorted({row["item_code"] for row in warehouse_rows})
		item_details = get_item_details(item_codes)
		grouped_rows = group_forecast_rows(warehouse_rows)
		parent_warehouse = resolve_parent_warehouse(clean_filters, warehouse, grouped_rows)
		forecast_doc = get_existing_forecast(start_date, clean_filters["company"], parent_warehouse)
		existing_items = get_existing_item_map(forecast_doc) if forecast_doc else {}

		if forecast_doc and forecast_doc.docstatus == 1:
			frappe.throw(
				_(
					"Sales Forecast {0} is already submitted for company {1}, warehouse {2}, and start date {3}. "
					"Please cancel/amend it or choose a different export window."
				).format(
					frappe.bold(forecast_doc.name),
					clean_filters["company"],
					parent_warehouse,
					start_date,
				)
			)

		selected_items = build_selected_items(item_codes, item_details, child_meta)
		child_rows = build_child_rows(
			grouped_rows=grouped_rows,
			item_details=item_details,
			existing_items=existing_items,
			child_meta=child_meta,
		)

		if not child_rows:
			continue

		if forecast_doc:
			forecast_doc.set("selected_items", [])
			forecast_doc.set("items", [])
			if detail_meta:
				forecast_doc.set("forecast_entries", [])
		else:
			forecast_doc = frappe.new_doc(parent_meta.name)

		apply_parent_values(
			doc=forecast_doc,
			parent_meta=parent_meta,
			company=clean_filters["company"],
			parent_warehouse=parent_warehouse,
			start_date=start_date,
			frequency=frequency,
			demand_number=demand_number,
		)

		for row in selected_items:
			forecast_doc.append("selected_items", row)

		for row in child_rows:
			forecast_doc.append("items", row)

		if forecast_doc.is_new():
			forecast_doc.insert()
			action = "created"
		else:
			forecast_doc.save()
			action = "updated"

		results.append(
			{
				"forecast_name": forecast_doc.name,
				"warehouse": parent_warehouse,
				"total_items": len({row["item_code"] for row in child_rows}),
				"message": _("Sales Forecast {0} successfully {1}.").format(forecast_doc.name, action),
			}
		)

	if not results:
		frappe.throw(_("No exportable forecast rows remained after applying warehouse grouping."))

	return {
		"forecast_name": results[0]["forecast_name"] if len(results) == 1 else None,
		"forecast_names": [row["forecast_name"] for row in results],
		"results": results,
		"total_items": sum(row["total_items"] for row in results),
		"total_entries": 0,
		"message": (
			results[0]["message"]
			if len(results) == 1
			else _("Sales Forecasts successfully created/updated for {0} warehouses.").format(len(results))
		),
	}


def normalize_filters(filters):
	filters = frappe._dict(filters or {})
	company = (filters.get("company") or "").strip()
	periodicity = (filters.get("periodicity") or "Monthly").strip()
	to_date = getdate(filters.get("to_date"))
	forecast_periods = cint(filters.get("forecast_periods"))

	if not company:
		frappe.throw(_("Company is required to export Sales Forecast."))
	if periodicity not in {"Weekly", "Monthly"}:
		frappe.throw(_("Sales Forecast export supports only Weekly and Monthly periodicity."))
	if not to_date:
		frappe.throw(_("To Date is required to compute the Sales Forecast start date."))
	if forecast_periods <= 0:
		frappe.throw(_("Forecast Periods must be greater than zero."))

	return frappe._dict(
		company=company,
		warehouse=(filters.get("warehouse") or "").strip() or None,
		periodicity=periodicity,
		to_date=to_date,
		forecast_periods=forecast_periods,
	)


def group_rows_by_warehouse(rows):
	grouped = defaultdict(list)
	for row in rows or []:
		warehouse = row.get("warehouse") or ""
		grouped[warehouse].append(row)
	return grouped


def clean_report_rows(rows, start_date):
	clean_rows = []
	for row in rows or []:
		row = frappe._dict(row or {})
		if row.get("row_type") and row.get("row_type") != "item":
			continue

		item_code = (row.get("item_code") or "").strip()
		warehouse = (row.get("warehouse") or "").strip()
		customer = (row.get("customer") or "").strip() or None
		is_locked = cint(row.get("is_locked"))

		period_fields = [key for key in row.keys() if key.startswith("forecast_qty_raw_")]
		if not period_fields:
			period_fields = [key for key in row.keys() if key.startswith("forecast_qty_")]

		for qty_field in period_fields:
			suffix = qty_field.split("forecast_qty_", 1)[1]
			period = parse_period_suffix(suffix)
			forecast_qty = flt(row.get(qty_field))
			if not item_code or forecast_qty <= 0 or not period or period < start_date:
				continue

			clean_rows.append(
				frappe._dict(
					item_code=item_code,
					period=period,
					forecast_qty=forecast_qty,
					forecast_value=flt(row.get(f"forecast_value_{suffix}")),
					warehouse=warehouse,
					is_locked=is_locked,
					customer=customer,
					item_group=(row.get("item_group") or "").strip() or None,
					group_key=(row.get("group_value") or "").strip() or None,
					item_name=(row.get("item_name") or "").strip() or None,
					period_label=(row.get("period_label") or "").strip() or None,
					price_list=(row.get("price_list") or "").strip() or None,
					price_list_rate=flt(row.get("price_list_rate")),
				)
			)

	return clean_rows


def parse_period_suffix(suffix):
	suffix = (suffix or "").strip()
	if not suffix:
		return None
	try:
		return getdate(suffix.replace("_", "-"))
	except Exception:
		return None


def group_forecast_rows(rows):
	grouped = defaultdict(lambda: {"forecast_qty": 0.0, "forecast_value": 0.0, "is_locked": 0})
	for row in rows or []:
		row = frappe._dict(row or {})
		key = (row.item_code, row.period, row.warehouse)
		grouped[key]["forecast_qty"] += flt(row.forecast_qty)
		grouped[key]["forecast_value"] += flt(row.forecast_value)
		grouped[key]["is_locked"] = max(grouped[key]["is_locked"], cint(row.is_locked))
	return grouped


def get_item_details(item_codes):
	if not item_codes:
		return {}

	items = frappe.get_all(
		"Item",
		filters={"name": ("in", item_codes)},
		fields=["name", "item_name", "stock_uom", "sales_uom"],
		limit_page_length=0,
	)

	item_details = {}
	for item in items:
		item_details[item.name] = {
			"item_name": item.item_name,
			"uom": item.sales_uom or item.stock_uom,
		}

	missing_items = sorted(set(item_codes) - set(item_details))
	if missing_items:
		frappe.throw(_("These items do not exist in ERPNext: {0}").format(", ".join(missing_items)))

	return item_details


def resolve_parent_warehouse(filters, warehouse, grouped_rows):
	if warehouse:
		return warehouse
	if filters.warehouse:
		return filters.warehouse

	warehouses = sorted({wh for _, _, wh in grouped_rows if wh})
	if len(warehouses) == 1:
		return warehouses[0]

	frappe.throw(
		_(
			"Warehouse is required for Sales Forecast export. Please select a warehouse filter in the report."
		)
	)


def get_existing_forecast(start_date, company, parent_warehouse):
	existing_name = frappe.db.get_value(
		"Sales Forecast",
		{
			"from_date": start_date,
			"company": company,
			"parent_warehouse": parent_warehouse,
			"docstatus": ("!=", 2),
		},
		"name",
		order_by="modified desc",
	)
	return frappe.get_doc("Sales Forecast", existing_name) if existing_name else None


def get_existing_item_map(forecast_doc):
	item_map = {}
	for row in forecast_doc.get("items") or []:
		key = (row.item_code, getdate(row.delivery_date), row.warehouse or "")
		item_map[key] = row
	return item_map


def build_selected_items(item_codes, item_details, child_meta):
	selected_items = []
	required_defaults = get_required_child_defaults(child_meta)
	for item_code in item_codes:
		item = item_details[item_code]
		row = dict(required_defaults)
		row.update({"item_code": item_code, "item_name": item.get("item_name"), "uom": item.get("uom")})
		selected_items.append(row)
	return selected_items


def build_detail_rows(rows, filters, detail_meta):
	return []


def build_child_rows(grouped_rows, item_details, existing_items, child_meta):
	child_rows = []
	has_adjust_qty = bool(child_meta.get_field("adjust_qty"))
	has_demand_qty = bool(child_meta.get_field("demand_qty"))

	for key in sorted(grouped_rows, key=lambda x: (x[1], x[0], x[2] or "")):
		item_code, delivery_date, warehouse = key
		row_data = grouped_rows[key]
		existing_row = existing_items.get(key)
		customer = row_data.get("customer")
		item_price = get_effective_item_price(
			item_code=item_code,
			customer=customer,
			price_list=get_customer_default_price_list(customer),
			period_start=delivery_date,
			period_end=delivery_date,
			uom=item_details[item_code].get("uom"),
		)
		price_rate = flt(item_price.price_list_rate)
		forecast_qty = rounded_quantity(row_data["forecast_qty"])
		forecast_value = rounded_value(forecast_qty * price_rate)
		adjust_qty = flt(getattr(existing_row, "adjust_qty", 0)) if existing_row else 0
		adjust_value = rounded_value(adjust_qty * price_rate)
		demand_qty = rounded_quantity(forecast_qty + adjust_qty)
		demand_value = rounded_value(demand_qty * price_rate)

		if row_data["is_locked"] and existing_row:
			saved_row = sanitize_child_row(existing_row, child_meta)
			saved_row.update(
				{"forecast_value": forecast_value, "adjust_value": adjust_value, "demand_value": demand_value}
			)
			child_rows.append(saved_row)
			continue

		if row_data["is_locked"] and not existing_row:
			continue

		item = item_details[item_code]
		child_row = {
			"item_code": item_code,
			"item_name": item.get("item_name"),
			"uom": item.get("uom"),
			"delivery_date": delivery_date,
			"forecast_qty": forecast_qty,
			"forecast_value": forecast_value,
			"warehouse": warehouse or None,
		}
		if has_adjust_qty:
			child_row["adjust_qty"] = adjust_qty
			child_row["adjust_value"] = adjust_value
		if has_demand_qty:
			child_row["demand_qty"] = demand_qty
			child_row["demand_value"] = demand_value
		child_rows.append(child_row)

	return child_rows


def apply_parent_values(doc, parent_meta, company, parent_warehouse, start_date, frequency, demand_number):
	set_if_present(doc, parent_meta, "company", company)
	set_if_present(doc, parent_meta, "parent_warehouse", parent_warehouse)
	set_if_present(doc, parent_meta, "posting_date", nowdate())
	set_if_present(doc, parent_meta, "from_date", start_date)
	set_if_present(doc, parent_meta, "frequency", frequency)
	set_if_present(doc, parent_meta, "demand_number", demand_number)
	if parent_meta.get_field("status"):
		doc.status = "Planned"
