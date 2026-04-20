from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from lensips.planning.services.forecast_export_service import (
	aggregate_rows_by_item_and_warehouse,
	PeriodSpec,
	build_export_rows,
	group_rows_by_parent_warehouse,
	make_naming_series,
	normalize_filters,
	normalize_report_rows,
	resolve_parent_warehouse,
)


class TestForecastExportService(FrappeTestCase):
	def test_make_naming_series_uses_parent_warehouse_and_date(self):
		self.assertEqual(
			make_naming_series("REGIONAL - H", "Monthly", date(2026, 1, 1)),
			"SF.YY.-.REGIONAL - H.-.Monthly.-.2026-01-01.-.##",
		)

	def test_group_rows_by_parent_warehouse_uses_parent_group(self):
		warehouse_map = {
			"161 - H": SimpleNamespace(name="161 - H", parent_warehouse="REGIONAL - H", is_group=0),
			"REGIONAL - H": SimpleNamespace(name="REGIONAL - H", parent_warehouse="All Warehouse", is_group=1),
		}
		rows = [
			frappe._dict({"warehouse": "161 - H", "item_code": "ITEM-001"}),
			frappe._dict({"warehouse": "REGIONAL - H", "item_code": "ITEM-002"}),
		]

		grouped = group_rows_by_parent_warehouse(rows, warehouse_map)

		self.assertEqual(sorted(grouped.keys()), ["REGIONAL - H"])
		self.assertEqual(len(grouped["REGIONAL - H"]), 2)
		self.assertEqual(resolve_parent_warehouse("161 - H", warehouse_map), "REGIONAL - H")

	def test_normalize_report_rows_prefers_item_rows(self):
		rows = [
			frappe._dict(
				{
					"row_type": "group",
					"item_code": "ITEM-001",
					"warehouse": "161 - H",
					"forecast_qty_total": 314,
				}
			),
			frappe._dict(
				{
					"row_type": "item",
					"item_code": "ITEM-001",
					"warehouse": "161 - H",
					"forecast_qty_total": 33,
				}
			),
		]

		normalized = normalize_report_rows(rows)

		self.assertEqual(len(normalized), 1)
		self.assertEqual(normalized[0]["forecast_qty_total"], 33)

	def test_aggregate_rows_by_item_and_warehouse_sums_by_delivery_date(self):
		rows = [
			frappe._dict(
				{
					"item_code": "ITEM-001",
					"item_name": "Item 001",
					"customer": "CUST-001",
					"uom": "CTN",
					"warehouse": "161 - H",
					"delivery_date": date(2026, 1, 1),
					"price_list": "Standard Buying",
					"is_locked": 0,
					"forecast_qty_2026_01_01": 10,
					"forecast_value_2026_01_01": 100.0,
					"actual_qty_2026_01_01": 3,
					"actual_value_2026_01_01": 30.0,
					"forecast_qty_total": 10,
					"forecast_value_total": 100.0,
					"actual_qty_total": 3,
					"actual_value_total": 30.0,
				}
			),
			frappe._dict(
				{
					"item_code": "ITEM-001",
					"item_name": "Item 001",
					"uom": "CTN",
					"warehouse": "161 - H",
					"delivery_date": date(2026, 1, 1),
					"price_list": "Standard Buying",
					"is_locked": 1,
					"forecast_qty_2026_01_01": 5,
					"forecast_value_2026_01_01": 50.0,
					"actual_qty_2026_01_01": 2,
					"actual_value_2026_01_01": 20.0,
					"forecast_qty_total": 5,
					"forecast_value_total": 50.0,
					"actual_qty_total": 2,
					"actual_value_total": 20.0,
				}
			),
			frappe._dict(
				{
					"item_code": "ITEM-001",
					"item_name": "Item 001",
					"uom": "CTN",
					"warehouse": "161 - H",
					"delivery_date": date(2026, 1, 8),
					"price_list": "Standard Buying",
					"is_locked": 0,
					"forecast_qty_2026_01_08": 7,
					"forecast_value_2026_01_08": 70.0,
					"actual_qty_2026_01_08": 1,
					"actual_value_2026_01_08": 10.0,
					"forecast_qty_total": 7,
					"forecast_value_total": 70.0,
					"actual_qty_total": 1,
					"actual_value_total": 10.0,
				}
			),
		]

		aggregated = aggregate_rows_by_item_and_warehouse(rows)

		self.assertEqual(len(aggregated), 2)
		row = next(row for row in aggregated if row["delivery_date"] == date(2026, 1, 1))
		self.assertEqual(row["forecast_qty_2026_01_01"], 15)
		self.assertEqual(row["forecast_value_2026_01_01"], 150.0)
		self.assertEqual(row["actual_qty_2026_01_01"], 5)
		self.assertEqual(row["actual_value_2026_01_01"], 50.0)
		self.assertEqual(row["forecast_qty_total"], 15)
		self.assertEqual(row["forecast_value_total"], 150.0)
		self.assertEqual(row["actual_qty_total"], 5)
		self.assertEqual(row["actual_value_total"], 50.0)
		self.assertEqual(row["is_locked"], 1)

		second_row = next(row for row in aggregated if row["delivery_date"] == date(2026, 1, 8))
		self.assertEqual(second_row["forecast_qty_2026_01_08"], 7)
		self.assertEqual(second_row["forecast_value_2026_01_08"], 70.0)
		self.assertEqual(second_row["actual_qty_2026_01_08"], 1)
		self.assertEqual(second_row["actual_value_2026_01_08"], 10.0)

	def test_build_export_rows_uses_same_period_actuals_and_preserves_locked_rows(self):
		child_meta = SimpleNamespace(
			fields=[
				SimpleNamespace(fieldname="item_code"),
				SimpleNamespace(fieldname="item_name"),
				SimpleNamespace(fieldname="uom"),
				SimpleNamespace(fieldname="delivery_date"),
				SimpleNamespace(fieldname="forecast_qty"),
				SimpleNamespace(fieldname="actual_qty"),
				SimpleNamespace(fieldname="actual_value"),
				SimpleNamespace(fieldname="price_list_rate"),
				SimpleNamespace(fieldname="adjust_value"),
				SimpleNamespace(fieldname="demand_value"),
				SimpleNamespace(fieldname="adjust_qty"),
				SimpleNamespace(fieldname="demand_qty"),
				SimpleNamespace(fieldname="warehouse"),
				SimpleNamespace(fieldname="locked"),
			]
		)
		detail_meta = object()
		future_specs = [PeriodSpec(period=date(2026, 1, 1), suffix="2026_01_01")]
		rows = [
			frappe._dict(
				{
					"item_code": "ITEM-001",
					"item_name": "Item 001",
					"uom": "CTN",
					"warehouse": "161 - H",
					"price_list": "Standard Buying",
					"price_list_rate": 10.0,
					"actual_qty_2026_01_01": 12.0,
					"actual_value_2026_01_01": 180.5,
					"forecast_qty_2026_01_01": 20,
					"forecast_value_2026_01_01": 300.0,
				}
			),
			frappe._dict(
				{
					"item_code": "ITEM-002",
					"item_name": "Item 002",
					"customer": "CUST-002",
					"uom": "PL1",
					"warehouse": "161 - H",
					"price_list": "Standard Buying",
					"price_list_rate": 10.0,
					"actual_qty_2026_01_01": 5.0,
					"actual_value_2026_01_01": 42.0,
					"forecast_qty_2026_01_01": 8,
					"forecast_value_2026_01_01": 120.0,
				}
			),
		]
		existing_items = {
			("ITEM-002", date(2026, 1, 1), "161 - H"): SimpleNamespace(
				item_code="ITEM-002",
				delivery_date=date(2026, 1, 1),
				warehouse="161 - H",
				adjust_qty=3,
				locked=1,
				actual_qty=5,
				actual_value=42,
				price_list_rate=10.0,
				forecast_qty=88,
				as_dict=lambda no_nulls=False: {
					"item_code": "ITEM-002",
					"delivery_date": date(2026, 1, 1),
					"warehouse": "161 - H",
					"adjust_qty": 3,
					"locked": 1,
					"actual_qty": 5,
					"actual_value": 42,
					"price_list_rate": 10.0,
					"adjust_value": 25.2,
					"demand_value": 67.2,
					"forecast_qty": 88,
				},
			)
		}

		item_rows, entry_rows = build_export_rows(
			rows=rows,
			detail_rows=rows,
			future_specs=future_specs,
			existing_items=existing_items,
			child_meta=child_meta,
			detail_meta=detail_meta,
			company="Hakka",
			periodicity="Monthly",
		)

		self.assertEqual(len(item_rows), 2)
		self.assertEqual(len(entry_rows), 2)
		self.assertEqual(entry_rows[0]["price_list"], "Standard Buying")
		self.assertIn("customer", entry_rows[0])

		new_row = next(row for row in item_rows if row["item_code"] == "ITEM-001")
		self.assertEqual(new_row["actual_qty"], 12)
		self.assertEqual(new_row["actual_value"], 180.5)
		self.assertEqual(new_row["demand_qty"], 12)
		self.assertEqual(new_row["price_list_rate"], 10.0)
		self.assertEqual(new_row["adjust_value"], 0)
		self.assertEqual(new_row["demand_value"], 180.5)
		self.assertEqual(new_row["locked"], 0)

		locked_row = next(row for row in item_rows if row["item_code"] == "ITEM-002")
		self.assertEqual(locked_row["actual_qty"], 5)
		self.assertEqual(locked_row["actual_value"], 42)
		self.assertEqual(locked_row["adjust_qty"], 3)
		self.assertEqual(locked_row["adjust_value"], 25.2)
		self.assertEqual(locked_row["demand_value"], 67.2)
		self.assertEqual(locked_row["locked"], 1)

	def test_normalize_filters_validates_export_rules(self):
		with self.assertRaises(frappe.ValidationError):
			normalize_filters({"company": "Hakka", "group_by": "Warehouse", "to_date": "2026-01-01"})

		with self.assertRaises(frappe.ValidationError):
			normalize_filters(
				{
					"company": "Hakka",
					"group_by": "Item",
					"periodicity": "Daily",
					"to_date": "2026-01-01",
				}
			)

		with self.assertRaises(frappe.ValidationError):
			normalize_filters(
				{
					"company": "Hakka",
					"group_by": "Item",
					"periodicity": "Monthly",
					"forecast_periods": 19,
					"to_date": "2026-01-01",
				}
			)
