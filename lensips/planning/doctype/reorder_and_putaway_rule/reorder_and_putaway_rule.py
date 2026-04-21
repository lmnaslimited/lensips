from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_first_day, get_first_day_of_week, getdate

from erpnext.stock.doctype.item.item import get_uom_conv_factor

ALLOWED_FREQUENCIES = {"Weekly", "Monthly"}
ALLOWED_REORDER_QTY_BASES = {"Forecast", "Demand"}
ITEM_REQUEST_TYPES = {
	"Purchase",
	"Transfer",
	"Material Issue",
	"Manufacture",
}


class ReorderandPutawayRule(Document):
	def autoname(self):
		self.name = f"{self.warehouse}-{self.frequency}-{self.period}"

	def validate(self):
		self.validate_frequency()
		self.validate_core_fields()
		self.update_coverage_metrics()
		self.sync_status()

	def before_submit(self):
		if not self.get("items"):
			frappe.throw(_("Please add at least one Reorder and Putaway item before submitting."))
		self.update_coverage_metrics()
		if flt(self.planned_item_percentage) < 100:
			frappe.msgprint(
				_(
					"Only {0}% of the forecast items are planned for this warehouse and period."
				).format(self.planned_item_percentage or 0),
				title=_("Coverage Warning"),
				indicator="orange",
			)
		self.sync_status("Submitted")

	def on_submit(self):
		self.sync_item_reorder_and_putaway_rules()
		self.sync_status("Submitted")

	def on_cancel(self):
		self.sync_status("Cancelled")

	def validate_frequency(self):
		if self.frequency not in ALLOWED_FREQUENCIES:
			frappe.throw(_("Frequency must be Weekly or Monthly."))

	def validate_core_fields(self):
		if not self.sales_forecast:
			frappe.throw(_("Sales Forecast is required."))
		if not self.company or not self.warehouse or not self.period:
			frappe.throw(_("Company, Warehouse and Period are required."))

	def sync_status(self, value: str | None = None):
		self.status = value or self.status or "Draft"

	def update_coverage_metrics(self):
		planned_count = len(self.get("items") or [])
		total_count = _get_forecast_item_count(self.sales_forecast, self.warehouse, self.frequency, self.period)
		self.planned_item_count = planned_count
		self.total_forecast_item_count = total_count
		self.planned_item_percentage = _compute_percentage(planned_count, total_count)

	def sync_item_reorder_and_putaway_rules(self):
		rows_by_item = defaultdict(list)
		for row in self.get("items") or []:
			if row.item_code and row.warehouse:
				rows_by_item[row.item_code].append(row)

		for item_code, rows in rows_by_item.items():
			item_doc = frappe.get_doc("Item", item_code)
			item_updated = False
			for row in rows:
				if _sync_item_reorder(item_doc, row):
					item_updated = True
				_sync_putaway_rule(self.company, row)

			if item_updated:
				item_doc.save(ignore_permissions=True)


@frappe.whitelist()
def create_from_sales_forecast(sales_forecast, warehouse=None):
	sales_forecast_doc = frappe.get_doc("Sales Forecast", sales_forecast)

	if sales_forecast_doc.docstatus != 1 or sales_forecast_doc.status != "Planned":
		frappe.throw(_("Only submitted Planned Sales Forecast documents can be converted."))

	frequency = _normalize_frequency(sales_forecast_doc.frequency)
	planning_settings = _get_planning_capacity_settings()
	rows = _get_source_rows(sales_forecast_doc, frequency, warehouse=warehouse)
	if not rows:
		frappe.throw(_("No locked Sales Forecast rows were found."))

	parent_docs = {}
	for row in rows:
		period = _get_period_start(row.delivery_date, frequency)
		key = (row.warehouse, period)
		parent = parent_docs.get(key)
		if not parent:
			parent = _get_or_create_parent_doc(
				sales_forecast=sales_forecast_doc.name,
				company=sales_forecast_doc.company,
				warehouse=row.warehouse,
				period=period,
				frequency=frequency,
			)
			parent_docs[key] = parent

		parent.append(
			"items",
			_build_child_row(
				sales_forecast_item=row,
				company=sales_forecast_doc.company,
				warehouse=row.warehouse,
				period=period,
				frequency=frequency,
				planning_settings=planning_settings,
			),
		)

	names = []
	for parent in parent_docs.values():
		if parent.is_new():
			parent.insert(ignore_permissions=True)
		else:
			parent.save(ignore_permissions=True)
		names.append(parent.name)

	return {
		"names": names,
		"message": _("Created {0} Reorder and Putaway Rule(s).").format(len(names)),
	}


