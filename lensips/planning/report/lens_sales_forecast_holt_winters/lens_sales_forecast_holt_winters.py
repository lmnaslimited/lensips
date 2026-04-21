from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import add_months, cint, cstr, flt, getdate

from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses
from erpnext.stock.doctype.item.item import get_uom_conv_factor
from erpnext.stock.get_item_details import get_conversion_factor as get_item_conversion_factor
from lensips.planning.services.forecast_pricing_service import (
	get_customer_default_price_list,
	get_effective_item_price,
)


ALLOWED_DOCUMENTS = {"Sales Order", "Sales Invoice", "Delivery Note"}
ALLOWED_PERIODICITIES = {"Weekly", "Monthly", "Quarterly", "Half-Yearly", "Yearly"}
ALLOWED_FORECAST_BASES = {"Order Date", "Delivery Date", "Document Date"}
MIN_FORECAST_PERIODS = {
	"Weekly": 52,
	"Monthly": 12,
	"Quarterly": 4,
	"Half-Yearly": 2,
	"Yearly": 1,
}
DEFAULT_SEASON_LENGTH = {
	"Weekly": 52,
	"Monthly": 12,
	"Quarterly": 4,
	"Half-Yearly": 2,
	"Yearly": 1,
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	normalized_filters = normalize_filters(filters)
	columns = get_columns()
	historical_rows = get_data(normalized_filters)
	grouped_data = group_data(historical_rows, normalized_filters)
	data = build_forecast_rows(grouped_data, normalized_filters)
	chart = get_chart_data(data)
	return columns, data, None, chart


def normalize_filters(raw_filters):
	periodicity = (raw_filters.get("periodicity") or "Monthly").strip()
	if periodicity not in ALLOWED_PERIODICITIES:
		periodicity = "Monthly"

	group_by = (raw_filters.get("group_by") or "Item").strip()
	if group_by not in {"Item", "Item Group", "Customer", "Sales Group"}:
		group_by = "Item"

	based_on_document = (raw_filters.get("based_on_document") or "Sales Order").strip()
	if based_on_document not in ALLOWED_DOCUMENTS:
		based_on_document = "Sales Order"

	forecast_based_on = normalize_forecast_based_on(
		raw_filters.get("forecast_based_on"), based_on_document
	)

	from_date = getdate(raw_filters.get("from_date"))
	to_date = getdate(raw_filters.get("to_date"))
	if not from_date or not to_date:
		frappe.throw("From Date and To Date are required.")
	if from_date > to_date:
		frappe.throw("From Date cannot be after To Date.")

	forecast_periods = cint(raw_filters.get("forecast_periods") or MIN_FORECAST_PERIODS[periodicity])
	forecast_periods = max(forecast_periods, MIN_FORECAST_PERIODS[periodicity])

	season_length = cint(raw_filters.get("season_length") or DEFAULT_SEASON_LENGTH[periodicity])
	season_length = max(season_length, 1)

	return frappe._dict(
		company=raw_filters.get("company"),
		from_date=from_date,
		to_date=to_date,
		periodicity=periodicity,
		group_by=group_by,
		based_on_document=based_on_document,
		forecast_based_on=forecast_based_on,
		warehouse=raw_filters.get("warehouse"),
		alpha=flt(raw_filters.get("alpha") or 0.3),
		beta=flt(raw_filters.get("beta") or 0.1),
		gamma=flt(raw_filters.get("gamma") or 0.1),
		season_length=season_length,
		forecast_periods=forecast_periods,
		manufacture_date=getdate(raw_filters.get("manufacture_date")) if raw_filters.get("manufacture_date") else None,
		item_code=(raw_filters.get("item_code") or "").strip() or None,
		item_group=(raw_filters.get("item_group") or "").strip() or None,
		customer=(raw_filters.get("customer") or "").strip() or None,
		sales_group=(raw_filters.get("sales_group") or "").strip() or None,
	)


def get_data(filters):
	parent_table = f"`tab{filters.based_on_document}`"
	child_table = f"`tab{filters.based_on_document} Item`"
	date_expression = get_date_expression(filters)
	data_to_date = get_actual_data_cutoff(filters)
	has_forecast_group = frappe.db.has_column("Customer", "forecast_group")
	forecast_group_expr = "COALESCE(cust.forecast_group, parent_doc.customer)" if has_forecast_group else "parent_doc.customer"
	uom_expression = "COALESCE(child_doc.stock_uom, child_doc.uom, item.sales_uom)"
	has_payment_fields = (
		filters.based_on_document == "Sales Order"
		and frappe.db.has_column("Sales Order Item", "paid_amount")
		and frappe.db.has_column("Sales Order Item", "payment_date")
	)
	actual_value_expression = get_actual_value_expression(filters, has_payment_fields)

	conditions = [
		"parent_doc.docstatus = 1",
		"COALESCE(parent_doc.status, '') NOT IN ('Draft', 'Cancelled')",
		f"{date_expression} BETWEEN %(from_date)s AND %(data_to_date)s",
	]
	query_filters = {
		"from_date": filters.from_date,
		"data_to_date": data_to_date,
	}

	if filters.company:
		conditions.append("parent_doc.company = %(company)s")
		query_filters["company"] = filters.company

	if filters.item_code:
		conditions.append("child_doc.item_code = %(item_code)s")
		query_filters["item_code"] = filters.item_code

	if filters.item_group:
		conditions.append("item.item_group = %(item_group)s")
		query_filters["item_group"] = filters.item_group

	if filters.customer:
		conditions.append("parent_doc.customer = %(customer)s")
		query_filters["customer"] = filters.customer

	if filters.sales_group:
		conditions.append(f"{forecast_group_expr} = %(sales_group)s")
		query_filters["sales_group"] = filters.sales_group

	if filters.warehouse:
		warehouses = tuple(get_child_warehouses(filters.warehouse) or [filters.warehouse])
		conditions.append("child_doc.warehouse IN %(warehouses)s")
		query_filters["warehouses"] = warehouses

	query = f"""
		SELECT
			child_doc.item_code,
			MAX(child_doc.item_name) AS item_name,
			MAX(item.item_group) AS item_group,
			MAX({uom_expression}) AS uom,
			parent_doc.customer,
			{forecast_group_expr} AS sales_group,
			child_doc.warehouse,
			{date_expression} AS posting_date,
			SUM(COALESCE(NULLIF(child_doc.stock_qty, 0), child_doc.qty, 0)) AS actual_qty,
			SUM({actual_value_expression}) AS actual_value
		FROM {child_table} child_doc
		INNER JOIN {parent_table} parent_doc ON parent_doc.name = child_doc.parent
		LEFT JOIN `tabItem` item ON item.item_code = child_doc.item_code
		LEFT JOIN `tabCustomer` cust ON cust.name = parent_doc.customer
		WHERE {' AND '.join(conditions)}
		GROUP BY
			child_doc.item_code,
			item.item_group,
			{uom_expression},
			parent_doc.customer,
			sales_group,
			child_doc.warehouse,
			{date_expression}
		ORDER BY {date_expression}
	"""
	return frappe.db.sql(query, query_filters, as_dict=True)


def get_actual_value_expression(filters, has_payment_fields):
	if filters.based_on_document == "Sales Order" and has_payment_fields:
		return (
			"COALESCE("
			"NULLIF(child_doc.paid_amount, 0), "
			"NULLIF(child_doc.base_net_amount, 0), "
			"NULLIF(child_doc.net_amount, 0), "
			"NULLIF(child_doc.base_amount, 0), "
			"NULLIF(child_doc.amount, 0), "
			"COALESCE(child_doc.rate, 0) * COALESCE(NULLIF(child_doc.stock_qty, 0), child_doc.qty, 0)"
			")"
		)

	return (
		"COALESCE("
		"NULLIF(child_doc.base_net_amount, 0), "
		"NULLIF(child_doc.net_amount, 0), "
		"NULLIF(child_doc.base_amount, 0), "
		"NULLIF(child_doc.amount, 0), "
		"COALESCE(child_doc.rate, 0) * COALESCE(NULLIF(child_doc.stock_qty, 0), child_doc.qty, 0)"
		")"
	)


def normalize_forecast_based_on(forecast_based_on, based_on_document):
	forecast_based_on = (forecast_based_on or "").strip()
	if forecast_based_on not in ALLOWED_FORECAST_BASES:
		forecast_based_on = ""

	if based_on_document == "Sales Order":
		return forecast_based_on if forecast_based_on in {"Order Date", "Delivery Date"} else "Delivery Date"

	return "Document Date"


def get_date_expression(filters):
	if filters.based_on_document == "Sales Order":
		if frappe.db.has_column("Sales Order Item", "payment_date"):
			return "COALESCE(child_doc.payment_date, parent_doc.transaction_date)"
		if filters.forecast_based_on == "Delivery Date":
			return "COALESCE(child_doc.delivery_date, parent_doc.delivery_date, parent_doc.transaction_date)"

		return "parent_doc.transaction_date"

	if filters.based_on_document in {"Sales Invoice", "Delivery Note"}:
		return "parent_doc.posting_date"

	return "parent_doc.transaction_date"


def get_actual_data_cutoff(filters):
	last_period = normalize_to_period(filters.to_date, filters.periodicity)
	for _ in range(filters.forecast_periods):
		last_period = next_period(last_period, filters.periodicity)

	return get_period_end(last_period, filters.periodicity)


def group_data(rows, filters):
	grouped = defaultdict(lambda: defaultdict(lambda: {"actual_qty": 0.0, "actual_value": 0.0}))
	for row in rows:
		key = (
			row.get("item_code"),
			row.get("item_name"),
			row.get("item_group"),
			row.get("uom"),
			row.get("customer"),
			row.get("sales_group"),
			row.get("warehouse"),
		)
		period = normalize_to_period(getdate(row.get("posting_date")), filters.periodicity)
		bucket = grouped[key][period]
		bucket["actual_qty"] += flt(row.get("actual_qty"))
		bucket["actual_value"] += flt(row.get("actual_value"))

	return grouped


def holt_winters_forecast(data, alpha, beta, gamma, season_length, periods):
	if not data:
		return [0] * periods

	if len(data) < max(2, season_length):
		average = max(0.0, sum(data) / len(data))
		return [rounded_quantity(average)] * periods

	series = [max(0.0, flt(value)) for value in data]
	season_length = min(season_length, len(series))

	level = series[0]
	trend = _initial_trend(series, season_length)
	seasonals = _initial_seasonals(series, season_length)

	for idx, value in enumerate(series):
		seasonal = seasonals[idx % season_length]
		prev_level = level
		level = alpha * (value - seasonal) + (1 - alpha) * (level + trend)
		trend = beta * (level - prev_level) + (1 - beta) * trend
		seasonals[idx % season_length] = gamma * (value - level) + (1 - gamma) * seasonal

	forecast = []
	for horizon in range(1, periods + 1):
		seasonal = seasonals[(len(series) + horizon - 1) % season_length]
		value = level + horizon * trend + seasonal
		forecast.append(rounded_quantity(max(0.0, value)))

	return forecast


def _initial_trend(series, season_length):
	if len(series) < season_length * 2:
		return (series[-1] - series[0]) / max(len(series) - 1, 1)

	trend_sum = 0
	for idx in range(season_length):
		trend_sum += (series[idx + season_length] - series[idx]) / season_length
	return trend_sum / season_length


def _initial_seasonals(series, season_length):
	seasonals = [0.0] * season_length
	n_seasons = max(len(series) // season_length, 1)
	season_averages = []

	for season_idx in range(n_seasons):
		start = season_idx * season_length
		chunk = series[start : start + season_length]
		if not chunk:
			continue
		season_averages.append(sum(chunk) / len(chunk))

	if not season_averages:
		return seasonals

	for idx in range(season_length):
		values = []
		for season_idx, avg in enumerate(season_averages):
			position = season_idx * season_length + idx
			if position < len(series):
				values.append(series[position] - avg)
		seasonals[idx] = sum(values) / len(values) if values else 0.0

	return seasonals


def build_forecast_rows(grouped_data, filters):
	rows = {}
	base_history_end = normalize_to_period(filters.to_date, filters.periodicity)
	lock_cutoff = None
	if filters.manufacture_date:
		lock_cutoff = normalize_to_period(add_months(filters.manufacture_date, -2), filters.periodicity)

	for key, actual_by_period in grouped_data.items():
		item_code, item_name, item_group, uom, customer, sales_group, warehouse = key
		last_actual_period = max(actual_by_period) if actual_by_period else base_history_end
		display_history_end = max(base_history_end, last_actual_period)
		display_history_periods = get_period_range(filters.from_date, display_history_end, filters.periodicity)
		training_periods = get_period_range(filters.from_date, filters.to_date, filters.periodicity)
		series = [flt(actual_by_period.get(period, {}).get("actual_qty", 0.0)) for period in training_periods]
		price_list = get_customer_default_price_list(customer)

		forecast_values = holt_winters_forecast(
			series,
			filters.alpha,
			filters.beta,
			filters.gamma,
			filters.season_length,
			filters.forecast_periods,
		)
		last_actual = rounded_quantity(series[-1]) if series else 0
		group_key = resolve_group_key(filters.group_by, item_code, item_group, customer, sales_group)

		for period in display_history_periods:
			row_key = (group_key, item_code, customer, sales_group, warehouse, period)
			period_bucket = actual_by_period.get(period, {})
			row = rows.get(row_key) or make_row(
				group_key=group_key,
				item_code=item_code,
				item_group=item_group,
				uom=uom,
				customer=customer,
				sales_group=sales_group,
				warehouse=warehouse,
				period=period,
				actual_qty=period_bucket.get("actual_qty", 0.0),
				forecast_qty=0,
				actual_value=period_bucket.get("actual_value", 0.0),
				forecast_value=0,
				is_locked=0,
				periodicity=filters.periodicity,
			)
			row["actual_qty"] = flt(period_bucket.get("actual_qty", 0.0))
			row["actual_value"] = rounded_value(period_bucket.get("actual_value", 0.0))
			row["forecast_value"] = 0
			rows[row_key] = row

		future_period = base_history_end
		for forecast_qty in forecast_values:
			future_period = next_period(future_period, filters.periodicity)
			is_locked = 1 if lock_cutoff and future_period <= lock_cutoff else 0
			forecast_or_frozen = last_actual if is_locked else forecast_qty
			price_info = get_effective_item_price(
				item_code=item_code,
				customer=customer,
				price_list=price_list,
				period_start=future_period,
				period_end=get_period_end(future_period, filters.periodicity),
				uom=uom,
			)
			forecast_value = rounded_value(forecast_or_frozen * flt(price_info.price_list_rate))

			row_key = (group_key, item_code, customer, sales_group, warehouse, future_period)
			row = rows.get(row_key) or make_row(
				group_key=group_key,
				item_code=item_code,
				item_group=item_group,
				uom=uom,
				customer=customer,
				sales_group=sales_group,
				warehouse=warehouse,
				period=future_period,
				actual_qty=0,
				forecast_qty=forecast_or_frozen,
				actual_value=0,
				forecast_value=forecast_value,
				is_locked=is_locked,
				periodicity=filters.periodicity,
			)
			row["forecast_qty"] = flt(forecast_or_frozen)
			row["forecast_value"] = forecast_value
			row["price_list"] = price_info.price_list
			row["price_list_rate"] = flt(price_info.price_list_rate)
			row["is_locked"] = is_locked
			rows[row_key] = row

	final_rows = list(rows.values())
	for row in final_rows:
		row["period"] = row["period_start"].isoformat()

	final_rows.sort(
		key=lambda d: (
			d["period"],
			cstr_or_empty(d["group_key"]),
			cstr_or_empty(d["warehouse"]),
			cstr_or_empty(d["item_code"]),
		)
	)
	return final_rows


def get_period_range(from_date, to_date, periodicity):
	periods = []
	current = normalize_to_period(from_date, periodicity)
	last_period = normalize_to_period(to_date, periodicity)

	while current <= last_period:
		periods.append(current)
		current = next_period(current, periodicity)

	return periods


def normalize_to_period(value, periodicity):
	if periodicity == "Weekly":
		return value - timedelta(days=value.weekday())
	if periodicity == "Monthly":
		return date(value.year, value.month, 1)
	if periodicity == "Quarterly":
		month = ((value.month - 1) // 3) * 3 + 1
		return date(value.year, month, 1)
	if periodicity == "Half-Yearly":
		month = 1 if value.month <= 6 else 7
		return date(value.year, month, 1)
	return date(value.year, 1, 1)


def next_period(period_start, periodicity):
	if periodicity == "Weekly":
		return period_start + timedelta(days=7)
	if periodicity == "Monthly":
		return add_months(period_start, 1)
	if periodicity == "Quarterly":
		return add_months(period_start, 3)
	if periodicity == "Half-Yearly":
		return add_months(period_start, 6)
	return add_months(period_start, 12)


def get_period_end(period_start, periodicity):
	return next_period(period_start, periodicity) - timedelta(days=1)


def resolve_group_key(group_by, item_code, item_group, customer, sales_group):
	mapping = {
		"Item": item_code,
		"Item Group": item_group,
		"Customer": customer,
		"Sales Group": sales_group,
	}
	return mapping.get(group_by)


def make_row(
	group_key,
	item_code,
	item_group,
	uom,
	customer,
	sales_group,
	warehouse,
	period,
	actual_qty,
	forecast_qty,
	actual_value,
	forecast_value,
	is_locked,
	periodicity,
):
	return {
		"group_key": group_key,
		"item_code": item_code,
		"item_group": item_group,
		"uom": uom,
		"customer": customer,
		"sales_group": sales_group,
		"warehouse": warehouse,
		"period_start": period,
		"period": period,
		"period_label": get_period_label(period, periodicity),
		"actual_qty": flt(actual_qty),
		"forecast_qty": rounded_quantity(forecast_qty),
		"actual_value": rounded_value(actual_value),
		"forecast_value": rounded_value(forecast_value),
		"is_locked": is_locked,
	}


def get_period_label(period, periodicity):
	if periodicity == "Weekly":
		return f"Week of {period.isoformat()}"
	if periodicity == "Monthly":
		return period.strftime("%b %Y")
	if periodicity == "Quarterly":
		quarter = ((period.month - 1) // 3) + 1
		return f"Q{quarter} {period.year}"
	if periodicity == "Half-Yearly":
		half = 1 if period.month == 1 else 2
		return f"H{half} {period.year}"
	return str(period.year)


def get_chart_data(data):
	aggregated = defaultdict(lambda: {"label": "", "actual": 0.0, "forecast": 0.0})
	for row in data:
		period = row.get("period")
		aggregated[period]["label"] = row.get("period_label")
		aggregated[period]["actual"] += flt(row.get("actual_qty"))
		aggregated[period]["forecast"] += flt(row.get("forecast_qty"))

	periods = sorted(aggregated)
	return {
		"data": {
			"labels": [aggregated[period]["label"] for period in periods],
			"datasets": [
				{"name": "Actual", "values": [aggregated[period]["actual"] for period in periods]},
				{"name": "Forecast", "values": [aggregated[period]["forecast"] for period in periods]},
			],
		},
		"type": "line",
	}


def get_columns():
	return [
		{"label": "Group Key", "fieldname": "group_key", "fieldtype": "Data", "width": 180},
		{"label": "Item", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": "Item Group", "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 130},
		{"label": "UOM", "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 90},
		{"label": "Customer", "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 150},
		{"label": "Sales Group", "fieldname": "sales_group", "fieldtype": "Data", "width": 130},
		{"label": "Warehouse", "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": "Period Start", "fieldname": "period", "fieldtype": "Date", "width": 110},
		{"label": "Period Label", "fieldname": "period_label", "fieldtype": "Data", "width": 110},
		{"label": "Actual Qty", "fieldname": "actual_qty", "fieldtype": "Float", "width": 120},
		{"label": "Actual Value", "fieldname": "actual_value", "fieldtype": "Currency", "width": 140},
		{
			"label": "Forecast Qty",
			"fieldname": "forecast_qty",
			"fieldtype": "Int",
			"width": 130,
		},
		{
			"label": "Forecast Value",
			"fieldname": "forecast_value",
			"fieldtype": "Currency",
			"width": 140,
		},
		{"label": "Locked", "fieldname": "is_locked", "fieldtype": "Check", "width": 90},
	]


def cstr_or_empty(value):
	return str(value) if value is not None else ""


def rounded_quantity(value):
	return int(round(flt(value)))


def rounded_value(value):
	return round(flt(value), 2)


# New implementation overrides the legacy row-based report above.


def execute(filters=None):
	filters = frappe._dict(filters or {})
	normalized_filters = normalize_filters(filters)
	source = load_source_facts(normalized_filters)
	masters = load_master_maps(source, normalized_filters)
	report_state = build_report_state(source, masters, normalized_filters)
	columns = get_columns(report_state, normalized_filters)
	data = build_tree_rows(report_state, normalized_filters, include_future_actuals=normalized_filters.show_actual)
	chart = get_chart_data(report_state)
	summary = get_summary_data(report_state)
	return columns, data, None, chart, summary


def get_sales_forecast_export_rows(filters=None):
	filters = frappe._dict(filters or {})
	normalized_filters = normalize_filters(filters)
	source = load_source_facts(normalized_filters)
	masters = load_master_maps(source, normalized_filters)
	report_state = build_report_state(source, masters, normalized_filters)
	return build_tree_rows(report_state, normalized_filters, include_future_actuals=True)


def normalize_filters(raw_filters):
	periodicity = (raw_filters.get("periodicity") or "Monthly").strip()
	if periodicity not in ALLOWED_PERIODICITIES:
		periodicity = "Monthly"

	group_by = (raw_filters.get("group_by") or "Item").strip()
	if group_by not in {
		"Item",
		"Item Group",
		"Customer",
		"Territory",
		"Product Segment",
		"Sales Category",
	}:
		group_by = "Item"

	based_on_document = (raw_filters.get("based_on_document") or "Sales Order").strip()
	if based_on_document not in ALLOWED_DOCUMENTS:
		based_on_document = "Sales Order"

	forecast_based_on = normalize_forecast_based_on(
		raw_filters.get("forecast_based_on"), based_on_document
	)

	from_date = getdate(raw_filters.get("from_date"))
	to_date = getdate(raw_filters.get("to_date"))
	if not from_date or not to_date:
		frappe.throw(_("From Date and To Date are required."))
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))

	forecast_periods = cint(raw_filters.get("forecast_periods") or MIN_FORECAST_PERIODS[periodicity])
	forecast_periods = max(forecast_periods, MIN_FORECAST_PERIODS[periodicity])

	season_length = cint(raw_filters.get("season_length") or DEFAULT_SEASON_LENGTH[periodicity])
	season_length = max(season_length, 1)

	return frappe._dict(
		company=(raw_filters.get("company") or "").strip() or None,
		from_date=from_date,
		to_date=to_date,
		periodicity=periodicity,
		group_by=group_by,
		based_on_document=based_on_document,
		forecast_based_on=forecast_based_on,
		warehouse=(raw_filters.get("warehouse") or "").strip() or None,
		uom=(raw_filters.get("uom") or "").strip() or None,
		show_past_data=cint(raw_filters.get("show_past_data")),
		show_actual=cint(raw_filters.get("show_actual")),
		alpha=flt(raw_filters.get("alpha") or 0.3),
		beta=flt(raw_filters.get("beta") or 0.1),
		gamma=flt(raw_filters.get("gamma") or 0.1),
		season_length=season_length,
		forecast_periods=forecast_periods,
		manufacture_date=getdate(raw_filters.get("manufacture_date")) if raw_filters.get("manufacture_date") else None,
		item_code=(raw_filters.get("item_code") or "").strip() or None,
		item_group=(raw_filters.get("item_group") or "").strip() or None,
		customer=(raw_filters.get("customer") or "").strip() or None,
		territory=(raw_filters.get("territory") or "").strip() or None,
		product_segment=(raw_filters.get("product_segment") or "").strip() or None,
		sales_category=(raw_filters.get("sales_category") or "").strip() or None,
	)


def load_source_facts(filters):
	parent_doctype = filters.based_on_document
	child_doctype = f"{parent_doctype} Item"
	data_to_date = get_actual_data_cutoff(filters)
	parent_fields = ["name", "customer", "company"]
	parent_filters = {"docstatus": 1, "company": filters.company}
	or_filters = None

	if parent_doctype == "Sales Order":
		parent_fields.extend(["transaction_date", "delivery_date"])
		parent_filters["transaction_date"] = ("between", [filters.from_date, data_to_date])
		if filters.forecast_based_on == "Delivery Date":
			or_filters = [{"delivery_date": ("between", [filters.from_date, data_to_date])}]
	elif parent_doctype in {"Sales Invoice", "Delivery Note"}:
		parent_fields.append("posting_date")
		parent_filters["posting_date"] = ("between", [filters.from_date, data_to_date])
	else:
		parent_fields.append("transaction_date")
		parent_filters["transaction_date"] = ("between", [filters.from_date, data_to_date])

	if filters.customer:
		parent_filters["customer"] = filters.customer

	parent_rows = frappe.get_all(
		parent_doctype,
		filters=parent_filters,
		or_filters=or_filters,
		fields=parent_fields,
		order_by="name asc",
		limit_page_length=0,
	)
	parent_map = {row.name: row for row in parent_rows}
	parent_names = list(parent_map)
	if not parent_names:
		return frappe._dict(parent_map=parent_map, child_rows=[], data_to_date=data_to_date)

	child_filters = {"parent": ("in", parent_names)}
	if filters.warehouse:
		warehouses = tuple(get_child_warehouses(filters.warehouse) or [filters.warehouse])
		child_filters["warehouse"] = ("in", warehouses)
	if filters.item_code:
		child_filters["item_code"] = filters.item_code

	child_fields = [
		"parent",
		"item_code",
		"warehouse",
		"uom",
		"qty",
		"stock_qty",
		"rate",
		"base_net_amount",
		"net_amount",
		"base_amount",
		"amount",
	]
	if parent_doctype == "Sales Order":
		child_fields.extend(["paid_amount", "payment_date", "delivery_date"])

	child_rows = frappe.get_all(
		child_doctype,
		filters=child_filters,
		fields=child_fields,
		order_by="parent asc, idx asc",
		limit_page_length=0,
	)
	return frappe._dict(parent_map=parent_map, child_rows=child_rows, data_to_date=data_to_date)


def load_master_maps(source, filters):
	item_codes = sorted({row.item_code for row in source.child_rows if row.get("item_code")})
	customer_names = sorted(
		{
			source.parent_map.get(row.parent).customer
			for row in source.child_rows
			if source.parent_map.get(row.parent) and source.parent_map.get(row.parent).customer
		}
	)
	warehouse_names = sorted({row.warehouse for row in source.child_rows if row.get("warehouse")})
	territory_names = set()

	item_fields = ["name", "item_name", "item_group", "stock_uom", "sales_uom"]
	if frappe.db.has_column("Item", "sales_category"):
		item_fields.append("sales_category")
	if frappe.db.has_column("Item", "product_segment"):
		item_fields.append("product_segment")

	items = frappe.get_all(
		"Item",
		filters={"name": ("in", item_codes)} if item_codes else None,
		fields=item_fields,
		limit_page_length=0,
	)
	item_map = {row.name: row for row in items}

	customers = frappe.get_all(
		"Customer",
		filters={"name": ("in", customer_names)} if customer_names else None,
		fields=["name", "customer_name", "territory", "default_price_list"],
		limit_page_length=0,
	)
	customer_map = {row.name: row for row in customers}
	for customer in customers:
		if customer.territory:
			territory_names.add(customer.territory)

	territories = frappe.get_all(
		"Territory",
		filters={"name": ("in", sorted(territory_names))} if territory_names else None,
		fields=["name", "territory_name", "parent_territory", "is_group"],
		limit_page_length=0,
	)
	territory_map = {row.name: row for row in territories}

	warehouses = frappe.get_all(
		"Warehouse",
		filters={"name": ("in", warehouse_names)} if warehouse_names else None,
		fields=["name", "warehouse_name", "parent_warehouse"],
		limit_page_length=0,
	)
	warehouse_map = {row.name: row for row in warehouses}

	price_lists = sorted({customer.default_price_list for customer in customers if customer.default_price_list})
	price_filters = {"item_code": ("in", item_codes)} if item_codes else None
	if price_filters and price_lists:
		price_filters["price_list"] = ("in", price_lists)
	elif price_lists:
		price_filters = {"price_list": ("in", price_lists)}

	price_rows = frappe.get_all(
		"Item Price",
		filters=price_filters,
		fields=[
			"item_code",
			"price_list",
			"price_list_rate",
			"currency",
			"valid_from",
			"valid_upto",
			"uom",
			"creation",
		],
		order_by="valid_from desc, creation desc",
		limit_page_length=0,
	)
	price_index = defaultdict(list)
	for row in price_rows:
		price_index[(row.item_code, row.price_list)].append(row)

	return frappe._dict(
		item_map=item_map,
		customer_map=customer_map,
		territory_map=territory_map,
		warehouse_map=warehouse_map,
		price_index=price_index,
		price_cache={},
		uom_factor_cache={},
	)


def build_report_state(source, masters, filters):
	base_history_end = normalize_to_period(filters.to_date, filters.periodicity)
	lock_cutoff = (
		normalize_to_period(add_months(filters.manufacture_date, -2), filters.periodicity)
		if filters.manufacture_date
		else None
	)
	leaf_buckets = defaultdict(lambda: _make_bucket())
	period_summary = defaultdict(lambda: _make_period_summary_bucket())
	group_children = defaultdict(list)

	for child in source.child_rows:
		parent = source.parent_map.get(child.parent)
		if not parent or not child.get("item_code"):
			continue

		item = masters.item_map.get(child.item_code)
		if not item:
			continue

		period = get_fact_period(child, parent, filters)
		if not period or period < filters.from_date or period > source.data_to_date:
			continue

		customer = parent.customer
		customer_info = masters.customer_map.get(customer) or frappe._dict()
		territory = customer_info.get("territory")
		item_group = item.get("item_group")
		sales_category = item.get("sales_category")
		product_segment = item.get("product_segment")
		group_value = resolve_group_value(
			filters.group_by,
			item_code=child.item_code,
			item_group=item_group,
			customer=customer,
			territory=territory,
			product_segment=product_segment,
			sales_category=sales_category,
		)

		key = (group_value, child.item_code, customer, child.get("warehouse") or None)
		is_new_bucket = key not in leaf_buckets
		bucket = leaf_buckets[key]
		if is_new_bucket:
			group_children[group_value].append(bucket)
		bucket["group_value"] = group_value
		bucket["item_code"] = child.item_code
		bucket["item_name"] = item.get("item_name")
		bucket["item_group"] = item_group
		bucket["customer"] = customer
		bucket["customer_name"] = customer_info.get("customer_name")
		bucket["territory"] = territory
		bucket["sales_category"] = sales_category
		bucket["product_segment"] = product_segment
		bucket["warehouse"] = child.get("warehouse") or None
		bucket["stock_uom"] = item.get("stock_uom")
		bucket["selected_uom"] = filters.uom or item.get("sales_uom") or item.get("stock_uom")
		bucket["price_list"] = customer_info.get("default_price_list")

		raw_qty = get_actual_qty(child)
		actual_value = get_actual_value(child, filters)
		period_bucket = bucket["periods"][period]
		period_bucket["raw_actual_qty"] += raw_qty
		period_bucket["actual_value"] += actual_value

		period_summary[period]["period"] = period
		period_summary[period]["period_label"] = get_period_label(period, filters.periodicity)
		period_summary[period]["actual_qty"] += convert_qty_to_display(
			raw_qty, bucket["stock_uom"], filters.uom, masters, child.item_code
		)
		period_summary[period]["actual_value"] += actual_value

	training_periods = get_period_range(filters.from_date, filters.to_date, filters.periodicity)
	for bucket in leaf_buckets.values():
		item = masters.item_map.get(bucket["item_code"]) or frappe._dict()
		raw_series = [flt(bucket["periods"][period]["raw_actual_qty"]) for period in training_periods]
		forecast_series = holt_winters_forecast(
			raw_series,
			filters.alpha,
			filters.beta,
			filters.gamma,
			filters.season_length,
			filters.forecast_periods,
		)

		future_period = base_history_end
		last_actual = raw_series[-1] if raw_series else 0.0
		for forecast_raw in forecast_series:
			future_period = next_period(future_period, filters.periodicity)
			effective_raw = last_actual if lock_cutoff and future_period <= lock_cutoff else forecast_raw
			display_qty = convert_qty_to_display(
				effective_raw, bucket["stock_uom"], filters.uom, masters, bucket["item_code"]
			)
			price_info = get_cached_effective_item_price(
				masters=masters,
				item_code=bucket["item_code"],
				customer=bucket["customer"],
				price_list=bucket["price_list"],
				period_start=future_period,
				period_end=get_period_end(future_period, filters.periodicity),
				uom=bucket["selected_uom"],
			)
			forecast_value = rounded_value(display_qty * flt(price_info.price_list_rate))
			period_bucket = bucket["periods"][future_period]
			period_bucket["forecast_qty_raw"] += effective_raw
			period_bucket["forecast_qty"] += display_qty
			period_bucket["forecast_value"] += forecast_value
			period_bucket["is_locked"] = 1 if lock_cutoff and future_period <= lock_cutoff else 0
			period_summary[future_period]["period"] = future_period
			period_summary[future_period]["period_label"] = get_period_label(
				future_period, filters.periodicity
			)
			period_summary[future_period]["forecast_qty"] += display_qty
			period_summary[future_period]["forecast_value"] += forecast_value

	group_buckets = defaultdict(lambda: _make_bucket())
	group_children = defaultdict(list)
	for bucket in leaf_buckets.values():
		group_bucket = group_buckets[bucket["group_value"]]
		group_bucket["group_value"] = bucket["group_value"]
		group_bucket["item_code"] = bucket["group_value"]
		group_bucket["item_name"] = bucket["group_value"]
		group_bucket["item_group"] = bucket["item_group"]
		group_bucket["customer"] = bucket["customer"]
		group_bucket["customer_name"] = bucket["customer_name"]
		group_bucket["territory"] = bucket["territory"]
		group_bucket["sales_category"] = bucket["sales_category"]
		group_bucket["product_segment"] = bucket["product_segment"]
		group_bucket["warehouse"] = bucket["warehouse"]
		group_bucket["stock_uom"] = bucket["stock_uom"]
		group_bucket["selected_uom"] = bucket["selected_uom"]
		group_bucket["price_list"] = bucket["price_list"]
		for period, metrics in bucket["periods"].items():
			target = group_bucket["periods"][period]
			target["raw_actual_qty"] += metrics.get("raw_actual_qty", 0.0)
			target["actual_value"] += metrics.get("actual_value", 0.0)
			target["forecast_qty_raw"] += metrics.get("forecast_qty_raw", 0.0)
			target["forecast_qty"] += metrics.get("forecast_qty", 0.0)
			target["forecast_value"] += metrics.get("forecast_value", 0.0)
			target["is_locked"] = max(target["is_locked"], metrics.get("is_locked", 0))

	return frappe._dict(
		leaf_buckets=leaf_buckets,
		group_buckets=group_buckets,
		group_children=group_children,
		period_summary=period_summary,
		base_history_end=base_history_end,
		training_periods=training_periods,
		masters=masters,
	)


def build_tree_rows(report_state, filters, include_future_actuals=False):
	rows = []
	group_children = getattr(report_state, "group_children", None) or defaultdict(list)
	for group_value in sorted(report_state.group_buckets, key=lambda value: cstr(value or "").lower()):
		group_bucket = report_state.group_buckets[group_value]
		group_label = resolve_group_label(group_bucket, filters.group_by)
		group_row = make_row(
			display_name=group_label,
			parent_display_name=None,
			row_type="group",
			bucket=group_bucket,
			report_state=report_state,
			filters=filters,
			include_future_actuals=include_future_actuals,
		)
		rows.append(group_row)

		children = sorted(
			group_children.get(group_value, []),
			key=lambda row: (
				cstr(row.get("item_code") or ""),
				cstr(row.get("customer") or ""),
				cstr(row.get("warehouse") or ""),
			),
		)
		for child in children:
			child_label = resolve_leaf_label(child)
			rows.append(
				make_row(
					display_name=child_label,
					parent_display_name=group_label,
					row_type="item",
					bucket=child,
					report_state=report_state,
					filters=filters,
					include_future_actuals=include_future_actuals,
				)
			)

	return rows


def make_row(
	display_name,
	parent_display_name,
	row_type,
	bucket,
	report_state,
	filters,
	include_future_actuals=False,
):
	is_item_group = row_type == "group" and filters.group_by == "Item"
	row = {
		"display_name": display_name,
		"parent_display_name": parent_display_name,
		"row_type": row_type,
		"group_value": bucket.get("group_value"),
		"item_code": bucket.get("item_code") if row_type == "item" or is_item_group else None,
		"item_name": bucket.get("item_name") if row_type == "item" or is_item_group else bucket.get("group_value"),
		"item_group": bucket.get("item_group"),
		"customer": bucket.get("customer"),
		"price_list": bucket.get("price_list"),
		"territory": bucket.get("territory"),
		"sales_category": bucket.get("sales_category"),
		"product_segment": bucket.get("product_segment"),
		"warehouse": bucket.get("warehouse"),
		"uom": filters.uom or bucket.get("selected_uom") or bucket.get("stock_uom"),
		"actual_qty_total": 0.0,
		"actual_value_total": 0.0,
		"forecast_qty_total": 0.0,
		"forecast_value_total": 0.0,
		"indent": 0 if row_type == "group" else 1,
		"is_locked": 0,
	}

	for period in sorted(bucket["periods"]):
		metrics = bucket["periods"][period]
		suffix = period_field_suffix(period)
		if period <= report_state.base_history_end:
			actual_qty = rounded_quantity(
				convert_qty_to_display(
					metrics.get("raw_actual_qty", 0.0),
					bucket.get("stock_uom"),
					filters.uom,
					report_state.masters,
					bucket.get("item_code"),
				)
			)
			actual_value = rounded_value(metrics.get("actual_value", 0.0))
			row[f"actual_qty_{suffix}"] = actual_qty
			row[f"actual_value_{suffix}"] = actual_value
			row["actual_qty_total"] += actual_qty
			row["actual_value_total"] += actual_value
		else:
			forecast_qty = rounded_quantity(metrics.get("forecast_qty", 0.0))
			forecast_value = rounded_value(metrics.get("forecast_value", 0.0))
			row[f"forecast_qty_raw_{suffix}"] = rounded_quantity(metrics.get("forecast_qty_raw", 0.0))
			row[f"forecast_qty_{suffix}"] = forecast_qty
			row[f"forecast_value_{suffix}"] = forecast_value
			row["forecast_qty_total"] += forecast_qty
			row["forecast_value_total"] += forecast_value

			if include_future_actuals:
				actual_qty = rounded_quantity(
					convert_qty_to_display(
						metrics.get("raw_actual_qty", 0.0),
						bucket.get("stock_uom"),
						filters.uom,
						report_state.masters,
						bucket.get("item_code"),
					)
				)
				actual_value = rounded_value(metrics.get("actual_value", 0.0))
				row[f"actual_qty_{suffix}"] = actual_qty
				row[f"actual_value_{suffix}"] = actual_value
				row["actual_qty_total"] += actual_qty
				row["actual_value_total"] += actual_value

		row["is_locked"] = max(row["is_locked"], cint(metrics.get("is_locked", 0)))

	return row


def get_columns(report_state, filters):
	columns = [
		{"label": _("Group / Item"), "fieldname": "display_name", "fieldtype": "Data", "width": 250},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 160},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 140},
		{"label": _("Territory"), "fieldname": "territory", "fieldtype": "Link", "options": "Territory", "width": 120},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 90},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 130},
		{"label": _("Sales Category"), "fieldname": "sales_category", "fieldtype": "Data", "width": 130},
		{"label": _("Product Segment"), "fieldname": "product_segment", "fieldtype": "Data", "width": 130},
	]

	for period in get_display_periods(report_state, filters):
		suffix = period_field_suffix(period)
		period_label = get_period_label(period, filters.periodicity)
		if period <= report_state.base_history_end:
			columns.append(
				{"label": _(f"{period_label} Actual Qty"), "fieldname": f"actual_qty_{suffix}", "fieldtype": "Float", "width": 130}
			)
			columns.append(
				{
					"label": _(f"{period_label} Actual Amount"),
					"fieldname": f"actual_value_{suffix}",
					"fieldtype": "Currency",
					"width": 140,
				}
			)
			continue

		if filters.show_actual:
			columns.append(
				{"label": _(f"{period_label} Actual Qty"), "fieldname": f"actual_qty_{suffix}", "fieldtype": "Float", "width": 130}
			)
			columns.append(
				{
					"label": _(f"{period_label} Actual Amount"),
					"fieldname": f"actual_value_{suffix}",
					"fieldtype": "Currency",
					"width": 140,
				}
			)

		columns.append(
			{
				"label": _(f"{period_label} Forecast Qty"),
				"fieldname": f"forecast_qty_{suffix}",
				"fieldtype": "Float",
				"width": 130,
			}
		)
		columns.append(
			{
				"label": _(f"{period_label} Forecast Amount"),
				"fieldname": f"forecast_value_{suffix}",
				"fieldtype": "Currency",
				"width": 140,
			}
		)

	columns.append(
		{"label": _("Forecast Qty Total"), "fieldname": "forecast_qty_total", "fieldtype": "Float", "width": 140}
	)
	columns.append(
		{
			"label": _("Forecast Amount Total"),
			"fieldname": "forecast_value_total",
			"fieldtype": "Currency",
			"width": 150,
		}
	)
	columns.append({"label": _("Locked"), "fieldname": "is_locked", "fieldtype": "Check", "width": 90})
	return columns


