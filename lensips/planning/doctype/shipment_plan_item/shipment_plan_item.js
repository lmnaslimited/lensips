// function recalculate_planned_row(frm, cdt, cdn, mode) {
// 	const row = locals[cdt][cdn];
// 	if (!row || !row.item_code) return;
// 	return frappe.call({
// 		method: "lensips.planning.doctype.shipment_plan.shipment_plan.calculate_planned_item_row",
// 		args: {
// 			item_code: row.item_code,
// 			open_qty: row.open_qty,
// 			planned_qty: row.planned_qty,
// 			transfer_uom: row.transfer_uom,
// 			shipment_uom: row.shipment_uom,
// 			open_uom: row.open_uom,
// 			source_warehouse: frm.doc.source_warehouse,
// 			mode: mode,
// 		},
// 		callback(r) {
// 			const values = r.message || {};
// 			Object.entries(values).forEach(([fieldname, value]) => {
// 				if (value !== undefined) {
// 					frappe.model.set_value(cdt, cdn, fieldname, value);
// 				}
// 			});
// 			frm.refresh_field("planned_items");
// 			frm.call("recalculate_totals").then(() => {
// 				frm.refresh_fields([
// 					"total_open_qty",
// 					"total_planned_qty",
// 					"total_shipment_qty",
// 					"total_stock_qty",
// 					"total_planned_stock_qty",
// 					"total_shipment_stock_qty",
// 					"total_weight_per_unit",
// 					"total_shipment_weight",
// 					"total_pallets",
// 				]);
// 			});
// 		},
// 	});
// }

// frappe.ui.form.on("Shipment Plan Item", {
// 	refresh(frm) {
// 		// row handlers are registered on the child doctype so inline grid edits recalculate correctly
// 	},

// 	planned_qty(frm, cdt, cdn) {
// 		recalculate_planned_row(frm, cdt, cdn, "planned_qty");
// 	},

// 	transfer_uom(frm, cdt, cdn) {
// 		recalculate_planned_row(frm, cdt, cdn, "recalculate_from_uom");
// 	},

// 	shipment_uom(frm, cdt, cdn) {
// 		recalculate_planned_row(frm, cdt, cdn, "shipment_uom");
// 	},

// 	open_qty(frm, cdt, cdn) {
// 		recalculate_planned_row(frm, cdt, cdn, "recalculate_from_uom");
// 	},

// 	item_code(frm, cdt, cdn) {
// 		recalculate_planned_row(frm, cdt, cdn, "recalculate_from_uom");
// 	},
// });
