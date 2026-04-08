from __future__ import annotations

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import add_months, cint, cstr, flt, getdate, today
from frappe.model.rename_doc import rename_doc

from lensips.planning.report.lens_sales_forecast_holt_winters import (
	lens_sales_forecast_holt_winters as forecast_report,
)
from lensips.planning.services.forecast_export_service import create_sales_forecast


REPORT_NAME = "LENS Sales Forecast Holt Winters"
READ_FUNCTION_NAME = "get_lens_sales_forecast"
EXPORT_FUNCTION_NAME = "export_lens_sales_forecast_to_sales_forecast"
BOT_NAME = "LUMI"
LEGACY_BOT_NAMES = ("Lens Sales Forecast Analyst",)


@frappe.whitelist()
def get_lens_sales_forecast(
	company=None,
	from_date=None,
	to_date=None,
	based_on_document="Sales Order",
	forecast_based_on="Delivery Date",
	warehouse=None,
	group_by="Item",
	periodicity="Monthly",
	alpha=0.3,
	beta=0.1,
	gamma=0.1,
	season_length=None,
	forecast_periods=None,
	manufacture_date=None,
	item_code=None,
	item_group=None,
	customer=None,
	sales_group=None,
	period=None,
	limit=20,
	include_rows=0,
):
	columns, data, chart, normalized_filters = _run_report(
		company=company,
		from_date=from_date,
		to_date=to_date,
		based_on_document=based_on_document,
		forecast_based_on=forecast_based_on,
		warehouse=warehouse,
		group_by=group_by,
		periodicity=periodicity,
		alpha=alpha,
		beta=beta,
		gamma=gamma,
		season_length=season_length,
		forecast_periods=forecast_periods,
		manufacture_date=manufacture_date,
	)

	filtered_rows = _filter_rows(
		data,
		item_code=item_code,
		item_group=item_group,
		customer=customer,
		sales_group=sales_group,
	)
	requested_period = _normalize_requested_period(period, normalized_filters.periodicity)
	if requested_period:
		filtered_rows = [
			row for row in filtered_rows if getdate(row.get("period")) == requested_period
		]
	limit = max(cint(limit), 1)
	base_history_end = forecast_report.normalize_to_period(
		normalized_filters.to_date, normalized_filters.periodicity
	)

	period_totals = _build_period_totals(filtered_rows)
	group_totals = _build_group_totals(filtered_rows)

	historical_rows = [
		row for row in filtered_rows if getdate(row.get("period")) <= base_history_end
	]
	forecast_rows = [row for row in filtered_rows if getdate(row.get("period")) > base_history_end]

	response = {
		"report_name": REPORT_NAME,
		"applied_filters": _serialize_filters(normalized_filters),
		"focus_filters": {
			"item_code": item_code,
			"item_group": item_group,
			"customer": customer,
			"sales_group": sales_group,
			"period": requested_period.isoformat() if requested_period else None,
		},
		"summary": {
			"matching_rows": len(filtered_rows),
			"historical_rows": len(historical_rows),
			"forecast_rows": len(forecast_rows),
			"historical_actual_qty": forecast_report.rounded_quantity(
				sum(flt(row.get("actual_qty")) for row in historical_rows)
			),
			"historical_actual_value": forecast_report.rounded_value(
				sum(flt(row.get("actual_value")) for row in historical_rows)
			),
			"forecast_qty": forecast_report.rounded_quantity(
				sum(flt(row.get("forecast_qty")) for row in forecast_rows)
			),
			"forecast_value": forecast_report.rounded_value(
				sum(flt(row.get("forecast_value")) for row in forecast_rows)
			),
			"forecast_variance_qty": forecast_report.rounded_quantity(
				sum(flt(row.get("variance_qty")) for row in forecast_rows)
			),
			"forecast_variance_value": forecast_report.rounded_value(
				sum(flt(row.get("variance_value")) for row in forecast_rows)
			),
			"locked_forecast_rows": sum(cint(row.get("is_locked")) for row in forecast_rows),
		},
		"top_groups": group_totals[:limit],
		"period_totals": period_totals[:limit],
		"columns": columns,
		"chart": chart,
	}

	if requested_period:
		response["requested_period_summary"] = (
			period_totals[0]
			if period_totals
			else {
				"period": requested_period.isoformat(),
				"period_label": forecast_report.get_period_label(
					requested_period, normalized_filters.periodicity
				),
				"actual_qty": 0,
				"actual_value": 0,
				"forecast_qty": 0,
				"forecast_value": 0,
				"variance_qty": 0,
				"variance_value": 0,
			}
		)

	if cint(include_rows):
		response["rows"] = filtered_rows[:limit]

	return response