@frappe.whitelist()
def refresh_from_sales_forecast(name):
	doc = frappe.get_doc("Reorder and Putaway Rule", name)
	if doc.docstatus != 0:
		frappe.throw(_("Only draft Reorder and Putaway Rules can be refreshed from Sales Forecast."))

	frequency = _normalize_frequency(doc.frequency)
	planning_settings = _get_planning_capacity_settings()
	sales_forecast_doc = frappe.get_doc("Sales Forecast", doc.sales_forecast)
	rows = _get_source_rows(
		sales_forecast_doc,
		frequency,
		warehouse=doc.warehouse,
		period=doc.period,
	)
	if not rows:
		frappe.throw(_("No locked Sales Forecast rows were found for this warehouse and period."))

	doc.set("items", [])
	for row in rows:
		doc.append(
			"items",
			_build_child_row(
				sales_forecast_item=row,
				company=doc.company,
				warehouse=doc.warehouse,
				period=doc.period,
				frequency=frequency,
				planning_settings=planning_settings,
			),
		)

	doc.update_coverage_metrics()
	doc.save(ignore_permissions=True)
	return {
		"name": doc.name,
		"planned_item_count": doc.planned_item_count,
		"total_forecast_item_count": doc.total_forecast_item_count,
		"planned_item_percentage": doc.planned_item_percentage,
	}


def _normalize_frequency(frequency):
	frequency = (frequency or "").strip()
	if frequency not in ALLOWED_FREQUENCIES:
		frappe.throw(_("Frequency must be Weekly or Monthly."))
	return frequency


def _get_planning_capacity_settings():
	settings = frappe.db.get_value(
		"Planning Settings",
		"Planning Settings",
		[
			"forecast_to_capacity_ratio",
			"forecast_to_reorder_level_ratio",
			"reorder_quantity_based_on",
		],
		as_dict=True,
	)
	if not settings:
		frappe.throw(
			_(
				"Please configure Planning Settings before creating Reorder and Putaway Rules."
			)
		)

	missing = []
	if settings.get("forecast_to_capacity_ratio") in (None, ""):
		missing.append(_("Forecast To Capacity Ratio"))
	if settings.get("forecast_to_reorder_level_ratio") in (None, ""):
		missing.append(_("Forecast To Reorder Level Ratio"))
	if not (settings.get("reorder_quantity_based_on") or "").strip():
		missing.append(_("Reorder Quantity Based On"))

	if missing:
		frappe.throw(
			_(
				"Please configure the following Planning Settings before creating Reorder and Putaway Rules: {0}."
			).format(", ".join(missing))
		)

	reorder_quantity_based_on = settings.get("reorder_quantity_based_on").strip()
	if reorder_quantity_based_on not in ALLOWED_REORDER_QTY_BASES:
		frappe.throw(
			_(
				"Invalid Reorder Quantity Based On value in Planning Settings: {0}. Allowed values are Forecast or Demand."
			).format(reorder_quantity_based_on)
		)

	return {
		"forecast_to_capacity_ratio": flt(settings.forecast_to_capacity_ratio),
		"forecast_to_reorder_level_ratio": flt(settings.forecast_to_reorder_level_ratio),
		"reorder_quantity_based_on": reorder_quantity_based_on,
	}


def _get_source_rows(sales_forecast_doc, frequency, warehouse=None, period=None):
	rows = []
	for row in sales_forecast_doc.get("items") or []:
		if not flt(row.locked):
			continue
		if not row.warehouse or not row.delivery_date:
			continue
		if warehouse and row.warehouse != warehouse:
			continue
		if period and _get_period_start(row.delivery_date, frequency) != getdate(period):
			continue
		rows.append(row)
	return rows


