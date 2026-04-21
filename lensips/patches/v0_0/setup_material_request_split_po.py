from __future__ import annotations

import frappe


def execute():
	if not frappe.db.exists("Client Script", "Split PO by Supplier"):
		return

	client_script = frappe.get_doc("Client Script", "Split PO by Supplier")
	client_script.enabled = 0
	client_script.script = "// Managed by lensips planning app.\n"
	client_script.save(ignore_permissions=True)