def get_display_periods(report_state, filters):
	periods = sorted(report_state.period_summary)
	if filters.show_past_data:
		return periods
	return [period for period in periods if period > report_state.base_history_end]


def get_chart_data(report_state):
	labels = []
	actual_values = []
	forecast_values = []
	for period in sorted(report_state.period_summary):
		bucket = report_state.period_summary[period]
		labels.append(bucket.get("period_label"))
		if period <= report_state.base_history_end:
			actual_values.append(rounded_quantity(bucket.get("actual_qty", 0.0)))
		else:
			actual_values.append(0)
		forecast_values.append(rounded_quantity(bucket.get("forecast_qty", 0.0)))

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{
					"name": _("Demand"),
					"values": actual_values,
					"chartType": "line",
				},
				{
					"name": _("Forecast"),
					"values": forecast_values,
					"chartType": "line",
				},
			],
		},
		"type": "line",
		"axisOptions": {"xIsSeries": 1},
		"lineOptions": {"regionFill": 1, "hideDots": 0, "dotSize": 4},
		"colors": ["#ff5858", "#5e64ff"],
		"height": 280,
	}


def get_summary_data(report_state):
	past_qty = 0.0
	past_value = 0.0
	future_forecast_qty = 0.0
	future_forecast_value = 0.0

	for period in sorted(report_state.period_summary):
		bucket = report_state.period_summary[period]
		if period <= report_state.base_history_end:
			past_qty += flt(bucket.get("actual_qty", 0.0))
			past_value += flt(bucket.get("actual_value", 0.0))
		else:
			future_forecast_qty += flt(bucket.get("forecast_qty", 0.0))
			future_forecast_value += flt(bucket.get("forecast_value", 0.0))

	return [
		{
			"label": _("Total Demand (Past Data) Qty"),
			"value": _format_summary_with_unit(past_qty / 1000.0, "K"),
			"datatype": "Data",
			"indicator": "blue",
		},
		{
			"label": _("Total Demand (Past Data) Value"),
			"value": _format_summary_with_unit(past_value / 1000000.0, "M AUD"),
			"datatype": "Data",
			"indicator": "blue",
		},
		{
			"label": _("Total Forecast (Future Data) Qty"),
			"value": _format_summary_with_unit(future_forecast_qty / 1000.0, "K"),
			"datatype": "Data",
			"indicator": "orange",
		},
		{
			"label": _("Total Forecast (Future Data) Value"),
			"value": _format_summary_with_unit(future_forecast_value / 1000000.0, "M AUD"),
			"datatype": "Data",
			"indicator": "orange",
		},
	]