def _get_period_start(delivery_date, frequency):
	delivery_date = getdate(delivery_date)
	if frequency == "Weekly":
		return get_first_day_of_week(delivery_date)
	return get_first_day(delivery_date)


def _get_forecast_item_count(sales_forecast, warehouse, frequency, period):
	if not sales_forecast or not warehouse or not period:
		return 0

	sales_forecast_doc = frappe.get_doc("Sales Forecast", sales_forecast)
	return len(
		_get_all_rows_for_period(
			sales_forecast_doc,
			frequency,
			warehouse=warehouse,
			period=period,
		)
	)


def _get_all_rows_for_period(sales_forecast_doc, frequency, warehouse=None, period=None):
	rows = []
	for row in sales_forecast_doc.get("items") or []:
		if not row.warehouse or not row.delivery_date:
			continue
		if warehouse and row.warehouse != warehouse:
			continue
		if period and _get_period_start(row.delivery_date, frequency) != getdate(period):
			continue
		rows.append(row)
	return rows


def _compute_percentage(planned_count, total_count):
	if not total_count:
		return 0
	return flt((flt(planned_count) / flt(total_count)) * 100)


def _round_qty(value):
	return flt(round(flt(value)))


def _get_or_create_parent_doc(sales_forecast, company, warehouse, period, frequency):
	parent_name = _get_parent_name(warehouse=warehouse, frequency=frequency, period=period)
	existing_doc = frappe.db.exists("Reorder and Putaway Rule", parent_name)
	if existing_doc:
		doc = frappe.get_doc("Reorder and Putaway Rule", parent_name)
		if doc.docstatus != 0:
			frappe.throw(_("Reorder and Putaway Rule {0} already exists.").format(parent_name))
		doc.set("items", [])
		doc.sales_forecast = sales_forecast
		doc.company = company
		doc.warehouse = warehouse
		doc.period = period
		doc.frequency = frequency
		doc.status = "Draft"
		return doc

	existing = frappe.get_all(
		"Reorder and Putaway Rule",
		filters={
			"sales_forecast": sales_forecast,
			"company": company,
			"warehouse": warehouse,
			"period": period,
			"frequency": frequency,
			"docstatus": 0,
		},
		pluck="name",
		order_by="modified desc",
		limit=1,
	)
	if existing:
		doc = frappe.get_doc("Reorder and Putaway Rule", existing[0])
		doc.set("items", [])
		doc.sales_forecast = sales_forecast
		doc.company = company
		doc.warehouse = warehouse
		doc.period = period
		doc.frequency = frequency
		doc.status = "Draft"
		return doc

	doc = frappe.new_doc("Reorder and Putaway Rule")
	doc.name = parent_name
	doc.sales_forecast = sales_forecast
	doc.company = company
	doc.warehouse = warehouse
	doc.period = period
	doc.frequency = frequency
	doc.status = "Draft"
	return doc


def _get_parent_name(warehouse, frequency, period):
	return f"{warehouse}-{frequency}-{period}"


