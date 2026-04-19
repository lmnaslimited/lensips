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
		company: DF.Link
		customer: DF.Link | None
		forecast_qty: DF.Float
		forecast_value: DF.Currency
		item_code: DF.Link
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		period: DF.Date
		price_list: DF.Link | None
		warehouse: DF.Link | None
	# end: auto-generated types

	pass
