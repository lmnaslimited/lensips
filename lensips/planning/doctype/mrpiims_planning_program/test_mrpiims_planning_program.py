from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase


class TestMRPIIMSPlanningProgram(FrappeTestCase):
	def test_configuration_doctypes_exist(self):
		for doctype in [
			"MRPIIMS Planning Program",
			"MRPIIMS Process Template",
			"Forecast Model",
			"Forecast Model Step",
			"Planning Measure",
			"Planning Measure Entry",
			"Planning Object Assignment",
			"Planning Segmentation Rule",
			"Planning Time Profile",
			"Integration Output Profile",
		]:
			meta = frappe.get_meta(doctype)
			self.assertEqual(meta.name, doctype)