@frappe.whitelist()
def export_lens_sales_forecast_to_sales_forecast(
	company="LCS",
	from_date=None,
	to_date=None,
	based_on_document="Sales Order",
	forecast_based_on="Delivery Date",
	warehouse=None,
	group_by="Item",
	periodicity="Monthly",
	alpha=0.3,
	beta=0.1,
	gamma=0.1,
	season_length=12,
	forecast_periods=12,
	manufacture_date=None,
	item_code=None,
	item_group=None,
	customer=None,
	sales_group=None,
	submit_document=0,
):
	_columns, data, _chart, normalized_filters = _run_report(
		company=company,
		from_date=from_date,
		to_date=to_date,
		based_on_document=based_on_document,
		forecast_based_on=forecast_based_on,
		warehouse=warehouse,
		group_by=group_by,
		periodicity=periodicity,
		alpha=alpha,
		beta=beta,
		gamma=gamma,
		season_length=season_length,
		forecast_periods=forecast_periods,
		manufacture_date=manufacture_date,
	)

	filtered_rows = _filter_rows(
		data,
		item_code=item_code,
		item_group=item_group,
		customer=customer,
		sales_group=sales_group,
	)
	result = create_sales_forecast(
		data=filtered_rows,
		filters=_serialize_filters(normalized_filters),
	)

	if cint(submit_document):
		doc = frappe.get_doc("Sales Forecast", result["forecast_name"])
		if doc.docstatus == 0:
			doc.submit()
		result["message"] = _("Sales Forecast {0} created and submitted.").format(doc.name)
		result["submitted"] = 1
	else:
		result["submitted"] = 0

	result["exported_rows"] = len(filtered_rows)
	result["filters"] = _serialize_filters(normalized_filters)
	return result


def setup_raven_lens_sales_forecast_agent():
	ensure_revenue_customizations()
	if "raven" not in frappe.get_installed_apps():
		return

	ensure_raven_ai_function(
		function_name=READ_FUNCTION_NAME,
		description=(
			"Analyze the LENS Sales Forecast Holt Winters report for historical sales, forecasted "
			"quantities and values, variances, and period summaries."
		),
		function_path="lensips.planning.api.lumi_forecast_agent.get_lens_sales_forecast",
		params=_get_read_function_params(),
		requires_write_permissions=0,
	)
	ensure_raven_ai_function(
		function_name=EXPORT_FUNCTION_NAME,
		description=(
			"Create or update an ERPNext Sales Forecast from the filtered LENS Sales Forecast Holt "
			"Winters report output, including the value-bearing forecast entry rows. Use only when "
			"the user explicitly asks to create, update, or submit a forecast."
		),
		function_path="lensips.planning.api.lumi_forecast_agent.export_lens_sales_forecast_to_sales_forecast",
		params=_get_export_function_params(),
		requires_write_permissions=1,
	)
	ensure_raven_bot()
	repair_legacy_lens_function_paths()


