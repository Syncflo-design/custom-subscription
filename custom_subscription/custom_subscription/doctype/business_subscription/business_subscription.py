# Copyright (c) 2026, Chipo Hameja and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class BusinessSubscription(Document):
	def before_save(self):
		next_invoice_date = None
		
		if self.frequency == "Monthly":
			next_invoice_date = frappe.utils.add_to_date(self.start_date, months=1)
		elif self.frequency == "Quarterly":
			next_invoice_date = frappe.utils.add_to_date(self.start_date, months=3)
		elif self.frequency == "Yearly":
			next_invoice_date = frappe.utils.add_to_date(self.start_date, years=1)

		self.next_invoice_date = next_invoice_date
		self.last_processed_date =  frappe.utils.getdate()

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

	new_sales_invoice.insert()

	if submitted:
		new_sales_invoice.submit()

	if doc.send_email:
		send_email(doc)

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
