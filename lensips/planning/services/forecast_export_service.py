from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate


def create_sales_forecast(data, filters):
	parent_meta, child_meta, _child_doctype = get_forecast_meta()
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

	return {
		"forecast_name": forecast_doc.name,
		"total_items": len({row["item_code"] for row in child_rows}),
		"message": _("Sales Forecast {0} successfully {1}.").format(forecast_doc.name, action),
	}


def get_forecast_meta():
	parent_meta = frappe.get_meta("Sales Forecast")
	items_field = parent_meta.get_field("items")
	selected_items_field = parent_meta.get_field("selected_items")

	if not items_field or not items_field.options:
		frappe.throw(_("Sales Forecast child table configuration is missing."))

	if not selected_items_field or not selected_items_field.options:
		frappe.throw(_("Sales Forecast selected_items configuration is missing."))

	child_doctype = items_field.options
	child_meta = frappe.get_meta(child_doctype)
	return parent_meta, child_meta, child_doctype


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
				warehouse=(row.get("warehouse") or "").strip(),
				is_locked=cint(row.get("is_locked")),
			)
		)

	return clean_rows


def group_forecast_rows(rows):
	grouped = defaultdict(lambda: {"forecast_qty": 0.0, "is_locked": 0})
	for row in rows:
		key = (row.item_code, row.period, row.warehouse)
		grouped[key]["forecast_qty"] += flt(row.forecast_qty)
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


def build_child_rows(grouped_rows, item_details, existing_items, child_meta):
	child_rows = []
	has_adjust_qty = bool(child_meta.get_field("adjust_qty"))
	has_demand_qty = bool(child_meta.get_field("demand_qty"))

	for key in sorted(grouped_rows, key=lambda x: (x[1], x[0], x[2] or "")):
		item_code, delivery_date, warehouse = key
		row_data = grouped_rows[key]
		existing_row = existing_items.get(key)

		if row_data["is_locked"] and existing_row:
			child_rows.append(sanitize_child_row(existing_row, child_meta))
			continue

		if row_data["is_locked"] and not existing_row:
			continue

		item = item_details[item_code]
		child_row = {
			"item_code": item_code,
			"item_name": item.get("item_name"),
			"uom": item.get("uom"),
			"delivery_date": delivery_date,
			"forecast_qty": rounded_quantity(row_data["forecast_qty"]),
			"warehouse": warehouse or None,
		}

		if has_adjust_qty:
			child_row["adjust_qty"] = flt(existing_row.adjust_qty) if existing_row else 0

		if has_demand_qty:
			adjust_qty = flt(child_row.get("adjust_qty"))
			child_row["demand_qty"] = rounded_quantity(child_row["forecast_qty"] + adjust_qty)

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
