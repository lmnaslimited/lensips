from __future__ import annotations

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.permissions import add_permission, update_permission_property
from frappe.utils import flt


PLANNING_USER_ROLE = "Planning User"
PLANNING_MANAGER_ROLE = "Planning Manager"
DEFAULT_UOMS = ("PL1", "PL2", "PLT", "CPL")
DEFAULT_WAREHOUSE_PALLET_UOMS = {
	"121 - H": "PL2",
	"122 - H": "PL1",
	"131 - H": "PL1",
	"141 - H": "PL2",
	"151 - H": "PL1",
	"161 - H": "PL1",
}
OBSOLETE_ITEM_CUSTOM_FIELDS = ("default_pallet_profile", "column_break_lensips_item_planning")


def ensure_planning_customizations():
	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "lensips_planning_tab",
					"label": "Lensips Planning",
					"fieldtype": "Tab Break",
					"insert_after": "manufacturing",
				},
				{
					"fieldname": "lensips_item_planning_section",
					"label": "Planning Attributes",
					"fieldtype": "Section Break",
					"insert_after": "lensips_planning_tab",
				},
				{
					"fieldname": "item_temperature",
					"label": "Item Temperature",
					"fieldtype": "Select",
					"options": "\nFrozen\nDry\nChilled",
					"insert_after": "lensips_item_planning_section",
					"in_list_view": 1,
				},
				{
					"fieldname": "carton_to_inner_factor",
					"label": "Carton to Inner Factor",
					"fieldtype": "Float",
					"insert_after": "item_temperature",
					"non_negative": 1,
				},
				{
					"fieldname": "cartons_per_layer",
					"label": "Cartons Per Layer",
					"fieldtype": "Float",
					"insert_after": "carton_to_inner_factor",
					"non_negative": 1,
				},
				{
					"fieldname": "carton_volume_cbm",
					"label": "Carton Volume (CBM)",
					"fieldtype": "Float",
					"insert_after": "cartons_per_layer",
					"non_negative": 1,
					"precision": "6",
				},
				{
					"fieldname": "carton_gross_weight",
					"label": "Carton Gross Weight",
					"fieldtype": "Float",
					"insert_after": "carton_volume_cbm",
					"non_negative": 1,
				},
				{
					"fieldname": "planning_source_type",
					"label": "Planning Source Type",
					"fieldtype": "Select",
					"options": "\nImported\nManufactured\nPurchased Local",
					"insert_after": "carton_gross_weight",
				},
				{
					"fieldname": "imported_container_eligible",
					"label": "Imported Container Eligible",
					"fieldtype": "Check",
					"insert_after": "planning_source_type",
				},
				{
					"fieldname": "default_container_profile",
					"label": "Default Container Profile",
					"fieldtype": "Link",
					"options": "Lens Container Profile",
					"insert_after": "imported_container_eligible",
				},
				{
					"fieldname": "lensips_item_pallet_rules_section",
					"label": "Pallet Rules",
					"fieldtype": "Section Break",
					"insert_after": "total_projected_qty",
				},
				{
					"fieldname": "lens_item_pallet_rules",
					"label": "Lens Item Pallet Rules",
					"fieldtype": "Table",
					"options": "Lens Item Pallet Rule",
					"insert_after": "lensips_item_pallet_rules_section",
					"columns": 0,
					"read_only": 1,
				},
			],
			"Warehouse": [
				{
					"fieldname": "lensips_planning_tab",
					"label": "Lensips Planning",
					"fieldtype": "Tab Break",
					"insert_after": "transit_section",
				},
				{
					"fieldname": "lensips_warehouse_planning_section",
					"label": "Planning Attributes",
					"fieldtype": "Section Break",
					"insert_after": "lensips_planning_tab",
				},
				{
					"fieldname": "freezer_capacity_plt",
					"label": "Freezer Capacity (PLT)",
					"fieldtype": "Float",
					"insert_after": "lensips_warehouse_planning_section",
					"non_negative": 1,
				},
				{
					"fieldname": "chiller_capacity_plt",
					"label": "Chiller Capacity (PLT)",
					"fieldtype": "Float",
					"insert_after": "freezer_capacity_plt",
					"non_negative": 1,
				},
				{
					"fieldname": "dry_capacity_plt",
					"label": "Dry Capacity (PLT)",
					"fieldtype": "Float",
					"insert_after": "chiller_capacity_plt",
					"non_negative": 1,
				},
				{
					"fieldname": "pallet_uom",
					"label": "Pallet UOM",
					"fieldtype": "Link",
					"options": "UOM",
					"insert_after": "dry_capacity_plt",
				},
				{
					"fieldname": "shipment_profile",
					"fieldtype": "Link",
					"label": "Shipment Profile",
					"options": "Shipment Profile"
				},
				{
					"fieldname": "column_break_lensips_warehouse_planning",
					"fieldtype": "Column Break",
					"insert_after": "shipment_profile",
					"insert_after": "shipment_profile",
				},
				{
					"fieldname": "warehouse_planning_role",
					"label": "Warehouse Planning Role",
					"fieldtype": "Select",
					"options": "\nOwned\nExternal\nTruck\nRegional",
					"insert_after": "column_break_lensips_warehouse_planning",
				},
				{
					"fieldname": "include_in_planning",
					"label": "Include In Planning",
					"fieldtype": "Check",
					"default": "1",
					"insert_after": "warehouse_planning_role",
				},
				{
					"fieldname": "include_in_external_nsw_logic",
					"label": "Include In External NSW Logic",
					"fieldtype": "Check",
					"insert_after": "include_in_planning",
				},
			],
		},
		ignore_validate=True,
		update=True,
	)

	for fieldname in OBSOLETE_ITEM_CUSTOM_FIELDS:
		_delete_custom_field_if_exists("Item", fieldname)


