import { registry } from "@web/core/registry";

// 1.10.0: „Erfassung abbrechen“ in Schritt 2 (Vorgang ohne Box).
registry.category("web_tour.tours").add("plcs_greenrx_abort_capture", {
    steps: () => [
        {
            content: "Vorgang in Erfassung aus 'Meine Aufgaben' öffnen",
            trigger: ".o_grx_task:contains(V-ABORT-1)",
            run: "click",
        },
        {
            content: "Schritt 2 ohne Box: Knopf sichtbar, Sicherheitsabfrage bestätigen",
            trigger: ".o_grx_actions .o_grx_abort",
            run: () => {
                window.confirm = () => true;
                document.querySelector(".o_grx_actions .o_grx_abort").click();
            },
        },
        {
            content: "Zurück auf 'Meine Aufgaben', Vorgang nicht mehr in Erfassung",
            trigger: ".o_grx_muted:contains(Kein Paket in Erfassung)",
        },
    ],
});
