from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase


class TestForecastModel(FrappeTestCase):
	def test_forecast_model_doctype_exists(self):
		meta = frappe.get_meta("Forecast Model")
		self.assertEqual(meta.name, "Forecast Model")
		self.assertTrue(meta.get_field("preprocessing_steps"))
		self.assertTrue(meta.get_field("forecasting_steps"))
		self.assertTrue(meta.get_field("postprocessing_steps"))

