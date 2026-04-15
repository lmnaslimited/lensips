import frappe
from frappe.utils import flt
from erpnext.stock.doctype.bin.bin import get_actual_qty

def custom_get_bin_details(bin_name):
    # We add our custom field to the fetch list
    return frappe.db.get_value(
        "Bin",
        bin_name,
        [
            "actual_qty",
            "ordered_qty",
            "reserved_qty",
            "indented_qty",
            "planned_qty",
            "reserved_qty_for_production",
            "reserved_qty_for_sub_contract",
            "reserved_qty_for_production_plan",
            "custom_reserved_qty_for_material_request" # Corrected field name
        ],
        as_dict=1,
    )

def custom_update_qty(bin_name, args):
    from erpnext.controllers.stock_controller import future_sle_exists

    bin_details = custom_get_bin_details(bin_name)
    actual_qty = flt(bin_details.actual_qty)

    if future_sle_exists(args):
        actual_qty = get_actual_qty(args.get("item_code"), args.get("warehouse"))

    ordered_qty = flt(bin_details.ordered_qty) + flt(args.get("ordered_qty"))
    reserved_qty = flt(bin_details.reserved_qty) + flt(args.get("reserved_qty"))
    indented_qty = flt(bin_details.indented_qty) + flt(args.get("indented_qty"))
    planned_qty = flt(bin_details.planned_qty) + flt(args.get("planned_qty"))

    # Compute projected qty using the NEW field
    projected_qty = (
        flt(actual_qty)
        + flt(ordered_qty)
        + flt(indented_qty)
        + flt(planned_qty)
        - flt(reserved_qty)
        - flt(bin_details.reserved_qty_for_production)
        - flt(bin_details.reserved_qty_for_sub_contract)
        - flt(bin_details.reserved_qty_for_production_plan)
        - flt(bin_details.custom_reserved_qty_for_material_request)
    )

    frappe.db.set_value(
        "Bin",
        bin_name,
        {
            "actual_qty": actual_qty,
            "ordered_qty": ordered_qty,
            "reserved_qty": reserved_qty,
            "indented_qty": indented_qty,
            "planned_qty": planned_qty,
            "projected_qty": projected_qty,
        },
        update_modified=True,
    )

def custom_set_projected_qty(self):
    # This logic is used when bin.save() or bin.recalculate_qty() is called
    self.projected_qty = (
        flt(self.actual_qty)
        + flt(self.ordered_qty)
        + flt(self.indented_qty)
        + flt(self.planned_qty)
        - flt(self.reserved_qty)
        - flt(self.reserved_qty_for_production)
        - flt(self.reserved_qty_for_sub_contract)
        - flt(self.reserved_qty_for_production_plan)
        - flt(self.custom_reserved_qty_for_material_request)
    )

def execute():
    # Keep this empty as it's not a migration patch
    pass
