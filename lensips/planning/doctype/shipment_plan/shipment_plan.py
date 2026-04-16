# Copyright (c) 2026, LMNAs and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, flt, getdate

WEEKDAY_TO_INDEX = {
	"Monday": 0,
	"Tuesday": 1,
	"Wednesday": 2,
	"Thursday": 3,
	"Friday": 4,
	"Saturday": 5,
	"Sunday": 6,
}


class ShipmentPlan(Document):
	def validate(self):
		self.set_shipment_dates()
		_update_totals(self)
		self.set_capacity_flags()

	def before_submit(self):
		self.set_capacity_flags()
		if self.exceeds_truck_capacity:
			frappe.throw(
				_(
					"Shipment Plan exceeds truck prerequisites. Pallets: {0}/{1}, Weight: {2}/{3}"
				).format(
					flt(self.total_pallets),
					flt(self.max_allowed_pallets),
					flt(self.total_shipment_weight),
					flt(self.max_allowed_weight),
				)
			)
		self.build_plan()
		self.status = "Submitted"

	def on_submit(self):
		self.status = "Submitted"

	def on_cancel(self):
		self.status = "Cancelled"

	def set_capacity_flags(self):
		capacity = _get_truck_prerequisites_data(self)
		self.max_allowed_pallets = capacity["max_allowed_pallets"]
		self.max_allowed_weight = capacity["max_allowed_weight"]
		self.exceeds_truck_capacity = capacity["exceeds_truck_capacity"]
		self.capacity_message = capacity["capacity_message"]

	@frappe.whitelist()
	def get_truck_prerequisites(self):
		return _get_truck_prerequisites_data(self)

	def set_shipment_dates(self):
		if not (self.source_warehouse and self.shipment_profile):
			return

		schedule = _get_warehouse_shipment_schedule(self.shipment_profile, self.source_warehouse)
		if not schedule:
			return

		if not schedule.shipment_sent_on:
			frappe.throw(
				_("Shipment Profile {0} has no Shipment Sent On value for warehouse {1}").format(
					self.shipment_profile, self.source_warehouse
				)
			)

		shipment_date = _get_next_shipment_date(getdate(), schedule.shipment_sent_on, flt(schedule.picking_time))
		self.shipment_date = shipment_date
		self.picking_date = add_days(shipment_date, -flt(schedule.picking_time or 0))

	@frappe.whitelist()
	def build_plan(self):
		self.set_open_material_requests()
		self.set_planned_items()

	@frappe.whitelist()
	def set_open_material_requests(self):
		requests = get_open_material_requests(self.company, self.source_warehouse, self.target_warehouse)
		self.set("material_requests", [])
		for mr in requests:
			self.append(
				"material_requests",
				{
					"material_request": mr.name,
					"transaction_date": mr.transaction_date,
					"required_by": mr.schedule_date,
					"status": mr.status,
				},
			)
		_update_totals(self)

	@frappe.whitelist()
	def set_planned_items(self):
		self.set("planned_items", [])
		transfer_uom = _get_transfer_uom()
		shipment_uom = _get_target_shipment_uom(self.target_warehouse)
		capacity = flt(_get_truck_capacity_in_pallets())
		used_capacity = 0.0

		for mr in get_open_material_requests(self.company, self.source_warehouse, self.target_warehouse):
			for item in mr.items:
				open_qty = _get_open_qty(item)
				if open_qty <= 0:
					continue

				item_doc = frappe.get_doc("Item", item.item_code)
				item_stock_uom = item.stock_uom or _get_item_stock_uom(item.item_code)
				stock_qty = _convert_item_qty(item.item_code, open_qty, item.uom, item_stock_uom)
				planned_qty = _convert_item_qty(item.item_code, open_qty, item.uom, transfer_uom)
				if planned_qty <= 0:
					continue

				shipment_qty = _convert_item_qty(item.item_code, planned_qty, transfer_uom, shipment_uom)
				if shipment_qty <= 0:
					continue

				if capacity and used_capacity + shipment_qty > capacity:
					remaining = capacity - used_capacity
					if remaining <= 0:
						return
					shipment_qty = remaining
					planned_qty = _convert_item_qty(item.item_code, shipment_qty, shipment_uom, transfer_uom)
					stock_qty = _convert_item_qty(item.item_code, planned_qty, transfer_uom, item_stock_uom)
					open_qty = _convert_item_qty(item.item_code, planned_qty, transfer_uom, item.uom)

				planned_stock_qty = _convert_item_qty(item.item_code, planned_qty, transfer_uom, item_stock_uom)
				shipment_stock_qty = _convert_item_qty(item.item_code, shipment_qty, shipment_uom, item_stock_uom)
				weight_per_unit = flt(item_doc.weight_per_unit or 0)
				weight_uom = item_doc.weight_uom
				shipment_weight = shipment_stock_qty * weight_per_unit if weight_per_unit else 0
				actual_qty = _get_source_warehouse_actual_qty(item.item_code, self.source_warehouse)

				used_capacity += shipment_qty
				self.append(
					"planned_items",
					{
						"material_request": mr.name,
						"material_request_item": item.name,
						"item_code": item.item_code,
						"item_name": item.item_name,
						"stock_uom": item_stock_uom,
						"open_uom": item.uom,
						"transfer_uom": transfer_uom,
						"shipment_uom": shipment_uom,
						"open_qty": open_qty,
						"planned_qty": planned_qty,
						"shipment_qty": shipment_qty,
						"open_uom_conversion": _get_item_uom_factor(item_doc, item.uom),
						"planned_uom_conversion": _get_item_uom_factor(item_doc, transfer_uom),
						"shipment_uom_conversion": _get_item_uom_factor(item_doc, shipment_uom),
						"stock_qty": stock_qty,
						"planned_stock_qty": planned_stock_qty,
						"shipment_stock_qty": shipment_stock_qty,
						"actual_qty": actual_qty,
						"weight_per_unit": weight_per_unit,
						"shipment_weight": shipment_weight,
						"weight_uom": weight_uom,
						"stock_status": _get_stock_status(actual_qty, planned_stock_qty, shipment_stock_qty),
						"source_warehouse": self.source_warehouse,
						"target_warehouse": self.target_warehouse,
					},
				)

		_update_totals(self)
		self.status = "Submitted" if self.get("planned_items") else "Draft"


