import frappe

def validate_subscriptions():
    business_subscriptions = frappe.get_all("Business Subscription", filters={"docstatus": 1})

    for business_subscription in business_subscriptions:
        business_subscription_doc = frappe.get_doc("Business Subscription", business_subscription.name)
        business_subscription_doc.create_doc()
