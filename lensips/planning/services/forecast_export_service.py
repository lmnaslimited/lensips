from __future__ import annotations

import re
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import add_months, cint, cstr, flt, getdate, nowdate


PERIOD_FIELD_RE = re.compile(r"^(forecast|actual)_(qty|value)_(\d{4}_\d{2}_\d{2})$")


@dataclass(frozen=True)
class PeriodSpec:
	period: date
	suffix: str


def create_sales_forecast(data, filters, columns=None):
	parent_meta, child_meta, detail_meta, _child_doctype = get_forecast_meta()
	clean_filters = normalize_filters(filters)
	start_date = compute_forecast_start_date(clean_filters["to_date"], clean_filters["periodicity"])
	frequency = get_frequency(clean_filters["periodicity"])
	report_rows = normalize_report_rows(data)

	if not report_rows:
		frappe.throw(_("No future forecast rows with positive forecast quantity were found to export."))

	report_rows = aggregate_rows_by_item_and_warehouse(report_rows)
	period_specs = extract_period_specs(columns, report_rows)
	future_specs = future_period_specs(period_specs, start_date)
	if not future_specs:
		frappe.throw(_("No future forecast periods were found to export."))

	warehouse_names = sorted({row.get("warehouse") for row in report_rows if row.get("warehouse")})
	warehouse_map = load_warehouse_map(warehouse_names)
	rows_by_parent_warehouse = group_rows_by_parent_warehouse(report_rows, warehouse_map)
	existing_forecasts = load_existing_forecasts(
		company=clean_filters["company"],
		start_date=start_date,
		frequency=frequency,
		parent_warehouses=rows_by_parent_warehouse.keys(),
	)

	results = []
	total_entries = 0
	for parent_warehouse in sorted(rows_by_parent_warehouse):
		rows = rows_by_parent_warehouse[parent_warehouse]
		forecast_doc = existing_forecasts.get(parent_warehouse) or frappe.new_doc(parent_meta.name)
		existing_items = get_existing_item_map(forecast_doc) if not forecast_doc.is_new() else {}
		selected_items = build_selected_items(rows, child_meta)
		item_rows, entry_rows = build_export_rows(
			rows=rows,
			future_specs=future_specs,
			existing_items=existing_items,
			child_meta=child_meta,
			detail_meta=detail_meta,
			company=clean_filters["company"],
			periodicity=clean_filters["periodicity"],
		)

		if not item_rows:
			continue

		apply_parent_values(
			doc=forecast_doc,
			parent_meta=parent_meta,
			company=clean_filters["company"],
			parent_warehouse=parent_warehouse,
			start_date=start_date,
			frequency=frequency,
			demand_number=cint(clean_filters["forecast_periods"]),
		)

		if forecast_doc.is_new():
			forecast_doc.naming_series = make_naming_series(parent_warehouse, frequency, start_date)
		else:
			forecast_doc.set("selected_items", [])
			forecast_doc.set("items", [])
			if detail_meta:
				forecast_doc.set("forecast_entries", [])

		for row in selected_items:
			forecast_doc.append("selected_items", row)

		for row in item_rows:
			forecast_doc.append("items", row)

		if detail_meta:
			for row in entry_rows:
				forecast_doc.append("forecast_entries", row)
			total_entries += len(entry_rows)

		if forecast_doc.docstatus == 1:
			forecast_doc.flags.ignore_validate_update_after_submit = True

		if forecast_doc.is_new():
			forecast_doc.insert()
			action = "created"
		else:
			forecast_doc.save(ignore_version=True)
			action = "updated"

		results.append(
			{
				"forecast_name": forecast_doc.name,
				"warehouse": parent_warehouse,
				"total_items": len({row["item_code"] for row in item_rows}),
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
		"total_entries": total_entries,
		"message": (
			results[0]["message"]
			if len(results) == 1
			else _("Sales Forecasts successfully created/updated for {0} warehouses.").format(len(results))
		),
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

	child_meta = frappe.get_meta(items_field.options)
	detail_meta = frappe.get_meta(detail_field.options) if detail_field and detail_field.options else None
	return parent_meta, child_meta, detail_meta, items_field.options


def normalize_filters(filters):
	filters = frappe._dict(filters or {})
	company = (filters.get("company") or "").strip()
	group_by = (filters.get("group_by") or "Item").strip()
	periodicity = (filters.get("periodicity") or "Monthly").strip()
	to_date = getdate(filters.get("to_date"))
	forecast_periods = cint(filters.get("forecast_periods"))

	if not company:
		frappe.throw(_("Company is required to export Sales Forecast."))
	if group_by != "Item":
		frappe.throw(_("Sales Forecast export only supports Group By = Item."))
	if periodicity not in {"Weekly", "Monthly"}:
		frappe.throw(_("Sales Forecast export supports only Weekly and Monthly periodicity."))
	if not to_date:
		frappe.throw(_("To Date is required to compute the Sales Forecast start date."))
	if forecast_periods <= 0:
		frappe.throw(_("Forecast Periods must be greater than zero."))
	if forecast_periods > 18:
		frappe.throw(_("Forecast Periods cannot be greater than 18."))

	return frappe._dict(
		company=company,
		warehouse=(filters.get("warehouse") or "").strip() or None,
		periodicity=periodicity,
		to_date=to_date,
		forecast_periods=forecast_periods,
		group_by=group_by,
	)


def compute_forecast_start_date(to_date, periodicity):
	return next_period(normalize_to_period(to_date, periodicity), periodicity)


def get_frequency(periodicity):
	return "Weekly" if periodicity == "Weekly" else "Monthly"


def normalize_report_rows(rows):
	group_rows = []
	item_rows = []
	for row in rows or []:
		row = frappe._dict(row or {})
		if row.get("row_type") not in {"group", "item"}:
			continue

		item_code = cstr(row.get("item_code")).strip()
		warehouse = cstr(row.get("warehouse")).strip()
		if not item_code or not warehouse:
			continue
		if flt(row.get("forecast_qty_total")) <= 0:
			continue

		if row.get("row_type") == "group":
			group_rows.append(row)
		else:
			item_rows.append(row)

	return item_rows or group_rows


def aggregate_rows_by_item_and_warehouse(rows):
	aggregated = {}
	for row in rows or []:
		item_code = cstr(row.get("item_code")).strip()
		warehouse = cstr(row.get("warehouse")).strip()
		delivery_date = getdate(row.get("delivery_date")) if row.get("delivery_date") else None
		if not item_code or not warehouse:
			continue

		key = (item_code, warehouse, delivery_date)
		target = aggregated.get(key)
		if not target:
			target = frappe._dict(
				{
					"item_code": item_code,
					"item_name": row.get("item_name") or item_code,
					"uom": row.get("uom"),
					"warehouse": warehouse,
					"delivery_date": delivery_date or row.get("delivery_date"),
					"price_list": row.get("price_list"),
					"is_locked": cint(row.get("is_locked", 0)),
					"actual_qty_total": 0.0,
					"actual_value_total": 0.0,
					"forecast_qty_total": 0.0,
					"forecast_value_total": 0.0,
				}
			)
			aggregated[key] = target
		else:
			target.item_name = target.item_name or row.get("item_name") or item_code
			target.uom = target.uom or row.get("uom")
			target.price_list = target.price_list or row.get("price_list")
			target.is_locked = max(cint(target.get("is_locked", 0)), cint(row.get("is_locked", 0)))

		for key_name, value in row.items():
			if key_name in {"item_code", "item_name", "uom", "warehouse", "delivery_date", "price_list", "is_locked"}:
				continue
			match = PERIOD_FIELD_RE.match(key_name)
			if match:
				target[key_name] = flt(target.get(key_name, 0)) + flt(value or 0)
				continue
			if key_name in {"actual_qty_total", "actual_value_total", "forecast_qty_total", "forecast_value_total"}:
				target[key_name] = flt(target.get(key_name, 0)) + flt(value or 0)

	return list(aggregated.values())


def extract_period_specs(columns, rows):
	suffixes = {}

	for column in columns or []:
		fieldname = (column or {}).get("fieldname") if isinstance(column, dict) else getattr(column, "fieldname", None)
		if not fieldname:
			continue
		match = PERIOD_FIELD_RE.match(fieldname)
		if not match or match.group(1) != "forecast":
			continue
		suffix = match.group(3)
		period = parse_period_suffix(suffix)
		if period:
			suffixes[period] = suffix

	if not suffixes and rows:
		sample = rows[0]
		for key in sample.keys():
			match = PERIOD_FIELD_RE.match(key)
			if not match or match.group(1) != "forecast":
				continue
			suffix = match.group(3)
			period = parse_period_suffix(suffix)
			if period:
				suffixes[period] = suffix

	return [PeriodSpec(period=period, suffix=suffix) for period, suffix in sorted(suffixes.items())]


def future_period_specs(period_specs, start_date):
	periods = [spec.period for spec in period_specs]
	return period_specs[bisect_left(periods, start_date) :]


def parse_period_suffix(suffix):
	suffix = (suffix or "").strip()
	if not suffix:
		return None
	try:
		return getdate(suffix.replace("_", "-"))
	except Exception:
		return None


def load_warehouse_map(warehouse_names):
	warehouse_map = {}
	pending = {name for name in warehouse_names if name}

	while pending:
		rows = frappe.get_all(
			"Warehouse",
			filters={"name": ("in", sorted(pending))},
			fields=["name", "parent_warehouse", "is_group"],
			limit_page_length=0,
		)
		pending = set()
		for row in rows:
			warehouse_map[row.name] = row
			parent_warehouse = (row.parent_warehouse or "").strip()
			if parent_warehouse and parent_warehouse not in warehouse_map:
				pending.add(parent_warehouse)

	return warehouse_map


def group_rows_by_parent_warehouse(rows, warehouse_map):
	grouped = defaultdict(list)
	for row in rows or []:
		warehouse = (row.get("warehouse") or "").strip()
		parent_warehouse = resolve_parent_warehouse(warehouse, warehouse_map)
		grouped[parent_warehouse].append(row)
	return grouped


def resolve_parent_warehouse(warehouse, warehouse_map):
	warehouse = (warehouse or "").strip()
	if not warehouse:
		return None

	wh = warehouse_map.get(warehouse)
	if not wh:
		return warehouse

	if cint(getattr(wh, "is_group", 0)):
		return wh.name

	return (wh.parent_warehouse or wh.name).strip()


def load_existing_forecasts(company, start_date, frequency, parent_warehouses):
	if not parent_warehouses:
		return {}

	forecast_rows = frappe.get_all(
		"Sales Forecast",
		filters={
			"company": company,
			"from_date": start_date,
			"frequency": frequency,
			"parent_warehouse": ("in", sorted(parent_warehouses)),
			"docstatus": ("!=", 2),
		},
		fields=["name", "parent_warehouse"],
		order_by="modified desc",
		limit_page_length=0,
	)

	forecast_map = {}
	for row in forecast_rows:
		if row.parent_warehouse not in forecast_map:
			forecast_map[row.parent_warehouse] = frappe.get_doc("Sales Forecast", row.name)

	return forecast_map


def get_existing_item_map(forecast_doc):
	item_map = {}
	for row in forecast_doc.get("items") or []:
		key = get_forecast_item_key(row.item_code, row.warehouse, row.delivery_date)
		item_map[key] = row
	return item_map


def build_selected_items(rows, child_meta):
	required_defaults = get_required_child_defaults(child_meta)
	selected = []
	seen = set()

	for row in rows or []:
		item_code = cstr(row.get("item_code")).strip()
		warehouse = cstr(row.get("warehouse")).strip()
		key = (item_code, warehouse)
		if not item_code or key in seen:
			continue
		seen.add(key)
		payload = dict(required_defaults)
		payload.update(
			{
				"item_code": item_code,
				"item_name": row.get("item_name"),
				"uom": row.get("uom"),
			}
		)
		selected.append(payload)

	return selected


def build_export_rows(rows, future_specs, existing_items, child_meta, detail_meta, company, periodicity):
	item_rows = []
	entry_rows = []

	for row in rows or []:
		item_code = cstr(row.get("item_code")).strip()
		warehouse = cstr(row.get("warehouse")).strip()
		customer = (row.get("customer") or "").strip() or None
		price_list_rate = rounded_value(row.get("price_list_rate") or 0)

		for spec in future_specs:
			forecast_qty = rounded_quantity(row.get(f"forecast_qty_{spec.suffix}") or 0)
			if forecast_qty <= 0:
				continue

			actual_qty = rounded_quantity(row.get(f"actual_qty_{spec.suffix}") or 0)
			actual_value = rounded_value(row.get(f"actual_value_{spec.suffix}") or 0)
			forecast_value = rounded_value(row.get(f"forecast_value_{spec.suffix}") or 0)
			key = get_forecast_item_key(item_code, warehouse, spec.period)
			existing_row = existing_items.get(key)
			adjust_qty = flt(getattr(existing_row, "adjust_qty", 0)) if existing_row else 0
			adjust_value = rounded_value((actual_value / actual_qty) * adjust_qty) if actual_qty else 0

			if existing_row and cint(getattr(existing_row, "locked", 0)):
				locked_row = sanitize_child_row(existing_row, child_meta)
				locked_row.update(
					{
						"actual_qty": actual_qty,
						"actual_value": actual_value,
						"forecast_qty": forecast_qty,
						"forecast_value": forecast_value,
						"price_list_rate": price_list_rate,
						"warehouse": warehouse or None,
						"locked": 1,
					}
				)
				item_rows.append(locked_row)
				if detail_meta:
					entry_rows.append(
						{
							"company": company,
							"customer": customer,
							"item_code": item_code,
							"warehouse": warehouse or None,
							"period": spec.period,
							"forecast_qty": forecast_qty,
							"forecast_value": forecast_value,
							"price_list": row.get("price_list"),
							"actual_qty": actual_qty,
							"actual_value": actual_value,
						}
					)
				continue

			adjust_qty = flt(getattr(existing_row, "adjust_qty", 0)) if existing_row else 0
			locked = cint(getattr(existing_row, "locked", row.get("is_locked", 0)))
			item_rows.append(
				{
					"item_code": item_code,
					"item_name": row.get("item_name"),
					"uom": row.get("uom"),
					"delivery_date": spec.period,
					"forecast_qty": forecast_qty,
					"forecast_value": forecast_value,
					"actual_qty": actual_qty,
					"actual_value": actual_value,
					"adjust_qty": adjust_qty,
					"price_list_rate": price_list_rate,
					"adjust_value": adjust_value,
					"demand_qty": rounded_quantity(actual_qty + adjust_qty),
					"demand_value": rounded_value(actual_value + adjust_value),
					"warehouse": warehouse or None,
					"locked": locked,
				}
			)
			if detail_meta:
				entry_rows.append(
					{
						"company": company,
						"customer": customer,
						"item_code": item_code,
						"warehouse": warehouse or None,
						"period": spec.period,
						"forecast_qty": forecast_qty,
						"forecast_value": forecast_value,
						"price_list": row.get("price_list"),
						"actual_qty": actual_qty,
						"actual_value": actual_value,
					}
				)

	return item_rows, entry_rows


def get_forecast_item_key(item_code, warehouse, delivery_date):
	return (cstr(item_code).strip(), cstr(warehouse).strip(), getdate(delivery_date) if delivery_date else None)


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


def make_naming_series(parent_warehouse, frequency, start_date):
	return f"SF.YY.-.{parent_warehouse}.-.{frequency}.-.{start_date.isoformat()}.-.##"


def sanitize_child_row(row, child_meta):
	allowed_fields = {field.fieldname for field in child_meta.fields if getattr(field, "fieldname", None)}
	allowed_fields.update({"name", "doctype", "idx", "parent", "parentfield", "parenttype"})

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
