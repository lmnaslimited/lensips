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
		if (frm.doc.docstatus !== 0) {
			return;
		}

		frm.add_custom_button(__("Pull Latest Locked Forecast"), () => {
			refresh_reorder_and_putaway_coverage(frm);
		});
	},
});
