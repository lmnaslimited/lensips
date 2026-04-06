frappe.query_reports["LENS Sales Forecast Holt Winters"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -24),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "based_on_document",
			label: __("Document Type"),
			fieldtype: "Select",
			options: ["Sales Order", "Sales Invoice", "Delivery Note"],
			default: "Sales Invoice",
			reqd: 1,
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => {
				const company = frappe.query_report.get_filter_value("company");
				if (!company) {
					return {};
				}

				return {
					filters: {
						company,
					},
				};
			},
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Item", "Item Group", "Customer", "Sales Group"],
			default: "Item",
			reqd: 1,
		},
		{
			fieldname: "periodicity",
			label: __("Periodicity"),
			fieldtype: "Select",
			options: ["Weekly", "Monthly", "Quarterly", "Half-Yearly", "Yearly"],
			default: "Monthly",
			reqd: 1,
		},
		{
			fieldname: "alpha",
			label: __("Alpha"),
			fieldtype: "Float",
			default: 0.2,
			reqd: 1,
		},
		{
			fieldname: "beta",
			label: __("Beta"),
			fieldtype: "Float",
			default: 0.1,
			reqd: 1,
		},
		{
			fieldname: "gamma",
			label: __("Gamma"),
			fieldtype: "Float",
			default: 0.1,
			reqd: 1,
		},
		{
			fieldname: "season_length",
			label: __("Season Length"),
			fieldtype: "Int",
			default: 12,
			reqd: 1,
		},
		{
			fieldname: "forecast_periods",
			label: __("Forecast Periods"),
			fieldtype: "Int",
			default: 12,
			reqd: 1,
		},
		{
			fieldname: "manufacture_date",
			label: __("Manufacture Date"),
			fieldtype: "Date",
		},
	],
};
