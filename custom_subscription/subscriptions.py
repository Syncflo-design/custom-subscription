import frappe
from frappe import _
from frappe.utils import getdate, add_days, cint


@frappe.whitelist()
def run_now(name=None):
	"""Manual, on-demand trigger that mirrors the daily scheduler.

	Lets an operator (or an API client) generate subscription documents
	immediately instead of waiting for the 07:00 cron — useful for testing
	and catch-ups. Guarded to System Manager / Accounts Manager so it is not
	callable by ordinary users despite being whitelisted.

	With ``name`` set, processes just that (submitted) Business Subscription;
	otherwise runs the full due-sweep, identical to ``validate_subscriptions``.
	"""
	if not ({"System Manager", "Accounts Manager"} & set(frappe.get_roles())):
		frappe.throw(_("Not permitted to run subscriptions"), frappe.PermissionError)

	if name:
		doc = frappe.get_doc("Business Subscription", name)
		if doc.docstatus != 1:
			frappe.throw(_("Subscription {0} is not submitted").format(name))
		doc.create_doc()
		frappe.db.commit()
		return {"processed": [name]}

	validate_subscriptions()
	return {"processed": "all due (check the Error Log for any per-record failures)"}


def validate_subscriptions():
	"""Daily scheduler entry point.

	Generates each *submitted* Business Subscription's document once the run
	date reaches ``next_invoice_date - advance_days`` — i.e. the invoice is
	produced (and emailed) ``advance_days`` days ahead of its validity date,
	default 7. The document's period/validity still uses next_invoice_date, not
	the run date.

	Using ``<=`` (a missed scheduler day self-heals on the next run) and a
	per-record try/except + commit (one bad record can't abort the whole run).
	"""
	today = getdate()
	subs = frappe.get_all(
		"Business Subscription",
		filters={"docstatus": 1},
		fields=["name", "next_invoice_date", "advance_days"],
	)

	for sub in subs:
		if not sub.next_invoice_date:
			continue
		# Legacy rows created before advance_days existed are NULL -> fall back
		# to the 7-day default. An explicit 0 means "no lead time".
		advance = cint(sub.advance_days) if sub.advance_days is not None else 7
		if add_days(getdate(sub.next_invoice_date), -advance) > today:
			continue
		try:
			frappe.get_doc("Business Subscription", sub.name).create_doc()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Custom Subscription: failed to process",
				message="Subscription {0}\n\n{1}".format(sub.name, frappe.get_traceback()),
			)
