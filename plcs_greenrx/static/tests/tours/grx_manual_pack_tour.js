import { registry } from "@web/core/registry";

// Werte wie in TestGreenrxManualPackUi (PZN 12345678, Charge MLOT, Verfall 12/2030)
const SCAN_IN_FIELD = "0104150123456782" + "21SCANIN1#10MLOT#17301231";

function count(n) {
    return `.o_grx_boxhead .o_grx_count_num[data-qty='${n}']`;
}

/**
 * Handeingabe einer Packung (Code nicht lesbar), Scan in ein Eingabefeld,
 * Sicherheitsabfrage beim Entfernen.
 */
registry.category("web_tour.tours").add("plcs_greenrx_manual_pack", {
    steps: () => [
        { content: "Offene Box fortsetzen", trigger: ".o_grx_task_now", run: "click" },
        { content: "Schritt 3: Box ist leer", trigger: count(0) },
        { content: "Handeingabe öffnen", trigger: ".o_grx_pk_open", run: "click" },
        { content: "PZN mit Tippfehler", trigger: "#grx_pk_code", run: "edit 12345679" },
        { content: "Charge", trigger: "#grx_pk_batch", run: "edit MLOT" },
        { content: "Verfall", trigger: "#grx_pk_expiry", run: "edit 12/2030" },
        { content: "Seriennummer (nur einmal)", trigger: "#grx_pk_serial", run: "edit MAN1" },
        { content: "Kein Wiederholungsfeld", trigger: ".o_grx_pk:not(:has(#grx_pk_serial2))" },
        { content: "Übernehmen", trigger: ".o_grx_pk_submit", run: "click" },
        { content: "Abgelehnt: Prüfziffer der PZN", trigger: ".o_grx_pk [role=alert]:contains(Prüfziffer)" },
        { content: "Nichts erfasst", trigger: count(0) },
        { content: "PZN richtig", trigger: "#grx_pk_code", run: "edit 12345678" },
        { content: "Zurück in das Feld Seriennummer", trigger: "#grx_pk_serial", run: "click" },
        { content: "Enter in der Seriennummer übernimmt", trigger: "#grx_pk_serial", run: "press Enter" },
        { content: "Packung von Hand erfasst", trigger: count(1) },
        { content: "In der Liste als Handeingabe markiert", trigger: ".o_grx_table tr:contains(MAN1) .o_grx_tag:contains(Hand)" },
        { content: "Maske bleibt offen für die nächste Packung", trigger: ".o_grx_pk #grx_pk_serial" },
        { content: "Scanner tippt in das Seriennummer-Feld", trigger: "#grx_pk_serial", run: `edit ${SCAN_IN_FIELD}` },
        { content: "Enter: wird als Scan verarbeitet", trigger: "#grx_pk_serial", run: "press Enter" },
        { content: "Gescannte Packung angekommen", trigger: count(2) },
        { content: "Gescannte Packung ohne Hand-Markierung", trigger: ".o_grx_table tr:contains(SCANIN1):not(:has(.o_grx_tag))" },
        { content: "Handeingabe schließen", trigger: ".o_grx_pk .o_grx_btn_ghost", run: "click" },
        { content: "Packung entfernen", trigger: ".o_grx_table tr:contains(MAN1) .o_grx_rm", run: "click" },
        { content: "Sicherheitsabfrage: behalten", trigger: ".modal button:contains(Nein, behalten)", run: "click" },
        { content: "Nichts entfernt", trigger: `${count(2)}` },
        { content: "Dialog geschlossen", trigger: "body:not(:has(.modal))" },
        { content: "Nochmal entfernen", trigger: ".o_grx_table tr:contains(MAN1) .o_grx_rm", run: "click" },
        { content: "Sicherheitsabfrage nennt die Seriennummer", trigger: ".modal:contains(MAN1)" },
        { content: "Ja, entfernen", trigger: ".modal button:contains(Ja, entfernen)", run: "click" },
        { content: "Packung entfernt", trigger: count(1) },
        { content: "Nur noch die gescannte Packung", trigger: ".o_grx_table:not(:has(tr:contains(MAN1)))" },
    ],
});
