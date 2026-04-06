from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import frappe
from frappe.utils import add_months, cint, flt, getdate

from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses


ALLOWED_DOCUMENTS = {"Sales Order", "Sales Invoice", "Delivery Note"}
ALLOWED_PERIODICITIES = {"Weekly", "Monthly", "Quarterly", "Half-Yearly", "Yearly"}
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

	based_on_document = (raw_filters.get("based_on_document") or "Sales Invoice").strip()
	if based_on_document not in ALLOWED_DOCUMENTS:
		based_on_document = "Sales Invoice"

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
		warehouse=raw_filters.get("warehouse"),
		alpha=flt(raw_filters.get("alpha") or 0.2),
		beta=flt(raw_filters.get("beta") or 0.1),
		gamma=flt(raw_filters.get("gamma") or 0.1),
		season_length=season_length,
		forecast_periods=forecast_periods,
		manufacture_date=getdate(raw_filters.get("manufacture_date")) if raw_filters.get("manufacture_date") else None,
	)


def get_data(filters):
	parent_table = f"`tab{filters.based_on_document}`"
	child_table = f"`tab{filters.based_on_document} Item`"
	date_field = get_date_field(filters.based_on_document)
	has_forecast_group = frappe.db.has_column("Customer", "forecast_group")
	forecast_group_expr = "COALESCE(cust.forecast_group, parent_doc.customer)" if has_forecast_group else "parent_doc.customer"

	conditions = [
		"parent_doc.docstatus = 1",
		f"parent_doc.{date_field} BETWEEN %(from_date)s AND %(to_date)s",
	]
	query_filters = {
		"from_date": filters.from_date,
		"to_date": filters.to_date,
	}

	if filters.company:
		conditions.append("parent_doc.company = %(company)s")
		query_filters["company"] = filters.company

	if filters.warehouse:
		warehouses = tuple(get_child_warehouses(filters.warehouse) or [filters.warehouse])
		conditions.append("child_doc.warehouse IN %(warehouses)s")
		query_filters["warehouses"] = warehouses

	query = f"""
		SELECT
			child_doc.item_code,
			MAX(child_doc.item_name) AS item_name,
			MAX(item.item_group) AS item_group,
			parent_doc.customer,
			{forecast_group_expr} AS sales_group,
			child_doc.warehouse,
			parent_doc.{date_field} AS posting_date,
			SUM(COALESCE(child_doc.stock_qty, child_doc.qty, 0)) AS actual_qty
		FROM {child_table} child_doc
		INNER JOIN {parent_table} parent_doc ON parent_doc.name = child_doc.parent
		LEFT JOIN `tabItem` item ON item.item_code = child_doc.item_code
		LEFT JOIN `tabCustomer` cust ON cust.name = parent_doc.customer
		WHERE {' AND '.join(conditions)}
		GROUP BY
			child_doc.item_code,
			item.item_group,
			parent_doc.customer,
			sales_group,
			child_doc.warehouse,
			parent_doc.{date_field}
		ORDER BY parent_doc.{date_field}
	"""
	return frappe.db.sql(query, query_filters, as_dict=True)


def get_date_field(doctype):
	if doctype in {"Sales Invoice", "Delivery Note"}:
		return "posting_date"

	return "transaction_date"


def group_data(rows, filters):
	grouped = defaultdict(lambda: defaultdict(float))
	for row in rows:
		key = (
			row.get("item_code"),
			row.get("item_name"),
			row.get("item_group"),
			row.get("customer"),
			row.get("sales_group"),
			row.get("warehouse"),
		)
		period = normalize_to_period(getdate(row.get("posting_date")), filters.periodicity)
		grouped[key][period] += flt(row.get("actual_qty"))

	return grouped