def _format_summary_with_unit(value, unit):
	return f"{rounded_value(value):,.3f} {unit}"


def get_fact_period(child, parent, filters):
	if filters.based_on_document == "Sales Order":
		if child.get("payment_date"):
			return normalize_to_period(getdate(child.payment_date), filters.periodicity)
		if filters.forecast_based_on == "Delivery Date":
			source_date = child.get("delivery_date") or parent.get("delivery_date") or parent.get("transaction_date")
		else:
			source_date = parent.get("transaction_date")
		return normalize_to_period(getdate(source_date), filters.periodicity) if source_date else None

	if filters.based_on_document in {"Sales Invoice", "Delivery Note"}:
		source_date = parent.get("posting_date")
		return normalize_to_period(getdate(source_date), filters.periodicity) if source_date else None

	source_date = parent.get("transaction_date")
	return normalize_to_period(getdate(source_date), filters.periodicity) if source_date else None


def get_actual_qty(child):
	return flt(child.get("stock_qty") or child.get("qty") or 0)


def get_actual_value(child, filters):
	if filters.based_on_document == "Sales Order" and child.get("paid_amount"):
		return rounded_value(child.get("paid_amount"))
	for field in ("base_net_amount", "net_amount", "base_amount", "amount"):
		if child.get(field):
			return rounded_value(child.get(field))
	return rounded_value(flt(child.get("rate") or 0) * get_actual_qty(child))


