from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	# Lets a paid invoice find the Business Subscription (and its app and
	# account IDs) that generated it.
	create_custom_fields({
		"Sales Invoice": [{
			"fieldname": "custom_business_subscription",
			"label": "Business Subscription",
			"fieldtype": "Link",
			"options": "Business Subscription",
			"insert_after": "custom_invoice_notes",
			"read_only": 1,
			"no_copy": 1,
		}]
	}, update=True)
