// Re-send a subscription invoice email from the invoice itself, with or
// without the customer's 12-month statement. Shown only when the customer
// has a submitted Business Subscription for this company; the email goes to
// that subscription's recipients with its subject and message.

frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        if (frm.is_new() || frm.doc.docstatus === 2) return;
        if (!frappe.model.can_read("Business Subscription")) return;

        frappe.db.get_list("Business Subscription", {
            filters: { customer: frm.doc.customer, company: frm.doc.company, docstatus: 1 },
            fields: ["name", "attach_statement"],
            order_by: "creation desc",
            limit: 20,
        }).then((subs) => {
            if (subs.length) {
                frm.add_custom_button(__("Re-send Invoice"), () => resend_invoice(frm, subs));
            }
        });
    },
});

function resend_invoice(frm, subs) {
    const statement_default = (name) => {
        const sub = subs.find((s) => s.name === name);
        return sub && sub.attach_statement ? 1 : 0;
    };
    const only = subs.length === 1 ? subs[0].name : null;

    const dialog = new frappe.ui.Dialog({
        title: __("Re-send Invoice {0}", [frm.doc.name]),
        fields: [
            {
                fieldname: "subscription",
                fieldtype: "Link",
                options: "Business Subscription",
                label: __("Subscription"),
                description: __("The email goes to this subscription's recipients."),
                reqd: 1,
                default: only,
                get_query: () => ({
                    filters: { customer: frm.doc.customer, company: frm.doc.company, docstatus: 1 },
                }),
                onchange() {
                    const name = dialog.get_value("subscription");
                    if (name) dialog.set_value("attach_statement", statement_default(name));
                },
            },
            {
                fieldname: "attach_statement",
                fieldtype: "Check",
                label: __("Attach Customer Statement"),
                default: only ? statement_default(only) : 0,
                description: __("Statement for the 12 months to the end of the month before the invoice date."),
            },
        ],
        primary_action_label: __("Send"),
        primary_action(values) {
            frappe.call({
                method: "custom_subscription.custom_subscription.doctype.business_subscription.business_subscription.resend_email",
                args: {
                    name: values.subscription,
                    document_name: frm.doc.name,
                    attach_statement: values.attach_statement ? 1 : 0,
                },
                freeze: true,
                freeze_message: __("Sending..."),
            }).then((r) => {
                dialog.hide();
                frappe.show_alert({
                    message: __("Email queued to {0}", [(r.message || []).join(", ")]),
                    indicator: "green",
                });
            });
        },
    });
    dialog.show();
}