def convert_qty_to_display(qty, stock_uom, target_uom, masters=None, item_code=None):
	qty = flt(qty)
	if not target_uom or not stock_uom or stock_uom == target_uom:
		return qty

	factor = _get_uom_display_factor(stock_uom, target_uom, masters, item_code)
	return qty / factor


def get_cached_effective_item_price(masters, item_code, customer, price_list, period_start, period_end, uom):
	cache_key = (
		item_code,
		customer,
		price_list,
		period_start.isoformat() if period_start else None,
		period_end.isoformat() if period_end else None,
		uom,
	)
	if cache_key not in masters.price_cache:
		masters.price_cache[cache_key] = get_effective_item_price_from_cache(
			masters=masters,
			item_code=item_code,
			customer=customer,
			price_list=price_list,
			period_start=period_start,
			period_end=period_end,
			uom=uom,
		)
	return masters.price_cache[cache_key]


def get_effective_item_price_from_cache(masters, item_code, customer, price_list, period_start, period_end, uom):
	price_list = price_list or getattr(masters.customer_map.get(customer) or frappe._dict(), "default_price_list", None)
	if not item_code or not price_list:
		return frappe._dict(price_list=price_list, price_list_rate=0.0, currency=None, uom=uom)

	rows = masters.price_index.get((item_code, price_list)) or []
	if not rows:
		return frappe._dict(price_list=price_list, price_list_rate=0.0, currency=None, uom=uom)

	period_start = getdate(period_start) if period_start else None
	period_end = getdate(period_end) if period_end else period_start

	def row_overlaps(row):
		if period_start and period_end:
			if row.valid_from and getdate(row.valid_from) > period_end:
				return False
			if row.valid_upto and getdate(row.valid_upto) < period_start:
				return False
		return True

	specific = [row for row in rows if row.get("uom") == uom]
	for row in specific or rows:
		if row_overlaps(row):
			return frappe._dict(
				price_list=price_list,
				price_list_rate=flt(row.price_list_rate),
				currency=row.currency,
				valid_from=getdate(row.valid_from) if row.valid_from else None,
				valid_upto=getdate(row.valid_upto) if row.valid_upto else None,
				uom=row.uom,
			)

	fallback = specific[0] if specific else rows[0]
	return frappe._dict(
		price_list=price_list,
		price_list_rate=flt(fallback.price_list_rate),
		currency=fallback.currency,
		valid_from=getdate(fallback.valid_from) if fallback.valid_from else None,
		valid_upto=getdate(fallback.valid_upto) if fallback.valid_upto else None,
		uom=fallback.uom,
	)


