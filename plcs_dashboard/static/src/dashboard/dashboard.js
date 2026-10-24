import { _t } from "@web/core/l10n/translation";
import { Component, onWillStart, proxy, t, useProps } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useChart } from "@web/core/utils/chart_hook";
import { formatFloat, formatMonetary } from "@web/views/fields/formatters";
import { ControlPanel } from "@web/search/control_panel/control_panel";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const MODEL = "plcs.management.dashboard";
const PERIODS = [
    ["today", _t("Heute")],
    ["7d", _t("7 Tage")],
    ["30d", _t("30 Tage")],
    ["90d", _t("90 Tage")],
    ["custom", _t("Zeitraum")],
];
// Kennzahlen, bei denen ein Anstieg schlecht ist (Farbe der Veränderung)
const BAD_WHEN_UP = new Set([
    "overdue", "processing_days", "expired", "expiry_30", "expiry_60", "expiry_90", "quarantine",
    "blocked_lots", "open_vp", "blocked_receipts", "receipt_deviation", "open_returns", "complaints",
    "low_stock", "open_orders",
]);
const PALETTE = ["#106e60", "#9aa5b1", "#e0a100", "#c0392b", "#3b7dd8", "#7d5ba6"];

function errorMessage(error) {
    return error?.data?.message || error?.message || String(error);
}

function formatDay(isoDate) {
    return isoDate ? luxon.DateTime.fromISO(isoDate).toFormat("dd.MM.yyyy") : "";
}

/** Ein Diagramm (Chart.js) aus den Daten des Servers. */
export class PlcsDashboardChart extends Component {
    static template = "plcs_dashboard.Chart";

    props = useProps({
        chart: t.object(),
        currencyId: t.number().optional(),
    });

    setup() {
        this.chartHook = useChart(() => this.config);
    }

    format(value) {
        const chart = this.props.chart;
        if (chart.unit === "monetary") {
            return formatMonetary(value, { currencyId: this.props.currencyId });
        }
        return formatFloat(value, { digits: [16, 0] });
    }

    get config() {
        const chart = this.props.chart;
        const round = chart.type === "doughnut";
        const datasets = chart.datasets.map((dataset, index) => ({
            label: dataset.label,
            data: dataset.data,
            backgroundColor: round ? PALETTE : PALETTE[index % PALETTE.length],
            borderColor: round ? "#ffffff" : PALETTE[index % PALETTE.length],
            borderDash: chart.type === "line" && index > 0 ? [6, 4] : undefined,
            tension: 0.25,
            fill: false,
        }));
        const label = (ctx) => {
            const value = typeof ctx.parsed === "object" ? ctx.parsed.y : ctx.parsed;
            return `${ctx.dataset.label}: ${this.format(value)}`;
        };
        return {
            type: chart.type,
            data: { labels: chart.labels, datasets },
            options: {
                animation: false,
                maintainAspectRatio: false,
                responsive: true,
                plugins: {
                    legend: { position: "bottom" },
                    tooltip: { callbacks: { label } },
                },
                scales: round
                    ? {}
                    : {
                          x: { stacked: Boolean(chart.stacked) },
                          y: {
                              stacked: Boolean(chart.stacked),
                              beginAtZero: true,
                              // Anzahlen nur in ganzen Schritten (sonst „0, 0, 1, 1“ bei leeren Daten)
                              ticks: {
                                  precision: chart.unit === "count" ? 0 : undefined,
                                  callback: (value) => this.format(value),
                              },
                          },
                      },
            },
        };
    }
}

/**
 * Geschäftsführungs-Dashboard: KPI-Karten und Diagramme je Bereich.
 * Alle Zahlen berechnet der Server (plcs.management.dashboard) mit den Rechten des Benutzers.
 */
export class PlcsManagementDashboard extends Component {
    static template = "plcs_dashboard.Dashboard";
    static components = { ControlPanel, PlcsDashboardChart };

    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.periods = PERIODS;
        this.state = proxy({
            period: "30d",
            dateFrom: "",
            dateTo: "",
            data: null,
            loading: false,
            error: false,
        });
        onWillStart(() => this.load());
    }

    get periodArgs() {
        const custom = this.state.period === "custom";
        return {
            period: this.state.period,
            date_from: custom ? this.state.dateFrom || null : null,
            date_to: custom ? this.state.dateTo || null : null,
        };
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(MODEL, "get_dashboard_data", [], this.periodArgs);
            this.state.error = false;
        } catch (error) {
            this.state.error = errorMessage(error);
        } finally {
            this.state.loading = false;
        }
    }

    setPeriod(period) {
        this.state.period = period;
        if (period !== "custom") {
            return this.load();
        }
        // Freier Zeitraum: mit dem aktuellen Zeitraum vorbelegen, Laden nach Änderung eines Datums
        this.state.dateFrom = this.state.dateFrom || this.state.data?.date_from || "";
        this.state.dateTo = this.state.dateTo || this.state.data?.date_to || "";
        return this.load();
    }

    setDate(field, value) {
        this.state[field] = value;
        if (this.state.dateFrom && this.state.dateTo) {
            return this.load();
        }
    }

    kpisOf(section) {
        return (this.state.data?.kpis || []).filter((kpi) => kpi.section === section);
    }

    chartsOf(section) {
        return (this.state.data?.charts || []).filter((chart) => chart.section === section);
    }

    formatDay(isoDate) {
        return formatDay(isoDate);
    }

    formatValue(value, unit) {
        if (value === null || value === undefined) {
            return "–";
        }
        switch (unit) {
            case "monetary":
                return formatMonetary(value, { currencyId: this.state.data.currency_id });
            case "days":
                return `${formatFloat(value, { digits: [16, 1] })} ${_t("Tage")}`;
            case "qty":
                return formatFloat(value, { digits: [16, 2] });
            default:
                return formatFloat(value, { digits: [16, 0] });
        }
    }

    delta(kpi) {
        if (!kpi.available || kpi.previous === null || kpi.previous === undefined) {
            return "";
        }
        if (!kpi.previous) {
            return kpi.value ? _t("neu ggü. Vorzeitraum") : _t("± 0 ggü. Vorzeitraum");
        }
        const pct = ((kpi.value - kpi.previous) / Math.abs(kpi.previous)) * 100;
        const sign = pct > 0 ? "+" : pct < 0 ? "−" : "±";
        return `${sign} ${formatFloat(Math.abs(pct), { digits: [16, 1] })} % ${_t("ggü. Vorzeitraum")}`;
    }

    deltaClass(kpi) {
        if (kpi.value === kpi.previous) {
            return "text-muted";
        }
        const up = kpi.value > kpi.previous;
        const good = BAD_WHEN_UP.has(kpi.key) ? !up : up;
        return good ? "text-success" : "text-danger";
    }

    async openKpi(kpi) {
        if (!kpi.available || !kpi.drill) {
            return;
        }
        const action = await this.orm.call(MODEL, "action_open_kpi", [kpi.key], this.periodArgs);
        return this.action.doAction(action);
    }
}

registry.category("actions").add("plcs_management_dashboard", PlcsManagementDashboard);
