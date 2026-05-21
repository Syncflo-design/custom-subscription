# Copyright (c) 2026, Chipo Hameja and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, format_date, add_months, add_days, nowdate


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
	new_sales_invoice = frappe.get_doc({
		"doctype": "Sales Invoice",
		"company": doc.company,
		"customer": doc.customer,
		"posting_date": getdate(),
		"due_date": getdate(),
	})

	for item in doc.items:
		new_sales_invoice.append("items", {
			"item_code": item.item_code,
			"qty": item.quantity,
			"rate": item.rate
		})

	new_sales_invoice.custom_invoice_notes = build_subscription_note(doc, new_sales_invoice.posting_date)
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

def send_email(sub, target_doc):
	"""Notify the recipients that a document was generated, attaching the
	*generated document* (not the subscription itself, which was the bug)."""
	if not sub.recipients:
		return

	recipients = [r.email for r in sub.recipients if r.email]
	if not recipients:
		return

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

	# Add a direct link so recipients can open the generated document.
	link = frappe.utils.get_url_to_form(target_doc.doctype, target_doc.name)
	message = (sub.message or "") + (
		'<p style="margin-top:12px">'
		'<a href="{0}">Open {1} {2} &rarr;</a></p>'
	).format(link, target_doc.doctype, target_doc.name)

	frappe.sendmail(
		recipients=recipients,
		subject=sub.subject or "{0} {1}".format(target_doc.doctype, target_doc.name),
		message=message,
		attachments=attachments,
	)
