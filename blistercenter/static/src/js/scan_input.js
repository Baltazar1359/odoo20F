import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useBus } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { BarcodePlugin } from "@barcodes/barcode_plugin";

import {
    Component, onMounted, onWillUnmount, proxy, signal, t, useListener, usePlugin, useProps,
} from "@odoo/owl";

// GS1-Trennzeichen (FNC1/GS, ASCII 29)
export const GS = String.fromCharCode(29);
// Windows setzt für Alt+29 das Codepage-437-Zeichen „↔“ ein (1.6.1)
const GS_LOOKALIKE = /[\u2194\u241d]/;
const GS_LOOKALIKE_ALL = /[\u2194\u241d]/g;
// Handscanner tippen schneller als ein Mensch: Abstand zwischen zwei Zeichen höchstens so viele ms
const SCANNER_KEY_GAP_MS = 60;
// Kein Enter nach dem Code: so lange Ruhe, dann gilt der Scan als fertig
const SCANNER_IDLE_MS = 350;
const SCANNER_MIN_CHARS = 14;

/**
 * Scan-Feld für den Blistercenter-Wareneingang (1.6.0, R56).
 *
 * Handscanner tippen den DataMatrix-Code wie eine Tastatur. Das Trennzeichen zwischen Charge und
 * Seriennummer (GS) senden sie je nach Einstellung als Strg+] oder Alt+029. In einem normalen
 * Eingabefeld geht es dabei verloren; dann ist die Charge nicht sicher lesbar. Dieses Feld setzt
 * für beide Tastenfolgen das GS-Zeichen ein. Mit Enter (oder Tab) wird die Packung abgeschickt,
 * das Feld ist sofort für die nächste frei. Die Packungen werden der Reihe nach verarbeitet;
 * keine geht verloren, auch wenn schnell hintereinander gescannt wird.
 */
export class BcScanInput extends Component {
    static template = "blistercenter.BcScanInput";
    props = useProps({ ...standardFieldProps, placeholder: t.string().optional() });
    input = signal.ref();

    setup() {
        this.state = proxy({ pending: 0, last: "" });
        this.queue = Promise.resolve();
        this.altDigits = null;
        this.burst = 0;
        this.lastKey = 0;
        this.idleTimer = null;
        useListener(this.input, "keydown", (ev) => this.onKeydown(ev));
        useListener(this.input, "keyup", (ev) => this.onKeyup(ev));
        useListener(this.input, "input", () => this.onInput());
        // 1.6.1: Scan, während der Cursor nicht in einem Eingabefeld steht (z. B. nach einem Klick
        // in die Liste). Odoo erkennt ihn als Barcode; er wird hier wie ein Scan im Feld behandelt.
        const barcode = usePlugin(BarcodePlugin);
        useBus(barcode.bus, "barcode_scanned", (ev) => {
            if (!this.props.readonly && this.input()?.isConnected) {
                this.enqueue(ev.detail.barcode);
            }
        });
        onWillUnmount(() => clearTimeout(this.idleTimer));
        onMounted(() => this.input()?.focus());
    }

    insert(text) {
        const el = this.input();
        const start = el.selectionStart ?? el.value.length;
        const end = el.selectionEnd ?? el.value.length;
        el.setRangeText(text, start, end, "end");
    }

    stop(ev) {
        ev.preventDefault();
        ev.stopPropagation();
    }

    onInput() {
        const el = this.input();
        if (GS_LOOKALIKE.test(el.value)) {
            const pos = el.selectionStart;
            el.value = el.value.replace(GS_LOOKALIKE_ALL, GS);
            el.setSelectionRange(pos, pos);
        }
    }

    /** Scanner ohne Enter am Ende: nach einer schnellen Zeichenfolge und kurzer Ruhe abschicken. */
    trackBurst(ev) {
        if (ev.key.length !== 1) {
            return;
        }
        const now = Date.now();
        this.burst = now - this.lastKey <= SCANNER_KEY_GAP_MS ? this.burst + 1 : 1;
        this.lastKey = now;
        clearTimeout(this.idleTimer);
        this.idleTimer = setTimeout(() => {
            const el = this.input();
            if (el && this.burst >= SCANNER_MIN_CHARS && el.value.length >= SCANNER_MIN_CHARS) {
                this.submit();
            }
        }, SCANNER_IDLE_MS);
    }

    onKeydown(ev) {
        if (!ev.key) {
            return;
        }
        // Enter/Tab am Ende des Scans: Packung abschicken
        if (ev.key === "Enter" || (ev.key === "Tab" && this.input().value)) {
            clearTimeout(this.idleTimer);
            this.stop(ev);
            this.submit();
            return;
        }
        // Strg+] (bei deutscher Tastatur dieselbe Taste wie „+“, oder Strg+AltGr+9): GS
        if (ev.ctrlKey && (ev.code === "BracketRight" || ev.key === "]")) {
            this.stop(ev);
            this.insert(GS);
            this.trackBurst({ key: GS });
            return;
        }
        this.trackBurst(ev);
        // Alt + Ziffernblock (Alt+029 bzw. Alt+0029): Ziffern sammeln, beim Loslassen von Alt auswerten
        if (ev.key === "Alt") {
            this.altDigits = "";
            ev.stopPropagation(); // keine Odoo-Tastenkürzel-Anzeige während des Scans
            return;
        }
        if (ev.altKey && this.altDigits !== null && /^Numpad\d$/.test(ev.code)) {
            this.stop(ev);
            this.altDigits += ev.code.slice(-1);
        }
    }

    onKeyup(ev) {
        if (ev.key !== "Alt" || this.altDigits === null) {
            return;
        }
        ev.stopPropagation();
        const digits = this.altDigits;
        this.altDigits = null;
        if (!/^0*29$/.test(digits)) {
            return;
        }
        // Manche Browser setzen das Zeichen selbst ein; dann nicht doppelt
        const before = this.input().value;
        setTimeout(() => {
            const el = this.input();
            if (el && el.value === before) {
                this.insert(GS);
            }
        }, 0);
    }

    submit() {
        clearTimeout(this.idleTimer);
        this.burst = 0;
        const el = this.input();
        const code = el.value;
        el.value = "";
        this.enqueue(code);
    }

    enqueue(code) {
        if (!code || !code.trim()) {
            return;
        }
        this.state.pending++;
        this.queue = this.queue
            .then(() => this.props.record.update({ [this.props.name]: code }))
            .catch(() => {})
            .finally(() => {
                this.state.pending--;
                this.input()?.focus();
            });
    }

    get placeholder() {
        return this.props.placeholder || _t("Hier scannen …");
    }
}

export const bcScanInput = {
    component: BcScanInput,
    displayName: _t("Scan-Feld (Handscanner)"),
    supportedTypes: ["char"],
    extractProps: ({ placeholder }) => ({ placeholder }),
};

registry.category("fields").add("bc_scan_input", bcScanInput);