def get_actual_data_cutoff(filters):
	last_period = normalize_to_period(filters.to_date, filters.periodicity)
	for _ in range(filters.forecast_periods):
		last_period = next_period(last_period, filters.periodicity)
	return get_period_end(last_period, filters.periodicity)


def normalize_forecast_based_on(forecast_based_on, based_on_document):
	forecast_based_on = (forecast_based_on or "").strip()
	if forecast_based_on not in ALLOWED_FORECAST_BASES:
		forecast_based_on = ""
	if based_on_document == "Sales Order":
		return forecast_based_on if forecast_based_on in {"Order Date", "Delivery Date"} else "Delivery Date"
	return "Document Date"


def resolve_group_value(group_by, item_code, item_group, customer, territory, product_segment, sales_category):
	if group_by == "Item":
		return item_code
	if group_by == "Item Group":
		return item_group
	if group_by == "Customer":
		return customer
	if group_by == "Territory":
		return territory
	if group_by == "Product Segment":
		return product_segment
	if group_by == "Sales Category":
		return sales_category
	return item_code


def resolve_group_label(bucket, group_by):
	if group_by == "Item":
		return cstr(bucket.get("group_value") or bucket.get("item_code") or _("Not Set"))
	if group_by == "Item Group":
		return cstr(bucket.get("group_value") or _("Not Set"))
	if group_by == "Customer":
		return cstr(bucket.get("customer_name") or bucket.get("customer") or _("Not Set"))
	if group_by == "Territory":
		return cstr(bucket.get("territory") or _("Not Set"))
	if group_by == "Product Segment":
		return cstr(bucket.get("product_segment") or _("Not Set"))
	if group_by == "Sales Category":
		return cstr(bucket.get("sales_category") or _("Not Set"))
	return cstr(bucket.get("group_value") or _("Not Set"))