def holt_winters_forecast(data, alpha, beta, gamma, season_length, periods):
	if not data:
		return [0.0] * periods

	if len(data) < max(2, season_length):
		average = max(0.0, sum(data) / len(data))
		return [average] * periods

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
		forecast.append(max(0.0, value))

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
	rows = []
	consolidated_by_period = defaultdict(float)
	history_periods = get_period_range(filters.from_date, filters.to_date, filters.periodicity)
	lock_cutoff = None
	if filters.manufacture_date:
		lock_cutoff = normalize_to_period(add_months(filters.manufacture_date, -2), filters.periodicity)

	for key, actual_by_period in grouped_data.items():
		item_code, item_name, item_group, customer, sales_group, warehouse = key
		series = [flt(actual_by_period.get(period, 0.0)) for period in history_periods]

		forecast_values = holt_winters_forecast(
			series,
			filters.alpha,
			filters.beta,
			filters.gamma,
			filters.season_length,
			filters.forecast_periods,
		)
		last_actual = series[-1] if series else 0.0

		for period in history_periods:
			group_key = resolve_group_key(filters.group_by, item_code, item_group, customer, sales_group)
			row = make_row(
				group_key=group_key,
				item_code=item_code,
				item_group=item_group,
				customer=customer,
				sales_group=sales_group,
				warehouse=warehouse,
				period=period,
				actual_qty=actual_by_period.get(period, 0.0),
				forecast_qty=0,
				is_locked=0,
				periodicity=filters.periodicity,
			)
			rows.append(row)
			consolidated_by_period[period] += row["actual_qty"]

		future_period = history_periods[-1] if history_periods else normalize_to_period(filters.to_date, filters.periodicity)
		for forecast_qty in forecast_values:
			future_period = next_period(future_period, filters.periodicity)
			is_locked = 1 if lock_cutoff and future_period <= lock_cutoff else 0
			forecast_or_frozen = max(0.0, last_actual if is_locked else forecast_qty)

			group_key = resolve_group_key(filters.group_by, item_code, item_group, customer, sales_group)
			row = make_row(
				group_key=group_key,
				item_code=item_code,
				item_group=item_group,
				customer=customer,
				sales_group=sales_group,
				warehouse=warehouse,
				period=future_period,
				actual_qty=0,
				forecast_qty=forecast_or_frozen,
				is_locked=is_locked,
				periodicity=filters.periodicity,
			)
			rows.append(row)
			consolidated_by_period[future_period] += row["forecast_qty"]

	for row in rows:
		row["consolidated_forecast_qty"] = consolidated_by_period[row["period_start"]]
		row["period"] = row["period_start"].isoformat()

	rows.sort(
		key=lambda d: (
			d["period"],
			cstr_or_empty(d["group_key"]),
			cstr_or_empty(d["warehouse"]),
			cstr_or_empty(d["item_code"]),
		)
	)
	return rows


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
	customer,
	sales_group,
	warehouse,
	period,
	actual_qty,
	forecast_qty,
	is_locked,
	periodicity,
):
	return {
		"group_key": group_key,
		"item_code": item_code,
		"item_group": item_group,
		"customer": customer,
		"sales_group": sales_group,
		"warehouse": warehouse,
		"period_start": period,
		"period": period,
		"period_label": get_period_label(period, periodicity),
		"actual_qty": flt(actual_qty),
		"forecast_qty": flt(forecast_qty),
		"consolidated_forecast_qty": 0,
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
	aggregated = defaultdict(lambda: {"label": "", "actual": 0.0, "forecast": 0.0, "consolidated": 0.0})
	for row in data:
		period = row.get("period")
		aggregated[period]["label"] = row.get("period_label")
		aggregated[period]["actual"] += flt(row.get("actual_qty"))
		aggregated[period]["forecast"] += flt(row.get("forecast_qty"))
		aggregated[period]["consolidated"] = flt(row.get("consolidated_forecast_qty"))

	periods = sorted(aggregated)
	return {
		"data": {
			"labels": [aggregated[period]["label"] for period in periods],
			"datasets": [
				{"name": "Actual", "values": [aggregated[period]["actual"] for period in periods]},
				{"name": "Forecast", "values": [aggregated[period]["forecast"] for period in periods]},
				{"name": "Consolidated", "values": [aggregated[period]["consolidated"] for period in periods]},
			],
		},
		"type": "line",
	}


def get_columns():
	return [
		{"label": "Group Key", "fieldname": "group_key", "fieldtype": "Data", "width": 180},
		{"label": "Item", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": "Item Group", "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 130},
		{"label": "Customer", "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 150},
		{"label": "Sales Group", "fieldname": "sales_group", "fieldtype": "Data", "width": 130},
		{"label": "Warehouse", "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": "Period Start", "fieldname": "period", "fieldtype": "Date", "width": 110},
		{"label": "Period Label", "fieldname": "period_label", "fieldtype": "Data", "width": 110},
		{"label": "Actual Qty", "fieldname": "actual_qty", "fieldtype": "Float", "width": 120},
		{"label": "Forecast Qty", "fieldname": "forecast_qty", "fieldtype": "Float", "width": 130},
		{
			"label": "Consolidated Forecast Qty",
			"fieldname": "consolidated_forecast_qty",
			"fieldtype": "Float",
			"width": 180,
		},
		{"label": "Locked", "fieldname": "is_locked", "fieldtype": "Check", "width": 90},
	]


def cstr_or_empty(value):
	return str(value) if value is not None else ""
