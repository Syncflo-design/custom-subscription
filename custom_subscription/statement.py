"""Customer statement of account, attached to subscription invoice emails.

Built straight from GL Entry instead of the General Ledger report: that
report's bottom "Total" row also adds up its own Opening / Total / Closing
rows, so a statement printed from it shows inflated totals. Here the closing
balance is opening + debits - credits by construction.
"""

import re

import frappe
from erpnext.accounts.party import get_party_account_currency
from frappe import _
from frappe.utils import add_months, flt, get_first_day, get_last_day, getdate, nowdate
from frappe.utils.pdf import get_pdf
from frappe.www.printview import get_letter_head, get_print_style

TEMPLATE = "custom_subscription/templates/customer_statement.html"
STATEMENT_MONTHS = 12


def statement_period(ref_date=None):
	"""The STATEMENT_MONTHS full calendar months ending with the month before
	ref_date (default today), e.g. 24 Nov 2026 -> 1 Nov 2025 to 31 Oct 2026."""
	ref = getdate(ref_date or nowdate())
	return get_first_day(add_months(ref, -STATEMENT_MONTHS)), get_last_day(add_months(ref, -1))


def get_statement_data(company, customer, from_date, to_date):
	"""Opening balance, per-voucher lines with a running balance, and totals.

	Amounts are in the party account currency (ERPNext keeps one currency per
	customer per company). Opening follows the General Ledger convention:
	everything before from_date plus any is_opening entries.
	"""
	params = {
		"company": company,
		"customer": customer,
		"from_date": getdate(from_date),
		"to_date": getdate(to_date),
	}
	base = (
		"from `tabGL Entry` where company = %(company)s and party_type = 'Customer'"
		" and party = %(customer)s and is_cancelled = 0"
	)

	opening = flt(
		frappe.db.sql(
			"select sum(debit_in_account_currency - credit_in_account_currency) "
			+ base
			+ " and posting_date <= %(to_date)s"
			" and (posting_date < %(from_date)s or is_opening = 'Yes')",
			params,
		)[0][0]
	)

	rows = frappe.db.sql(
		"select posting_date, voucher_type, voucher_no,"
		" sum(debit_in_account_currency) as debit, sum(credit_in_account_currency) as credit "
		+ base
		+ " and posting_date between %(from_date)s and %(to_date)s and is_opening != 'Yes'"
		" group by posting_date, voucher_type, voucher_no"
		" order by posting_date, min(creation)",
		params,
		as_dict=True,
	)

	balance = opening
	total_debit = total_credit = 0
	for row in rows:
		row.debit, row.credit = flt(row.debit), flt(row.credit)
		balance += row.debit - row.credit
		total_debit += row.debit
		total_credit += row.credit
		row.balance = balance
		row.label = _line_label(row)

	return frappe._dict(
		opening=opening,
		rows=rows,
		total_debit=total_debit,
		total_credit=total_credit,
		closing=opening + total_debit - total_credit,
		currency=get_party_account_currency("Customer", customer, company)
		or frappe.get_cached_value("Company", company, "default_currency"),
	)


def _line_label(row):
	if row.voucher_type == "Sales Invoice":
		return _("Credit Note") if row.credit > row.debit else _("Invoice")
	if row.voucher_type == "Payment Entry":
		return _("Refund") if row.debit > row.credit else _("Payment")
	return _(row.voucher_type)


def get_statement_html(company, customer, from_date, to_date):
	data = get_statement_data(company, customer, from_date, to_date)
	cust = frappe.db.get_value("Customer", customer, ["customer_name", "primary_address"], as_dict=True)

	# Same letter head the invoice uses: the company's default, else the site default.
	letter_head = get_letter_head(
		frappe._dict(letter_head=frappe.get_cached_value("Company", company, "default_letter_head")), 0
	)
	letter_head_html = letter_head.get("content") if letter_head else None
	if letter_head_html:
		letter_head_html = frappe.render_template(letter_head_html, {"doc": frappe._dict(company=company)})

	return frappe.render_template(
		TEMPLATE,
		{
			"company": company,
			"customer_name": cust.customer_name or customer,
			"customer_address": cust.primary_address or "",
			"from_date": getdate(from_date),
			"to_date": getdate(to_date),
			"letter_head": letter_head_html,
			"print_css": get_print_style(),
			**data,
		},
	)


def get_statement_attachment(company, customer, ref_date=None):
	"""Last-12-months statement (see statement_period) as a sendmail attachment dict."""
	from_date, to_date = statement_period(ref_date)
	html = get_statement_html(company, customer, from_date, to_date)
	safe_name = re.sub(r'[\\/:*?"<>|]+', "-", customer).strip()
	return {
		"fname": "Statement - {0} - {1}.pdf".format(safe_name, to_date.strftime("%Y-%m")),
		"fcontent": get_pdf(html, {"orientation": "Portrait"}),
	}