def ensure_hakka_warehouse_122():
	if frappe.db.exists("Warehouse", "122 - H"):
		return

	parent_warehouse = "NSW - H" if frappe.db.exists("Warehouse", "NSW - H") else None
	warehouse = frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": "122",
			"company": _get_default_company(),
			"parent_warehouse": parent_warehouse,
			"is_group": 0,
		}
	)
	warehouse.insert(ignore_permissions=True)


def ensure_hakka_reference_setup():
	ensure_planning_roles()
	ensure_standard_planning_access()
	ensure_required_uoms()
	ensure_planning_settings()
	ensure_planning_customizations()
	ensure_item_layout_customizations()
	ensure_hakka_warehouse_122()
	assign_default_warehouse_pallet_uoms()


def ensure_planning_roles():
	for role_name in (PLANNING_USER_ROLE, PLANNING_MANAGER_ROLE):
		if frappe.db.exists("Role", role_name):
			continue

		role = frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role_name,
				"desk_access": 1,
				"is_custom": 1,
			}
		)
		role.insert(ignore_permissions=True)


def ensure_standard_planning_access():
	manager_rights = ("read", "write", "create", "delete", "report", "export", "print", "email", "share")
	user_rights = ("read", "write", "create", "report", "export", "print", "email", "share")

	for doctype in ("Item", "Warehouse", "Sales Forecast", "Planning Settings"):
		_ensure_doctype_permissions(doctype, PLANNING_MANAGER_ROLE, manager_rights)
		_ensure_doctype_permissions(doctype, PLANNING_USER_ROLE, user_rights)


