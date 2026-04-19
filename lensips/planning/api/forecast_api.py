from __future__ import annotations

import importlib

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.background_jobs import get_job, get_job_status

from lensips.planning.report.lens_sales_forecast_holt_winters import (
	lens_sales_forecast_holt_winters as forecast_report,
)


def _run_sales_forecast_export(filters, submit_document=0):
	return _execute_sales_forecast_export(filters=filters, report_rows=None, submit_document=submit_document)


def _execute_sales_forecast_export(filters, report_rows=None, report_columns=None, submit_document=0):
	report_module = importlib.reload(forecast_report)
	export_service = importlib.reload(
		importlib.import_module("lensips.planning.services.forecast_export_service")
	)
	if report_rows is None:
		data = report_module.get_sales_forecast_export_rows(frappe._dict(filters or {}))
	else:
		data = report_rows

	result = export_service.create_sales_forecast(data=data, filters=filters, columns=report_columns)

	if cint(submit_document):
		for forecast_name in result.get("forecast_names") or (
			[result.get("forecast_name")] if result.get("forecast_name") else []
		):
			doc = frappe.get_doc("Sales Forecast", forecast_name)
			if doc.docstatus == 0:
				doc.submit()
		result["submitted"] = 1
		result["message"] = _("Sales Forecast(s) successfully created and submitted.")
	else:
		result["submitted"] = 0

	return result
@frappe.whitelist()
def create_sales_forecast_from_report(data=None, filters=None, submit_document=0):
	if filters is None and data:
		filters = data
	filters = frappe.parse_json(filters) or {}
	job_id = f"sales-forecast-export-{frappe.generate_hash(length=10)}"

	frappe.enqueue(
		_run_sales_forecast_export,
		queue="short",
		timeout=1500,
		enqueue_after_commit=False,
		job_id=job_id,
		filters=filters,
		submit_document=cint(submit_document),
	)

	return {
		"queued": 1,
		"job_id": job_id,
		"forecast_name": None,
		"forecast_names": [],
		"results": [],
		"total_items": 0,
		"total_entries": 0,
		"message": _("Sales Forecast export has been queued in the background. Job ID: {0}").format(job_id),
	}


@frappe.whitelist()
def create_sales_forecast_from_report_live(data=None, filters=None, submit_document=0):
	if filters is None and data:
		filters = data
	filters = frappe.parse_json(filters) or {}

	result = _execute_sales_forecast_export(
		filters=filters,
		report_rows=None,
		report_columns=None,
		submit_document=cint(submit_document),
	)
	result["queued"] = 0
	result["live"] = 1
	return result


@frappe.whitelist()
def get_sales_forecast_export_status(job_id):
	job_id = (job_id or "").strip()
	if not job_id:
		frappe.throw(_("Job ID is required."))

	job = get_job(job_id)
	status = get_job_status(job_id)
	status_name = getattr(status, "name", None) or (str(status) if status else "not_found")
	status_name = status_name.lower()
	result = None

	if job and status_name == "finished":
		result = job.result

	return {
		"job_id": job_id,
		"status": status_name,
		"is_finished": status_name == "finished",
		"is_failed": status_name == "failed",
		"is_started": status_name == "started",
		"is_queued": status_name == "queued",
		"result": result,
		"message": (
			_("Export job is running.")
			if status_name == "started"
			else _("Export job is queued.")
			if status_name == "queued"
			else _("Export job finished.")
			if status_name == "finished"
			else _("Export job failed.")
			if status_name == "failed"
			else _("Export job was not found.")
		),
	}
