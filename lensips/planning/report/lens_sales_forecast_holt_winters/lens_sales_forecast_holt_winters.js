frappe.query_reports["LENS Sales Forecast Holt Winters"] = {
	onload: function (report) {
		toggle_forecast_based_on_filter(report);
		add_export_button(report);
	},
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
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -36),
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
			default: "Sales Order",
			reqd: 1,
			on_change: function () {
				toggle_forecast_based_on_filter(frappe.query_report);
			},
		},
		{
			fieldname: "forecast_based_on",
			label: __("Forecast Based On"),
			fieldtype: "Select",
			options: ["Order Date", "Delivery Date"],
			default: "Delivery Date",
			hidden: 1,
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
			default: 0.3,
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

function toggle_forecast_based_on_filter(report) {
	const document_type = report.get_filter_value("based_on_document");
	const forecast_filter = report.get_filter("forecast_based_on");
	const show_forecast_basis = document_type === "Sales Order";

	forecast_filter.toggle(show_forecast_basis);
}

function add_export_button(report) {
	report.page.add_inner_button(__("Export to Sales Forecast"), () => {
		const data = (frappe.query_report.data || []).filter((row) => row && !row._rowIndex);
		const filters = frappe.query_report.get_filter_values();

		frappe.call({
			method: "lensips.planning.api.forecast_api.create_sales_forecast_from_report",
			args: {
				data,
				filters,
			},
			freeze: true,
			freeze_message: __("Creating Sales Forecast..."),
			callback: function (r) {
				if (!r.message) {
					return;
				}

				frappe.msgprint(__("Sales Forecast Created: {0}", [r.message.forecast_name]));
			},
		});
	});
}
