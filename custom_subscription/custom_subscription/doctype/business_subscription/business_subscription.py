# Copyright (c) 2026, Chipo Hameja and contributors
# For license information, please see license.txt

from html import escape

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, cint, flt, format_date, getdate, nowdate

from custom_subscription.statement import get_statement_attachment

FREQUENCY_MONTHS = {"Monthly": 1, "Quarterly": 3, "Annually": 12}
FREQUENCY_STEP = {
	# "Daily" is a UAT/testing cadence — advances the next run date by one
	# day. Remove this entry (and the Select option) once UAT is complete.
	"Daily":     {"days": 1},
	"Monthly":   {"months": 1},
	"Quarterly": {"months": 3},
	"Annually":  {"years": 1},
}
TEMPLATE_FIELD = {
	"Monthly":   "custom_monthly_note_template",
	"Quarterly": "custom_quarterly_note_template",
	"Annually":  "custom_annually_note_template",
}

# Appended to every outgoing subscription email. Edit the details here; a
# code deploy picks up the change. Kept as plain inline-styled HTML so it
# renders consistently across mail clients without relying on the portal.
EMAIL_SIGNATURE = (
	'<br>'
	'<table style="margin-top:16px;font-family:Arial,Helvetica,sans-serif;'
	'font-size:12px;color:#444444;line-height:1.5">'
	'<tr><td style="padding-bottom:8px">'
	'<img src="https://www.nesterp.co.za/files/SyncfloLogo_small.png" '
	'alt="Syncflo" width="180" '
	'style="display:block;border:0;outline:none;max-width:180px;height:auto">'
	'</td></tr>'
	'<tr><td>'
	'<strong style="color:#111111">Syncflo (Pty) Ltd</strong><br>'
	'Accounts Team<br>'
	'<a href="mailto:accounts@syncflo.co.za" style="color:#1a73e8;text-decoration:none">accounts@syncflo.co.za</a>'
	'&nbsp;&nbsp;|&nbsp;&nbsp;'
	'<a href="https://www.syncflo.co.za" style="color:#1a73e8;text-decoration:none">www.syncflo.co.za</a>'
	'</td></tr></table>'
)


def get_period_start(sub, posting_date=None):
	"""Anchor on start_date and walk forward in steps of frequency until we
	land on the cycle that contains posting_date (default: today)."""
	months = FREQUENCY_MONTHS.get(sub.frequency)
	if not months:
		return getdate(sub.start_date)
	target = getdate(posting_date or nowdate())
	period = getdate(sub.start_date)
	while add_months(period, months) <= target:
		period = add_months(period, months)
	return period


def build_subscription_note(sub, posting_date=None):
	"""Render the appropriate per-frequency template into a validity sentence."""
	months = FREQUENCY_MONTHS.get(sub.frequency)
	if not months:
		return ""
	template = (sub.get(TEMPLATE_FIELD[sub.frequency]) or "").strip()
	if not template:
		return ""
	period_start = get_period_start(sub, posting_date)
	period_end   = add_days(add_months(period_start, months), -1)
	try:
		return template.format(
			start_date=format_date(period_start),
			end_date=format_date(period_end),
			frequency=sub.frequency,
			customer=sub.customer or "",
			subscription_id=sub.name,
		)
	except (KeyError, IndexError):
		# Bad placeholder in template — return raw rather than crash scheduler
		return template


class BusinessSubscription(Document):
	def before_save(self):
		# Only seed the schedule on creation. Doing this on every save
		# (the previous behaviour) silently rewound a running subscription
		# back to its start_date whenever anyone edited it.
		if self.is_new():
			if not self.last_processed_date:
				self.last_processed_date = self.start_date
			if not self.next_invoice_date:
				self.next_invoice_date = self.start_date

	def set_next_invoice_date(self):
		# Step forward from the date we just processed (next_invoice_date),
		# not from last_processed_date.
		step = FREQUENCY_STEP.get(self.frequency, {"months": 1})
		next_invoice_date = frappe.utils.add_to_date(getdate(self.next_invoice_date), **step)

		# Auto-stop: once the next run would fall after the subscription's
		# end date, clear next_invoice_date so it drops out of the daily
		# query and never generates again.
		if self.end_date and next_invoice_date > getdate(self.end_date):
			next_invoice_date = None

		self.next_invoice_date = next_invoice_date
		self.last_processed_date = getdate()

		frappe.db.set_value("Business Subscription", self.name, {
			"next_invoice_date": next_invoice_date,
			"last_processed_date": getdate(),
		})

	def create_doc(self):
		# Respect the end date: if this due date is already past the end
		# date, finish the subscription without generating anything.
		if self.end_date and getdate(self.next_invoice_date) > getdate(self.end_date):
			frappe.db.set_value("Business Subscription", self.name, "next_invoice_date", None)
			return

		if self.document_type == "Sales Order":
			create_sales_order(self)
		elif self.document_type == "Sales Invoice (Draft)":
			create_sales_invoice(self)
		else:
			create_sales_invoice(self, True)

# Get all submitted Business Subscription records whose next invoice date is due
# For every record, check the document type and create the necessary document in the required docstatus
# Then advance the next invoice date based on the frequency (stopping at end_date)

