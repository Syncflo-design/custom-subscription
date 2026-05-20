import frappe
from frappe.utils import getdate


def validate_subscriptions():
	"""Daily scheduler entry point.

	Picks up every *submitted* Business Subscription that is due
	(next_invoice_date on or before today) and generates its document.

	Using ``<=`` (instead of an exact ``== today`` match) means a missed
	scheduler day self-heals on the next run instead of skipping the
	subscription forever. Each subscription is processed in its own
	try/except + commit so one bad record can't abort the whole run.
	"""
	today = getdate()
	names = frappe.get_all(
		"Business Subscription",
		filters={"docstatus": 1, "next_invoice_date": ["<=", today]},
		pluck="name",
	)

	for name in names:
		try:
			frappe.get_doc("Business Subscription", name).create_doc()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Custom Subscription: failed to process",
				message="Subscription {0}\n\n{1}".format(name, frappe.get_traceback()),
			)
