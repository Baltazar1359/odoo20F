import { registry } from "@web/core/registry";

// Paket fertig, obwohl eine Box einer anderen Person noch offen ist (TestGreenrxPackageFinish)
registry.category("web_tour.tours").add("plcs_greenrx_finish_open_box", {
    steps: () => [
        {
            content: "Paket aus 'Meine Aufgaben' öffnen",
            trigger: ".o_grx_task:contains(V-FIN-OPEN)",
            run: "click",
        },
        {
            content: "Schritt 2: Box scannen",
            trigger: ".o_grx_wait",
        },
        {
            trigger: ".o_grx_actions button:contains(Paket fertig)",
            run: "click",
        },
        {
            content: "Meldung nennt die offene Box, den Schritt und die Person",
            trigger: ".o_grx_flash_error:contains(GX161):contains(Drucken):contains(Lager Fertig)",
        },
        {
            content: "Keine Begründung abfragen, solange eine Box offen ist",
            trigger: ".o_grx:not(:has(#grx_dev))",
        },
    ],
});

// Paket fertig in Schritt 2 mit Soll/Ist-Abweichung: Begründung muss sichtbar sein
registry.category("web_tour.tours").add("plcs_greenrx_finish_deviation", {
    steps: () => [
        {
            content: "Paket aus 'Meine Aufgaben' öffnen",
            trigger: ".o_grx_task:contains(V-FIN-DEV)",
            run: "click",
        },
        {
            content: "Schritt 2: Box scannen",
            trigger: ".o_grx_wait",
        },
        {
            trigger: ".o_grx_actions button:contains(Paket fertig)",
            run: "click",
        },
        {
            content: "Begründungsfeld erscheint in Schritt 2",
            trigger: ".o_grx_deviation:contains(1 zu viel) #grx_dev",
            run: "edit Eine Packung mehr im Paket als angemeldet",
        },
        {
            trigger: ".o_grx_deviation button:contains(Mit Begründung abschließen)",
            run: "click",
        },
        {
            content: "Paket gebucht, zurück auf 'Meine Aufgaben'",
            trigger: ".o_grx_scanbig",
        },
    ],
});
