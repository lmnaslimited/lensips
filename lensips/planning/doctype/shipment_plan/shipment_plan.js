function recalculate_planned_row(frm, cdt, cdn, mode) {
	const row = locals[cdt][cdn];
	if (!row || !row.item_code) return;
	return frappe.call({
		method: "lensips.planning.doctype.shipment_plan.shipment_plan.calculate_planned_item_row",
		args: {
			item_code: row.item_code,
			open_qty: row.open_qty,
			planned_qty: row.planned_qty,
			transfer_uom: row.transfer_uom,
			shipment_uom: row.shipment_uom,
			open_uom: row.open_uom,
			source_warehouse: frm.doc.source_warehouse,
			mode: mode,
		},
		callback(r) {
			const values = r.message || {};
			Object.entries(values).forEach(([fieldname, value]) => {
				if (value !== undefined) {
					frappe.model.set_value(cdt, cdn, fieldname, value);
				}
			});
			frm.refresh_field("planned_items");
		},
	});
}

function update_capacity_banner(frm) {
	if (!frm.doc.name) return;
	return frm.call("get_truck_prerequisites").then((r) => {
		const data = r.message || {};
		if (data.exceeds_truck_capacity) {
			const parts = [];
			if (data.exceeds_pallets) {
				parts.push(__("Pallets {0} exceed allowed {1}.", [flt(data.total_pallets).toFixed(2), flt(data.max_allowed_pallets).toFixed(2)]));
			}
			if (data.exceeds_weight) {
				parts.push(__("Weight {0} exceeds allowed {1}.", [flt(data.total_weight).toFixed(2), flt(data.max_allowed_weight).toFixed(2)]));
			}
			frm.dashboard.clear_headline();
			frm.dashboard.set_headline_alert(parts.join(" "), "red");
		} else if (data.max_allowed_pallets || data.max_allowed_weight) {
			frm.dashboard.clear_headline();
			frm.dashboard.set_headline_alert(
				__("Truck capacity OK. Pallets {0}/{1}, Weight {2}/{3}.", [
					flt(data.total_pallets).toFixed(2),
					flt(data.max_allowed_pallets || 0).toFixed(2),
					flt(data.total_weight).toFixed(2),
					flt(data.max_allowed_weight || 0).toFixed(2),
				]),
				"green"
			);
		} else {
			frm.dashboard.clear_headline();
		}
	});
}

frappe.ui.form.on("Shipment Plan", {
	setup(frm) {
		frm.set_indicator_formatter("item_code", function (doc) {
			if (!doc.item_code) {
				return "blue";
			}
			const actual_qty = flt(doc.actual_qty);
			const required_qty = flt(doc.shipment_stock_qty || doc.planned_stock_qty || doc.stock_qty);
			if (!actual_qty) {
				return "red";
			}
			if (required_qty && actual_qty < required_qty) {
				return "orange";
			}
			return "green";
		});
	},

	get_material_requests(frm) {
		if (!frm.doc.company || !frm.doc.source_warehouse || !frm.doc.target_warehouse) {
			frappe.msgprint({ title: __("Missing Details"), message: __("Please select Company, Source Warehouse, and Target Warehouse first."), indicator: "orange" });
			return;
		}
		return frm.call("set_open_material_requests").then(() => {
			frm.refresh_field("material_requests");
			frm.dirty();
			return frm.save();
		});
	},

	get_planned_items(frm) {
		if (!frm.doc.company || !frm.doc.source_warehouse || !frm.doc.target_warehouse || !frm.doc.shipment_profile) {
			frappe.msgprint({ title: __("Missing Details"), message: __("Please select Company, Source Warehouse, Target Warehouse, and Shipment Profile first."), indicator: "orange" });
			return;
		}
		return frm.call("set_planned_items").then(() => {
			frm.refresh_field("planned_items");
			frm.dirty();
			return frm.save().then(() => update_capacity_banner(frm));
		});
	},

	refresh(frm) {
		update_capacity_banner(frm);
		if (frm.doc.docstatus === 1 && frm.doc.status === "Submitted") {
			frm.add_custom_button(__("Start Plan"), () => {
				frappe.confirm(__("Create a Pick List and start this Shipment Plan?"), () => {
					frm.call("start_plan").then((r) => {
						const msg = r.message || {};
						if (msg.pick_lists && msg.pick_lists.length) {
							frappe.show_alert({
								message: __("Created {0} Pick List(s): {1}", [msg.pick_lists.length, msg.pick_lists.join(", ")]),
								indicator: "green",
							});
						}
						frm.reload_doc();
					});
				});
			}, __("Actions"));
		}
	},
});

frappe.ui.form.on("Shipment Plan Item", {
	planned_qty(frm, cdt, cdn) {
		recalculate_planned_row(frm, cdt, cdn, "planned_qty");
	},

	transfer_uom(frm, cdt, cdn) {
		recalculate_planned_row(frm, cdt, cdn, "recalculate_from_uom");
	},

	shipment_uom(frm, cdt, cdn) {
		recalculate_planned_row(frm, cdt, cdn, "shipment_uom");
	},

	open_qty(frm, cdt, cdn) {
		recalculate_planned_row(frm, cdt, cdn, "recalculate_from_uom");
	},

	item_code(frm, cdt, cdn) {
		recalculate_planned_row(frm, cdt, cdn, "recalculate_from_uom");
	},
});
