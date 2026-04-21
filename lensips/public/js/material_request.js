frappe.ui.form.on("Material Request", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.material_request_type === "Purchase") {
			frm.add_custom_button(
				__("Create PO Consolidated"),
				() => frm.events.create_split_pos(frm),
				__("Actions")
			);
		}
	},

	create_split_pos(frm) {
		frm.call({
			method: "lensips.planning.api.material_request.create_purchase_orders_by_supplier",
			args: {
				source_name: frm.doc.name,
			},
			freeze: true,
			freeze_message: __("Creating draft Purchase Orders"),
			callback(r) {
				const result = r.message || {};
				const purchaseOrders = result.purchase_orders || [];
				const skippedItems = result.items_without_supplier || [];
				const parts = [];

				if (purchaseOrders.length) {
					parts.push(
						__("Created draft Purchase Orders: {0}", [purchaseOrders.join(", ")])
					);
				}

				if (skippedItems.length) {
					parts.push(
						__("Items without Default Supplier: {0}", [skippedItems.join(", ")])
					);
				}

				frappe.msgprint({
					title: __("Split Purchase Orders"),
					indicator: purchaseOrders.length ? "green" : "orange",
					message: parts.length ? parts.join("<br><br>") : __("No Purchase Orders were created."),
				});

				frm.reload_doc();
			},
		});
	},
});
