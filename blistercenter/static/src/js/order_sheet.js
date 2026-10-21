/** Online-Bestellschein: Summe vor dem Absenden, Knopf nur einmal (Prüfbericht 21.10.2026). */
(function () {
    "use strict";

    function setup(form) {
        const inputs = form.querySelectorAll("input[data-bc-price]");
        const qtyOut = form.querySelector(".o_bc_sum_qty");
        const netOut = form.querySelector(".o_bc_sum_net");
        const currency = form.dataset.bcCurrency || "EUR";
        const discount = parseFloat(form.dataset.bcDiscount || "0") || 0;
        let fmt;
        try {
            fmt = new Intl.NumberFormat("de-DE", { style: "currency", currency: currency });
        } catch (e) {
            fmt = { format: (v) => v.toFixed(2) + " " + currency };
        }
        function update() {
            let qty = 0;
            let net = 0;
            inputs.forEach((input) => {
                const n = parseInt(input.value, 10);
                if (n > 0) {
                    qty += n;
                    // wie Odoo: Zeilensumme = Menge × HAP × (1 − Rabatt), je Zeile gerundet
                    net += Math.round(n * parseFloat(input.dataset.bcPrice) * (1 - discount / 100) * 100) / 100;
                }
            });
            if (qtyOut) {
                qtyOut.textContent = String(qty);
            }
            if (netOut) {
                netOut.textContent = fmt.format(net);
            }
        }
        inputs.forEach((input) => input.addEventListener("input", update));
        update();
        form.addEventListener("submit", () => {
            const button = form.querySelector("button[type=submit]");
            if (button) {
                // Erst nach dem Absenden sperren, damit der Browser das Formular noch sendet
                setTimeout(() => {
                    button.disabled = true;
                }, 0);
            }
        });
    }

    // Zurück-Taste des Browsers zeigt die Seite aus dem Zwischenspeicher: Knopf wieder freigeben
    window.addEventListener("pageshow", () => {
        document.querySelectorAll("form.o_bc_order_sheet_form button[type=submit]").forEach((button) => {
            button.disabled = false;
        });
    });

    function init() {
        document.querySelectorAll("form.o_bc_order_sheet_form").forEach(setup);
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
