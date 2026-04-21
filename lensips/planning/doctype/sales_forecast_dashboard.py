from frappe import _


def get_data(data=None):
	return {
		"fieldname": "sales_forecast",
		"transactions": [
			{
				"label": _("MPS"),
				"items": ["Master Production Schedule"],
			},
			{
				"label": _("Reorder and Putaway"),
				"items": ["Reorder and Putaway Rule"],
			},
		],
	}