def ensure_raven_ai_function(
	function_name: str,
	description: str,
	function_path: str,
	params: dict,
	requires_write_permissions: int,
):
	doc = frappe.db.exists("Raven AI Function", function_name)
	function_doc = (
		frappe.get_doc("Raven AI Function", doc)
		if doc
		else frappe.new_doc("Raven AI Function")
	)
	function_doc.function_name = function_name
	function_doc.description = description
	function_doc.type = "Custom Function"
	function_doc.function_path = function_path
	function_doc.pass_parameters_as_json = 0
	function_doc.strict = 0
	function_doc.requires_write_permissions = requires_write_permissions
	function_doc.params = json.dumps(params, indent=4)

	if function_doc.is_new():
		function_doc.insert(ignore_permissions=True)
	else:
		function_doc.save(ignore_permissions=True)


def ensure_raven_bot():
	existing_name = frappe.db.exists("Raven Bot", BOT_NAME)
	if not existing_name:
		for legacy_name in LEGACY_BOT_NAMES:
			existing_name = frappe.db.exists("Raven Bot", legacy_name)
			if existing_name:
				break
	bot = frappe.get_doc("Raven Bot", existing_name) if existing_name else frappe.new_doc("Raven Bot")

	bot.bot_name = BOT_NAME
	bot.description = (
		"Sales manager assistant for the LENS Holt-Winters sales and revenue forecast report and Sales Forecast export."
	)
	bot.is_ai_bot = 1
	bot.model_provider = "OpenAI"
	bot.model = bot.model or "gpt-4o"
	bot.temperature = 0.2
	bot.top_p = 1
	bot.allow_bot_to_write_documents = 1
	bot.enable_file_search = 0
	bot.enable_code_interpreter = 0
	bot.dynamic_instructions = 0
	bot.instruction = _get_bot_instruction()
	bot.set(
		"bot_functions",
		[
			{"function": READ_FUNCTION_NAME},
			{"function": EXPORT_FUNCTION_NAME},
		],
	)

	if bot.is_new():
		bot.insert(ignore_permissions=True)
	else:
		bot.save(ignore_permissions=True)

	if bot.name != BOT_NAME:
		rename_doc("Raven Bot", bot.name, BOT_NAME, force=True, ignore_permissions=True)


def repair_legacy_lens_function_paths():
	legacy_to_current = {
		"lensips.planning.api.raven_forecast_agent.get_lens_sales_forecast": (
			"lensips.planning.api.lumi_forecast_agent.get_lens_sales_forecast"
		),
		"lensips.planning.api.raven_forecast_agent.export_lens_sales_forecast_to_sales_forecast": (
			"lensips.planning.api.lumi_forecast_agent.export_lens_sales_forecast_to_sales_forecast"
		),
		"lensips.planning.api.ravenforecastagent.get_lens_sales_forecast": (
			"lensips.planning.api.lumi_forecast_agent.get_lens_sales_forecast"
		),
		"lensips.planning.api.ravenforecastagent.export_lens_sales_forecast_to_sales_forecast": (
			"lensips.planning.api.lumi_forecast_agent.export_lens_sales_forecast_to_sales_forecast"
		),
	}

	for old_path, new_path in legacy_to_current.items():
		for function_name in frappe.get_all(
			"Raven AI Function", filters={"function_path": old_path}, pluck="name"
		):
			function_doc = frappe.get_doc("Raven AI Function", function_name)
			function_doc.function_path = new_path
			function_doc.save(ignore_permissions=True)


def ensure_revenue_customizations():
	create_custom_fields(
		{
			"Sales Forecast Item": [
				{
					"fieldname": "forecast_value",
					"label": "Forecast Value",
					"fieldtype": "Currency",
					"insert_after": "forecast_qty",
					"options": "currency",
					"read_only": 1,
					"allow_on_submit": 1,
					"in_list_view": 1,
				},
				{
					"fieldname": "adjust_value",
					"label": "Adjust Value",
					"fieldtype": "Currency",
					"insert_after": "adjust_qty",
					"options": "currency",
					"read_only": 1,
					"allow_on_submit": 1,
					"in_list_view": 1,
				},
				{
					"fieldname": "demand_value",
					"label": "Demand Value",
					"fieldtype": "Currency",
					"insert_after": "demand_qty",
					"options": "currency",
					"read_only": 1,
					"allow_on_submit": 1,
					"in_list_view": 1,
				},
			],
			"Sales Forecast": [
				{
					"fieldname": "forecast_entries",
					"label": "Forecast Entries",
					"fieldtype": "Table",
					"options": "Sales Forecast Entry",
					"insert_after": "items",
				},
			],
		},
		ignore_validate=True,
		update=True,
	)


