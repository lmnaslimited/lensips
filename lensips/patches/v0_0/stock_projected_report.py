import frappe
from frappe import _
from frappe.utils import flt
import erpnext.stock.report.stock_projected_qty.stock_projected_qty as base_report
from erpnext.stock.utils import update_included_uom_in_report, is_reposting_item_valuation_in_progress
from erpnext.accounts.doctype.pos_invoice.pos_invoice import get_pos_reserved_qty
from pypika.terms import ExistsCriterion

def custom_execute(filters=None):
    is_reposting_item_valuation_in_progress()
    filters = frappe._dict(filters or {})
    include_uom = filters.get("include_uom")
    
    columns = custom_get_columns()
    bin_list = custom_get_bin_list(filters)
    item_map = base_report.get_item_map(filters.get("item_code"), include_uom)
    
    data = []
    conversion_factors = []
    
    for b in bin_list:
        item = item_map.get(b.item_code)
        if not item: continue

        # Standard Reorder Level Logic
        re_order_level = re_order_qty = 0
        for d in item.get("reorder_levels", []):
            if d.warehouse == b.warehouse:
                re_order_level = d.warehouse_reorder_level
                re_order_qty = d.warehouse_reorder_qty

        shortage_qty = 0
        if (re_order_level or re_order_qty) and re_order_level > b.projected_qty:
            shortage_qty = re_order_level - flt(b.projected_qty)

        # Standard POS Logic
        reserved_qty_for_pos = get_pos_reserved_qty(b.item_code, b.warehouse) or 0.0
        # Important: POS logic reduces the projected_qty variable in the standard report
        projected_qty = flt(b.projected_qty)
        if reserved_qty_for_pos:
            projected_qty -= reserved_qty_for_pos

        # BUILD THE ROW (Order must match custom_get_columns exactly)
        row = [
            item.name, item.item_name, item.description, item.item_group, item.brand,
            b.warehouse, item.stock_uom, 
            flt(b.actual_qty), flt(b.planned_qty), flt(b.indented_qty),
            flt(b.ordered_qty), flt(b.reserved_qty),
            flt(b.custom_reserved_qty_for_material_request), # Index 12 
            flt(b.reserved_qty_for_production),
            flt(b.reserved_qty_for_production_plan), 
            flt(b.reserved_qty_for_sub_contract),         
            flt(reserved_qty_for_pos),                       # Index 16
            flt(projected_qty),                              # Index 17
            flt(re_order_level),                             # Index 18
            flt(re_order_qty),                               # Index 19
            flt(shortage_qty)                                # Index 20
        ]
        
        data.append(row)

        if include_uom:
            conversion_factors.append(item.get("conversion_factor") or 1.0)

    # This function looks at the "convertible" flag in columns to apply conversion_factors
    update_included_uom_in_report(columns, data, include_uom, conversion_factors)
    
    return columns, data

def custom_get_columns():
    # Use base_report's get_columns and insert ours
    columns = base_report.get_columns()
    
    # Standard columns list is length 20. We want to insert at 15 (before POS)
    columns.insert(12, {
        "label": _("Reserved for Mat Req"),
        "fieldname": "custom_reserved_qty_for_material_request",
        "fieldtype": "Float",
        "width": 110,
        "convertible": "qty", # ESSENTIAL for UOM conversion to work
    })
    return columns

# def custom_get_bin_list(filters):
#     bin = frappe.qb.DocType("Bin")
#     query = (
#         frappe.qb.from_(bin)
#         .select(
#             bin.item_code, bin.warehouse, bin.actual_qty, bin.planned_qty,
#             bin.indented_qty, bin.ordered_qty, bin.reserved_qty,
#             bin.reserved_qty_for_production, bin.reserved_qty_for_sub_contract,
#             bin.reserved_qty_for_production_plan,
#             bin.custom_reserved_qty_for_material_request, # FETCH OUR FIELD
#             bin.projected_qty,
#         )
#         .orderby(bin.item_code, bin.warehouse)
#     )
#     if filters.item_code:
# 	    query = query.where(bin.item_code == filters.item_code)
     
#     if filters.warehouse:
#         warehouse_details = frappe.db.get_value("Warehouse", filters.warehouse, ["lft", "rgt"], as_dict=1)
    
#         if warehouse_details:
#             wh = frappe.qb.DocType("Warehouse")
#             query = query.where(
#                     ExistsCriterion(
#                         frappe.qb.from_(wh)
#                         .select(wh.name)
#                         .where(
#                             (wh.lft >= warehouse_details.lft)
#                             & (wh.rgt <= warehouse_details.rgt)
#                             & (bin.warehouse == wh.name)
#                         )
#                     )
#                 )
#         bin_list = query.run(as_dict=True)
def get_bin_list(filters):
	bin = frappe.qb.DocType("Bin")
	query = (
		frappe.qb.from_(bin)
		.select(
			bin.item_code,
			bin.warehouse,
			bin.actual_qty,
			bin.planned_qty,
			bin.indented_qty,
			bin.ordered_qty,
			bin.reserved_qty,
			bin.reserved_qty_for_production,
			bin.reserved_qty_for_sub_contract,
			bin.reserved_qty_for_production_plan,
            bin.custom_reserved_qty_for_material_request,
			bin.projected_qty,
		)
		.orderby(bin.item_code, bin.warehouse)
	)

	if filters.item_code:
		query = query.where(bin.item_code == filters.item_code)

	if filters.warehouse:
		warehouse_details = frappe.db.get_value("Warehouse", filters.warehouse, ["lft", "rgt"], as_dict=1)

		if warehouse_details:
			wh = frappe.qb.DocType("Warehouse")
			query = query.where(
				ExistsCriterion(
					frappe.qb.from_(wh)
					.select(wh.name)
					.where(
						(wh.lft >= warehouse_details.lft)
						& (wh.rgt <= warehouse_details.rgt)
						& (bin.warehouse == wh.name)
					)
				)
			)

	bin_list = query.run(as_dict=True)

	return bin_list
   
