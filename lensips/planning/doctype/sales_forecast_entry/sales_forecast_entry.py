# Copyright (c) 2026, LMNAs and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class SalesForecastEntry(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		actual_qty: DF.Float
		actual_value: DF.Currency
		adjust_qty: DF.Float
		adjust_value: DF.Currency
		company: DF.Link
		customer: DF.Link | None
		demand_qty: DF.Float
		demand_value: DF.Currency
		forecast_qty: DF.Float
		forecast_value: DF.Currency
		group_key: DF.Data | None
		item_code: DF.Link
		item_group: DF.Link | None
		item_name: DF.Data | None
		is_locked: DF.Check
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		period: DF.Date
		period_label: DF.Data | None
		price_list: DF.Link | None
		price_list_rate: DF.Currency
		sales_group: DF.Data | None
		warehouse: DF.Link | None
	# end: auto-generated types

	pass