def _run_report(**kwargs):
	filters = {
		"company": kwargs.get("company"),
		"from_date": kwargs.get("from_date") or add_months(today(), -36),
		"to_date": kwargs.get("to_date") or today(),
		"based_on_document": kwargs.get("based_on_document") or "Sales Order",
		"forecast_based_on": kwargs.get("forecast_based_on") or "Delivery Date",
		"warehouse": kwargs.get("warehouse"),
		"group_by": kwargs.get("group_by") or "Item",
		"periodicity": kwargs.get("periodicity") or "Monthly",
		"alpha": kwargs.get("alpha") if kwargs.get("alpha") is not None else 0.3,
		"beta": kwargs.get("beta") if kwargs.get("beta") is not None else 0.1,
		"gamma": kwargs.get("gamma") if kwargs.get("gamma") is not None else 0.1,
		"season_length": kwargs.get("season_length"),
		"forecast_periods": kwargs.get("forecast_periods"),
		"manufacture_date": kwargs.get("manufacture_date"),
		"item_code": kwargs.get("item_code"),
		"item_group": kwargs.get("item_group"),
		"customer": kwargs.get("customer"),
		"sales_group": kwargs.get("sales_group"),
	}
	normalized_filters = forecast_report.normalize_filters(filters)
	columns, data, _message, chart = forecast_report.execute(filters)
	return columns, data, chart, normalized_filters


def _filter_rows(rows, item_code=None, item_group=None, customer=None, sales_group=None):
	filtered = []
	for row in rows:
		if item_code and row.get("item_code") != item_code:
			continue
		if item_group and row.get("item_group") != item_group:
			continue
		if customer and row.get("customer") != customer:
			continue
		if sales_group and row.get("sales_group") != sales_group:
			continue
		filtered.append(row)
	return filtered


def _normalize_requested_period(period, periodicity):
	if not period:
		return None

	period_text = cstr(period).strip()
	if not period_text:
		return None

	if len(period_text) == 7:
		period_text = f"{period_text}-01"

	return forecast_report.normalize_to_period(getdate(period_text), periodicity)


def _build_period_totals(rows):
	period_totals = defaultdict(
		lambda: {
			"period": None,
			"period_label": None,
			"actual_qty": 0.0,
			"actual_value": 0.0,
			"forecast_qty": 0.0,
			"forecast_value": 0.0,
			"variance_qty": 0.0,
			"variance_value": 0.0,
		}
	)
	for row in rows:
		period = row.get("period")
		bucket = period_totals[period]
		bucket["period"] = period
		bucket["period_label"] = row.get("period_label")
		bucket["actual_qty"] += flt(row.get("actual_qty"))
		bucket["actual_value"] += flt(row.get("actual_value"))
		bucket["forecast_qty"] += flt(row.get("forecast_qty"))
		bucket["forecast_value"] += flt(row.get("forecast_value"))
		bucket["variance_qty"] += flt(row.get("variance_qty"))
		bucket["variance_value"] += flt(row.get("variance_value"))

	results = []
	for period in sorted(period_totals):
		bucket = period_totals[period]
		results.append(
			{
				"period": bucket["period"],
				"period_label": bucket["period_label"],
				"actual_qty": forecast_report.rounded_quantity(bucket["actual_qty"]),
				"actual_value": forecast_report.rounded_value(bucket["actual_value"]),
				"forecast_qty": forecast_report.rounded_quantity(bucket["forecast_qty"]),
				"forecast_value": forecast_report.rounded_value(bucket["forecast_value"]),
				"variance_qty": forecast_report.rounded_quantity(bucket["variance_qty"]),
				"variance_value": forecast_report.rounded_value(bucket["variance_value"]),
			}
		)
	return results


