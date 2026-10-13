"""Playwright-Prüfung: 5 Packungen direkt hintereinander scannen, alle 5 müssen ankommen.

Simuliert einen Handscanner im Tastaturmodus (Zeichen + Enter, "#" als GS-Trennzeichen)
in der Scan-Oberfläche GreenRX › Wareneingang scannen.

Voraussetzungen: laufendes Odoo mit installiertem plcs_greenrx, Benutzer in der Gruppe
"GreenRX › Lager (Erfassung)", `pip install playwright` und ein installiertes Chrome.

    python e2e/playwright_scan_queue.py --url http://127.0.0.1:8069 --db test_db --login admin

Das Passwort kommt aus der Umgebungsvariablen ODOO_PASSWORD. Das Skript legt einen eigenen
Testvorgang (V-PW-…) mit neuen Seriennummern an. Nur gegen Test-Datenbanken verwenden.
"""
import argparse
import os
import sys
import time

from playwright.sync_api import Error as PlaywrightError, sync_playwright

PZN = "04711003"
GTIN = "04150047110036"


def call(page, model, method, args=None, kwargs=None):
    return page.evaluate(
        """async ([model, method, args, kwargs]) => {
            const res = await fetch("/web/dataset/call_kw", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({jsonrpc: "2.0", method: "call",
                    params: {model, method, args, kwargs}}),
            });
            const data = await res.json();
            if (data.error) { throw new Error(data.error.data?.message || data.error.message); }
            return data.result;
        }""",
        [model, method, args or [], kwargs or {}],
    )


def prepare(page, run):
    """Testartikel, Vorgang mit 5 angemeldeten Packungen und eine leere Box anlegen."""
    if not call(page, "product.product", "search", [[["plcs_pzn", "=", PZN]]]):
        call(page, "product.product", "create", [{
            "name": "Queue-Testartikel", "is_storable": True, "tracking": "lot",
            "use_expiration_date": True, "barcode": GTIN, "plcs_pzn": PZN}])
    ticket = "V-PW-%s" % run
    res = call(page, "plcs.greenrx.ticket", "api_upsert", [[{
        "name": ticket, "partner_name": "Playwright-Apotheke",
        "lines": [{"ticket_number": "PW%s" % run, "pzn": PZN, "batch": "QLOT", "qty": 5}]}]])
    if res["errors"]:
        raise RuntimeError(res["errors"])
    ticket_id = call(page, "plcs.greenrx.ticket", "search", [[["name", "=", ticket]]])[0]
    for n in range(200, 0, -1):
        try:
            box = call(page, "plcs.greenrx.box", "grx_open_box", [ticket_id, "GX%03d" % n])
            return ticket, box["id"], box["box"]
        except PlaywrightError:  # Box belegt → nächste versuchen
            continue
    raise RuntimeError("Keine freie Box gefunden.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8069")
    parser.add_argument("--db", required=True)
    parser.add_argument("--login", default="admin")
    parser.add_argument("--headed", action="store_true")
    opts = parser.parse_args()
    password = os.environ.get("ODOO_PASSWORD")
    if not password:
        sys.exit("ODOO_PASSWORD ist nicht gesetzt.")
    run = time.strftime("%H%M%S")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=not opts.headed)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto("%s/web/login?db=%s" % (opts.url, opts.db))
        page.fill("input[name=login]", opts.login)
        page.fill("input[name=password]", password)
        page.click("button[type=submit]")
        page.wait_for_url("**/odoo**")

        ticket, box_id, box_name = prepare(page, run)
        print("Vorgang %s, Box %s" % (ticket, box_name))

        page.goto("%s/odoo/action-plcs_greenrx.action_grx_app" % opts.url)
        page.locator(".o_grx_task", has_text=ticket).click()
        page.wait_for_selector(".o_grx_count_num[data-qty='0']")

        # 5 Scans direkt hintereinander, ohne auf Antworten zu warten
        for n in range(1, 6):
            page.keyboard.type("01%s21PW%s%s#10QLOT#17301231" % (GTIN, run, n))
            page.keyboard.press("Enter")

        page.wait_for_selector(".o_grx_count_num[data-qty='5']", timeout=30000)
        page.wait_for_selector(".o_grx:not(:has(.o_grx_pending))", timeout=30000)
        log = page.locator(".o_grx_scanlog_item").count()
        ok = page.locator(".o_grx_scanlog_ok").count()
        server_qty = call(page, "plcs.greenrx.box", "read", [[box_id], ["qty"]])[0]["qty"]
        page.screenshot(path="playwright_scan_queue.png", full_page=True)
        browser.close()

    print("Anzeige: 5 Packungen · Rückmeldungen: %s (davon ok: %s) · Server: %s Packungen" % (log, ok, server_qty))
    if not (log == 5 and ok == 5 and server_qty == 5):
        sys.exit("FEHLER: nicht alle 5 Scans sind angekommen.")
    print("OK: alle 5 Scans angekommen.")


if __name__ == "__main__":
    main()
