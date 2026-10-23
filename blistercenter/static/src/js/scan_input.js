import { _t } from "@web/core/l10n/translation";
import { ConnectionLostError } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { useBus } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { BarcodePlugin } from "@barcodes/barcode_plugin";
import { BarcodeDialog } from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { isMobileOS } from "@web/core/browser/feature_detection";

import {
    Component, onMounted, onWillUnmount, proxy, signal, t, useListener, usePlugin, useProps,
} from "@odoo/owl";

// GS1-Trennzeichen (FNC1/GS, ASCII 29)
export const GS = String.fromCharCode(29);
// Windows setzt für Alt+29 das Codepage-437-Zeichen „↔“ ein (1.6.1)
const GS_LOOKALIKE = /[\u2194\u241d]/;
const GS_LOOKALIKE_ALL = /[\u2194\u241d]/g;
// Zeichen, das ein Browser nach Alt+029 bzw. Alt+29 selbst einsetzen kann (1.6.4, Befund 4)
const ALT_GS_CHAR = /^[\x1d\u2194\u241d]$/;
// Handscanner tippen schneller als ein Mensch: Abstand zwischen zwei Zeichen höchstens so viele ms
const SCANNER_KEY_GAP_MS = 60;
// Kein Enter nach dem Code: so lange Ruhe, dann gilt der Scan als fertig
const SCANNER_IDLE_MS = 350;
const SCANNER_MIN_CHARS = 14;

/**
 * Kamera als Scanner (1.6.2). Öffnet Odoos Kamera-Fenster und liefert den gelesenen Code,
 * oder null, wenn das Fenster ohne Code geschlossen wurde (Odoos scanBarcode bleibt dann hängen).
 * Als Objekt exportiert, damit der Browser-Test die Kamera ersetzen kann.
 */
export const cameraScanner = {
    scan(env) {
        return new Promise((resolve) => {
            let done = false;
            env.services.dialog.add(
                BarcodeDialog,
                {
                    facingMode: "environment",
                    onResult: (code) => {
                        done = true;
                        resolve(code);
                    },
                    onError: () => {
                        done = true;
                        resolve(null);
                    },
                },
                {
                    // Beim Schließen ohne Treffer; nach einem Treffer kommt onResult zuerst an
                    onClose: () => setTimeout(() => !done && resolve(null), 0),
                }
            );
        });
    },
};

/**
 * Scan-Feld für den Blistercenter-Wareneingang (1.6.0, R56).
 *
 * Handscanner tippen den DataMatrix-Code wie eine Tastatur. Das Trennzeichen zwischen Charge und
 * Seriennummer (GS) senden sie je nach Einstellung als Strg+] oder Alt+029. In einem normalen
 * Eingabefeld geht es dabei verloren; dann ist die Charge nicht sicher lesbar. Dieses Feld setzt
 * für beide Tastenfolgen das GS-Zeichen ein. Mit Enter (oder Tab) wird die Packung abgeschickt,
 * das Feld ist sofort für die nächste frei. Die Packungen werden der Reihe nach verarbeitet;
 * keine geht verloren, auch wenn schnell hintereinander gescannt wird.
 *
 * 1.6.4 (Code-Review 22.10.2026):
 * - Befund 2: Vor jeder Knopf-Aktion („Übernehmen“, „Hinzufügen“) meldet Odoo NEED_LOCAL_CHANGES.
 *   Das Feld schickt dann einen Scan ohne Enter ab und wartet, bis die ganze Warteschlange verarbeitet
 *   ist. Erst danach speichert Odoo und führt die Aktion aus.
 * - Befund 4: Alt+029 setzt das GS sofort beim Loslassen von Alt ein (kein Timer mehr). Setzt der
 *   Browser selbst ein Zeichen dafür ein, wird es verworfen.
 * - Befund 5: Scheitert die Verarbeitung (Server/Verbindung), bleibt der Scan erhalten, die Meldung
 *   wird rot, die Kamera hält an. „Wiederholen“ schickt die Scans erneut; doppelt zählt dabei nichts
 *   (die gescheiterte Anfrage hat nichts verändert, und dieselbe Seriennummer zählt nur einmal, R66).
 */
export class BcScanInput extends Component {
    static template = "blistercenter.BcScanInput";
    props = useProps({ ...standardFieldProps, placeholder: t.string().optional() });
    input = signal.ref();

