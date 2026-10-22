import { registry } from "@web/core/registry";

// 1.6.0 (R55, R56): Packungen erfassen im Blistercenter-Wareneingang.
// Handscanner tippen den Code; das Trennzeichen GS kommt als Strg+] oder Alt+029.
const GTIN = "04150123456782"; // PZN 12345678 (siehe Python-Test)

function key(el, type, init) {
    el.dispatchEvent(new KeyboardEvent(type, { bubbles: true, cancelable: true, ...init }));
}
function typeText(el, text) {
    el.setRangeText(text, el.value.length, el.value.length, "end");
    el.dispatchEvent(new InputEvent("input", { bubbles: true }));
}
function enter(el) {
    key(el, "keydown", { key: "Enter", code: "Enter" });
}
function scanCtrl(el, lot, serial) {
    typeText(el, "01" + GTIN + "17280331" + "10" + lot);
    // deutsche Tastatur: Strg + Taste rechts neben Ü („+“) = Strg+]
    key(el, "keydown", { key: "+", code: "BracketRight", ctrlKey: true });
    typeText(el, "21" + serial);
    enter(el);
}
async function scanAlt(el, lot, serial) {
    typeText(el, "01" + GTIN + "17280331" + "10" + lot);
    key(el, "keydown", { key: "Alt", code: "AltLeft", altKey: true });
    for (const d of "0029") {
        key(el, "keydown", { key: d, code: "Numpad" + d, altKey: true });
        key(el, "keyup", { key: d, code: "Numpad" + d, altKey: true });
    }
    key(el, "keyup", { key: "Alt", code: "AltLeft" });
    await new Promise((resolve) => setTimeout(resolve, 30));
    typeText(el, "21" + serial);
    enter(el);
}

registry.category("web_tour.tours").add("blistercenter_receipt_scan", {
    steps: () => [
        {
            content: "Packungen erfassen öffnen",
            trigger: ".o_form_view button[name='action_bc_scan']",
            run: "click",
        },
        {
            content: "Drei Packungen schnell hintereinander scannen (Strg+], Strg+], Alt+029)",
            trigger: ".modal .o_bc_scan_input input",
            run: async () => {
                const el = document.querySelector(".modal .o_bc_scan_input input");
                scanCtrl(el, "CH-STRG", "S1");
                scanCtrl(el, "CH-STRG", "S2");
                await scanAlt(el, "CH-ALT", "S3");
            },
        },
        {
            content: "Letzter Scan verarbeitet",
            trigger: ".modal .alert-success:contains(CH-ALT)",
        },
        {
            content: "Charge mit Strg+] zweimal gezählt",
            trigger: ".modal .o_field_widget[name='line_ids'] .o_data_row:contains(CH-STRG)",
        },
        {
            content: "Handeingabe: Artikel",
            trigger: ".modal .o_field_widget[name='manual_product_id'] input",
            run: "edit Testarzneimittel",
        },
        {
            trigger: ".o-autocomplete--dropdown-item:contains(Testarzneimittel)",
            run: "click",
        },
        {
            content: "Handeingabe: Charge",
            trigger: ".modal .o_field_widget[name='manual_lot'] input",
            run: "edit CH-HAND",
        },
        {
            content: "Handeingabe: Verfall",
            trigger: ".modal .o_field_widget[name='manual_expiry'] input",
            run: "edit 05/2028",
        },
        {
            content: "Handeingabe: Anzahl",
            trigger: ".modal .o_field_widget[name='manual_qty'] input",
            run: "edit 2",
        },
        {
            content: "Hinzufügen",
            trigger: ".modal button[name='action_add_manual']",
            run: "click",
        },
        {
            content: "Hand-Charge in der Liste, Übersicht vollständig",
            trigger: ".modal .o_field_widget[name='line_ids'] .o_data_row:contains(CH-HAND)",
        },
        {
            trigger: ".modal .o_field_widget[name='overview']:contains(vollständig)",
        },
        {
            content: "In den Wareneingang übernehmen",
            trigger: ".modal button[name='action_apply']",
            run: "click",
        },
        {
            content: "Fenster geschlossen",
            trigger: "body:not(:has(.modal)) .o_form_view button[name='action_bc_scan']",
        },
    ],
});
