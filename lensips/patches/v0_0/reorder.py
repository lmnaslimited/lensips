import frappe
from frappe.utils import nowdate, add_days, cint, flt
from math import ceil
from erpnext.stock.reorder_item import send_email_notification, notify_errors

def custom_create_material_request(material_requests):
    """Refined Override: Injects Source Warehouse and Transfer UOM without breaking core logic"""
    mr_list = []
    exceptions_list = []
    company_wise_mr = frappe._dict({})

    # Fetch your custom settings
    planning_settings = frappe.get_cached_doc("Planning Settings")

    for request_type in material_requests:
        for company in material_requests[request_type]:
            try:
                items = material_requests[request_type][company]
                if not items:
                    continue

                mr = frappe.new_doc("Material Request")
                mr.update({
                    "company": company,
                    "auto_created_via_reorder": 1,
                    "transaction_date": nowdate(),
                    "material_request_type": "Material Transfer" if request_type == "Transfer" else request_type,
                })

                for d in items:
                    d = frappe._dict(d)
                    item = d.get("item_details")
                    uom = item.stock_uom
                    conversion_factor = 1.0
                    from_warehouse = None

                    # --- START CUSTOM LOGIC ---
                    if request_type == "Purchase":
                        uom = item.purchase_uom or item.stock_uom
                    
                    elif request_type == "Transfer":
                        # Assign Source Warehouse from Planning Settings
                        from_warehouse = planning_settings.source_warehouse
                        # Assign Custom Transfer UOM from Planning Settings
                        uom = planning_settings.default_transfer_uom or item.stock_uom
                    # --- END CUSTOM LOGIC ---

                    # Standard Conversion Factor Logic
                    if uom != item.stock_uom:
                        conversion_factor = frappe.db.get_value("UOM Conversion Detail",
                            {"parent": item.name, "uom": uom}, "conversion_factor") or 1.0

                    must_be_whole_number = frappe.db.get_value("UOM", uom, "must_be_whole_number", cache=True)
                    qty = d.reorder_qty / conversion_factor
                    if must_be_whole_number:
                        qty = ceil(qty)

                    mr.append("items", {
                        "doctype": "Material Request Item",
                        "item_code": d.item_code,
                        "schedule_date": add_days(nowdate(), cint(item.lead_time_days)),
                        "qty": qty,
                        "conversion_factor": conversion_factor,
                        "uom": uom,
                        "stock_uom": item.stock_uom,
                        "warehouse": d.warehouse,
                        # "set_from_warehouse": from_warehouse, # Injected
                        "item_name": item.item_name,
                        "description": item.description,
                        "item_group": item.item_group,
                        "brand": item.brand,
                        "reorder_qty": d.original_reorder_qty,
                        "projected_on_hand": d.projected_on_hand,
                        "reorder_level": d.reorder_level,
                    })

                # Standard Post-Item Processing
                schedule_dates = [d.schedule_date for d in mr.items]
                mr.schedule_date = max(schedule_dates or [nowdate()])
                mr.set_from_warehouse = from_warehouse # Injected
                mr.flags.ignore_mandatory = True
                mr.insert()
                mr.submit()
                mr_list.append(mr)
                company_wise_mr.setdefault(company, []).append(mr)

            except Exception as exception:
                exceptions_list.append(exception)
                

    # Standard Notifications
    if company_wise_mr:
        if getattr(frappe.local, "reorder_email_notify", None) is None:
            frappe.local.reorder_email_notify = cint(frappe.db.get_single_value("Stock Settings", "reorder_email_notify"))

        if frappe.local.reorder_email_notify:
            send_email_notification(company_wise_mr)

    if exceptions_list:
        notify_errors(exceptions_list)

    return mr_list
