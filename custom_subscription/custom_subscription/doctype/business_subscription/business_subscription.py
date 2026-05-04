# Copyright (c) 2026, Chipo Hameja and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, format_date, add_months, add_days, nowdate


FREQUENCY_MONTHS = {"Monthly": 1, "Quarterly": 3, "Annually": 12}
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
		self.last_processed_date = self.start_date
		self.next_invoice_date = self.start_date

	def set_next_invoice_date(self):
		next_invoice_date = self.last_processed_date
		
		if self.frequency == "Monthly":
			next_invoice_date = frappe.utils.add_to_date(next_invoice_date, months=1)
		elif self.frequency == "Quarterly":
			next_invoice_date = frappe.utils.add_to_date(next_invoice_date, months=3)
		elif self.frequency == "Annually":
			next_invoice_date = frappe.utils.add_to_date(next_invoice_date, years=1)

		self.next_invoice_date = next_invoice_date
		self.last_processed_date = frappe.utils.getdate()

		frappe.db.set_value("Business Subscription", self.name, {
			"next_invoice_date": next_invoice_date,
			"last_processed_date": frappe.utils.getdate()
		})

	def create_doc(self):
		if self.document_type == "Sales Order":
			create_sales_order(self)
		elif self.document_type == "Sales Invoice (Draft)":
			create_sales_invoice(self)
		else:
			create_sales_invoice(self, True)

# Get all submitted Business Subscription records whose next invoice date is today
# For every invoice, check the document type and create the necessary record in the required docstatus
# Update the next invoice date of the business subscription record based on the frequency

def create_sales_invoice(doc, submitted=False):
	new_sales_invoice = frappe.get_doc({
		"doctype": "Sales Invoice",
		"company": doc.company,
		"customer": doc.customer,
		"posting_date": frappe.utils.getdate(),
		"due_date": frappe.utils.getdate(),
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
		send_email(doc)
	
	doc.set_next_invoice_date()

def create_sales_order(doc):
	new_sales_order = frappe.get_doc({
		"doctype": "Sales Order",
		"company": doc.company,
		"customer": doc.customer,
		"transaction_date": frappe.utils.getdate(),
		"delivery_date": frappe.utils.getdate(),
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
		send_email(doc)
	
	doc.set_next_invoice_date()

def send_email(doc):
	if len(doc.recipients) > 0:
		recipients = [recipient.email for recipient in doc.recipients]
		
		frappe.sendmail(
			recipients=recipients,
			subject=doc.subject,
			message=doc.message,
			attachments=[
				frappe.attach_print(
					doc.doctype,
					doc.name,
					print_format=doc.print_format
				)
			]
		)
