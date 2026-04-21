from __future__ import annotations

import frappe
from frappe.modules.import_file import import_file_by_path


def execute():
	file_path = frappe.get_app_path("lensips", "desktop_icon", "planning.json")
	import_file_by_path(file_path, force=True, ignore_version=True, reset_permissions=False)
	frappe.clear_cache()