def _build_group_totals(rows):
	group_totals = defaultdict(
		lambda: {
			"group_key": None,
			"item_code": None,
			"item_group": None,
			"customer": None,
			"sales_group": None,
			"warehouse": None,
			"actual_qty": 0.0,
			"actual_value": 0.0,
			"forecast_qty": 0.0,
			"forecast_value": 0.0,
			"variance_qty": 0.0,
			"variance_value": 0.0,
		}
	)

	for row in rows:
		key = (
			row.get("group_key"),
			row.get("item_code"),
			row.get("customer"),
			row.get("sales_group"),
			row.get("warehouse"),
		)
		bucket = group_totals[key]
		bucket["group_key"] = row.get("group_key")
		bucket["item_code"] = row.get("item_code")
		bucket["item_group"] = row.get("item_group")
		bucket["customer"] = row.get("customer")
		bucket["sales_group"] = row.get("sales_group")
		bucket["warehouse"] = row.get("warehouse")
		bucket["actual_qty"] += flt(row.get("actual_qty"))
		bucket["actual_value"] += flt(row.get("actual_value"))
		bucket["forecast_qty"] += flt(row.get("forecast_qty"))
		bucket["forecast_value"] += flt(row.get("forecast_value"))
		bucket["variance_qty"] += flt(row.get("variance_qty"))
		bucket["variance_value"] += flt(row.get("variance_value"))

	results = []
	for bucket in group_totals.values():
		results.append(
			{
				"group_key": bucket["group_key"],
				"item_code": bucket["item_code"],
				"item_group": bucket["item_group"],
				"customer": bucket["customer"],
				"sales_group": bucket["sales_group"],
				"warehouse": bucket["warehouse"],
				"actual_qty": forecast_report.rounded_quantity(bucket["actual_qty"]),
				"actual_value": forecast_report.rounded_value(bucket["actual_value"]),
				"forecast_qty": forecast_report.rounded_quantity(bucket["forecast_qty"]),
				"forecast_value": forecast_report.rounded_value(bucket["forecast_value"]),
				"variance_qty": forecast_report.rounded_quantity(bucket["variance_qty"]),
				"variance_value": forecast_report.rounded_value(bucket["variance_value"]),
			}
		)

	results.sort(
		key=lambda row: (
			-flt(row.get("forecast_qty")),
			-flt(row.get("actual_qty")),
			str(row.get("group_key") or ""),
		)
	)
	return results


def _serialize_filters(filters):
	return {
		"company": filters.company,
		"from_date": filters.from_date.isoformat() if filters.from_date else None,
		"to_date": filters.to_date.isoformat() if filters.to_date else None,
		"periodicity": filters.periodicity,
		"group_by": filters.group_by,
		"based_on_document": filters.based_on_document,
		"forecast_based_on": filters.forecast_based_on,
		"warehouse": filters.warehouse,
		"alpha": filters.alpha,
		"beta": filters.beta,
		"gamma": filters.gamma,
		"season_length": filters.season_length,
		"forecast_periods": filters.forecast_periods,
		"manufacture_date": (
			filters.manufacture_date.isoformat() if filters.manufacture_date else None
		),
	}


