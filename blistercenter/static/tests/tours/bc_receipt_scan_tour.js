import { registry } from "@web/core/registry";
import { ConnectionLostError, RPCError, rpc } from "@web/core/network/rpc";
import { cameraScanner } from "@blistercenter/js/scan_input";

// 1.6.0 (R55, R56): Packungen erfassen im Blistercenter-Wareneingang.
// Handscanner tippen den Code; das Trennzeichen GS kommt als Strg+] oder Alt+029.
const GTIN = "04150123456782"; // PZN 12345678 (siehe Python-Test)
const GS_CHAR = String.fromCharCode(29);
const realCameraScan = cameraScanner.scan;
const realRpc = rpc._rpc;
const WIZARD = "blistercenter.receipt.scan";

function isScanOnchange(params) {
    return params?.model === WIZARD && params?.method === "onchange";
}
// 1.6.4 Befund 2: nur die nächste Serverantwort auf einen Scan verzögern
function delayNextScanOnchange(ms) {
    let armed = true;
    rpc._rpc = function (url, params, settings) {
        const prom = realRpc.call(this, url, params, settings);
        if (armed && isScanOnchange(params)) {
            armed = false;
            const delayed = new Promise((resolve, reject) =>
                setTimeout(() => prom.then(resolve, reject), ms)
            );
            delayed.abort = prom.abort;
            return delayed;
        }
        return prom;
    };
}
// 1.6.4 Befund 5: Verarbeitung von Scans scheitert – Verbindung weg (Odoo verschluckt das beim
// Onchange) oder Serverfehler (update wirft)
function failScanOnchange(kind = "connection") {
    rpc._rpc = function (url, params, settings) {
        if (isScanOnchange(params)) {
            let error;
            if (kind === "connection") {
                error = new ConnectionLostError(url);
            } else {
                error = new RPCError("Serverfehler");
                error.data = { message: "Testfehler auf dem Server" };
            }
            const prom = Promise.reject(error);
            prom.abort = () => {};
            return prom;
        }
        return realRpc.call(this, url, params, settings);
    };
}
function restoreRpc() {
    rpc._rpc = realRpc;
}
function scanInput() {
    return document.querySelector(".modal .o_bc_scan_input input");
}
function code(lot, serial) {
    return GS_CHAR + "01" + GTIN + "21" + serial + GS_CHAR + "10" + lot + GS_CHAR + "17280331";
}

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
// 1.6.4 Befund 4: Alt+029 und die nächsten Zeichen ohne jede Pause (schneller Scanner)
function altGs(el) {
    key(el, "keydown", { key: "Alt", code: "AltLeft", altKey: true });
    for (const d of "0029") {
        key(el, "keydown", { key: d, code: "Numpad" + d, altKey: true });
        key(el, "keyup", { key: d, code: "Numpad" + d, altKey: true });
    }
    key(el, "keyup", { key: "Alt", code: "AltLeft" });
}
function typeChars(el, text) {
    for (const ch of text) {
        key(el, "keydown", { key: ch });
        typeText(el, ch);
    }
}
function scanAltNoPause(el, lot, serial) {
    typeChars(el, "01" + GTIN + "17280331" + "10" + lot);
    altGs(el);
    typeChars(el, "21" + serial);
    enter(el);
}
// Wie Chrome unter Windows bei Alt+29: der Browser setzt nach dem Loslassen von Alt selbst „↔“ ein
function scanAltBrowserChar(el, lot, serial) {
    typeChars(el, "01" + GTIN + "17280331" + "10" + lot);
    altGs(el);
    const ev = new InputEvent("beforeinput", {
        bubbles: true, cancelable: true, inputType: "insertText", data: "\u2194",
    });
    if (el.dispatchEvent(ev)) {
        typeText(el, "\u2194");
    }
    typeChars(el, "21" + serial);
    enter(el);
}

