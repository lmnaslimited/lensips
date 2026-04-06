from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import frappe
from frappe.utils import add_months, cint, flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	normalized_filters = normalize_filters(filters)
	columns = get_columns()
	historical_rows = get_data(normalized_filters)
	grouped_data = group_data(historical_rows)
	data = build_forecast_rows(grouped_data, normalized_filters)
	chart = get_chart_data(data)
	return columns, data, None, chart


def normalize_filters(raw_filters):
	periodicity = (raw_filters.get("periodicity") or "Monthly").strip()
	if periodicity not in {"Monthly", "Weekly"}:
		periodicity = "Monthly"

	group_by = (raw_filters.get("group_by") or "Item").strip()
	if group_by not in {"Item", "Item Group", "Customer", "Sales Group"}:
		group_by = "Item"

	from_date = getdate(raw_filters.get("from_date"))
	to_date = getdate(raw_filters.get("to_date"))
	if not from_date or not to_date:
		frappe.throw("From Date and To Date are required.")
	if from_date > to_date:
		frappe.throw("From Date cannot be after To Date.")

	forecast_periods = cint(raw_filters.get("forecast_periods") or 12)
	minimum_periods = 12 if periodicity == "Monthly" else 52
	forecast_periods = max(forecast_periods, minimum_periods)

	return frappe._dict(
		company=raw_filters.get("company"),
		from_date=from_date,
		to_date=to_date,
		periodicity=periodicity,
		group_by=group_by,
		alpha=flt(raw_filters.get("alpha") or 0.2),
		beta=flt(raw_filters.get("beta") or 0.1),
		gamma=flt(raw_filters.get("gamma") or 0.1),
		season_length=max(cint(raw_filters.get("season_length") or 12), 2),
		forecast_periods=forecast_periods,
		manufacture_date=getdate(raw_filters.get("manufacture_date")) if raw_filters.get("manufacture_date") else None,
	)


def get_data(filters):
	has_forecast_group = frappe.db.has_column("Customer", "forecast_group")
	forecast_group_expr = "COALESCE(cust.forecast_group, si.customer)" if has_forecast_group else "si.customer"

	period_expr = (
		"DATE_FORMAT(si.posting_date, '%%Y-%%m-01')"
		if filters.periodicity == "Monthly"
		else "DATE_SUB(si.posting_date, INTERVAL WEEKDAY(si.posting_date) DAY)"
	)

	conditions = [
		"si.docstatus = 1",
		"si.posting_date BETWEEN %(from_date)s AND %(to_date)s",
	]
	if filters.company:
		conditions.append("si.company = %(company)s")

	query = f"""
		SELECT
			sii.item_code,
			MAX(sii.item_name) AS item_name,
			MAX(i.item_group) AS item_group,
			si.customer,
			{forecast_group_expr} AS sales_group,
			{period_expr} AS period,
			SUM(sii.qty) AS actual_qty
		FROM `tabSales Invoice Item` sii
		INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
		LEFT JOIN `tabItem` i ON i.item_code = sii.item_code
		LEFT JOIN `tabCustomer` cust ON cust.name = si.customer
		WHERE {' AND '.join(conditions)}
		GROUP BY
			sii.item_code,
			i.item_group,
			si.customer,
			sales_group,
			period
		ORDER BY period
	"""
	return frappe.db.sql(query, filters, as_dict=True)


def group_data(rows):
	grouped = defaultdict(list)
	for row in rows:
		key = (
			row.get("item_code"),
			row.get("item_name"),
			row.get("item_group"),
			row.get("customer"),
			row.get("sales_group"),
		)
		grouped[key].append(
			{
				"period": getdate(row.get("period")),
				"actual_qty": flt(row.get("actual_qty")),
			}
		)

	for points in grouped.values():
		points.sort(key=lambda x: x["period"])

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
	lock_cutoff = add_months(filters.manufacture_date, -2) if filters.manufacture_date else None

	for key, points in grouped_data.items():
		item_code, item_name, item_group, customer, sales_group = key
		actual_by_period = {point["period"]: point["actual_qty"] for point in points}
		periods_sorted = sorted(actual_by_period)
		series = [actual_by_period[period] for period in periods_sorted]

		forecast_values = holt_winters_forecast(
			series,
			filters.alpha,
			filters.beta,
			filters.gamma,
			filters.season_length,
			filters.forecast_periods,
		)
		last_actual = series[-1] if series else 0.0

		for period in periods_sorted:
			group_key = resolve_group_key(filters.group_by, item_code, item_group, customer, sales_group)
			row = make_row(
				group_key=group_key,
				item_code=item_code,
				item_group=item_group,
				customer=customer,
				sales_group=sales_group,
				period=period,
				actual_qty=actual_by_period[period],
				forecast_qty=0,
				is_locked=0,
			)
			rows.append(row)
			consolidated_by_period[period] += row["actual_qty"]

		future_period = normalize_to_period(filters.to_date, filters.periodicity)
		for index, forecast_qty in enumerate(forecast_values):
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
				period=future_period,
				actual_qty=0,
				forecast_qty=forecast_or_frozen,
				is_locked=is_locked,
			)
			rows.append(row)
			consolidated_by_period[future_period] += row["forecast_qty"]

	for row in rows:
		row["consolidated_forecast_qty"] = consolidated_by_period[row["period"]]
		row["period"] = row["period"].isoformat()

	rows.sort(key=lambda d: (d["period"], cstr_or_empty(d["group_key"]), cstr_or_empty(d["item_code"])))
	return rows


def normalize_to_period(value, periodicity):
	if periodicity == "Monthly":
		return date(value.year, value.month, 1)
	return value - timedelta(days=value.weekday())


def next_period(period_start, periodicity):
	if periodicity == "Monthly":
		return add_months(period_start, 1)
	return period_start + timedelta(days=7)


def resolve_group_key(group_by, item_code, item_group, customer, sales_group):
	mapping = {
		"Item": item_code,
		"Item Group": item_group,
		"Customer": customer,
		"Sales Group": sales_group,
	}
	return mapping.get(group_by)


def make_row(group_key, item_code, item_group, customer, sales_group, period, actual_qty, forecast_qty, is_locked):
	return {
		"group_key": group_key,
		"item_code": item_code,
		"item_group": item_group,
		"customer": customer,
		"sales_group": sales_group,
		"period": period,
		"quarter": get_quarter_label(period),
		"actual_qty": flt(actual_qty),
		"forecast_qty": flt(forecast_qty),
		"consolidated_forecast_qty": 0,
		"is_locked": is_locked,
	}


def get_quarter_label(period):
	quarter = ((period.month - 1) // 3) + 1
	return f"Q{quarter}"


def get_chart_data(data):
	aggregated = defaultdict(lambda: {"actual": 0.0, "forecast": 0.0, "consolidated": 0.0})
	for row in data:
		period = row.get("period")
		aggregated[period]["actual"] += flt(row.get("actual_qty"))
		aggregated[period]["forecast"] += flt(row.get("forecast_qty"))
		aggregated[period]["consolidated"] += flt(row.get("consolidated_forecast_qty"))

	labels = sorted(aggregated)
	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": "Actual", "values": [aggregated[label]["actual"] for label in labels]},
				{"name": "Forecast", "values": [aggregated[label]["forecast"] for label in labels]},
				{
					"name": "Consolidated",
					"values": [aggregated[label]["consolidated"] for label in labels],
				},
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
		{"label": "Period", "fieldname": "period", "fieldtype": "Date", "width": 110},
		{"label": "Quarter", "fieldname": "quarter", "fieldtype": "Data", "width": 70},
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
