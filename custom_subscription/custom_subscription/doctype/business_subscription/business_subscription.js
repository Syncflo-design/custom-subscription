// Copyright (c) 2026, Chipo Hameja and contributors
// For license information, please see license.txt

frappe.ui.form.on("Business Subscription", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Re-send Email"), () => resend_email(frm));
        }
    },
});

// Re-send the subscription email for an already generated document, with
// or without last month's statement.
function resend_email(frm) {
    const doctype = frm.doc.document_type === "Sales Order" ? "Sales Order" : "Sales Invoice";
    const filters = { customer: frm.doc.customer, company: frm.doc.company, docstatus: ["!=", 2] };

    const dialog = new frappe.ui.Dialog({
        title: __("Re-send Email"),
        fields: [
            {
                fieldname: "document_name",
                fieldtype: "Link",
                options: doctype,
                label: __(doctype),
                reqd: 1,
                get_query: () => ({ filters }),
            },
            {
                fieldname: "attach_statement",
                fieldtype: "Check",
                label: __("Attach Customer Statement"),
                default: frm.doc.attach_statement ? 1 : 0,
                description: __("Statement for the 12 months to the end of the month before the document's date."),
            },
        ],
        primary_action_label: __("Send"),
        primary_action(values) {
            frappe.call({
                method: "custom_subscription.custom_subscription.doctype.business_subscription.business_subscription.resend_email",
                args: {
                    name: frm.doc.name,
                    document_name: values.document_name,
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

    // Default to the customer's most recent document.
    frappe.db.get_list(doctype, { filters, fields: ["name"], order_by: "creation desc", limit: 1 })
        .then((rows) => {
            if (rows.length) dialog.set_value("document_name", rows[0].name);
        });

    dialog.show();
}

frappe.ui.form.on("Business Subscription Item", {
    quantity(frm, cdt, cdn) {
        calculate_total(frm, cdt, cdn);
    },
    rate(frm, cdt, cdn) {
        calculate_total(frm, cdt, cdn);
    },
    items_remove(frm, cdt, cdn) {
        calculate_total(frm, cdt, cdn, true);
    }
})

function calculate_total(frm, cdt, cdn, remove=false) {
    let total_price = 0.00, total_qty = 0.00;

    (frm.doc.items || []).forEach(row => {
        let item_total_price = flt(row.quantity * row.rate);

        total_price += item_total_price;
        total_qty += flt(row.quantity);
        
        if (!remove) {
            cur_row = locals[cdt][cdn];
            cur_row.amount = item_total_price;
        }
    });

    frm.set_value({
        "total_quantity": total_qty,
        "total": total_price
    })

    frm.refresh_field("items")
    frm.refresh_field("total_quantity")
}