// Scanner tippt sehr schnell, Zeichen für Zeichen (ohne Enter am Ende)
function typeFast(el, text) {
    for (const ch of text) {
        key(el, "keydown", { key: ch });
        el.setRangeText(ch, el.value.length, el.value.length, "end");
        el.dispatchEvent(new InputEvent("input", { bubbles: true }));
    }
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
            trigger: ".modal .alert-success:contains(Charge CH-ALT ·)",
        },
        {
            content: "1.6.4 Befund 4: Alt+029 ohne Pause, Folgezeichen sofort",
            trigger: ".modal .o_bc_scan_input input",
            run: () => scanAltNoPause(scanInput(), "CH-ALT0", "S7"),
        },
        {
            content: "GS nicht verloren: Charge ohne Seriennummer gelesen",
            trigger: ".modal .alert-success:contains(Charge CH-ALT0 ·)",
        },
        {
            content: "1.6.4 Befund 4: Browser setzt für Alt+29 selbst ein Zeichen ein – nur ein GS",
            trigger: ".modal .o_bc_scan_input input",
            run: () => scanAltBrowserChar(scanInput(), "CH-ALTB", "S8"),
        },
        {
            content: "Kein doppeltes Trennzeichen",
            trigger: ".modal .alert-success:contains(Charge CH-ALTB ·)",
        },
        {
            content: "1.6.1: Scanner ohne Enter; Windows-Zeichen „↔“ für Alt+29 als Trennzeichen",
            trigger: ".modal .o_bc_scan_input input",
            run: async () => {
                const el = document.querySelector(".modal .o_bc_scan_input input");
                typeFast(el, "01" + GTIN + "21S4\u219410CH-AUTO\u219417280331");
                await new Promise((resolve) => setTimeout(resolve, 800));
            },
        },
        {
            content: "Ohne Enter abgeschickt und gezählt",
            trigger: ".modal .alert-success:contains(CH-AUTO)",
        },
        {
            content: "1.6.1: Cursor nicht im Scan-Feld – Scan geht über Odoos Barcode-Erkennung",
            trigger: ".modal .o_bc_scan_input input",
            run: async () => {
                document.activeElement.blur();
                const body = document.body;
                const send = (k) => key(body, "keydown", { key: k });
                for (const ch of "01" + GTIN + "21S5") send(ch);
                send("Control");
                send("]");
                for (const ch of "10CH-BODY") send(ch);
                send("Control");
                send("]");
                for (const ch of "17280331") send(ch);
                send("Enter");
            },
        },
        {
            content: "Scan ohne Fokus gezählt",
            trigger: ".modal .alert-success:contains(CH-BODY)",
        },
        {
            content: "1.6.2: Kamera – Packungen hintereinander, dann Kamera schließen",
            trigger: ".modal .o_bc_camera",
            run: () => {
                // Kamera ersetzen: liefert Codes (wie Odoos Kamera: GS am Anfang), dann „geschlossen“.
                // 1.6.4 Befund 3: K1 wird zweimal gelesen (Packung noch vor der Kamera) – zählt einmal.
                const codes = [
                    GS_CHAR + "01" + GTIN + "21K1" + GS_CHAR + "10CH-KAMERA" + GS_CHAR + "17280331",
                    GS_CHAR + "01" + GTIN + "21K1" + GS_CHAR + "10CH-KAMERA" + GS_CHAR + "17280331",
                    "]d2" + "01" + GTIN + "21K2" + GS_CHAR + "10CH-KAMERA" + GS_CHAR + "17280331",
                ];
                cameraScanner.scan = async () => codes.shift() || null;
                document.querySelector(".modal .o_bc_camera").click();
            },
        },
        {
            content: "Beide Kamera-Scans gezählt, Kamera-Knopf wieder frei",
            trigger: ".modal .alert-success:contains(CH-KAMERA):contains(Menge 2)",
        },
        {
            trigger: ".modal .o_bc_camera:not([disabled])",
            run: () => {
                // Echtes Kamera-Fenster (im Test ohne Kamera): öffnen und ohne Treffer schließen
                cameraScanner.scan = realCameraScan;
                document.querySelector(".modal .o_bc_camera").click();
            },
        },
        {
            content: "Odoos Kamera-Fenster ist offen",
            trigger: ".o-barcode-modal",
        },
        {
            content: "Kamera-Fenster schließen",
            trigger: ".o-barcode-modal .modal-header button[aria-label=Close]",
            run: "click",
        },
        {
            content: "Nach dem Schließen ohne Treffer ist der Kamera-Knopf wieder frei",
            trigger: "body:not(:has(.o-barcode-modal)) .modal .o_bc_camera:not([disabled])",
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

// 1.6.4 Befund 2: „Hinzufügen“ und „Übernehmen“ warten auf alle angenommenen Scans,
// auch wenn die erste Serverantwort verzögert ist und der letzte Scan ohne Enter kommt.
registry.category("web_tour.tours").add("blistercenter_receipt_scan_race", {
    steps: () => [
        {
            content: "Packungen erfassen öffnen",
            trigger: ".o_form_view button[name='action_bc_scan']",
            run: "click",
        },
        {
            trigger: ".modal .o_field_widget[name='manual_product_id'] input",
            run: "edit Testarzneimittel",
        },
        {
            trigger: ".o-autocomplete--dropdown-item:contains(Testarzneimittel)",
            run: "click",
        },
        {
            trigger: ".modal .o_field_widget[name='manual_lot'] input",
            run: "edit CH-HAND-R",
        },
        {
            trigger: ".modal .o_field_widget[name='manual_expiry'] input",
            run: "edit 05/2028",
        },
        {
            content: "Zwei Scans (erste Antwort verzögert), sofort „Hinzufügen“",
            trigger: ".modal .o_bc_scan_input input",
            run: () => {
                delayNextScanOnchange(800);
                const el = scanInput();
                scanCtrl(el, "CH-RACE-A", "R1");
                scanCtrl(el, "CH-RACE-A", "R2");
                document.querySelector(".modal button[name='action_add_manual']").click();
            },
        },
        {
            content: "Beide Scans und die Handeingabe in der Liste",
            trigger: ".modal .o_field_widget[name='line_ids'] .o_data_row:contains(CH-HAND-R)",
        },
        {
            trigger: ".modal .o_field_widget[name='line_ids'] .o_data_row:contains(CH-RACE-A):contains(2)",
        },
        {
            content: "Drei schnelle Scans, einer ohne Enter, erste Antwort verzögert, sofort „Übernehmen“",
            trigger: ".modal .o_bc_scan_input input:not(:disabled)",
            run: () => {
                delayNextScanOnchange(800);
                const el = scanInput();
                scanCtrl(el, "CH-RACE-B", "R3");
                scanCtrl(el, "CH-RACE-B", "R4");
                scanCtrl(el, "CH-RACE-B", "R5");
                typeFast(el, "01" + GTIN + "21R6\u219410CH-RACE-C\u219417280331");
                document.querySelector(".modal button[name='action_apply']").click();
            },
        },
        {
            content: "Fenster geschlossen",
            trigger: "body:not(:has(.modal)) .o_form_view button[name='action_bc_scan']",
            run: () => restoreRpc(),
        },
    ],
});

// 1.6.4 Befund 5: Verarbeitungsfehler – Scan bleibt erhalten, rote Meldung, Kamera hält an,
// „Übernehmen“ lehnt ab, „Wiederholen“ zählt ohne Doppelzählung.
registry.category("web_tour.tours").add("blistercenter_receipt_scan_failure", {
    steps: () => [
        {
            content: "Packungen erfassen öffnen",
            trigger: ".o_form_view button[name='action_bc_scan']",
            run: "click",
        },
        {
            content: "Erste Packung normal",
            trigger: ".modal .o_bc_scan_input input",
            run: () => scanCtrl(scanInput(), "CH-FAIL-A", "F1"),
        },
        {
            trigger: ".modal .alert-success:contains(Charge CH-FAIL-A ·):contains(Menge 1)",
        },
        {
            content: "Verbindung weg: zweite Packung scheitert",
            trigger: ".modal .o_bc_scan_input input",
            run: () => {
                failScanOnchange();
                scanCtrl(scanInput(), "CH-FAIL-A", "F2");
            },
        },
        {
            content: "Scan aufbewahrt, rote Meldung statt der grünen",
            trigger: ".modal .o_bc_scan_failed[data-count='1']:contains(Keine Verbindung)",
        },
        {
            trigger: ".modal:not(:has(.alert-success)) .alert-danger:contains(nicht gezählt)",
        },
        {
            content: "Kamera bei Verarbeitungsfehler: hält nach dem ersten Code an",
            trigger: ".modal .o_bc_camera:not([disabled])",
            run: () => {
                failScanOnchange("server");
                window.__bcCameraCalls = 0;
                const codes = [code("CH-FAIL-A", "F3"), code("CH-FAIL-A", "F4")];
                cameraScanner.scan = async () => {
                    window.__bcCameraCalls++;
                    return codes.shift() || null;
                };
                document.querySelector(".modal .o_bc_camera").click();
            },
        },
        {
            content: "Zwei Scans offen, Serverfehler angezeigt, Kamera wieder frei",
            trigger: ".modal .o_bc_scan_failed[data-count='2']:contains(Testfehler auf dem Server)",
        },
        {
            trigger: ".modal .o_bc_camera:not([disabled])",
            run: () => {
                cameraScanner.scan = realCameraScan;
                if (window.__bcCameraCalls !== 1) {
                    throw new Error("Kamera lief nach dem Fehler weiter: " + window.__bcCameraCalls);
                }
            },
        },
        {
            content: "„Übernehmen“ mit ungezählten Scans",
            trigger: ".modal button[name='action_apply']",
            run: "click",
        },
        {
            content: "Abgelehnt mit verständlicher Meldung",
            trigger: ".o_error_dialog:contains(noch nicht gezählt):contains(Wiederholen) .o-default-button",
            run: "click",
        },
        {
            content: "Verbindung wieder da: Wiederholen",
            trigger: ".modal .o_bc_scan_retry",
            run: () => {
                restoreRpc();
                document.querySelector(".modal .o_bc_scan_retry").click();
            },
        },
        {
            content: "Beide aufbewahrten Scans gezählt, Fehlerbox weg",
            trigger: ".modal:not(:has(.o_bc_scan_failed)):not(:has(.o_error_dialog)) .alert-success:contains(Charge CH-FAIL-A ·):contains(Menge 3)",
        },
        {
            content: "Dieselbe Packung noch einmal: nicht doppelt gezählt",
            trigger: ".modal .o_bc_scan_input input",
            run: () => scanCtrl(scanInput(), "CH-FAIL-A", "F2"),
        },
        {
            trigger: ".modal .alert-warning:contains(schon gezählt):contains(Menge 3)",
        },
        {
            content: "Übernehmen",
            trigger: ".modal button[name='action_apply']",
            run: "click",
        },
        {
            content: "Fenster geschlossen",
            trigger: "body:not(:has(.modal)) .o_form_view button[name='action_bc_scan']",
        },
    ],
});
