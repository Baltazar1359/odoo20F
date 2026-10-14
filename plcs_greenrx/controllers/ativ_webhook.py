"""Webhook-Empfänger für ATIV (Odoo Online, Server-Aktion „Webhook senden“).

ATIV sendet bei einer Änderung ``{"_model": ..., "_id": ..., ...}`` per POST mit 1 Sekunde Zeitlimit.
Der Empfänger prüft nur den Schlüssel und merkt die Änderung vor. Die Daten selbst liest PLCS danach
über die API, weil der Webhook weder Positionen noch Dateien vollständig liefern kann.
"""
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

MAX_BODY = 256 * 1024


class AtivWebhook(http.Controller):

    @http.route("/plcs_greenrx/ativ/webhook", type="http", auth="public", methods=["POST"], csrf=False,
                save_session=False)
    def ativ_webhook(self, **kw):
        token = request.httprequest.args.get("token") or request.httprequest.headers.get("X-PLCS-Token")
        connector = request.env["plcs.greenrx.connector"].sudo()._ativ_webhook_connector(token)
        if not connector:
            return request.make_json_response({"ok": False, "error": "unauthorized"}, status=401)
        raw = request.httprequest.get_data(cache=False, as_text=False)[:MAX_BODY + 1]
        if len(raw) > MAX_BODY:
            return request.make_json_response({"ok": False, "error": "too large"}, status=413)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return request.make_json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return request.make_json_response({"ok": False, "error": "invalid json"}, status=400)
        accepted = connector._ativ_webhook_receive(payload)
        return request.make_json_response({"ok": True, "queued": bool(accepted)})
