frappe.query_reports["LENS Sales Forecast Holt Winters"] = {
	onload: function (report) {
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
			fieldname: "uom",
			label: __("Qty UOM"),
			fieldtype: "Link",
			options: "UOM",
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Item", "Item Group", "Customer", "Territory", "Product Segment", "Sales Category"],
			default: "Item",
			reqd: 1,
		},
		{
			fieldname: "show_past_data",
			label: __("Show Past Data"),
			fieldtype: "Check",
			default: 0,
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
	tree: true,
	name_field: "display_name",
	parent_field: "parent_display_name",
	initial_depth: 0,
	formatter: function (value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (data && data.row_type === "group") {
			return `<b>${formatted}</b>`;
		}
		return formatted;
	},
};

function add_export_button(report) {
	report.page.add_inner_button(__("Export to Sales Forecast"), () => {
		const data = (frappe.query_report.data || []).filter((row) => row && row.row_type === "item");
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

				const forecast_names = r.message.forecast_names || (r.message.forecast_name ? [r.message.forecast_name] : []);
				frappe.msgprint(
					__(
						"Sales Forecast Created: {0}",
						[forecast_names.length ? forecast_names.join(", ") : __("No document returned")]
					)
				);
			},
		});
	});
}
