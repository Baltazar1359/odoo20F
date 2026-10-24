import { registry } from "@web/core/registry";

/** Dashboard lädt, Zeitfilter wirkt, „nicht verfügbar“ ist gekennzeichnet, Klick öffnet die Datensätze. */
registry.category("web_tour.tours").add("plcs_dashboard_tour", {
    steps: () => [
        { content: "Bereiche geladen", trigger: ".o_plcs_dashboard .o_plcs_section[data-section='quality']" },
        { content: "Umsatz (Finanzen) mit Wert", trigger: ".o_plcs_kpi.o_plcs_clickable[data-kpi='revenue'] .o_plcs_kpi_value" },
        { content: "GreenRX-Umsatz als nicht verfügbar", trigger: ".o_plcs_kpi[data-kpi='revenue_greenrx'].o_plcs_unavailable" },
        { content: "Diagramm gezeichnet", trigger: ".o_plcs_chart[data-chart='revenue_trend'] canvas" },
        { content: "Zeitraum 7 Tage", trigger: ".o_plcs_filters button[data-period='7d']", run: "click" },
        { content: "7 Tage aktiv", trigger: ".o_plcs_filters button[data-period='7d'].btn-primary" },
        { content: "Freier Zeitraum", trigger: ".o_plcs_filters button[data-period='custom']", run: "click" },
        { content: "Datumsfelder sichtbar", trigger: ".o_plcs_filters input.o_plcs_date_from" },
        { content: "Zurück auf 30 Tage", trigger: ".o_plcs_filters button[data-period='30d']", run: "click" },
        { content: "Umsatz wieder geladen", trigger: ".o_plcs_filters button[data-period='30d'].btn-primary" },
        { content: "Kennzahl anklicken", trigger: ".o_plcs_kpi.o_plcs_clickable[data-kpi='revenue']", run: "click" },
        { content: "Rechnungsliste geöffnet", trigger: ".o_list_view .o_data_row" },
    ],
});
