"""Tell a subscription's app (Data Fusion, and any app added later) when its
invoice is paid, so the app can extend the customer's access to the end of the
period paid for.

Each app is a Subscription App record: endpoint URL + secret. The app receives
a POST of JSON {"invoice", "account_ids", "paid_to"} with the hex HMAC-SHA256
of the body (keyed by the secret) in the X-Subscription-Signature header, and
answers 200 when done. Safe to repeat: apps only ever move the end date forward.
"""

import hashlib
import hmac
import json

import frappe
import requests
from frappe.utils import getdate, today


def payment_entry_on_submit(doc, method=None):
	"""Payment Entry hook: once the payment is committed, notify the app of
	every subscription invoice it paid."""
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
	not ended, in case an app was unreachable when the payment came in."""
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
				title="Subscription App: payment notify failed",
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

	sub = frappe.db.get_value(
		"Business Subscription", inv.custom_business_subscription,
		["subscription_app", "app_account_ids"],
		as_dict=True,
	)
	if not sub or not sub.subscription_app:
		return
	account_ids = [i.strip() for i in (sub.app_account_ids or "").split(",") if i.strip()]
	if not account_ids:
		return

	app = frappe.get_doc("Subscription App", sub.subscription_app)
	if not app.enabled:
		return

	body = json.dumps({
		"invoice": name,
		"account_ids": account_ids,
		"paid_to": str(getdate(inv.to_date)),
	}, separators=(",", ":")).encode("utf-8")
	signature = hmac.new(
		app.get_password("webhook_secret").encode("utf-8"), body, hashlib.sha256
	).hexdigest()

	response = requests.post(
		app.endpoint_url,
		data=body,
		headers={"Content-Type": "application/json", "X-Subscription-Signature": signature},
		timeout=30,
	)
	if response.status_code != 200:
		frappe.throw("{0} answered {1}: {2}".format(
			app.name, response.status_code, response.text[:500]
		))
