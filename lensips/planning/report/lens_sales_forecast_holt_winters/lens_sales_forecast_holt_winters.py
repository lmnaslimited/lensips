from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import frappe
from frappe.utils import add_months, cint, flt, getdate

from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses
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
			SUM(
				COALESCE(
					NULLIF(child_doc.base_net_amount, 0),
					NULLIF(child_doc.net_amount, 0),
					NULLIF(child_doc.base_amount, 0),
					NULLIF(child_doc.amount, 0),
					COALESCE(child_doc.rate, 0) * COALESCE(NULLIF(child_doc.stock_qty, 0), child_doc.qty, 0)
				)
			) AS actual_value
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


def normalize_forecast_based_on(forecast_based_on, based_on_document):
	forecast_based_on = (forecast_based_on or "").strip()
	if forecast_based_on not in ALLOWED_FORECAST_BASES:
		forecast_based_on = ""

	if based_on_document == "Sales Order":
		return forecast_based_on if forecast_based_on in {"Order Date", "Delivery Date"} else "Delivery Date"

	return "Document Date"


def get_date_expression(filters):
	if filters.based_on_document == "Sales Order":
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
	variance_by_period = defaultdict(float)
	variance_value_by_period = defaultdict(float)
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
		if row["period_start"] > base_history_end:
			row["variance_qty"] = flt(row["forecast_qty"]) - flt(row["actual_qty"])
			row["variance_value"] = flt(row["forecast_value"]) - flt(row["actual_value"])
			variance_by_period[row["period_start"]] += row["variance_qty"]
			variance_value_by_period[row["period_start"]] += row["variance_value"]
		else:
			row["variance_qty"] = None
			row["variance_value"] = None

	for row in final_rows:
		row["variance_qty_total"] = (
			variance_by_period[row["period_start"]] if row["period_start"] > base_history_end else None
		)
		row["variance_value_total"] = (
			variance_value_by_period[row["period_start"]]
			if row["period_start"] > base_history_end
			else None
		)
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
		"variance_qty": 0,
		"variance_value": 0,
		"variance_qty_total": 0,
		"variance_value_total": 0,
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
	aggregated = defaultdict(lambda: {"label": "", "actual": 0.0, "forecast": 0.0, "variance": 0.0})
	for row in data:
		period = row.get("period")
		aggregated[period]["label"] = row.get("period_label")
		aggregated[period]["actual"] += flt(row.get("actual_qty"))
		aggregated[period]["forecast"] += flt(row.get("forecast_qty"))
		aggregated[period]["variance"] = flt(row.get("variance_qty_total"))

	periods = sorted(aggregated)
	return {
		"data": {
			"labels": [aggregated[period]["label"] for period in periods],
			"datasets": [
				{"name": "Actual", "values": [aggregated[period]["actual"] for period in periods]},
				{"name": "Variance", "values": [aggregated[period]["variance"] for period in periods]},
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
		{
			"label": "Variance Qty",
			"fieldname": "variance_qty",
			"fieldtype": "Float",
			"width": 130,
		},
		{
			"label": "Variance Value",
			"fieldname": "variance_value",
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"label": "Total Variance Qty",
			"fieldname": "variance_qty_total",
			"fieldtype": "Float",
			"width": 180,
		},
		{
			"label": "Total Variance Value",
			"fieldname": "variance_value_total",
			"fieldtype": "Currency",
			"width": 180,
		},
		{"label": "Locked", "fieldname": "is_locked", "fieldtype": "Check", "width": 90},
	]


def cstr_or_empty(value):
	return str(value) if value is not None else ""


def rounded_quantity(value):
	return int(round(flt(value)))


def rounded_value(value):
	return round(flt(value), 2)
