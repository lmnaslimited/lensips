from __future__ import annotations

import frappe


OLD_REPORT_NAMES = (
	"LENS Sales Forecast (Holt-Winters)",
	"LENS Sales Forecast Holt-Winters",
)
NEW_REPORT_NAME = "LENS Sales Forecast Holt Winters"


def execute():
	for old_report_name in OLD_REPORT_NAMES:
		if not frappe.db.exists("Report", old_report_name):
			continue

		if frappe.db.exists("Report", NEW_REPORT_NAME):
			frappe.delete_doc("Report", old_report_name, force=True, ignore_missing=True)
			continue

		frappe.rename_doc("Report", old_report_name, NEW_REPORT_NAME, force=True)