def resolve_leaf_label(bucket):
	parts = [bucket.get("item_code") or _("Not Set")]
	if bucket.get("item_name"):
		parts.append(bucket.get("item_name"))
	if bucket.get("customer"):
		parts.append(bucket.get("customer"))
	if bucket.get("warehouse"):
		parts.append(bucket.get("warehouse"))
	return " - ".join(cstr(part) for part in parts if part)


def load_source_facts(filters):
	parent_doctype = filters.based_on_document
	child_doctype = f"{parent_doctype} Item"
	data_to_date = get_actual_data_cutoff(filters)
	parent_fields = ["name", "customer", "company"]
	parent_filters = {"docstatus": 1, "company": filters.company}
	or_filters = None

	if parent_doctype == "Sales Order":
		parent_fields.extend(["transaction_date", "delivery_date"])
		parent_filters["transaction_date"] = ("between", [filters.from_date, data_to_date])
		if filters.forecast_based_on == "Delivery Date":
			or_filters = [{"delivery_date": ("between", [filters.from_date, data_to_date])}]
	elif parent_doctype in {"Sales Invoice", "Delivery Note"}:
		parent_fields.append("posting_date")
		parent_filters["posting_date"] = ("between", [filters.from_date, data_to_date])
	else:
		parent_fields.append("transaction_date")
		parent_filters["transaction_date"] = ("between", [filters.from_date, data_to_date])

	if filters.customer:
		parent_filters["customer"] = filters.customer

	parent_rows = frappe.get_all(
		parent_doctype,
		filters=parent_filters,
		or_filters=or_filters,
		fields=parent_fields,
		order_by="name asc",
		limit_page_length=0,
	)
	parent_map = {row.name: row for row in parent_rows}
	parent_names = list(parent_map)
	if not parent_names:
		return frappe._dict(parent_map=parent_map, child_rows=[], data_to_date=data_to_date)

	child_filters = {"parent": ("in", parent_names)}
	if filters.warehouse:
		warehouses = tuple(get_child_warehouses(filters.warehouse) or [filters.warehouse])
		child_filters["warehouse"] = ("in", warehouses)
	if filters.item_code:
		child_filters["item_code"] = filters.item_code

	child_fields = [
		"parent",
		"item_code",
		"warehouse",
		"uom",
		"qty",
		"stock_qty",
		"rate",
		"base_net_amount",
		"net_amount",
		"base_amount",
		"amount",
	]
	if parent_doctype == "Sales Order":
		child_fields.extend(["paid_amount", "payment_date", "delivery_date"])

	child_rows = frappe.get_all(
		child_doctype,
		filters=child_filters,
		fields=child_fields,
		order_by="parent asc, idx asc",
		limit_page_length=0,
	)
	return frappe._dict(parent_map=parent_map, child_rows=child_rows, data_to_date=data_to_date)