def _update_totals(doc):
	doc.total_open_qty = sum(flt(d.open_qty) for d in doc.get("planned_items") or [])
	doc.total_planned_qty = sum(flt(d.planned_qty) for d in doc.get("planned_items") or [])
	doc.total_shipment_qty = sum(flt(d.shipment_qty) for d in doc.get("planned_items") or [])
	doc.total_stock_qty = sum(flt(d.stock_qty) for d in doc.get("planned_items") or [])
	doc.total_planned_stock_qty = sum(flt(d.planned_stock_qty) for d in doc.get("planned_items") or [])
	doc.total_shipment_stock_qty = sum(flt(d.shipment_stock_qty) for d in doc.get("planned_items") or [])
	doc.total_weight_per_unit = sum(flt(d.weight_per_unit) for d in doc.get("planned_items") or [])
	doc.total_shipment_weight = sum(flt(d.shipment_weight) for d in doc.get("planned_items") or [])
	doc.total_pallets = sum(flt(d.shipment_qty) for d in doc.get("planned_items") or [])


@frappe.whitelist()
def recalculate_totals(name: str):
	doc = frappe.get_doc("Shipment Plan", name)
	_update_totals(doc)
	doc.save(ignore_permissions=True)
	return {
		"total_open_qty": doc.total_open_qty,
		"total_planned_qty": doc.total_planned_qty,
		"total_shipment_qty": doc.total_shipment_qty,
		"total_stock_qty": doc.total_stock_qty,
		"total_planned_stock_qty": doc.total_planned_stock_qty,
		"total_shipment_stock_qty": doc.total_shipment_stock_qty,
		"total_weight_per_unit": doc.total_weight_per_unit,
		"total_shipment_weight": doc.total_shipment_weight,
		"total_pallets": doc.total_pallets,
	}


@frappe.whitelist()
def get_truck_prerequisites(name: str):
	doc = frappe.get_doc("Shipment Plan", name)
	return _get_truck_prerequisites_data(doc)


