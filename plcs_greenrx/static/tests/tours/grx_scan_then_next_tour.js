import { registry } from "@web/core/registry";

// Code-Review R03: Scans in der Warteschlange und sofort F9 („Weiter“).
// Alle Scans müssen vor dem Schrittwechsel verarbeitet werden, keiner wird verworfen.
const GS = "\x1d";
const code = (n) => `0104150047110036` + `21NEXT${n}${GS}10QLOT${GS}17301231`;

function scan(codes) {
    for (const c of codes) {
        for (const key of c) {
            document.body.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
        }
        document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    }
}

registry.category("web_tour.tours").add("plcs_greenrx_scan_then_next", {
    steps: () => [
        {
            content: "Offene Box aus 'Meine Aufgaben' fortsetzen",
            trigger: ".o_grx_task_now",
            run: "click",
        },
        {
            content: "Erste Packung scannen",
            trigger: ".o_grx_boxhead .o_grx_count_num[data-qty='0']",
            run: () => scan([code(1)]),
        },
        {
            content: "Vier weitere Packungen und sofort F9",
            trigger: ".o_grx_boxhead .o_grx_count_num[data-qty='1']",
            run: () => {
                scan([code(2), code(3), code(4), code(5)]);
                window.dispatchEvent(new KeyboardEvent("keydown", { key: "F9", bubbles: true }));
            },
        },
        {
            content: "Vorprüfung erreicht",
            trigger: ".o_grx:contains(Vorschlag aus deinen Antworten)",
        },
        {
            content: "Warteschlange ist leer",
            trigger: ".o_grx:not(:has(.o_grx_pending))",
        },
    ],
});