def create_sales_invoice(doc, submitted=False):
	# Due on the first day of the period it pays for (never before today: a
	# missed scheduler day can run after that date).
	period_start = getdate(doc.next_invoice_date)
	new_sales_invoice = frappe.get_doc({
		"doctype": "Sales Invoice",
		"company": doc.company,
		"customer": doc.customer,
		"posting_date": getdate(),
		"due_date": max(getdate(), period_start),
		"custom_business_subscription": doc.name,
	})

	# The period the invoice pays for. Once the invoice is paid, the
	# subscription's app extends access to to_date (custom_subscription.datafusion).
	months = FREQUENCY_MONTHS.get(doc.frequency)
	if months:
		new_sales_invoice.from_date = period_start
		new_sales_invoice.to_date = add_days(add_months(period_start, months), -1)

	for item in doc.items:
		new_sales_invoice.append("items", {
			"item_code": item.item_code,
			"qty": item.quantity,
			"rate": item.rate
		})

	# Period note keys off the validity date (next_invoice_date), not the
	# posting/send date — the invoice may be generated up to advance_days early.
	new_sales_invoice.custom_invoice_notes = build_subscription_note(doc, doc.next_invoice_date)
	new_sales_invoice.insert()

	if submitted:
		new_sales_invoice.submit()

	if doc.send_email:
		send_email(doc, new_sales_invoice)

	doc.set_next_invoice_date()

def create_sales_order(doc):
	new_sales_order = frappe.get_doc({
		"doctype": "Sales Order",
		"company": doc.company,
		"customer": doc.customer,
		"transaction_date": getdate(),
		"delivery_date": getdate(),
		"order_type": "Sales"
	})

	for item in doc.items:
		new_sales_order.append("items", {
			"item_code": item.item_code,
			"qty": item.quantity,
			"rate": item.rate
		})

	new_sales_order.insert().submit()

	if doc.send_email:
		send_email(doc, new_sales_order)

	doc.set_next_invoice_date()

def send_email(sub, target_doc, attach_statement=None):
	"""Notify the recipients that a document was generated, attaching the
	*generated document* (not the subscription itself, which was the bug).

	With attach_statement (default: the subscription's own setting), the
	customer's statement for the 12 months to the end of the month before the
	document's date goes in the same email."""
	if not sub.recipients:
		return

	recipients = [r.email for r in sub.recipients if r.email]
	if not recipients:
		return

	if attach_statement is None:
		attach_statement = sub.get("attach_statement")

	attachments = []
	try:
		attachments = [frappe.attach_print(
			target_doc.doctype,
			target_doc.name,
			print_format=sub.print_format or None,
		)]
	except Exception:
		# A bad/missing print format must not stop the notification or the run
		frappe.log_error(
			title="Custom Subscription: attach_print failed",
			message="{0} -> {1} {2}\n\n{3}".format(
				sub.name, target_doc.doctype, target_doc.name, frappe.get_traceback()
			),
		)

	if attach_statement:
		try:
			attachments.append(get_statement_attachment(
				sub.company,
				sub.customer,
				target_doc.get("posting_date") or target_doc.get("transaction_date"),
			))
		except Exception:
			# A statement failure must not hold back the invoice email
			frappe.log_error(
				title="Custom Subscription: statement failed",
				message="{0} -> {1}\n\n{2}".format(sub.name, sub.customer, frappe.get_traceback()),
			)

	# No "view online" link: customers have no portal access yet. The
	# generated document travels as the PDF attachment above.
	message = (sub.message or "") + pay_link_html(sub, target_doc) + EMAIL_SIGNATURE

	frappe.sendmail(
		recipients=recipients,
		subject=sub.subject or "{0} {1}".format(target_doc.doctype, target_doc.name),
		message=message,
		attachments=attachments,
	)


def pay_link_html(sub, target_doc):
	"""A Paystack "Pay now" button for a submitted, unpaid Sales Invoice, or ""
	when the subscription has no pay link, Paystack is not installed or set up,
	or the link fails. A pay link problem never holds back the invoice email."""
	if not sub.get("include_pay_link"):
		return ""
	if target_doc.doctype != "Sales Invoice" or target_doc.docstatus != 1:
		return ""
	if flt(target_doc.outstanding_amount) <= 0:
		return ""
	if "frappe_paystack" not in frappe.get_installed_apps():
		return ""

	try:
		from frappe_paystack.api import create_payment_link

		# Re-uses the invoice's open Payment Request, so a re-send gives the same link.
		url = create_payment_link("Sales Invoice", target_doc.name)
	except Exception:
		frappe.log_error(
			title="Custom Subscription: pay link failed",
			message="{0} -> {1}\n\n{2}".format(sub.name, target_doc.name, frappe.get_traceback()),
		)
		return ""

	if not url:
		return ""

	return (
		'<p style="margin:20px 0">'
		'<a href="{0}" style="background:#1a73e8;color:#ffffff;padding:10px 22px;'
		'border-radius:4px;text-decoration:none;font-family:Arial,Helvetica,sans-serif;'
		'font-weight:bold">Pay now</a></p>'
		'<p style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#666666">'
		'Card or Instant EFT, secured by Paystack.</p>'
	).format(escape(url))


@frappe.whitelist()
def resend_email(name, document_name, attach_statement=None):
	"""Re-send the subscription email for an existing Sales Invoice (called from
	the invoice form) to the subscription's recipients. The statement follows
	the subscription's setting unless attach_statement is passed."""
	sub = frappe.get_doc("Business Subscription", name)
	sub.check_permission("write")
	if sub.docstatus != 1:
		frappe.throw(_("Subscription {0} is not submitted").format(name))

	doctype = "Sales Invoice"
	target = frappe.get_doc(doctype, document_name)
	if target.docstatus == 2 or target.customer != sub.customer or target.company != sub.company:
		frappe.throw(_("{0} {1} is cancelled or is not for {2} / {3}").format(
			doctype, document_name, sub.customer, sub.company
		))

	recipients = [r.email for r in sub.recipients if r.email]
	if not recipients:
		frappe.throw(_("Subscription {0} has no recipients").format(name))

	send_email(sub, target, cint(sub.get("attach_statement") if attach_statement is None else attach_statement))
	return recipients
