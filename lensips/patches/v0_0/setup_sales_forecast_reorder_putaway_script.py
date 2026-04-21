from __future__ import annotations

import frappe


CLIENT_SCRIPT_NAME = "Sales Forecast - Create Reorder and Put Away"


def execute():
	script = """
function set_sales_forecast_item_value(cdt, cdn, fieldname, value) {
	const row = locals[cdt][cdn];
	if (flt(row[fieldname]) === flt(value)) {
		return;
	}

	frappe.model.set_value(cdt, cdn, fieldname, value);
}

function update_sales_forecast_item_row(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const actual_qty = flt(row.actual_qty);
	const actual_value = flt(row.actual_value);
	const adjust_qty = flt(row.adjust_qty);
	const adjust_value = actual_qty ? (actual_value / actual_qty) * adjust_qty : 0;
	const demand_qty = actual_qty + adjust_qty;
	const demand_value = actual_value + adjust_value;

	set_sales_forecast_item_value(cdt, cdn, "adjust_value", adjust_value);
	set_sales_forecast_item_value(cdt, cdn, "demand_qty", demand_qty);
	set_sales_forecast_item_value(cdt, cdn, "demand_value", demand_value);
}

function set_sales_forecast_item_row_state(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	const grid_row = grid && grid.grid_rows_by_docname && grid.grid_rows_by_docname[cdn];

	if (!grid_row) {
		return;
	}

	grid_row.toggle_editable("adjust_qty", !cint(row.locked));
	grid_row.refresh();
}

function add_create_reorder_and_putaway_button(frm) {
	frm.add_custom_button(__("Create Reorder and Put Away"), () => {
		if (frm.doc.docstatus !== 1) {
			frappe.msgprint(__("Submit the Sales Forecast before creating Reorder and Putaway Rules."));
			return;
		}

		frappe.prompt(
			[
				{
					fieldname: "warehouse",
					fieldtype: "Link",
					label: __("Warehouse"),
					options: "Warehouse",
					reqd: 1,
				},
			],
			(values) => {
				frm.call({
					method: "lensips.planning.doctype.reorder_and_putaway_rule.reorder_and_putaway_rule.create_from_sales_forecast",
					args: {
						sales_forecast: frm.doc.name,
						warehouse: values.warehouse,
					},
					freeze: true,
					freeze_message: __("Creating Reorder and Putaway Rules..."),
					callback(r) {
						const result = r.message || {};
						const names = result.names || [];
						frappe.msgprint({
							title: __("Create Reorder and Put Away"),
							indicator: names.length ? "green" : "orange",
							message: names.length
								? __("Created Reorder and Putaway Rule(s): {0}", [names.join(", ")])
								: __("No Reorder and Putaway Rule was created."),
						});
					},
				});
			},
			__("Create Reorder and Put Away"),
			__("Create")
		);
	});
}

frappe.ui.form.on("Sales Forecast", {
	refresh(frm) {
		for (const row of frm.doc.items || []) {
			update_sales_forecast_item_row(frm, row.doctype, row.name);
			set_sales_forecast_item_row_state(frm, row.doctype, row.name);
		}

		add_create_reorder_and_putaway_button(frm);
	},
	items_on_form_rendered(frm, cdt, cdn) {
		update_sales_forecast_item_row(frm, cdt, cdn);
		set_sales_forecast_item_row_state(frm, cdt, cdn);
	},
});

frappe.ui.form.on("Sales Forecast Item", {
	locked(frm, cdt, cdn) {
		set_sales_forecast_item_row_state(frm, cdt, cdn);
	},
	adjust_qty(frm, cdt, cdn) {
		update_sales_forecast_item_row(frm, cdt, cdn);
	},
	actual_qty(frm, cdt, cdn) {
		update_sales_forecast_item_row(frm, cdt, cdn);
	},
	actual_value(frm, cdt, cdn) {
		update_sales_forecast_item_row(frm, cdt, cdn);
	},
	form_render(frm, cdt, cdn) {
		update_sales_forecast_item_row(frm, cdt, cdn);
		set_sales_forecast_item_row_state(frm, cdt, cdn);
	},
});
""".strip()

	doc = frappe.get_doc(
		{
			"doctype": "Client Script",
			"name": CLIENT_SCRIPT_NAME,
			"dt": "Sales Forecast",
			"view": "Form",
			"module": "Planning",
			"enabled": 1,
			"script": script,
		}
	)

	if frappe.db.exists("Client Script", CLIENT_SCRIPT_NAME):
		existing = frappe.get_doc("Client Script", CLIENT_SCRIPT_NAME)
		existing.dt = doc.dt
		existing.view = doc.view
		existing.module = doc.module
		existing.enabled = doc.enabled
		existing.script = doc.script
		existing.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)

	frappe.clear_cache(doctype="Sales Forecast")
