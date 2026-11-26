// Create > Send Statement on the Customer form: emails the customer's
// 12-month statement on its own, with no invoice. Recipients are filled in
// from the customer's subscription for the chosen company, else from the
// customer's own email, and can be edited before sending.

frappe.ui.form.on("Customer", {
    refresh(frm) {
        if (frm.is_new()) return;
        if (!frappe.user.has_role(["Accounts User", "Accounts Manager", "System Manager"])) return;
        frm.add_custom_button(__("Send Statement"), () => send_statement(frm), __("Create"));
    },
});

function send_statement(frm) {
    const can_read_subs = frappe.model.can_read("Business Subscription");

    const latest_subscription = (company) => {
        if (!can_read_subs) return Promise.resolve(null);
        const filters = { customer: frm.doc.name, docstatus: 1 };
        if (company) filters.company = company;
        return frappe.db
            .get_list("Business Subscription", {
                filters,
                fields: ["name", "company"],
                order_by: "creation desc",
                limit: 1,
            })
            .then((rows) => rows[0] || null);
    };

    const fill_recipients = async () => {
        const company = dialog.get_value("company");
        if (!company) return;
        let emails = [];
        const sub = await latest_subscription(company);
        if (sub) {
            const doc = await frappe.db.get_doc("Business Subscription", sub.name);
            emails = (doc.recipients || []).map((r) => r.email).filter(Boolean);
        }
        if (!emails.length && frm.doc.email_id) emails = [frm.doc.email_id];
        dialog.set_value("recipients", emails.join(", "));
    };

    const dialog = new frappe.ui.Dialog({
        title: __("Send Statement to {0}", [frm.doc.customer_name || frm.doc.name]),
        fields: [
            {
                fieldname: "company",
                fieldtype: "Link",
                options: "Company",
                label: __("Company"),
                reqd: 1,
                onchange: fill_recipients,
            },
            {
                fieldname: "recipients",
                fieldtype: "Small Text",
                label: __("Send To"),
                reqd: 1,
                description: __("Separate several email addresses with commas."),
            },
            {
                fieldname: "period_note",
                fieldtype: "HTML",
                options: `<p class="text-muted small">${__(
                    "The statement covers the last 12 full months, up to the end of last month."
                )}</p>`,
            },
        ],
        primary_action_label: __("Send"),
        primary_action(values) {
            frappe.call({
                method: "custom_subscription.custom_subscription.doctype.business_subscription.business_subscription.send_statement",
                args: { customer: frm.doc.name, company: values.company, recipients: values.recipients },
                freeze: true,
                freeze_message: __("Sending..."),
            }).then((r) => {
                dialog.hide();
                frappe.show_alert({
                    message: __("Statement queued to {0}", [(r.message || []).join(", ")]),
                    indicator: "green",
                });
            });
        },
    });

    dialog.show();

    // Default to the company of the customer's latest subscription, else the user's default.
    latest_subscription(null).then((sub) => {
        dialog.set_value("company", (sub && sub.company) || frappe.defaults.get_user_default("Company") || "");
    });
}