def _ensure_doctype_permissions(doctype: str, role: str, rights: tuple[str, ...]):
	perm_name = frappe.db.get_value(
		"Custom DocPerm",
		{"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
	)
	if not perm_name:
		add_permission(doctype, role, 0, ptype="read")

	for right in rights:
		update_permission_property(doctype, role, 0, right, 1, validate=False)


def ensure_item_layout_customizations():
	meta = frappe.get_meta("Item")
	field_order = [df.fieldname for df in meta.fields if df.fieldname]

	field_order = _move_field_after(field_order, "production_capacity", "default_container_profile")
	field_order = _move_field_after(field_order, "total_projected_qty", "production_capacity")
	field_order = _move_field_after(field_order, "lensips_item_pallet_rules_section", "total_projected_qty")
	field_order = _move_field_after(field_order, "lens_item_pallet_rules", "lensips_item_pallet_rules_section")

	_upsert_doctype_property_setter(
		doctype="Item",
		property_name="field_order",
		value=json.dumps(field_order),
		property_type="Data",
	)
	_upsert_property_setter("Item", "production_capacity", "insert_after", "default_container_profile", "Data")
	_upsert_property_setter("Item", "total_projected_qty", "insert_after", "production_capacity", "Data")
	_upsert_property_setter("Item", "lensips_item_pallet_rules_section", "insert_after", "total_projected_qty", "Data")
	_upsert_property_setter("Item", "lens_item_pallet_rules", "insert_after", "lensips_item_pallet_rules_section", "Data")
	_upsert_property_setter("Item", "lens_item_pallet_rules", "columns", "0", "Int")
	_upsert_property_setter("Item", "lens_item_pallet_rules", "read_only", "1", "Check")
	_upsert_property_setter("Item", "total_projected_qty", "hidden", "0", "Check")
	_upsert_property_setter("Item", "total_projected_qty", "read_only", "1", "Check")


def ensure_required_uoms():
	for uom_name in DEFAULT_UOMS:
		if frappe.db.exists("UOM", uom_name):
			continue
		frappe.get_doc({"doctype": "UOM", "uom_name": uom_name, "enabled": 1}).insert(ignore_permissions=True)


def ensure_planning_settings():
	if not frappe.db.exists("DocType", "Planning Settings"):
		return

	settings = (
		frappe.get_single("Planning Settings")
		if frappe.db.exists("Singles", {"doctype": "Planning Settings"})
		else frappe.new_doc("Planning Settings")
	)
	settings.carton_uom = settings.carton_uom or "CTN"
	settings.carton_per_layer_uom = settings.carton_per_layer_uom or "CPL"
	settings.base_pallet_uom = settings.base_pallet_uom or "PLT"
	settings.truck_load_to_plt_ratio = flt(settings.truck_load_to_plt_ratio) or 22
	if not settings.get("warehouse_pallet_uom_rules"):
		settings.warehouse_pallet_uom_rules = []
	for pallet_uom, ratio in (("PL1", 7), ("PL2", 10)):
		_upsert_settings_rule(settings, pallet_uom, ratio)
	settings.save(ignore_permissions=True)


def assign_default_warehouse_pallet_uoms():
	for warehouse_name, pallet_uom in DEFAULT_WAREHOUSE_PALLET_UOMS.items():
		if not frappe.db.exists("Warehouse", warehouse_name):
			continue
		current_pallet_uom = frappe.db.get_value("Warehouse", warehouse_name, "pallet_uom")
		if current_pallet_uom == pallet_uom:
			continue
		frappe.db.set_value("Warehouse", warehouse_name, "pallet_uom", pallet_uom, update_modified=False)
	frappe.clear_cache()


def sync_item_planning_data(doc, method=None):
    if doc.doctype != "Item":
        return

    settings = _get_planning_settings()
    if not settings:
        return

    # -----------------------------
    # Base calculations
    # -----------------------------
    carton_to_inner = flt(doc.get("carton_to_inner_factor"))
    cartons_per_layer = flt(doc.get("cartons_per_layer"))

    cpl_to_inner = carton_to_inner * cartons_per_layer

    # -----------------------------
    # Existing UOM sync
    # -----------------------------
    _sync_item_uom(doc, settings.carton_uom, carton_to_inner)

    _sync_item_uom(
        doc,
        settings.carton_per_layer_uom,
        cpl_to_inner,
    )

    # -----------------------------
    # NEW: Pallet 1 (PL1)
    # -----------------------------
    if settings.pallet_1_uom:
        pl1_to_cpl = _get_uom_factor(
            settings.pallet_1_uom,
            settings.carton_per_layer_uom,
        )

        if pl1_to_cpl:
            pl1_to_inner = pl1_to_cpl * cpl_to_inner

            _sync_item_uom(
                doc,
                settings.pallet_1_uom,
                pl1_to_inner,
            )

    # -----------------------------
    # NEW: Pallet 2 (PL2)
    # -----------------------------
    if settings.pallet_2_uom:
        pl2_to_cpl = _get_uom_factor(
            settings.pallet_2_uom,
            settings.carton_per_layer_uom,
        )

        if pl2_to_cpl:
            pl2_to_inner = pl2_to_cpl * cpl_to_inner

            _sync_item_uom(
                doc,
                settings.pallet_2_uom,
                pl2_to_inner,
            )

    # -----------------------------
    # Continue existing logic
    # -----------------------------
    _populate_item_pallet_rules(doc, settings)


def configure_sample_planning_items():
	container_profile = _get_or_create_container_profile(
		{
			"profile_name": "P A Frozen Container",
			"container_size": "Standard Frozen Container",
			"max_weight_tonnes": 26,
			"usable_volume_pct": 95,
			"buffer_pct": 5,
			"notes": "Configured from Demo_Truck_Pallet_Container_Info.xlsx for imported frozen items.",
		}
	)

	configurations = [
		{
			"item_code": "121554A",
			"item_fields": {
				"item_temperature": "Frozen",
				"planning_source_type": "Imported",
				"imported_container_eligible": 1,
				"default_container_profile": container_profile.name,
				"carton_to_inner_factor": 4,
				"cartons_per_layer": 10,
				"carton_volume_cbm": 0.027536,
				"carton_gross_weight": 9.248,
				"min_order_qty": 50,
				"lead_time_days": 91,
			},
		},
		{
			"item_code": "121555A",
			"item_fields": {
				"item_temperature": "Frozen",
				"planning_source_type": "Imported",
				"imported_container_eligible": 1,
				"default_container_profile": container_profile.name,
				"carton_to_inner_factor": 4,
				"cartons_per_layer": 10,
				"carton_volume_cbm": 0.027536,
				"carton_gross_weight": 10.048,
				"min_order_qty": 50,
				"lead_time_days": 91,
			},
		},
		{
			"item_code": "121562A",
			"item_fields": {
				"item_temperature": "Frozen",
				"planning_source_type": "Imported",
				"imported_container_eligible": 1,
				"default_container_profile": container_profile.name,
				"carton_to_inner_factor": 8,
				"cartons_per_layer": 6,
				"carton_volume_cbm": 0.0318,
				"carton_gross_weight": 11.672,
				"min_order_qty": 50,
				"lead_time_days": 91,
			},
		},
		{
			"item_code": "101501A",
			"item_fields": {
				"item_temperature": "Frozen",
				"planning_source_type": "Manufactured",
				"cartons_per_layer": 9,
			},
		},
	]

	for configuration in configurations:
		item = frappe.get_doc("Item", configuration["item_code"])
		item.update(configuration["item_fields"])
		sync_item_planning_data(item)
		item.save(ignore_permissions=True)

	frappe.db.commit()


def refresh_all_item_pallet_rules():
	settings = _get_planning_settings()
	if not settings:
		return
	for item_name in frappe.get_all("Item", pluck="name"):
		item = frappe.get_doc("Item", item_name)
		sync_item_planning_data(item)
		item.save(ignore_permissions=True)
	frappe.db.commit()


def _get_default_company() -> str:
	company = frappe.db.get_value("Global Defaults", None, "default_company")
	if company:
		return company

	company = frappe.db.get_value("Company", {}, "name", order_by="creation asc")
	if company:
		return company

	frappe.throw("No Company found for creating Hakka warehouse 122 - H.")


def _sync_item_uom(item, uom_name: str | None, conversion_factor: float):
	if not uom_name:
		return

	row = next((d for d in item.get("uoms") if d.uom == uom_name), None)
	if conversion_factor <= 0:
		if row:
			item.uoms = [d for d in item.get("uoms") if d.uom != uom_name]
		return

	if not row:
		item.append("uoms", {"uom": uom_name, "conversion_factor": conversion_factor})
		return

	row.conversion_factor = conversion_factor

def _get_uom_factor(from_uom, to_uom):
    # direct
    factor = frappe.db.get_value(
        "UOM Conversion Factor",
        {
            "from_uom": from_uom,
            "to_uom": to_uom,
        },
        "value",
    )

    if factor:
        return factor

    # reverse
    reverse = frappe.db.get_value(
        "UOM Conversion Factor",
        {
            "from_uom": to_uom,
            "to_uom": from_uom,
        },
        "value",
    )

    if reverse:
        return 1 / reverse

    return None

# def _populate_item_pallet_rules(item, settings):
# 	carton_uom = settings.carton_uom
# 	carton_per_layer_uom = settings.carton_per_layer_uom
# 	if not carton_uom or not carton_per_layer_uom:
# 		item.set("lens_item_pallet_rules", [])
# 		return

# 	cpl_factor = _get_item_uom_factor(item, carton_per_layer_uom)
# 	if cpl_factor <= 0:
# 		item.set("lens_item_pallet_rules", [])
# 		return

# 	pallet_uom_ratios = {d.pallet_uom: flt(d.plt_conversion_ratio) for d in settings.get("warehouse_pallet_uom_rules") if d.pallet_uom}
# 	rows = []
# 	for warehouse_name in DEFAULT_WAREHOUSE_PALLET_UOMS:
# 		if not frappe.db.exists("Warehouse", warehouse_name):
# 			continue
# 		pallet_uom = frappe.db.get_value("Warehouse", warehouse_name, "pallet_uom")
# 		ratio = flt(pallet_uom_ratios.get(pallet_uom))
# 		if not pallet_uom or ratio <= 0:
# 			continue
# 		conversion_factor = cpl_factor * ratio
# 		rows.append(
# 			{
# 				"warehouse": warehouse_name,
# 				"qty": conversion_factor,
# 				"uom": carton_uom,
# 				"pallet_uom": pallet_uom,
# 				"conversion_factor": conversion_factor,
# 			}
# 		)

# 	item.set("lens_item_pallet_rules", rows)
def _populate_item_pallet_rules(item, settings):
    carton_uom = settings.carton_uom
    carton_per_layer_uom = settings.carton_per_layer_uom

    if not carton_uom or not carton_per_layer_uom:
        item.set("lens_item_pallet_rules", [])
        return

    # Carton → Inner
    carton_factor = _get_item_uom_factor(item, carton_uom)
    if carton_factor <= 0:
        item.set("lens_item_pallet_rules", [])
        return

    # -----------------------------
    # Fetch warehouses dynamically
    # -----------------------------
    warehouses = frappe.get_all(
        "Warehouse",
        filters={"pallet_uom": ["is", "set"]},
        fields=["name", "pallet_uom"],
    )

    rows = []

    for wh in warehouses:
        warehouse_name = wh.name
        pallet_uom = wh.pallet_uom

        if not pallet_uom:
            continue

        # Pallet → Inner (from Item UOM)
        pallet_factor = _get_item_uom_factor(item, pallet_uom)

        if pallet_factor <= 0:
            continue

        # -----------------------------
        # Derive cartons per pallet
        # -----------------------------
        cartons_per_pallet = pallet_factor / carton_factor

        rows.append(
            {
                "warehouse": warehouse_name,
                "qty": cartons_per_pallet,   # carton quantity per pallet
                "uom": carton_uom,
                "pallet_uom": pallet_uom,
                "conversion_factor": pallet_factor,  # pallet → inner
            }
        )

    item.set("lens_item_pallet_rules", rows)


def _get_item_uom_factor(item, uom_name: str) -> float:
	for row in item.get("uoms") or []:
		if row.uom == uom_name:
			return flt(row.conversion_factor)
	return 0


def _get_planning_settings():
	if not frappe.db.exists("DocType", "Planning Settings"):
		return None
	return frappe.get_single("Planning Settings")


def _upsert_settings_rule(settings, pallet_uom: str, ratio: float):
	row = next((d for d in settings.get("warehouse_pallet_uom_rules") if d.pallet_uom == pallet_uom), None)
	if row:
		row.plt_conversion_ratio = ratio
		return
	settings.append("warehouse_pallet_uom_rules", {"pallet_uom": pallet_uom, "plt_conversion_ratio": ratio})


def _upsert_property_setter(doctype: str, fieldname: str, property_name: str, value, property_type: str = "Data"):
	existing_name = frappe.db.get_value(
		"Property Setter",
		{"doc_type": doctype, "field_name": fieldname, "property": property_name, "doctype_or_field": "DocField"},
	)
	if existing_name:
		property_setter = frappe.get_doc("Property Setter", existing_name)
		property_setter.value = value
		property_setter.property_type = property_type
		property_setter.save(ignore_permissions=True)
		return

	frappe.make_property_setter(
		{
			"doctype": doctype,
			"doctype_or_field": "DocField",
			"fieldname": fieldname,
			"property": property_name,
			"value": value,
			"property_type": property_type,
		},
		validate_fields_for_doctype=False,
	)


def _upsert_doctype_property_setter(doctype: str, property_name: str, value, property_type: str = "Data"):
	existing_name = frappe.db.get_value(
		"Property Setter",
		{"doc_type": doctype, "property": property_name, "doctype_or_field": "DocType"},
	)
	if existing_name:
		property_setter = frappe.get_doc("Property Setter", existing_name)
		property_setter.value = value
		property_setter.property_type = property_type
		property_setter.save(ignore_permissions=True)
		return

	frappe.make_property_setter(
		{
			"doctype": doctype,
			"doctype_or_field": "DocType",
			"property": property_name,
			"value": value,
			"property_type": property_type,
		},
		validate_fields_for_doctype=False,
	)


def _delete_custom_field_if_exists(doctype: str, fieldname: str):
	custom_field_name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": fieldname})
	if custom_field_name:
		frappe.delete_doc("Custom Field", custom_field_name, ignore_permissions=True, force=True)


def _move_field_after(field_order: list[str], fieldname: str, insert_after: str) -> list[str]:
	if fieldname not in field_order or insert_after not in field_order:
		return field_order
	field_order = [field for field in field_order if field != fieldname]
	field_order.insert(field_order.index(insert_after) + 1, fieldname)
	return field_order


def _get_or_create_container_profile(values: dict):
	existing = frappe.db.exists("Lens Container Profile", values["profile_name"])
	doc = frappe.get_doc("Lens Container Profile", existing) if existing else frappe.get_doc({"doctype": "Lens Container Profile", **values})
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.update(values)
		doc.save(ignore_permissions=True)
	return doc