def _build_child_row(sales_forecast_item, company, warehouse, period, frequency, planning_settings):
	item_doc = frappe.get_cached_doc("Item", sales_forecast_item.item_code)
	request_type = _resolve_material_request_type(
		item_doc.default_material_request_type, warehouse=warehouse, company=company
	)
	target_uom = _get_target_uom(item_doc, request_type)
	source_uom = sales_forecast_item.uom or item_doc.sales_uom or item_doc.stock_uom

	demand_qty = _convert_qty(item_doc, flt(sales_forecast_item.demand_qty), source_uom, target_uom)
	forecast_qty = _convert_qty(item_doc, flt(sales_forecast_item.forecast_qty), source_uom, target_uom)
	capacity = forecast_qty * flt(planning_settings["forecast_to_capacity_ratio"])
	warehouse_reorder_level = forecast_qty * flt(planning_settings["forecast_to_reorder_level_ratio"])
	reorder_qty_basis = planning_settings["reorder_quantity_based_on"]
	warehouse_reorder_qty = forecast_qty if reorder_qty_basis == "Forecast" else demand_qty

	return {
		"sales_forecast_item": sales_forecast_item.name,
		"item_code": sales_forecast_item.item_code,
		"item_name": sales_forecast_item.item_name,
		"delivery_date": sales_forecast_item.delivery_date,
		"warehouse": warehouse,
		"material_request_type": request_type,
		"uom": target_uom,
		"demand_qty": _round_qty(demand_qty),
		"forecast_qty": _round_qty(forecast_qty),
		"capacity": _round_qty(capacity),
		"warehouse_reorder_level": _round_qty(warehouse_reorder_level),
		"warehouse_reorder_qty": _round_qty(warehouse_reorder_qty),
	}


def _resolve_material_request_type(default_type, warehouse, company):
	request_type = (default_type or "Transfer").strip()
	if request_type == "Material Transfer":
		request_type = "Transfer"
	if request_type not in ITEM_REQUEST_TYPES:
		request_type = "Transfer"

	if request_type == "Purchase":
		return "Purchase"

	if request_type == "Manufacture":
		source_warehouse = frappe.db.get_single_value("Planning Settings", "source_warehouse")
		if source_warehouse and warehouse != source_warehouse:
			return "Transfer"
		return "Manufacture"

	return request_type


def _get_target_uom(item_doc, request_type):
	planning_transfer_uom = frappe.db.get_single_value("Planning Settings", "default_transfer_uom")

	if request_type == "Purchase":
		return item_doc.purchase_uom or item_doc.stock_uom
	if request_type == "Transfer":
		return planning_transfer_uom or item_doc.stock_uom
	return item_doc.stock_uom


def _convert_qty(item_doc, qty, from_uom, to_uom):
	qty = flt(qty)
	from_uom = from_uom or item_doc.stock_uom
	to_uom = to_uom or item_doc.stock_uom
	if from_uom == to_uom:
		return qty

	from_factor = _get_uom_factor(item_doc, from_uom)
	to_factor = _get_uom_factor(item_doc, to_uom)
	return qty * from_factor / to_factor


def _get_uom_factor(item_doc, uom):
	uom = uom or item_doc.stock_uom
	if uom == item_doc.stock_uom:
		return 1.0

	for row in item_doc.get("uoms") or []:
		if row.uom == uom:
			return flt(row.conversion_factor)

	factor = get_uom_conv_factor(uom, item_doc.stock_uom)
	if not factor:
		frappe.throw(
			_("Missing UOM conversion for Item {0} from {1} to {2}.").format(
				item_doc.name, uom, item_doc.stock_uom
			)
		)
	return flt(factor)


def _sync_item_reorder(item_doc, row):
	reorder_rows = item_doc.get("reorder_levels") or []
	target_row = next((d for d in reorder_rows if d.warehouse == row.warehouse), None)
	if not target_row:
		target_row = item_doc.append("reorder_levels", {})

	target_row.warehouse = row.warehouse
	target_row.warehouse_reorder_level = flt(row.warehouse_reorder_level or row.forecast_qty)
	target_row.warehouse_reorder_qty = flt(row.warehouse_reorder_qty or row.demand_qty)
	target_row.material_request_type = row.material_request_type
	return True


def _sync_putaway_rule(company, row):
	rule_name = frappe.db.get_value(
		"Putaway Rule",
		{"item_code": row.item_code, "warehouse": row.warehouse, "priority": 1},
		"name",
	)

	if rule_name:
		rule = frappe.get_doc("Putaway Rule", rule_name)
	else:
		rule = frappe.new_doc("Putaway Rule")

	rule.company = company
	rule.item_code = row.item_code
	rule.warehouse = row.warehouse
	rule.priority = 1
	rule.capacity = flt(row.capacity)
	rule.uom = row.uom
	rule.disable = 0
	rule.save(ignore_permissions=True)