    setup() {
        this.state = proxy({ pending: 0, last: "", camera: false, failed: [], error: "" });
        this.cameraSupported = isBarcodeScannerSupported();
        this.queue = Promise.resolve();
        this.altDigits = null;
        this.altGsPending = false;
        this.burst = 0;
        this.lastKey = 0;
        this.idleTimer = null;
        useListener(this.input, "keydown", (ev) => this.onKeydown(ev));
        useListener(this.input, "keyup", (ev) => this.onKeyup(ev));
        useListener(this.input, "beforeinput", (ev) => this.onBeforeInput(ev));
        useListener(this.input, "input", () => this.onInput());
        // Befund 2: Odoo fragt vor dem Speichern (Knopf-Aktion) offene Änderungen ab
        useBus(this.props.record.model.bus, "NEED_LOCAL_CHANGES", ({ detail }) =>
            detail.proms.push(this.commitPending())
        );
        // 1.6.1: Scan, während der Cursor nicht in einem Eingabefeld steht (z. B. nach einem Klick
        // in die Liste). Odoo erkennt ihn als Barcode; er wird hier wie ein Scan im Feld behandelt.
        const barcode = usePlugin(BarcodePlugin);
        useBus(barcode.bus, "barcode_scanned", (ev) => {
            if (!this.props.readonly && this.input()?.isConnected) {
                this.enqueue(ev.detail.barcode);
            }
        });
        onWillUnmount(() => clearTimeout(this.idleTimer));
        // Auf Handy/Tablet nicht automatisch fokussieren, sonst springt die Tastatur auf
        onMounted(() => !isMobileOS() && this.input()?.focus());
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

    /** Befund 4: Zeichen, das der Browser für Alt+029 selbst einsetzt, nicht zusätzlich übernehmen. */
    onBeforeInput(ev) {
        if (this.altGsPending && ev.data && ALT_GS_CHAR.test(ev.data)) {
            this.altGsPending = false;
            ev.preventDefault();
        }
    }

    onInput() {
        const el = this.input();
        if (GS_LOOKALIKE.test(el.value)) {
            const pos = el.selectionStart;
            el.value = el.value.replace(GS_LOOKALIKE_ALL, GS);
            el.setSelectionRange(pos, pos);
        }
        if (this.altGsPending) {
            // Rückfall, falls der Browser das Einsetzen nicht abbrechen ließ: doppeltes GS am Cursor entfernen
            this.altGsPending = false;
            const pos = el.selectionStart;
            if (pos >= 2 && el.value[pos - 1] === GS && el.value[pos - 2] === GS) {
                el.value = el.value.slice(0, pos - 1) + el.value.slice(pos);
                el.setSelectionRange(pos - 1, pos - 1);
            }
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
        // Nächstes echtes Zeichen: der Browser hat für Alt+029 kein eigenes Zeichen eingesetzt
        if (ev.key.length === 1 && !(ev.altKey && /^Numpad\d$/.test(ev.code))) {
            this.altGsPending = false;
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
        // Befund 4: sofort einsetzen. Früher per Timer und nur bei unverändertem Feld – folgten die
        // nächsten Zeichen schneller, ging das GS verloren. Ein vom Browser selbst eingesetztes Zeichen
        // fangen onBeforeInput/onInput ab.
        this.insert(GS);
        this.altGsPending = true;
    }

    submit() {
        clearTimeout(this.idleTimer);
        this.burst = 0;
        const el = this.input();
        const code = el.value;
        el.value = "";
        this.enqueue(code);
    }

    /** Text im Feld (Scanner ohne Enter, Ruhezeit noch nicht um) sofort abschicken. */
    flushInput() {
        const el = this.input();
        if (el && el.value.trim()) {
            this.submit();
            return true;
        }
        clearTimeout(this.idleTimer);
        return false;
    }

    /** Warten, bis alle angenommenen Scans verarbeitet sind (auch die während des Wartens dazukommen). */
    async drain() {
        let current;
        do {
            current = this.queue;
            await current;
        } while (current !== this.queue);
    }

    /**
     * Befund 2: vor dem Speichern (Knopf-Aktion) alles verarbeiten. Bleiben Scans ungezählt (Befund 5)
     * oder endete ein eben erst verarbeiteter Scan mit roter Meldung, setzt das Feld „scan_hold“; der
     * Server lehnt „Übernehmen“/„Hinzufügen“ dann mit dieser Meldung ab. Ein zweiter Klick ohne neue
     * Scans geht durch (rote Scan-Meldung gelesen); ungezählte Scans halten bis „Wiederholen“/„Verwerfen“.
     */
    async commitPending() {
        const hadWork = this.flushInput() || this.state.pending > 0;
        await this.drain();
        const record = this.props.record;
        let hold = false;
        if (this.state.failed.length) {
            hold = _t(
                "%(count)s Scan(s) nicht verarbeitet – diese Packungen sind noch nicht gezählt. " +
                    "Bitte im Fenster „Wiederholen“ (oder „Verwerfen“) wählen.",
                { count: this.state.failed.length }
            );
        } else if (hadWork && record.data.message_type === "error") {
            hold = _t(
                "Der zuletzt gescannte Code wurde nicht gezählt: %(message)s Bitte prüfen; " +
                    "danach erneut klicken.",
                { message: record.data.message || "" }
            );
        }
        if ("scan_hold" in record.data && (record.data.scan_hold || false) !== hold) {
            await record.update({ scan_hold: hold });
        }
    }

    errorText(error) {
        if (error instanceof ConnectionLostError) {
            return _t("Keine Verbindung zum Server.");
        }
        return error?.data?.message || error?.message || String(error);
    }

    enqueue(code) {
        if (!code || !code.trim()) {
            return this.queue;
        }
        this.state.pending++;
        this.queue = this.queue.then(async () => {
            try {
                await this.props.record.update({ [this.props.name]: code });
                // Odoo verschluckt einen Verbindungsabbruch beim Onchange (update endet ohne Fehler,
                // der Scan bleibt unverarbeitet im Feld). Der Server leert das Feld bei jeder
                // Verarbeitung; steht der Code noch drin, ist er nicht angekommen.
                if (this.props.record.data[this.props.name]) {
                    throw new ConnectionLostError("onchange");
                }
            } catch (error) {
                // Befund 5: Scan aufbewahren, Fehler zeigen (statt still zu verwerfen)
                this.state.failed.push(code);
                this.state.error = this.errorText(error);
                await this.showFailure();
            } finally {
                this.state.pending--;
                // Nicht während der Kamera und nicht auf dem Handy (sonst springt die Tastatur auf)
                if (!this.state.camera && !isMobileOS()) {
                    this.input()?.focus();
                }
            }
        });
        return this.queue;
    }

    /** Rote Meldung statt der letzten grünen; nur im Formular, ohne Serveranfrage. */
    async showFailure() {
        const count = this.state.failed.length;
        const message =
            count === 1
                ? _t("1 Scan nicht verarbeitet – Packung noch nicht gezählt. %(error)s", {
                      error: this.state.error,
                  })
                : _t("%(count)s Scans nicht verarbeitet – Packungen noch nicht gezählt. %(error)s", {
                      count,
                      error: this.state.error,
                  });
        try {
            // message/message_type haben kein Onchange: rein lokal, ohne Serveranfrage
            await this.props.record.update({ message, message_type: "error" });
        } catch {
            // Anzeige im Scan-Feld bleibt (rote Box mit „Wiederholen“)
        }
    }

    /** Gescheiterte Scans erneut verarbeiten (Befund 5). */
    async retryFailed() {
        const codes = this.state.failed.splice(0);
        this.state.error = "";
        for (const code of codes) {
            this.enqueue(code);
        }
        await this.drain();
    }

    /** Gescheiterte Scans bewusst verwerfen: diese Packungen sind nicht gezählt. */
    async dropFailed() {
        this.state.failed.splice(0);
        this.state.error = "";
        await this.props.record.update({
            message: _t("Nicht verarbeitete Scans verworfen – diese Packungen bitte erneut scannen."),
            message_type: "warning",
        });
    }

    /** Kamera: Packung für Packung lesen, bis das Fenster geschlossen wird oder ein Fehler kommt. */
    async openCamera() {
        if (this.state.camera) {
            return;
        }
        this.state.camera = true;
        try {
            for (;;) {
                const code = await cameraScanner.scan(this.env);
                if (!code) {
                    break;
                }
                this.enqueue(code);
                await this.drain();
                if (this.state.failed.length || this.props.record.data.message_type === "error") {
                    break; // Meldung lesen lassen; bei Verarbeitungsfehler anhalten (Befund 5)
                }
            }
        } finally {
            this.state.camera = false;
            if (!isMobileOS()) {
                this.input()?.focus();
            }
        }
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
