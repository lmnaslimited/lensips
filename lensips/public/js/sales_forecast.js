function update_sales_forecast_item_row(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const actual_qty = flt(row.actual_qty);
	const adjust_qty = flt(row.adjust_qty);
	const actual_value = flt(row.actual_value);
	const adjust_value = actual_qty ? (actual_value / actual_qty) * adjust_qty : 0;
	const demand_qty = actual_qty + adjust_qty;
	const demand_value = actual_value + adjust_value;

	frappe.model.set_value(cdt, cdn, "demand_qty", demand_qty);
	frappe.model.set_value(cdt, cdn, "adjust_value", adjust_value);
	frappe.model.set_value(cdt, cdn, "demand_value", demand_value);
	set_sales_forecast_item_row_state(frm, cdt, cdn);
}

function set_sales_forecast_item_grid_properties(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;

	if (!grid) {
		return;
	}

	for (const fieldname of ["locked", "adjust_qty"]) {
		grid.update_docfield_property(fieldname, "read_only", 0);
	}

	for (const fieldname of [
		"actual_qty",
		"actual_value",
		"forecast_qty",
		"forecast_value",
		"price_list_rate",
		"adjust_value",
		"demand_qty",
		"demand_value",
	]) {
		grid.update_docfield_property(fieldname, "read_only", 1);
	}

	frm.refresh_field("items");
}

function set_sales_forecast_item_row_state(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	const grid_row = grid && grid.grid_rows_by_docname && grid.grid_rows_by_docname[cdn];
	if (!grid_row) {
		return;
	}

	const locked = cint(row.locked);
	grid_row.toggle_editable("locked", true);
	grid_row.toggle_editable("adjust_qty", !locked);
	grid_row.toggle_editable("actual_qty", false);
	grid_row.toggle_editable("actual_value", false);
	grid_row.toggle_editable("forecast_qty", false);
	grid_row.toggle_editable("forecast_value", false);
	grid_row.toggle_editable("price_list_rate", false);
	grid_row.toggle_editable("adjust_value", false);
	grid_row.toggle_editable("demand_qty", false);
	grid_row.toggle_editable("demand_value", false);
	grid_row.refresh();
}

frappe.ui.form.on("Sales Forecast", {
	refresh(frm) {
		set_sales_forecast_item_grid_properties(frm);

		for (const row of frm.doc.items || []) {
			update_sales_forecast_item_row(frm, row.doctype, row.name);
			set_sales_forecast_item_row_state(frm, row.doctype, row.name);
		}
	},
	items_on_form_rendered(frm, cdt, cdn) {
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
	price_list_rate(frm, cdt, cdn) {
		update_sales_forecast_item_row(frm, cdt, cdn);
	},
	actual_qty(frm, cdt, cdn) {
		update_sales_forecast_item_row(frm, cdt, cdn);
	},
	actual_value(frm, cdt, cdn) {
		update_sales_forecast_item_row(frm, cdt, cdn);
	},
	form_render(frm, cdt, cdn) {
		set_sales_forecast_item_row_state(frm, cdt, cdn);
	},
});