def _get_source_warehouse_actual_qty(item_code: str, warehouse: str) -> float:
	if not item_code or not warehouse:
		return 0
	return flt(
		frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": warehouse},
			"actual_qty",
		)
		or 0
	)


def _get_stock_status(actual_qty: float, planned_qty: float, shipment_qty: float) -> str:
	actual_qty = flt(actual_qty)
	planned_qty = flt(planned_qty)
	shipment_qty = flt(shipment_qty)

	if actual_qty <= 0:
		return "Short"
	if shipment_qty and actual_qty < shipment_qty:
		return "Short"
	if planned_qty and actual_qty < planned_qty:
		return "Low"
	return "Good"


def _get_truck_prerequisites_data(doc):
	total_pallets = round(flt(doc.total_pallets or 0), 2)
	total_weight = round(flt(doc.total_shipment_weight or 0), 2)
	max_allowed_pallets = round(flt(_get_truck_capacity_in_pallets() or 0), 2)
	template_weight = round(flt(_get_shipment_parcel_template_weight(doc.shipment_parcel_template) or 0), 2)
	max_allowed_weight = round(template_weight if template_weight else 0, 2)
	exceeds_pallets = bool(max_allowed_pallets and total_pallets > max_allowed_pallets)
	exceeds_weight = bool(max_allowed_weight and total_weight > max_allowed_weight)
	message_parts = []
	if exceeds_pallets:
		message_parts.append(
			_("Pallets {0} exceed allowed {1}").format(total_pallets, max_allowed_pallets)
		)
	if exceeds_weight:
		message_parts.append(
			_("Weight {0} exceeds allowed {1}").format(total_weight, max_allowed_weight)
		)
	return {
		"total_pallets": total_pallets,
		"total_weight": total_weight,
		"max_allowed_pallets": max_allowed_pallets,
		"template_weight": template_weight,
		"max_allowed_weight": max_allowed_weight,
		"exceeds_pallets": exceeds_pallets,
		"exceeds_weight": exceeds_weight,
		"exceeds_truck_capacity": exceeds_pallets or exceeds_weight,
		"capacity_message": "<br>".join(message_parts) if message_parts else "",
	}



def get_open_material_requests(company: str, source_warehouse: str, target_warehouse: str):
	filters = {
		"company": company,
		"material_request_type": "Material Transfer",
		"docstatus": 1,
		"status": ["not in", ["Stopped", "Cancelled"]],
		"set_from_warehouse": source_warehouse,
		"set_warehouse": target_warehouse,
	}
	requests = frappe.get_all(
		"Material Request",
		filters=filters,
		fields=["name", "transaction_date", "schedule_date", "status"],
		order_by="transaction_date asc, creation asc",
	)
	result = []
	for row in requests:
		doc = frappe.get_doc("Material Request", row.name)
		open_items = [d for d in doc.items if d.from_warehouse == source_warehouse and d.warehouse == target_warehouse and _get_open_qty(d) > 0]
		if open_items:
			result.append(doc)
	return result