def load_master_maps(source, filters):
	item_codes = sorted({row.item_code for row in source.child_rows if row.get("item_code")})
	customer_names = sorted(
		{
			source.parent_map.get(row.parent).customer
			for row in source.child_rows
			if source.parent_map.get(row.parent) and source.parent_map.get(row.parent).customer
		}
	)
	warehouse_names = sorted({row.warehouse for row in source.child_rows if row.get("warehouse")})
	territory_names = set()

	item_fields = ["name", "item_name", "item_group", "stock_uom", "sales_uom"]
	if frappe.db.has_column("Item", "sales_category"):
		item_fields.append("sales_category")
	if frappe.db.has_column("Item", "product_segment"):
		item_fields.append("product_segment")

	items = frappe.get_all(
		"Item",
		filters={"name": ("in", item_codes)} if item_codes else None,
		fields=item_fields,
		limit_page_length=0,
	)
	item_map = {row.name: row for row in items}

	customers = frappe.get_all(
		"Customer",
		filters={"name": ("in", customer_names)} if customer_names else None,
		fields=["name", "customer_name", "territory", "default_price_list"],
		limit_page_length=0,
	)
	customer_map = {row.name: row for row in customers}
	for customer in customers:
		if customer.territory:
			territory_names.add(customer.territory)

	territories = frappe.get_all(
		"Territory",
		filters={"name": ("in", sorted(territory_names))} if territory_names else None,
		fields=["name", "territory_name", "parent_territory", "is_group"],
		limit_page_length=0,
	)
	territory_map = {row.name: row for row in territories}

	warehouses = frappe.get_all(
		"Warehouse",
		filters={"name": ("in", warehouse_names)} if warehouse_names else None,
		fields=["name", "warehouse_name", "parent_warehouse"],
		limit_page_length=0,
	)
	warehouse_map = {row.name: row for row in warehouses}

	return frappe._dict(
		item_map=item_map,
		customer_map=customer_map,
		territory_map=territory_map,
		warehouse_map=warehouse_map,
		price_cache={},
		uom_factor_cache={},
	)


def build_report_state(source, masters, filters):
	base_history_end = normalize_to_period(filters.to_date, filters.periodicity)
	lock_cutoff = (
		normalize_to_period(add_months(filters.manufacture_date, -2), filters.periodicity)
		if filters.manufacture_date
		else None
	)
	leaf_buckets = defaultdict(lambda: _make_bucket())
	period_summary = defaultdict(lambda: _make_period_summary_bucket())
	training_periods = get_period_range(filters.from_date, filters.to_date, filters.periodicity)

	for child in source.child_rows:
		parent = source.parent_map.get(child.parent)
		if not parent or not child.get("item_code"):
			continue

		item = masters.item_map.get(child.item_code)
		if not item:
			continue

		period = get_fact_period(child, parent, filters)
		if not period or period < filters.from_date or period > source.data_to_date:
			continue

		customer = parent.customer
		customer_info = masters.customer_map.get(customer) or frappe._dict()
		territory = customer_info.get("territory")
		item_group = item.get("item_group")
		sales_category = item.get("sales_category")
		product_segment = item.get("product_segment")
		group_value = resolve_group_value(
			filters.group_by,
			item_code=child.item_code,
			item_group=item_group,
			customer=customer,
			territory=territory,
			product_segment=product_segment,
			sales_category=sales_category,
		)

		key = (group_value, child.item_code, customer, child.get("warehouse") or None)
		bucket = leaf_buckets[key]
		bucket["group_value"] = group_value
		bucket["item_code"] = child.item_code
		bucket["item_name"] = item.get("item_name")
		bucket["item_group"] = item_group
		bucket["customer"] = customer
		bucket["customer_name"] = customer_info.get("customer_name")
		bucket["territory"] = territory
		bucket["sales_category"] = sales_category
		bucket["product_segment"] = product_segment
		bucket["warehouse"] = child.get("warehouse") or None
		bucket["stock_uom"] = item.get("stock_uom")
		bucket["selected_uom"] = filters.uom or item.get("sales_uom") or item.get("stock_uom")
		bucket["price_list"] = customer_info.get("default_price_list")

		raw_qty = get_actual_qty(child)
		actual_value = get_actual_value(child, filters)
		period_bucket = bucket["periods"][period]
		period_bucket["raw_actual_qty"] += raw_qty
		period_bucket["actual_value"] += actual_value

		period_summary[period]["period"] = period
		period_summary[period]["period_label"] = get_period_label(period, filters.periodicity)
		period_summary[period]["actual_qty"] += convert_qty_to_display(
			raw_qty, bucket["stock_uom"], filters.uom, masters, bucket["item_code"]
		)
		period_summary[period]["actual_value"] += actual_value

	for bucket in leaf_buckets.values():
		raw_series = [flt(bucket["periods"][period]["raw_actual_qty"]) for period in training_periods]
		forecast_series = holt_winters_forecast(
			raw_series,
			filters.alpha,
			filters.beta,
			filters.gamma,
			filters.season_length,
			filters.forecast_periods,
		)

		future_period = base_history_end
		last_actual = raw_series[-1] if raw_series else 0.0
		for forecast_raw in forecast_series:
			future_period = next_period(future_period, filters.periodicity)
			effective_raw = last_actual if lock_cutoff and future_period <= lock_cutoff else forecast_raw
			display_qty = convert_qty_to_display(
				effective_raw, bucket["stock_uom"], filters.uom, masters, bucket["item_code"]
			)
			price_info = get_cached_effective_item_price(
				masters=masters,
				item_code=bucket["item_code"],
				customer=bucket["customer"],
				price_list=bucket["price_list"],
				period_start=future_period,
				period_end=get_period_end(future_period, filters.periodicity),
				uom=bucket["selected_uom"],
			)
			forecast_value = rounded_value(display_qty * flt(price_info.price_list_rate))
			period_bucket = bucket["periods"][future_period]
			period_bucket["forecast_qty_raw"] += effective_raw
			period_bucket["forecast_qty"] += display_qty
			period_bucket["forecast_value"] += forecast_value
			period_bucket["is_locked"] = 1 if lock_cutoff and future_period <= lock_cutoff else 0
			period_summary[future_period]["period"] = future_period
			period_summary[future_period]["period_label"] = get_period_label(
				future_period, filters.periodicity
			)
			period_summary[future_period]["forecast_qty"] += display_qty
			period_summary[future_period]["forecast_value"] += forecast_value

	group_buckets = defaultdict(lambda: _make_bucket())
	group_children = defaultdict(list)
	for bucket in leaf_buckets.values():
		group_bucket = group_buckets[bucket["group_value"]]
		group_bucket["group_value"] = bucket["group_value"]
		group_bucket["item_code"] = bucket["group_value"]
		group_bucket["item_name"] = bucket["group_value"]
		group_bucket["item_group"] = bucket["item_group"]
		group_bucket["customer"] = bucket["customer"]
		group_bucket["customer_name"] = bucket["customer_name"]
		group_bucket["territory"] = bucket["territory"]
		group_bucket["sales_category"] = bucket["sales_category"]
		group_bucket["product_segment"] = bucket["product_segment"]
		group_bucket["warehouse"] = bucket["warehouse"]
		group_bucket["stock_uom"] = bucket["stock_uom"]
		group_bucket["selected_uom"] = bucket["selected_uom"]
		group_bucket["price_list"] = bucket["price_list"]
		for period, metrics in bucket["periods"].items():
			target = group_bucket["periods"][period]
			target["raw_actual_qty"] += metrics.get("raw_actual_qty", 0.0)
			target["actual_value"] += metrics.get("actual_value", 0.0)
			target["forecast_qty_raw"] += metrics.get("forecast_qty_raw", 0.0)
			target["forecast_qty"] += metrics.get("forecast_qty", 0.0)
			target["forecast_value"] += metrics.get("forecast_value", 0.0)
			target["is_locked"] = max(target["is_locked"], metrics.get("is_locked", 0))
		group_children[bucket["group_value"]].append(bucket)

	return frappe._dict(
		leaf_buckets=leaf_buckets,
		group_buckets=group_buckets,
		group_children=group_children,
		period_summary=period_summary,
		base_history_end=base_history_end,
		training_periods=training_periods,
	)


