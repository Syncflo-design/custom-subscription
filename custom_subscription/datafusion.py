"""Tell Data Fusion when a subscription invoice is paid, so the customer's
access (CompanyMaster.UseEndDate) runs to the end of the period paid for.

Site config (bench --site <site> set-config <key> <value>):
    datafusion_webhook_url     e.g. https://<host>/za/PaymentNotify.ashx
    datafusion_webhook_secret  same value as PaymentWebhookSecret in Data Fusion's Web.config

Safe to repeat: Data Fusion only ever moves the date forward.
"""

import hashlib
import hmac
import json

import frappe
import requests
from frappe.utils import getdate, today


def payment_entry_on_submit(doc, method=None):
	"""Payment Entry hook: once the payment is committed, notify Data Fusion
	for every Sales Invoice it paid."""
	invoices = list({
		r.reference_name for r in doc.references
		if r.reference_doctype == "Sales Invoice" and r.reference_name
	})
	if invoices:
		frappe.enqueue(
			"custom_subscription.datafusion.notify_invoices",
			invoices=invoices,
			enqueue_after_commit=True,
		)


def push_current_periods():
	"""Daily catch-up: re-send every paid subscription invoice whose period has
	not ended, in case Data Fusion was unreachable when the payment came in."""
	notify_invoices(frappe.get_all(
		"Sales Invoice",
		filters={
			"docstatus": 1,
			"status": "Paid",
			"custom_business_subscription": ["is", "set"],
			"to_date": [">=", today()],
		},
		pluck="name",
	))


def notify_invoices(invoices):
	for name in invoices:
		try:
			notify_invoice(name)
		except Exception:
			frappe.log_error(
				title="Data Fusion: payment notify failed",
				message="{0}\n\n{1}".format(name, frappe.get_traceback()),
			)


def notify_invoice(name):
	inv = frappe.db.get_value(
		"Sales Invoice", name,
		["docstatus", "status", "to_date", "custom_business_subscription"],
		as_dict=True,
	)
	if not inv or inv.docstatus != 1 or inv.status != "Paid":
		return
	if not inv.to_date or not inv.custom_business_subscription:
		return

	ids = frappe.db.get_value(
		"Business Subscription", inv.custom_business_subscription, "datafusion_company_ids"
	) or ""
	company_ids = [i.strip() for i in ids.split(",") if i.strip()]
	if not company_ids:
		return

	url = frappe.conf.get("datafusion_webhook_url")
	secret = frappe.conf.get("datafusion_webhook_secret")
	if not url or not secret:
		frappe.throw("datafusion_webhook_url / datafusion_webhook_secret missing from site config")

	body = json.dumps({
		"invoice": name,
		"company_ids": company_ids,
		"paid_to": str(getdate(inv.to_date)),
	}, separators=(",", ":")).encode("utf-8")
	signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

	response = requests.post(
		url,
		data=body,
		headers={"Content-Type": "application/json", "X-DataFusion-Signature": signature},
		timeout=30,
	)
	if response.status_code != 200:
		frappe.throw("Data Fusion answered {0}: {1}".format(response.status_code, response.text[:500]))
