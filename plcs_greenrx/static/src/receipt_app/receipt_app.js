import { _t } from "@web/core/l10n/translation";
import { Component, onWillStart, proxy, signal, useListener, usePlugin, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { url } from "@web/core/utils/urls";
import { scanBarcode } from "@web/core/barcode/barcode_dialog";
import { isBarcodeScannerSupported } from "@web/core/barcode/barcode_video_scanner";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { BarcodePlugin } from "@barcodes/barcode_plugin";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const STEP_LABELS = ["Paket wählen", "Box scannen", "Packungen scannen", "Vorprüfung", "Drucken"];
const AMPEL = {
    gruen: { label: "Grün – unauffällig", short: "Grün", desc: "An die VP zur Freigabe" },
    gelb: { label: "Gelb – auffällig", short: "Gelb", desc: "An die VP, sie entscheidet nach Prüfung" },
    rot: { label: "Rot – sperren", short: "Rot", desc: "Rotes Sperrblatt an die Box, ins Sperrregal, VP informiert" },
};
const RANK = { gruen: 0, gelb: 1, rot: 2 };
// Felder der Handeingabe einer Packung, in Eingabereihenfolge (Enter springt weiter)
const PACK_FIELDS = ["code", "batch", "expiry", "serial"];

function emptyPack() {
    return { code: "", batch: "", expiry: "", serial: "" };
}

/** Sieht die Eingabe wie ein gescannter Packungscode aus (Scanner tippt in ein Eingabefeld)? */
function looksLikeScan(value) {
    const v = String(value || "").trim();
    return v.length > 20 || /[\x1d\x1e]|^\]d2|^\[\)>|^\(01\)/.test(v) || /^01\d{14}21/.test(v);
}

function errorMessage(error) {
    return error?.data?.message || error?.message || String(error);
}

/**
 * GreenRX-Wareneingang nach dem Entwurf "PLCS Lager-Oberfläche":
 * ein Schritt pro Bildschirm (Handy), auf dem PC mit Seitenleiste.
 */
export class GreenRxReceiptApp extends Component {
    static template = "plcs_greenrx.ReceiptApp";

