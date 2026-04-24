// Copyright (c) 2026, Chipo Hameja and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Business Subscription", {
// 	refresh(frm) {

// 	},
// });

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
