import { registry } from "@web/core/registry";

function count(n) {
    return `.o_grx_boxhead .o_grx_count_num[data-qty='${n}']`;
}

/**
 * GPT-Review F03: Handeingabe mit verzögerter Serverantwort.
 * Der Server antwortet auf SLOW1 erst nach einer Pause. In dieser Zeit tippt das Lager
 * schon die nächste Seriennummer. Erfasst werden muss SLOW1 (nicht die neue Eingabe),
 * und die neue Eingabe darf nicht gelöscht werden.
 */
registry.category("web_tour.tours").add("plcs_greenrx_manual_pack_delay", {
    steps: () => [
        { content: "Offene Box fortsetzen", trigger: ".o_grx_task_now", run: "click" },
        { content: "Box ist leer", trigger: count(0) },
        { content: "Handeingabe öffnen", trigger: ".o_grx_pk_open", run: "click" },
        { content: "PZN", trigger: "#grx_pk_code", run: "edit 12345678" },
        { content: "Charge", trigger: "#grx_pk_batch", run: "edit MLOT" },
        { content: "Verfall", trigger: "#grx_pk_expiry", run: "edit 12/2030" },
        { content: "Seriennummer SLOW1", trigger: "#grx_pk_serial", run: "edit SLOW1" },
        { content: "Übernehmen (Server antwortet verzögert)", trigger: ".o_grx_pk_submit", run: "click" },
        { content: "Noch nichts erfasst", trigger: count(0) },
        { content: "Während des Wartens nächste Seriennummer tippen", trigger: "#grx_pk_serial", run: "edit NEXT2" },
        { content: "SLOW1 ist angekommen", trigger: count(1) },
        { content: "Erfasst wurde SLOW1, nicht die neue Eingabe", trigger: ".o_grx_table tr:contains(SLOW1)" },
        { content: "Keine Packung NEXT2 erfasst", trigger: ".o_grx_table:not(:has(tr:contains(NEXT2)))" },
        { content: "Neue Eingabe steht noch im Feld", trigger: "#grx_pk_serial:value(NEXT2)" },
        { content: "NEXT2 übernehmen", trigger: ".o_grx_pk_submit", run: "click" },
        { content: "Beide erfasst", trigger: count(2) },
        { content: "NEXT2 in der Liste", trigger: ".o_grx_table tr:contains(NEXT2)" },
    ],
});
