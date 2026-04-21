from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _

from erpnext.stock.doctype.material_request.material_request import (
	make_purchase_order_based_on_supplier,
)


def _get_default_supplier_map(material_request):
	supplier_map = defaultdict(list)
	items_without_supplier = []

	item_codes = [item.item_code for item in material_request.items if item.item_code]
	if not item_codes:
		return supplier_map, items_without_supplier

	item_defaults = frappe.get_all(
		"Item Default",
		filters={
			"parenttype": "Item",
			"company": material_request.company,
			"parent": ["in", item_codes],
		},
		fields=["parent", "default_supplier"],
	)

	default_supplier_map = {row.parent: row.default_supplier for row in item_defaults if row.default_supplier}

	for item in material_request.items:
		supplier = default_supplier_map.get(item.item_code)
		if supplier:
			supplier_map[supplier].append(item.item_code)
		else:
			items_without_supplier.append(item.item_code)

	return supplier_map, list(dict.fromkeys(items_without_supplier))


@frappe.whitelist()
def create_purchase_orders_by_supplier(source_name):
	material_request = frappe.get_doc("Material Request", source_name)

	if material_request.docstatus != 1 or material_request.material_request_type != "Purchase":
		frappe.throw(_("Only submitted Purchase Material Requests can be converted to Purchase Orders."))

	supplier_map, items_without_supplier = _get_default_supplier_map(material_request)

	if not supplier_map:
		message = _("No items found with a valid Default Supplier in Item Defaults.")
		if items_without_supplier:
			frappe.logger("lensips.planning.material_request").warning(
				"Material Request %s has items without default supplier: %s",
				material_request.name,
				", ".join(items_without_supplier),
			)
		frappe.throw(message)

	purchase_orders = []

	for supplier in sorted(supplier_map):
		po = make_purchase_order_based_on_supplier(
			source_name,
			args={"supplier": supplier, "supplier_items": supplier_map[supplier]},
		)

		if not po.get("items"):
			continue

		po.insert(ignore_permissions=True)
		purchase_orders.append(po.name)

	if items_without_supplier:
		frappe.logger("lensips.planning.material_request").warning(
			"Material Request %s has items without default supplier: %s",
			material_request.name,
			", ".join(items_without_supplier),
		)

	return {
		"purchase_orders": purchase_orders,
		"items_without_supplier": items_without_supplier,
		"message": (
			_("Created {0} Purchase Order(s).").format(len(purchase_orders))
			if purchase_orders
			else _("No Purchase Orders were created.")
		),
	}
