from __future__ import annotations

import frappe


CLIENT_SCRIPT_NAME = "Reorder and Putaway Rule - Coverage Actions"


def execute():
	script = """
function refresh_reorder_and_putaway_coverage(frm) {
	if (!frm.doc.sales_forecast || !frm.doc.warehouse || !frm.doc.period) {
		return;
	}

	frm.call({
		method: "lensips.planning.doctype.reorder_and_putaway_rule.reorder_and_putaway_rule.refresh_from_sales_forecast",
		args: {
			name: frm.doc.name,
		},
		freeze: true,
		freeze_message: __("Refreshing forecast items..."),
		callback(r) {
			if (!r.message) {
				return;
			}

			frm.reload_doc();
		},
	});
}

frappe.ui.form.on("Reorder and Putaway Rule", {
	refresh(frm) {
		if (frm.doc.docstatus !== 0 || frm.__lensips_reorder_and_putaway_button_added) {
			return;
		}

		frm.__lensips_reorder_and_putaway_button_added = true;
		frm.add_custom_button(__("Pull Latest Locked Forecast"), () => {
			refresh_reorder_and_putaway_coverage(frm);
		});
	},
});
""".strip()

	doc = frappe.get_doc(
		{
			"doctype": "Client Script",
			"name": CLIENT_SCRIPT_NAME,
			"dt": "Reorder and Putaway Rule",
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

	frappe.clear_cache(doctype="Reorder and Putaway Rule")