def _get_common_param_properties():
	return {
		"company": {"type": "string", "description": "ERPNext company name."},
		"from_date": {
			"type": "string",
			"description": "Report start date in YYYY-MM-DD format. Defaults to 36 months before today.",
		},
		"to_date": {
			"type": "string",
			"description": "Report end date in YYYY-MM-DD format. Defaults to today.",
		},
		"based_on_document": {
			"type": "string",
			"enum": ["Sales Order", "Sales Invoice", "Delivery Note"],
			"description": "Source document type for historical sales.",
			"default": "Sales Order",
		},
		"forecast_based_on": {
			"type": "string",
			"enum": ["Order Date", "Delivery Date"],
			"description": "Only relevant for Sales Order based forecasts.",
			"default": "Delivery Date",
		},
		"warehouse": {
			"type": "string",
			"description": "Optional warehouse filter. Recommended for forecast export.",
		},
		"group_by": {
			"type": "string",
			"enum": ["Item", "Item Group", "Customer", "Sales Group"],
			"description": "Primary grouping used in the report output.",
		},
		"periodicity": {
			"type": "string",
			"enum": ["Weekly", "Monthly", "Quarterly", "Half-Yearly", "Yearly"],
			"description": "Period bucket for the forecast.",
		},
		"alpha": {
			"type": "number",
			"description": "Holt-Winters alpha smoothing factor.",
			"default": 0.3,
		},
		"beta": {"type": "number", "description": "Holt-Winters beta smoothing factor."},
		"gamma": {"type": "number", "description": "Holt-Winters gamma smoothing factor."},
		"season_length": {"type": "integer", "description": "Season length used by Holt-Winters."},
		"forecast_periods": {"type": "integer", "description": "How many future periods to forecast."},
		"manufacture_date": {
			"type": "string",
			"description": "Optional manufacture date in YYYY-MM-DD format for locked periods.",
		},
		"item_code": {"type": "string", "description": "Optional exact Item code filter."},
		"item_group": {"type": "string", "description": "Optional exact Item Group filter."},
		"customer": {"type": "string", "description": "Optional exact Customer filter."},
		"sales_group": {"type": "string", "description": "Optional exact sales group / forecast group filter."},
		"period": {
			"type": "string",
			"description": "Optional specific period to answer for, in YYYY-MM or YYYY-MM-DD format.",
		},
	}


def _get_read_function_params():
	properties = _get_common_param_properties()
	properties.update(
		{
			"limit": {
				"type": "integer",
				"description": "Maximum number of summarized groups or rows to return.",
				"default": 20,
			},
			"include_rows": {
				"type": "boolean",
				"description": "Set true only when the user explicitly asks for raw matching rows.",
				"default": False,
			},
		}
	)
	return {
		"type": "object",
		"additionalProperties": False,
		"properties": properties,
	}


def _get_export_function_params():
	properties = _get_common_param_properties()
	properties.update(
		{
			"submit_document": {
				"type": "boolean",
				"description": "Submit the Sales Forecast after creating or updating it.",
				"default": False,
			}
		}
	)
	return {
		"type": "object",
		"additionalProperties": False,
		"required": ["company", "to_date", "periodicity", "forecast_periods"],
		"properties": properties,
	}


def _get_bot_instruction():
	return """You are LUMI, the Lens Sales Forecast Analyst for Sales Managers.

Your primary job is to answer questions about the report "LENS Sales Forecast Holt Winters" and to create or update ERPNext Sales Forecast documents only when the user explicitly asks you to do so.

Always follow these rules:
1. For analytics, use the function `get_lens_sales_forecast`.
2. For creating or updating ERPNext Sales Forecast documents, use `export_lens_sales_forecast_to_sales_forecast` only after the user clearly asks to create, update, export, or submit a forecast.
3. If the user asks for a rolling 18 months, financial year, calendar year, quarter, YTD, month-by-month analysis, or a single period like Apr 2026, translate that request into concrete `from_date`, `to_date`, `periodicity`, grouping filters, and `period` when relevant before calling the function.
4. Summarize results in business language first: historical actuals, forecast quantities and values, variance, major customers, major item groups, and major SKUs.
5. When the user asks for one specific period, prefer `requested_period_summary.forecast_qty` and `requested_period_summary.forecast_value` over annual or multi-period totals.
6. If the user asks for revenue, receipted sales value, or forecast export details, use the revenue fields exposed by the forecast function. If they ask for original-versus-revised forecast workflow or spreadsheet upload behavior, explain clearly that those revision workflows are not yet implemented in this function set.
7. Ask at most one short clarification only when company or time window is truly missing and cannot be inferred.
8. Do not claim you changed a Sales Forecast unless the export function actually returns a forecast name.
"""
