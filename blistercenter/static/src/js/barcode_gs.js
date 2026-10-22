import { patch } from "@web/core/utils/patch";
import { BarcodePlugin } from "@barcodes/barcode_plugin";

// 1.6.1 (wie plcs_greenrx/static/src/receipt_app/barcode_patch.js): Handscanner senden das
// GS1-Trennzeichen als Alt+029 oder Strg+]. Odoo entfernt „Alt“ und „Control“ aus dem Scan;
// vorher hier in ASCII 29 umwandeln, damit Charge und Seriennummer getrennt bleiben.
// Doppelte Anwendung (GreenRX installiert) ist unschädlich.
const GS = String.fromCharCode(29);

patch(BarcodePlugin.prototype, {
    cleanBarcode(barcode) {
        barcode = barcode
            .replace(/Alt0{0,2}29/g, GS)
            .replace(/Control(\]|\+)/g, GS)
            .replace(/↔/g, GS);
        return super.cleanBarcode(barcode);
    },
});
