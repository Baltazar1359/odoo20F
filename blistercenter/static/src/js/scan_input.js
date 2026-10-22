import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { Component, onMounted, proxy, signal, t, useListener, useProps } from "@odoo/owl";

// GS1-Trennzeichen (FNC1/GS, ASCII 29)
export const GS = String.fromCharCode(29);

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
        useListener(this.input, "keydown", (ev) => this.onKeydown(ev));
        useListener(this.input, "keyup", (ev) => this.onKeyup(ev));
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

    onKeydown(ev) {
        // Enter/Tab am Ende des Scans: Packung abschicken
        if (ev.key === "Enter" || (ev.key === "Tab" && this.input().value)) {
            this.stop(ev);
            this.submit();
            return;
        }
        // Strg+] (bei deutscher Tastatur dieselbe Taste wie „+“): GS
        if (ev.ctrlKey && (ev.code === "BracketRight" || (!ev.altKey && ev.key === "]"))) {
            this.stop(ev);
            this.insert(GS);
            return;
        }
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
        const el = this.input();
        const code = el.value;
        el.value = "";
        if (!code.trim()) {
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