    props = useProps(standardActionServiceProps);
    searchInput = signal.ref();
    manualInput = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.cameraSupported = isBarcodeScannerSupported();
        this.stepLabels = STEP_LABELS;
        this.ampelMeta = AMPEL;
        const mode = this.props.action.context?.grx_mode || "receipt";
        this.state = proxy({
            mode,
            screen: mode === "vp" ? "vp_list" : "home",
            step: 1,
            busy: false,
            home: null,
            query: "",
            results: [],
            searched: false,
            selected: null,
            ticket: null,
            box: null,
            flash: null,
            manual: false,
            manualValue: "",
            manualError: "",
            camera: false,
            noteDraft: "",
            deviationNote: "",
            askDeviation: false,
            lastClosed: null,
            vp: null,
            vpBox: null,
            vpReason: "",
            vpAsk: false,
            scanLog: [],
            pendingScans: 0,
            // Handeingabe einer Packung (Code nicht lesbar)
            packManual: false,
            pack: emptyPack(),
            packError: "",
        });
        // Warteschlange: Scans werden der Reihe nach verarbeitet, keiner geht verloren
        this.scanQueue = Promise.resolve();
        this.scanSeq = 0;
        this.barcode = usePlugin(BarcodePlugin);
        useBus(this.barcode.bus, "barcode_scanned", (ev) => this.onScan(ev.detail.barcode));
        useListener(window, "keydown", (ev) => this.onKeydown(ev));
        onWillStart(async () => {
            this.sounds = {};
            const ext = new Audio().canPlayType("audio/ogg") ? "ogg" : "mp3";
            this.sounds.error = new Audio(url(`/barcodes/static/src/audio/error.${ext}`));
            this.sounds.ok = new Audio(url(`/mail/static/src/audio/ting.${ext}`));
            await (mode === "vp" ? this.loadVp() : this.loadHome());
        });
        this.searchTimer = null;
    }

    // ------------------------------------------------------------------
    // Hilfen
    // ------------------------------------------------------------------
    play(kind) {
        const sound = this.sounds?.[kind];
        if (sound) {
            // eigene Kopie je Ton: schnelle Scans hintereinander sind alle hörbar
            sound.cloneNode().play().catch(() => {});
        }
        if (kind === "error" && "vibrate" in navigator) {
            navigator.vibrate([80, 60, 80]);
        }
    }

    async call(model, method, args = [], kwargs = {}) {
        this.state.busy = true;
        try {
            return await this.orm.call(model, method, args, kwargs);
        } finally {
            this.state.busy = false;
        }
    }

    async run(fn) {
        try {
            return await fn();
        } catch (error) {
            this.showError(error);
            return false;
        }
    }

    showError(error, title = _t("Stopp")) {
        this.play("error");
        this.state.flash = { status: "error", title, message: errorMessage(error) };
        this.scrollTop();
    }

    scrollTop() {
        // Meldungen stehen oben – bei langen Seiten dorthin springen
        setTimeout(() => document.querySelector(".o_grx_body")?.scrollTo({ top: 0, behavior: "smooth" }), 30);
    }

    clearFlash() {
        this.state.flash = null;
    }

    get progress() {
        return this.stepLabels.map((label, i) => ({
            label,
            done: i + 1 < this.state.step,
            active: i + 1 === this.state.step,
        }));
    }

    get stepNo() {
        return Math.min(this.state.step, 5);
    }

    get stepLabel() {
        return this.stepLabels[this.stepNo - 1];
    }

    get box() {
        return this.state.box;
    }

    get ticket() {
        return this.state.box?.ticket || this.state.ticket;
    }

    get packageBoxes() {
        return this.state.ticket?.boxes || [];
    }

    get packageTotal() {
        const boxes = this.packageBoxes;
        const n = boxes.reduce((a, b) => a + b.qty, 0);
        return `${n} Packungen in ${boxes.length} ${boxes.length === 1 ? "Box" : "Boxen"}`;
    }

    get expectedForBox() {
        const box = this.state.box;
        if (!box || !box.product) {
            return "";
        }
        if (!box.ticket_number) {
            return _t("Dieser Artikel steht nicht auf dem Retourenschein.");
        }
        const row = (box.ticket.soll_ist || []).find((r) => r.ticket === box.ticket_number);
        let text = _t("Laut Schein: Ticket %(t)s · %(n)s × %(p)s", {
            t: box.ticket_number,
            n: box.slip_qty,
            p: box.product,
        });
        if (box.slip_batch) {
            text += _t(" · Ch. %s", box.slip_batch);
        }
        if (row) {
            text += _t(" (bisher %s erfasst)", row.ist);
        }
        return text;
    }

    get answeredCount() {
        return (this.state.box?.questions || []).filter((q) => q.value).length;
    }

    get allAnswered() {
        const qs = this.state.box?.questions || [];
        return qs.length && this.answeredCount === qs.length;
    }

    get lights() {
        const box = this.state.box;
        const suggest = box?.suggest || "gruen";
        return ["gruen", "gelb", "rot"].map((key) => ({
            key,
            ...AMPEL[key],
            selected: box?.ampel === key,
            blocked: RANK[key] < RANK[suggest],
            isSuggest: key === suggest,
        }));
    }

    get noteRequired() {
        const box = this.state.box;
        return box && ["gelb", "rot"].includes(box.ampel) && !(box.reasons || []).length;
    }

    get canProceedCheck() {
        const box = this.state.box;
        if (!box || !this.allAnswered || !box.ampel) {
            return false;
        }
        return !this.noteRequired || (this.state.noteDraft || "").trim().length > 0;
    }

    get proceedHint() {
        if (!this.allAnswered) {
            return _t("Erst alle Fragen beantworten – noch %s offen", (this.state.box?.questions.length || 0) - this.answeredCount);
        }
        if (!this.state.box?.ampel) {
            return _t("Ampel wählen");
        }
        return _t("Grund eintragen");
    }

    ampelOf(key) {
        return AMPEL[key] || { label: "", short: "", desc: "" };
    }

    // ------------------------------------------------------------------
    // Startseite
    // ------------------------------------------------------------------
    async loadHome() {
        this.state.home = await this.call("plcs.greenrx.ticket", "grx_home");
    }

    async goHome() {
        this.state.screen = "home";
        this.state.step = 1;
        this.state.box = null;
        this.state.ticket = null;
        this.state.selected = null;
        this.state.query = "";
        this.state.results = [];
        this.state.searched = false;
        this.state.flash = null;
        this.state.askDeviation = false;
        this.state.lastClosed = null;
        await this.run(() => this.loadHome());
    }

    exitApp() {
        window.location.href = "/odoo";
    }

    startNew() {
        this.state.screen = "step";
        this.state.step = 1;
        this.state.flash = null;
        this.state.box = null;
        this.state.ticket = null;
        this.state.selected = null;
        this.state.query = "";
        this.state.results = [];
        this.state.searched = false;
        setTimeout(() => this.searchInput()?.focus(), 50);
    }

    async resumeTicket(ticketId) {
        await this.run(async () => {
            const data = await this.call("plcs.greenrx.ticket", "grx_resume", [[ticketId]]);
            this.state.ticket = data.ticket;
            this.state.screen = "step";
            this.state.askDeviation = false;
            if (data.box) {
                this.applyBox(data.box);
            } else {
                this.state.box = null;
                this.state.step = 2;
            }
        });
    }

    // ------------------------------------------------------------------
    // Schritt 1: Paket wählen
    // ------------------------------------------------------------------
    onQueryInput(ev) {
        this.state.query = ev.target.value;
        this.state.selected = null;
        clearTimeout(this.searchTimer);
        this.searchTimer = setTimeout(() => this.search(), 250);
    }

    async onQueryKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            clearTimeout(this.searchTimer);
            // Retourenschein-QR über einen Handscanner im Suchfeld: jede Zeile endet mit Enter.
            const value = ev.target.value || "";
            if (this.slipBuffer || value.trim().toUpperCase().startsWith("REF:")) {
                ev.target.value = "";
                this.state.query = "";
                this.collectSlip(value);
                return;
            }
            await this.search();
            if (this.state.results.length === 1) {
                this.selectTicket(this.state.results[0]);
            }
        }
    }

    async search() {
        const q = (this.state.query || "").trim();
        if (!q) {
            this.state.results = [];
            this.state.searched = false;
            return;
        }
        await this.run(async () => {
            this.state.results = await this.call("plcs.greenrx.ticket", "grx_search", [q]);
            this.state.searched = true;
        });
    }

    selectTicket(ticket) {
        this.state.selected = ticket;
        this.clearFlash();
    }

    clearSelection() {
        this.state.selected = null;
        setTimeout(() => this.searchInput()?.focus(), 50);
    }

    async confirmTicket() {
        if (!this.state.selected || this.state.selected.startable === false) {
            return;
        }
        await this.run(async () => {
            this.state.ticket = await this.call("plcs.greenrx.ticket", "grx_start", [[this.state.selected.id]]);
            this.state.step = 2;
            this.state.flash = null;
        });
    }

    // ------------------------------------------------------------------
    // Scannen (Handscanner, Kamera)
    // ------------------------------------------------------------------
    /**
     * Teile eines Retourenschein-Codes sammeln. Handscanner im Tastaturmodus liefern
     * den QR-Code zeilenweise (Enter) und Odoo trennt zusätzlich an "|". Nach einer
     * kurzen Pause wird alles zusammengesetzt und an Odoo geschickt.
     */
    collectSlip(part) {
        this.slipBuffer = (this.slipBuffer || "") + part + "\n";
        clearTimeout(this.slipTimer);
        this.slipTimer = setTimeout(() => {
            const raw = this.slipBuffer;
            this.slipBuffer = "";
            this.openSlip(raw);
        }, 450);
    }

    async openSlip(raw) {
        if (this.state.screen === "step" && this.state.step > 1 && this.ticket) {
            if (raw.includes(`REF:${this.ticket.name}`)) {
                this.state.flash = { status: "info", title: _t("Gleiches Paket"), message: _t("Dieser Retourenschein gehört zum Paket in Arbeit.") };
                return;
            }
            this.play("error");
            this.state.flash = {
                status: "error",
                title: _t("Anderes Paket"),
                message: _t("Erst das Paket %s fertig erfassen oder über „Meine Aufgaben“ wechseln.", this.ticket.name),
            };
            return;
        }
        if (this.state.screen !== "step") {
            this.startNew();
        }
        await this.run(async () => {
            const data = await this.call("plcs.greenrx.ticket", "grx_from_slip", [raw]);
            this.state.results = [data];
            this.state.searched = true;
            this.selectTicket(data);
            this.play("ok");
            if (data.created_from_slip) {
                this.state.flash = {
                    status: "info",
                    title: _t("Vorgang aus dem Retourenschein angelegt"),
                    message: _t("Referenz %s war noch nicht in Odoo. Die Daten stammen aus dem QR-Code.", data.name),
                };
            } else if (data.slip_note) {
                this.state.flash = { status: "warning", title: _t("Retourenschein weicht ab"), message: data.slip_note };
            }
        });
    }

    onScan(raw) {
        if (this.state.mode === "vp") {
            return this.onVpScan(raw);
        }
        // Retourenschein: erster Teil beginnt mit REF:, weitere Teile folgen sofort
        if (this.slipBuffer || String(raw).trim().toUpperCase().startsWith("REF:")) {
            this.collectSlip(raw);
            return;
        }
        // Jeder Scan kommt in die Warteschlange und wird nach dem vorherigen verarbeitet.
        // Doppelscans erkennt der Server.
        this.state.pendingScans++;
        const done = this.scanQueue
            .then(() => this.handleScan(raw))
            .catch((error) => this.showError(error))
            .finally(() => this.state.pendingScans--);
        this.scanQueue = done;
        return done;
    }

    async handleScan(raw) {
        if (this.state.screen === "home") {
            this.startNew();
            this.state.query = raw;
            await this.search();
            if (this.state.results.length === 1) {
                this.selectTicket(this.state.results[0]);
            }
            return;
        }
        const step = this.state.step;
        if (step === 1) {
            this.state.query = raw;
            await this.search();
            if (this.state.results.length === 1) {
                this.selectTicket(this.state.results[0]);
            }
        } else if (step === 2 || step === 6) {
            await this.openBox(raw, false);
        } else if (step === 3) {
            await this.scanPack(raw);
        } else {
            this.play("error");
            this.state.flash = {
                status: "info",
                title: _t("Scan ignoriert"),
                message: _t("In diesem Schritt wird nichts gescannt."),
            };
        }
    }

    async openCamera() {
        this.state.camera = true;
        while (this.state.camera) {
            let code = null;
            try {
                code = await scanBarcode(this.env, "environment");
            } catch (error) {
                this.state.camera = false;
                this.notification.add(errorMessage(error), { type: "warning" });
                break;
            }
            if (!code) {
                this.state.camera = false;
                break;
            }
            await this.onScan(code);
            if (this.state.step !== 3 || this.state.flash?.status === "error") {
                // Nur im Packungsschritt fortlaufend scannen, bei Fehler anhalten
                this.state.camera = false;
            }
        }
    }

    // ------------------------------------------------------------------
    // Schritt 2: Box
    // ------------------------------------------------------------------
    applyBox(box) {
        if (this.state.box?.id !== box.id) {
            this.state.scanLog = [];
            this.closePackManual();
        }
        this.state.box = box;
        this.state.noteDraft = box.note || "";
        if (box.ticket) {
            this.state.ticket = Object.assign({}, this.state.ticket || {}, box.ticket);
        }
        const map = { scan: 3, check: 4, print: 5 };
        this.state.step = map[box.state] || 6;
    }

    async openBox(code, manual) {
        const ticketId = this.ticket?.id;
        if (!ticketId) {
            return;
        }
        try {
            const box = await this.call("plcs.greenrx.box", "grx_open_box", [ticketId, code], { manual });
            this.state.manual = false;
            this.state.manualValue = "";
            this.state.manualError = "";
            this.state.lastClosed = null;
            this.applyBox(box);
            this.play("ok");
            this.state.flash = box.box_manual
                ? { status: "info", title: _t("Box von Hand eingegeben"), message: _t("Die Handeingabe ist im Verlauf vermerkt.") }
                : null;
        } catch (error) {
            if (manual) {
                this.state.manualError = errorMessage(error);
                this.play("error");
            } else {
                this.showError(error, _t("Box nicht möglich"));
            }
        }
    }

    toggleManual(value) {
        this.state.manual = value;
        this.state.manualValue = "";
        this.state.manualError = "";
        if (value) {
            setTimeout(() => this.manualInput()?.focus(), 50);
        }
    }

    onManualInput(ev) {
        this.state.manualValue = ev.target.value.toUpperCase();
        this.state.manualError = "";
    }

    async onManualKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            await this.confirmManual();
        }
    }

    async confirmManual() {
        const value = (this.state.manualValue || "").trim();
        if (!value) {
            this.state.manualError = _t("Box-Nummer eingeben, z. B. GX012 oder 12.");
            return;
        }
        await this.openBox(value, true);
    }

    // ------------------------------------------------------------------
    // Schritt 3: Packungen
    // ------------------------------------------------------------------
    async scanPack(raw) {
        const box = this.state.box;
        if (!box) {
            return;
        }
        try {
            const res = await this.call("plcs.greenrx.box", "grx_scan", [[box.id], raw]);
            this.state.box = res.box;
            const entry = this.packageBoxes.find((b) => b.id === res.box.id);
            if (entry) {
                entry.qty = res.box.qty;
            }
            this.confirmScan(res.status, res.title, res.message);
        } catch (error) {
            this.confirmScan("error", _t("Scan nicht verarbeitet"), errorMessage(error));
        }
    }

    /**
     * Rückmeldung zu genau einem Scan: Meldung oben, Eintrag in "Letzte Scans" und Ton.
     * So bleibt jeder Scan sichtbar, auch wenn mehrere schnell hintereinander kommen.
     */
    confirmScan(status, title, message) {
        this.state.flash = { status, title, message };
        this.state.scanLog.unshift({
            id: ++this.scanSeq,
            status,
            title,
            message,
            time: new Date().toLocaleTimeString("de-DE"),
        });
        this.state.scanLog.splice(10);
        this.play(status === "ok" || status === "info" ? "ok" : "error");
        this.scrollTop();
    }

    removeScan(scan) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Packung entfernen?"),
            body: _t("Packung SN %(sn)s (Charge %(ch)s) aus Box %(box)s nehmen? Die Packung zählt dann nicht mehr zur Box.", {
                sn: scan.serial,
                ch: scan.batch || "—",
                box: this.state.box?.box || "",
            }),
            confirmLabel: _t("Ja, entfernen"),
            confirmClass: "btn-danger",
            cancelLabel: _t("Nein, behalten"),
            confirm: () => this.doRemoveScan(scan),
            cancel: () => {},
        });
    }

    async doRemoveScan(scan) {
        await this.run(async () => {
            this.state.box = await this.call("plcs.greenrx.box", "grx_remove_scan", [[this.state.box.id], scan.id]);
            this.state.flash = { status: "info", title: _t("Packung entfernt"), message: _t("SN %s wieder aus der Box genommen.", scan.serial) };
        });
    }

    // ------------------------------------------------------------------
    // Schritt 3: Packung von Hand eingeben (Code nicht lesbar)
    // ------------------------------------------------------------------
    openPackManual() {
        this.state.packManual = true;
        this.state.packError = "";
        this.focusPackField(this.state.pack.code ? "serial" : "code");
    }

    closePackManual() {
        this.state.packManual = false;
        this.state.pack = emptyPack();
        this.state.packError = "";
    }

    focusPackField(name) {
        setTimeout(() => document.getElementById(`grx_pk_${name}`)?.focus(), 50);
    }

    onPackInput(name, ev) {
        this.state.pack[name] = ev.target.value;
        this.state.packError = "";
    }

    async onPackKeydown(name, ev) {
        if (ev.key !== "Enter") {
            return;
        }
        ev.preventDefault();
        const value = ev.target.value;
        if (looksLikeScan(value)) {
            // Der Scanner hat in das Eingabefeld getippt: als normalen Scan verarbeiten
            this.state.pack[name] = "";
            ev.target.value = "";
            this.state.packError = "";
            await this.onScan(value);
            this.focusPackField(name);
            return;
        }
        const next = PACK_FIELDS[PACK_FIELDS.indexOf(name) + 1];
        if (next) {
            this.focusPackField(next);
        } else {
            await this.submitPackManual();
        }
    }

    async submitPackManual() {
        const box = this.state.box;
        const p = this.state.pack;
        if (!box) {
            return;
        }
        const missing = [];
        if (!p.code.trim()) missing.push(_t("PZN"));
        if (!p.batch.trim()) missing.push(_t("Charge"));
        if (!p.expiry.trim()) missing.push(_t("Verfall"));
        if (!p.serial.trim()) missing.push(_t("Seriennummer"));
        if (missing.length) {
            this.state.packError = _t("Bitte ausfüllen: %s", missing.join(", "));
            this.play("error");
            return;
        }
        // Über die Warteschlange, damit sich Handeingabe und Scans nicht überholen
        this.state.pendingScans++;
        const done = this.scanQueue
            .then(async () => {
                let res;
                try {
                    res = await this.call("plcs.greenrx.box", "grx_scan_manual", [[box.id], p.code, p.serial, p.batch, p.expiry]);
                } catch (error) {
                    // Eingabe unvollständig oder ungültig: Maske bleibt offen, nichts erfasst
                    this.state.packError = errorMessage(error);
                    this.play("error");
                    return;
                }
                this.state.box = res.box;
                const entry = this.packageBoxes.find((b) => b.id === res.box.id);
                if (entry) {
                    entry.qty = res.box.qty;
                }
                this.confirmScan(res.status, res.title, res.message);
                if (res.scan_id) {
                    // Nächste Packung: PZN, Charge und Verfall stehen lassen, Seriennummer neu
                    this.state.pack.serial = "";
                    this.focusPackField("serial");
                } else {
                    this.state.packError = res.message;
                }
            })
            .catch((error) => this.showError(error))
            .finally(() => this.state.pendingScans--);
        this.scanQueue = done;
        return done;
    }

    async toCheck() {
        await this.run(async () => {
            this.applyBox(await this.call("plcs.greenrx.box", "grx_to_check", [[this.state.box.id]]));
            this.state.flash = null;
        });
    }

    async cancelBox() {
        if (!window.confirm(_t("Erfassung dieser Box abbrechen? Die gescannten Packungen werden wieder freigegeben."))) {
            return;
        }
        await this.run(async () => {
            this.state.ticket = await this.call("plcs.greenrx.box", "grx_cancel", [[this.state.box.id]]);
            this.state.box = null;
            this.state.step = 2;
            this.state.flash = { status: "info", title: _t("Erfassung abgebrochen"), message: _t("Die Box ist wieder frei.") };
        });
    }

    async back() {
        await this.run(async () => {
            this.applyBox(await this.call("plcs.greenrx.box", "grx_back", [[this.state.box.id]]));
            this.state.flash = null;
        });
    }

    // ------------------------------------------------------------------
    // Schritt 4: Vorprüfung
    // ------------------------------------------------------------------
    async answer(question, value) {
        await this.run(async () => {
            const answers = { [question.field]: value };
            this.state.box = await this.call("plcs.greenrx.box", "grx_save_check", [[this.state.box.id]], { answers });
        });
    }

    async pickAmpel(light) {
        if (light.blocked) {
            return;
        }
        await this.run(async () => {
            this.state.box = await this.call("plcs.greenrx.box", "grx_save_check", [[this.state.box.id]], {
                ampel: light.key,
            });
        });
    }

    onNoteInput(ev) {
        this.state.noteDraft = ev.target.value;
    }

    async toPrint() {
        await this.run(async () => {
            await this.call("plcs.greenrx.box", "grx_save_check", [[this.state.box.id]], { note: this.state.noteDraft || "" });
            this.applyBox(await this.call("plcs.greenrx.box", "grx_to_print", [[this.state.box.id]]));
            this.state.flash = null;
        });
    }

    // ------------------------------------------------------------------
    // Schritt 5: Drucken und abschließen
    // ------------------------------------------------------------------
    async printDocs() {
        const box = this.state.box;
        await this.run(async () => {
            await this.call("plcs.greenrx.box", "grx_print", [[box.id]]);
            window.open(`/report/pdf/plcs_greenrx.report_box_documents/${box.id}`, "_blank");
            this.state.box = await this.call("plcs.greenrx.box", "grx_confirm_signed", [[box.id], false]);
        });
    }

    async toggleSigned(ev) {
        await this.run(async () => {
            this.state.box = await this.call("plcs.greenrx.box", "grx_confirm_signed", [[this.state.box.id], ev.target.checked]);
        });
    }

    async closeBox() {
        await this.run(async () => {
            const box = await this.call("plcs.greenrx.box", "grx_close", [[this.state.box.id]]);
            this.state.lastClosed = box;
            this.state.ticket = await this.call("plcs.greenrx.ticket", "grx_data", [[box.ticket_id]]);
            this.state.box = null;
            this.state.step = 6;
            this.state.flash = null;
            this.play("ok");
        });
    }

    nextBox() {
        this.state.step = 2;
        this.state.box = null;
        this.state.lastClosed = null;
        this.state.flash = null;
    }

    async finishPackage(withNote = false) {
        // aktuellen Stand holen: Boxen anderer Personen können sich inzwischen geändert haben
        const fresh = await this.run(() => this.call("plcs.greenrx.ticket", "grx_data", [[this.state.ticket.id]]));
        if (!fresh) {
            return;
        }
        this.state.ticket = fresh;
        const ticket = fresh;
        const openBoxes = (ticket.boxes || []).filter((b) => ["scan", "check", "print"].includes(b.state));
        if (openBoxes.length) {
            this.state.askDeviation = false;
            this.showError(
                _t(
                    "Noch nicht abgeschlossen: %s. Box-Nummer scannen oder von Hand eingeben, dann abschließen oder abbrechen.",
                    openBoxes.map((b) => `${b.box} (${b.state_label}, ${b.user})`).join(", ")
                ),
                _t("Paket noch nicht fertig")
            );
            return;
        }
        const deviations = (ticket.soll_ist || []).filter((r) => r.status !== "ok");
        if (deviations.length && !withNote) {
            this.state.askDeviation = true;
            this.state.flash = null;
            setTimeout(() => document.getElementById("grx_dev")?.focus(), 50);
            return;
        }
        if (withNote && !(this.state.deviationNote || "").trim()) {
            this.state.flash = { status: "error", title: _t("Begründung fehlt"), message: _t("Bitte die Abweichung begründen.") };
            return;
        }
        await this.run(async () => {
            await this.call("plcs.greenrx.ticket", "grx_finish", [[ticket.id]], {
                deviation_note: withNote ? this.state.deviationNote : false,
            });
            this.state.askDeviation = false;
            this.state.deviationNote = "";
            this.notification.add(_t("Paket %s ist gebucht. Die Boxen liegen bei der VP.", ticket.name), { type: "success" });
            await this.goHome();
        });
    }

    onDeviationInput(ev) {
        this.state.deviationNote = ev.target.value;
    }

    // ------------------------------------------------------------------
    // VP
    // ------------------------------------------------------------------
    async loadVp() {
        this.state.vp = await this.call("plcs.greenrx.box", "grx_vp_list");
    }

    async openVp() {
        this.state.mode = "vp";
        this.state.screen = "vp_list";
        this.state.vpBox = null;
        this.state.flash = null;
        await this.run(() => this.loadVp());
    }

    openVpBox(box) {
        this.state.vpBox = box;
        this.state.vpReason = "";
        this.state.vpAsk = false;
        this.state.screen = "vp_detail";
        this.state.flash = null;
    }

    async onVpScan(raw) {
        const list = this.state.vp?.boxes || [];
        const code = String(raw).trim().toUpperCase();
        const box = list.find((b) => b.box === code);
        if (box) {
            this.openVpBox(box);
        } else {
            this.play("error");
            this.state.flash = { status: "error", title: _t("Box nicht in der Liste"), message: _t("%s wartet nicht auf deine Freigabe.", code) };
        }
    }

    onVpReason(ev) {
        this.state.vpReason = ev.target.value;
    }

    async vpRelease() {
        const box = this.state.vpBox;
        const reasonRequired = box.state === "blocked" || box.ampel === "gelb" || box.ampel === "rot";
        if (reasonRequired && !(this.state.vpReason || "").trim()) {
            this.state.vpAsk = "release";
            return;
        }
        await this.run(async () => {
            await this.call("plcs.greenrx.box", "grx_vp_release", [[box.id]], { note: this.state.vpReason || false });
            this.notification.add(_t("Box %s freigegeben → GreenRX-Bestand.", box.box), { type: "success" });
            await this.openVp();
        });
    }

    async vpBlock() {
        const box = this.state.vpBox;
        if (!(this.state.vpReason || "").trim()) {
            this.state.vpAsk = "block";
            return;
        }
        await this.run(async () => {
            await this.call("plcs.greenrx.box", "grx_vp_block", [[box.id], this.state.vpReason]);
            this.notification.add(_t("Box %s gesperrt.", box.box), { type: "warning" });
            await this.openVp();
        });
    }

    printVpBox() {
        window.open(`/report/pdf/plcs_greenrx.report_box_documents/${this.state.vpBox.id}`, "_blank");
    }

    // ------------------------------------------------------------------
    // Tastatur (PC)
    // ------------------------------------------------------------------
    onKeydown(ev) {
        if (ev.defaultPrevented) {
            return;
        }
        if (ev.key === "F8" && this.state.screen === "step" && this.state.step === 5) {
            ev.preventDefault();
            this.printDocs();
        } else if (ev.key === "F9" && this.state.screen === "step") {
            ev.preventDefault();
            if (this.state.step === 3 && this.state.box?.qty) {
                this.toCheck();
            } else if (this.state.step === 4 && this.canProceedCheck) {
                this.toPrint();
            } else if (this.state.step === 5 && this.state.box?.signed_confirmed) {
                this.closeBox();
            }
        } else if (ev.key === "Escape" && this.state.flash) {
            this.clearFlash();
        }
    }
}

registry.category("actions").add("plcs_greenrx.receipt_app", GreenRxReceiptApp);
