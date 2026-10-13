import { patch } from "@web/core/utils/patch";
import { BarcodePlugin } from "@barcodes/barcode_plugin";

// Handscanner senden das GS1-Trennzeichen (FNC1/GS) je nach Einstellung als
// Alt+029 (Ziffernblock) oder Strg+]. Ohne Umwandlung gehen Seriennummer und
// Charge ineinander über. Hier wird beides zu ASCII 29, bevor Odoo "Alt" und
// "Control" aus dem Scan entfernt.
const GS = String.fromCharCode(29);

patch(BarcodePlugin.prototype, {
    cleanBarcode(barcode) {
        barcode = barcode.replace(/Alt0?029/g, GS).replace(/Control\]/g, GS);
        return super.cleanBarcode(barcode);
    },
});