def _make_bucket():
	return {
		"group_value": None,
		"item_code": None,
		"item_name": None,
		"item_group": None,
		"customer": None,
		"customer_name": None,
		"territory": None,
		"sales_category": None,
		"product_segment": None,
		"warehouse": None,
		"stock_uom": None,
		"selected_uom": None,
		"price_list": None,
		"periods": defaultdict(
			lambda: {
				"raw_actual_qty": 0.0,
				"actual_value": 0.0,
				"forecast_qty_raw": 0.0,
				"forecast_qty": 0.0,
				"forecast_value": 0.0,
				"is_locked": 0,
			}
		),
	}


def _make_period_summary_bucket():
	return {
		"period": None,
		"period_label": None,
		"actual_qty": 0.0,
		"actual_value": 0.0,
		"forecast_qty": 0.0,
		"forecast_value": 0.0,
	}


def get_period_range(from_date, to_date, periodicity):
	periods = []
	current = normalize_to_period(from_date, periodicity)
	last_period = normalize_to_period(to_date, periodicity)
	while current <= last_period:
		periods.append(current)
		current = next_period(current, periodicity)
	return periods


def get_period_label(period, periodicity):
	if periodicity == "Weekly":
		return f"Week of {period.isoformat()}"
	if periodicity == "Monthly":
		return period.strftime("%b %Y")
	if periodicity == "Quarterly":
		quarter = ((period.month - 1) // 3) + 1
		return f"Q{quarter} {period.year}"
	if periodicity == "Half-Yearly":
		half = 1 if period.month == 1 else 2
		return f"H{half} {period.year}"
	return str(period.year)


def period_field_suffix(period):
	return period.isoformat().replace("-", "_")


def normalize_to_period(value, periodicity):
	value = getdate(value)
	if periodicity == "Weekly":
		return value - timedelta(days=value.weekday())
	if periodicity == "Monthly":
		return date(value.year, value.month, 1)
	if periodicity == "Quarterly":
		month = ((value.month - 1) // 3) * 3 + 1
		return date(value.year, month, 1)
	if periodicity == "Half-Yearly":
		month = 1 if value.month <= 6 else 7
		return date(value.year, month, 1)
	return date(value.year, 1, 1)


def next_period(period_start, periodicity):
	if periodicity == "Weekly":
		return period_start + timedelta(days=7)
	if periodicity == "Monthly":
		return add_months(period_start, 1)
	if periodicity == "Quarterly":
		return add_months(period_start, 3)
	if periodicity == "Half-Yearly":
		return add_months(period_start, 6)
	return add_months(period_start, 12)


def get_period_end(period_start, periodicity):
	return next_period(period_start, periodicity) - timedelta(days=1)


def holt_winters_forecast(data, alpha, beta, gamma, season_length, periods):
	if not data:
		return [0] * periods

	if len(data) < max(2, season_length):
		average = max(0.0, sum(data) / len(data))
		return [rounded_quantity(average)] * periods

	series = [max(0.0, flt(value)) for value in data]
	season_length = min(season_length, len(series))

	level = series[0]
	trend = _initial_trend(series, season_length)
	seasonals = _initial_seasonals(series, season_length)

	for idx, value in enumerate(series):
		seasonal = seasonals[idx % season_length]
		prev_level = level
		level = alpha * (value - seasonal) + (1 - alpha) * (level + trend)
		trend = beta * (level - prev_level) + (1 - beta) * trend
		seasonals[idx % season_length] = gamma * (value - level) + (1 - gamma) * seasonal

	forecast = []
	for horizon in range(1, periods + 1):
		seasonal = seasonals[(len(series) + horizon - 1) % season_length]
		value = level + horizon * trend + seasonal
		forecast.append(rounded_quantity(max(0.0, value)))
	return forecast


def _initial_trend(series, season_length):
	if len(series) < season_length * 2:
		return (series[-1] - series[0]) / max(len(series) - 1, 1)
	trend_sum = 0
	for idx in range(season_length):
		trend_sum += (series[idx + season_length] - series[idx]) / season_length
	return trend_sum / season_length


def _initial_seasonals(series, season_length):
	seasonals = [0.0] * season_length
	n_seasons = max(len(series) // season_length, 1)
	season_averages = []
	for season_idx in range(n_seasons):
		start = season_idx * season_length
		chunk = series[start : start + season_length]
		if chunk:
			season_averages.append(sum(chunk) / len(chunk))
	if not season_averages:
		return seasonals
	for idx in range(season_length):
		values = []
		for season_idx, avg in enumerate(season_averages):
			position = season_idx * season_length + idx
			if position < len(series):
				values.append(series[position] - avg)
		seasonals[idx] = sum(values) / len(values) if values else 0.0
	return seasonals


def get_display_periods(report_state, filters):
	periods = sorted(report_state.period_summary)
	if filters.show_past_data:
		return periods
	return [period for period in periods if period > report_state.base_history_end]


def get_chart_data(report_state):
	labels = []
	actual_values = []
	forecast_values = []
	for period in sorted(report_state.period_summary):
		bucket = report_state.period_summary[period]
		labels.append(bucket.get("period_label"))
		actual_values.append(rounded_quantity(bucket.get("actual_qty", 0.0)))
		forecast_values.append(rounded_quantity(bucket.get("forecast_qty", 0.0)))
	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _("Actual"), "values": actual_values},
				{"name": _("Forecast"), "values": forecast_values},
			],
		},
		"type": "line",
		"colors": ["#5e64ff", "#ff7a45"],
		"lineOptions": {
			"regionFill": 1,
			"hideDots": 0,
			"dotSize": 4,
		},
	}


def get_fact_period(child, parent, filters):
	if filters.based_on_document == "Sales Order":
		if child.get("payment_date"):
			return normalize_to_period(getdate(child.payment_date), filters.periodicity)
		if filters.forecast_based_on == "Delivery Date":
			source_date = child.get("delivery_date") or parent.get("delivery_date") or parent.get("transaction_date")
		else:
			source_date = parent.get("transaction_date")
		return normalize_to_period(getdate(source_date), filters.periodicity) if source_date else None
	if filters.based_on_document in {"Sales Invoice", "Delivery Note"}:
		source_date = parent.get("posting_date")
		return normalize_to_period(getdate(source_date), filters.periodicity) if source_date else None
	source_date = parent.get("transaction_date")
	return normalize_to_period(getdate(source_date), filters.periodicity) if source_date else None


def get_actual_qty(child):
	return flt(child.get("stock_qty") or child.get("qty") or 0)


def get_actual_value(child, filters):
	if filters.based_on_document == "Sales Order" and child.get("paid_amount"):
		return rounded_value(child.get("paid_amount"))
	for field in ("base_net_amount", "net_amount", "base_amount", "amount"):
		if child.get(field):
			return rounded_value(child.get(field))
	return rounded_value(flt(child.get("rate") or 0) * get_actual_qty(child))


def convert_qty_to_display(qty, stock_uom, target_uom, masters=None, item_code=None):
	qty = flt(qty)
	if not target_uom or not stock_uom or stock_uom == target_uom:
		return qty

	factor = _get_uom_display_factor(stock_uom, target_uom, masters, item_code)
	return qty / factor


def _get_uom_display_factor(stock_uom, target_uom, masters=None, item_code=None):
	cache = getattr(masters, "uom_factor_cache", None)
	if cache is None:
		cache = {}

	cache_key = (item_code, stock_uom, target_uom)
	if cache_key in cache:
		return cache[cache_key] or 1.0

	factor = None
	if item_code and target_uom and target_uom != stock_uom:
		conversion = get_item_conversion_factor(item_code, target_uom)
		if isinstance(conversion, dict):
			conversion = conversion.get("conversion_factor")
		factor = flt(conversion or 0)

	if not factor:
		try:
			factor = flt(get_uom_conv_factor(target_uom, stock_uom))
		except Exception:
			factor = 0

	cache[cache_key] = factor or 1.0
	if masters is not None:
		masters.uom_factor_cache = cache
	return cache[cache_key]


def get_cached_effective_item_price(masters, item_code, customer, price_list, period_start, period_end, uom):
	cache_key = (
		item_code,
		customer,
		price_list,
		period_start.isoformat() if period_start else None,
		period_end.isoformat() if period_end else None,
		uom,
	)
	if cache_key not in masters.price_cache:
		masters.price_cache[cache_key] = get_effective_item_price(
			item_code=item_code,
			customer=customer,
			price_list=price_list,
			period_start=period_start,
			period_end=period_end,
			uom=uom,
		)
	return masters.price_cache[cache_key]
