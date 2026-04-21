from __future__ import annotations

import unittest
from unittest.mock import patch

from lensips.planning.report.lens_sales_forecast_holt_winters.lens_sales_forecast_holt_winters import (
	convert_qty_to_display,
)


class TestLensSalesForecastHoltWinters(unittest.TestCase):
	def test_convert_qty_to_display_uses_item_uom_table(self):
		module_path = "lensips.planning.report.lens_sales_forecast_holt_winters.lens_sales_forecast_holt_winters"
		with patch(
			f"{module_path}.get_item_conversion_factor",
			return_value={"conversion_factor": 5},
		), patch(
			f"{module_path}.get_uom_conv_factor",
			side_effect=RuntimeError("fallback should not be used"),
		):
			self.assertEqual(convert_qty_to_display(10, "Inner", "CTN", None, "101501A"), 2.0)


if __name__ == "__main__":
	unittest.main()
