import { registry } from "@web/core/registry";

/** Handscanner im Tastaturmodus: Zeichen einzeln, Enter am Ende. */
function scan(code) {
    for (const key of code) {
        document.body.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    }
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
}

/**
 * GPT-Review F01: Eine von VP 1 gesperrte Box ist für eine andere VP in der Liste
 * sichtbar, per Box-Scan erreichbar und mit Begründung freigebbar.
 */
registry.category("web_tour.tours").add("plcs_greenrx_vp_blocked", {
    steps: () => [
        { content: "VP-Freigabe öffnen", trigger: ".o_grx_task:contains(VP-Freigabe)", run: "click" },
        { content: "Bereich für gesperrte Boxen", trigger: ".o_grx_kicker:contains(Gesperrte Boxen erneut prüfen)" },
        { content: "Box GX135 mit Sperrgrund", trigger: ".o_grx_task:contains(GX135):contains(Umkarton nass)" },
        { content: "Box per Scan öffnen", trigger: ".o_grx_main", run: () => scan("GX135") },
        { content: "Detail zeigt die Sperre", trigger: ".o_grx_vp .o_grx_hint_warn:contains(Umkarton nass)" },
        { content: "Kein zweites Sperren", trigger: ".o_grx_vp:not(:has(button:contains(Sperre bestätigen)))" },
        { content: "Freigeben", trigger: "button:contains(Freigeben → GreenRX-Bestand)", run: "click" },
        { content: "Begründung ist Pflicht", trigger: "#grx_vp_reason", run: "edit Packungen geprüft, trocken" },
        { content: "Freigeben bestätigen", trigger: "button:contains(Freigeben → GreenRX-Bestand)", run: "click" },
        { content: "Zurück in der Liste, Box nicht mehr gesperrt", trigger: ".o_grx_main:not(:has(.o_grx_task:contains(GX135)))" },
    ],
});
