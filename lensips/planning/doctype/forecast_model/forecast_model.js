frappe.ui.form.on("Forecast Model", {
	refresh(frm) {
		frm.set_intro(
			__("Configuration only. Define IBP-style preprocessing, forecasting, and postprocessing setup here."),
		);
	},
});