@frappe.whitelist()
def calculate_planned_item_row(
	item_code: str,
	open_qty: float,
	planned_qty: float | None = None,
	transfer_uom: str | None = None,
	shipment_uom: str | None = None,
	open_uom: str | None = None,
	source_warehouse: str | None = None,
	mode: str | None = None,
):
	item_doc = frappe.get_doc("Item", item_code)
	stock_uom = item_doc.stock_uom
	weight_per_unit = flt(item_doc.weight_per_unit or 0)
	weight_uom = item_doc.weight_uom
	transfer_uom = transfer_uom or frappe.db.get_single_value("Planning Settings", "default_transfer_uom") or frappe.db.get_single_value("Planning Settings", "carton_uom")
	shipment_uom = shipment_uom or None
	open_uom = open_uom or stock_uom
	open_qty = flt(open_qty)
	if mode == "recalculate_from_uom" or planned_qty is None:
		planned_qty = _convert_item_qty(item_code, open_qty, open_uom, transfer_uom)
	else:
		planned_qty = flt(planned_qty)

	shipment_qty = _convert_item_qty(item_code, planned_qty, transfer_uom, shipment_uom) if shipment_uom else 0
	stock_qty = _convert_item_qty(item_code, open_qty, open_uom, stock_uom)
	planned_stock_qty = _convert_item_qty(item_code, planned_qty, transfer_uom, stock_uom)
	shipment_stock_qty = _convert_item_qty(item_code, shipment_qty, shipment_uom, stock_uom) if shipment_uom else 0
	actual_qty = _get_source_warehouse_actual_qty(item_code, source_warehouse)
	return {
		"stock_uom": stock_uom,
		"open_uom": open_uom,
		"transfer_uom": transfer_uom,
		"shipment_uom": shipment_uom,
		"open_qty": open_qty,
		"planned_qty": planned_qty,
		"shipment_qty": shipment_qty,
		"open_uom_conversion": _get_item_uom_factor(item_doc, open_uom),
		"planned_uom_conversion": _get_item_uom_factor(item_doc, transfer_uom),
		"shipment_uom_conversion": _get_item_uom_factor(item_doc, shipment_uom) if shipment_uom else 0,
		"stock_qty": stock_qty,
		"planned_stock_qty": planned_stock_qty,
		"shipment_stock_qty": shipment_stock_qty,
		"actual_qty": actual_qty,
		"weight_per_unit": weight_per_unit,
		"weight_uom": weight_uom,
		"shipment_weight": shipment_stock_qty * weight_per_unit if weight_per_unit and shipment_stock_qty else 0,
		"stock_status": _get_stock_status(actual_qty, planned_stock_qty, shipment_stock_qty),
	}


def _get_open_qty(item):
	ordered = flt(item.get("ordered_qty"))
	qty = flt(item.get("qty"))
	return max(qty - ordered, 0)


def _get_transfer_uom():
	return frappe.db.get_single_value("Planning Settings", "default_transfer_uom") or frappe.db.get_single_value("Planning Settings", "carton_uom")


def _get_target_shipment_uom(target_warehouse: str):
	return frappe.db.get_value("Warehouse", target_warehouse, "pallet_uom")


def _get_truck_capacity_in_pallets():
	return frappe.db.get_single_value("Planning Settings", "truck_load_to_plt_ratio") or 0


def _get_shipment_parcel_template_weight(parcel_template: str):
	if not parcel_template:
		return 0
	return frappe.db.get_value("Shipment Parcel Template", parcel_template, "weight") or 0


def _get_item_stock_uom(item_code: str):
	return frappe.db.get_value("Item", item_code, "stock_uom")


def _get_item_weight_per_unit(item_code: str):
	return frappe.db.get_value("Item", item_code, "weight_per_unit") or 0


def _get_item_weight_uom(item_code: str):
	return frappe.db.get_value("Item", item_code, "weight_uom")


def _convert_item_qty(item_code: str, qty: float, from_uom: str | None, to_uom: str | None) -> float:
	qty = flt(qty)
	if not qty or not from_uom or not to_uom or from_uom == to_uom:
		return qty
	item = frappe.get_doc("Item", item_code)
	from_factor = _get_item_uom_factor(item, from_uom)
	to_factor = _get_item_uom_factor(item, to_uom)
	if not from_factor or not to_factor:
		return 0
	return qty * from_factor / to_factor


def _get_item_uom_factor(item, uom_name: str) -> float:
	if item.stock_uom == uom_name:
		return 1
	for row in item.get("uoms") or []:
		if row.uom == uom_name:
			return flt(row.conversion_factor)
	return 0


def _get_warehouse_shipment_schedule(shipment_profile: str, source_warehouse: str):
	profile = frappe.get_doc("Shipment Profile", shipment_profile)
	for row in profile.get("shipment_profile_schedule") or []:
		if row.source_warehouse == source_warehouse:
			return row
	return None


def _get_next_shipment_date(reference_date, shipment_day: str, picking_time: int):
	if shipment_day not in WEEKDAY_TO_INDEX:
		frappe.throw(_("Invalid shipment day: {0}").format(shipment_day))

	reference = getdate(reference_date)
	target_weekday = WEEKDAY_TO_INDEX[shipment_day]
	days_until_shipment = (target_weekday - reference.weekday()) % 7
	shipment_date = add_days(reference, days_until_shipment)
	picking_date = add_days(shipment_date, -int(picking_time or 0))
	if getdate() > picking_date:
		shipment_date = add_days(shipment_date, 7)
	return shipment_date
