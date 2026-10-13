import { registry } from "@web/core/registry";

// Packungscodes wie in TestGreenrxScanQueue (PZN 04711003, Charge QLOT, Verfall 12/2030)
const GS = "\x1d";
const CODES = [1, 2, 3, 4, 5].map((n) => `0104150047110036` + `21QUEUE${n}${GS}10QLOT${GS}17301231`);

/**
 * Handscanner im Tastaturmodus: jedes Zeichen als Taste, Enter am Ende,
 * alle Codes direkt hintereinander ohne Pause.
 */
function scanFast(codes) {
    for (const code of codes) {
        for (const key of code) {
            document.body.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
        }
        document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    }
}

registry.category("web_tour.tours").add("plcs_greenrx_scan_queue", {
    steps: () => [
        {
            content: "Offene Box aus 'Meine Aufgaben' fortsetzen",
            trigger: ".o_grx_task_now",
            run: "click",
        },
        {
            content: "Schritt 3: Box ist leer",
            trigger: ".o_grx_boxhead .o_grx_count_num[data-qty='0']",
        },
        {
            content: "5 Packungen direkt hintereinander scannen",
            trigger: ".o_grx_boxhead",
            run: () => scanFast(CODES),
        },
        {
            content: "Alle 5 Packungen sind angekommen",
            trigger: ".o_grx_boxhead .o_grx_count_num[data-qty='5']",
        },
        {
            content: "Jeder Scan hat eine eigene Rückmeldung",
            trigger: ".o_grx_scanlog:has(.o_grx_scanlog_ok ~ .o_grx_scanlog_ok ~ .o_grx_scanlog_ok ~ .o_grx_scanlog_ok ~ .o_grx_scanlog_ok)",
        },
        {
            content: "Warteschlange ist leer",
            trigger: ".o_grx:not(:has(.o_grx_pending))",
        },
    ],
});
