from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from lensips.planning.doctype.reorder_and_putaway_rule.reorder_and_putaway_rule import (
	_build_child_row,
	_compute_percentage,
	_get_planning_capacity_settings,
	_get_period_start,
	_get_forecast_item_count,
	_convert_qty,
	_resolve_material_request_type,
)


class TestReorderAndPutawayRule(unittest.TestCase):
	def test_monthly_period_uses_first_day_of_month(self):
		self.assertEqual(_get_period_start("2026-04-21", "Monthly"), date(2026, 4, 1))

	def test_weekly_period_uses_week_start(self):
		with patch(
			"lensips.planning.doctype.reorder_and_putaway_rule.reorder_and_putaway_rule.get_first_day_of_week",
			return_value=date(2026, 4, 20),
		):
			self.assertEqual(_get_period_start("2026-04-21", "Weekly"), date(2026, 4, 20))

	def test_request_type_manufacture_downgrades_outside_source_warehouse(self):
		module_path = "lensips.planning.doctype.reorder_and_putaway_rule.reorder_and_putaway_rule"
		with patch(
			f"{module_path}.frappe",
			new=SimpleNamespace(db=SimpleNamespace(get_single_value=lambda *args, **kwargs: "121 - H")),
		):
			self.assertEqual(
				_resolve_material_request_type("Manufacture", warehouse="122 - H", company="Test Company"),
				"Transfer",
			)

	def test_request_type_purchase_stays_purchase(self):
		self.assertEqual(
			_resolve_material_request_type("Purchase", warehouse="121 - H", company="Test Company"),
			"Purchase",
		)

	def test_missing_planning_settings_stop_creation(self):
		module_path = "lensips.planning.doctype.reorder_and_putaway_rule.reorder_and_putaway_rule"
		fake_frappe = SimpleNamespace(
			db=SimpleNamespace(get_value=lambda *args, **kwargs: None),
			throw=lambda msg: (_ for _ in ()).throw(RuntimeError(str(msg))),
		)
		with patch(f"{module_path}.frappe", new=fake_frappe):
			with self.assertRaisesRegex(RuntimeError, "Please configure Planning Settings"):
				_get_planning_capacity_settings()

	def test_child_row_uses_planning_setting_ratios(self):
		module_path = "lensips.planning.doctype.reorder_and_putaway_rule.reorder_and_putaway_rule"
		fake_item = SimpleNamespace(
			name="TEST-ITEM",
			item_name="Test Item",
			sales_uom="Nos",
			stock_uom="Nos",
			purchase_uom="Box",
			default_material_request_type="Purchase",
		)
		fake_frappe = SimpleNamespace(
			db=SimpleNamespace(get_single_value=lambda *args, **kwargs: 0),
			get_cached_doc=lambda doctype, name: fake_item,
			throw=lambda msg: (_ for _ in ()).throw(RuntimeError(str(msg))),
		)
		planning_settings = {
			"forecast_to_capacity_ratio": 1.5,
			"forecast_to_reorder_level_ratio": 0.25,
			"reorder_quantity_based_on": "Demand",
		}
		row = SimpleNamespace(
			name="row-1",
			item_code="TEST-ITEM",
			item_name="Test Item",
			uom="Nos",
			demand_qty=12,
			forecast_qty=8,
			delivery_date="2026-04-21",
		)

		with patch(f"{module_path}.frappe", new=fake_frappe):
			with patch(
				f"{module_path}._convert_qty",
				side_effect=lambda item_doc, qty, from_uom, to_uom: qty,
			):
				child = _build_child_row(
					sales_forecast_item=row,
					company="Test Company",
					warehouse="121 - H",
					period="2026-04-01",
					frequency="Monthly",
					planning_settings=planning_settings,
				)

		self.assertEqual(child["capacity"], 12.0)
		self.assertEqual(child["warehouse_reorder_level"], 2.0)
		self.assertEqual(child["warehouse_reorder_qty"], 12.0)

	def test_compute_percentage_handles_empty_total(self):
		self.assertEqual(_compute_percentage(5, 0), 0)

	def test_compute_percentage_returns_ratio(self):
		self.assertEqual(_compute_percentage(3, 4), 75.0)

	def test_forecast_item_count_includes_unlocked_items(self):
		module_path = "lensips.planning.doctype.reorder_and_putaway_rule.reorder_and_putaway_rule"
		fake_doc = SimpleNamespace(
			get=lambda key: [
				SimpleNamespace(warehouse="121 - H", delivery_date="2026-04-21", locked=1),
				SimpleNamespace(warehouse="121 - H", delivery_date="2026-04-21", locked=0),
				SimpleNamespace(warehouse="122 - H", delivery_date="2026-04-21", locked=1),
			],
		)
		with patch(f"{module_path}.frappe", new=SimpleNamespace(get_doc=lambda *args, **kwargs: fake_doc)):
			self.assertEqual(_get_forecast_item_count("SF-1", "121 - H", "Monthly", "2026-04-01"), 2)

	def test_convert_qty_uses_item_uom_table_before_global_fallback(self):
		module_path = "lensips.planning.doctype.reorder_and_putaway_rule.reorder_and_putaway_rule"
		fake_item = SimpleNamespace(
			name="101501A",
			stock_uom="Inner",
			get=lambda key: [
				SimpleNamespace(uom="Inner", conversion_factor=1),
				SimpleNamespace(uom="CTN", conversion_factor=5),
			]
			if key == "uoms"
			else None,
		)
		with patch(f"{module_path}.frappe", new=SimpleNamespace(throw=lambda msg: (_ for _ in ()).throw(RuntimeError(str(msg))))), patch(
			f"{module_path}.get_uom_conv_factor",
			side_effect=RuntimeError("global fallback should not be used"),
		):
			self.assertEqual(_convert_qty(fake_item, 10, "CTN", "Inner"), 50.0)


if __name__ == "__main__":
	unittest.main()
