"""Geschäftsführungs-Dashboard PLCS: Kennzahlen aus den Standarddaten von Odoo.

Grundsätze
* Nur echte Daten: jede Kennzahl wird aus Odoo-Datensätzen berechnet. Fehlt die Datengrundlage,
  liefert die Kennzahl „nicht verfügbar“ mit Begründung statt eines Platzhalterwerts.
* Kein sudo(): alle Abfragen laufen mit den Rechten des angemeldeten Benutzers. Fehlt ihm ein
  Leserecht, erscheint die Kennzahl als „keine Berechtigung“.
* Nur die aktive Firma (PLCS): jede Abfrage ist auf env.company beschränkt.
* Aggregation in der Datenbank (_read_group); Python-Schleifen nur über kleine Mengen.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

PERIODS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
MAX_CUSTOM_DAYS = 731
OPEN_PICKING_STATES = ("waiting", "confirmed", "assigned")
SECTIONS = [
    ("business", "Umsatz und Geschäft"),
    ("operations", "Aufträge und operative Prozesse"),
    ("stock", "Lager und Warenbestand"),
    ("quality", "Qualitätsmanagement und GDP"),
]


class Unavailable(Exception):
    """Kennzahl ohne verlässliche Datengrundlage (Begründung als Text)."""


class PlcsManagementDashboard(models.AbstractModel):
    _name = "plcs.management.dashboard"
    _description = "PLCS Geschäftsführungs-Dashboard"

    # ------------------------------------------------------------------
    # Öffentliche Aufrufe (Client)
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, period="30d", date_from=None, date_to=None):
        self._check_dashboard_access()
        rng = self._period_range(period, date_from, date_to)
        drill_keys = set(self._drill_definitions(rng))
        kpis = [self._run_kpi(key, label, section, unit, finance, compute, rng, key in drill_keys)
                for key, label, section, unit, finance, compute in self._kpi_definitions()]
        charts = [self._run_chart(key, title, section, kind, finance, compute, rng)
                  for key, title, section, kind, finance, compute in self._chart_definitions()]
        company = self.env.company
        return {
            "company": company.name,
            "currency_id": company.currency_id.id,
            "period": period,
            "date_from": fields.Date.to_string(rng["start"]),
            "date_to": fields.Date.to_string(rng["end"]),
            "prev_from": fields.Date.to_string(rng["prev_start"]),
            "prev_to": fields.Date.to_string(rng["prev_end"]),
            "finance": self._has_finance(),
            "sections": [{"key": key, "label": self.env._(label)} for key, label in SECTIONS],
            "kpis": kpis,
            "charts": charts,
        }

    @api.model
    def action_open_kpi(self, key, period="30d", date_from=None, date_to=None):
        """Die Datensätze hinter einer Kennzahl öffnen (gleiche Domain wie die Berechnung)."""
        self._check_dashboard_access()
        rng = self._period_range(period, date_from, date_to)
        drills = self._drill_definitions(rng)
        if key not in drills:
            raise UserError(self.env._("Zu dieser Kennzahl gibt es keine Detailliste."))
        model, domain, name, finance = drills[key]
        if finance and not self._has_finance():
            raise AccessError(self.env._("Finanzkennzahlen nur für die Rolle „Dashboard: Finanzen“."))
        self.env[model].check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "domain": domain,
            "views": [[False, "list"], [False, "form"]],
            "target": "current",
            "context": {"create": False},
        }

    # ------------------------------------------------------------------
    # Zugriff, Zeitraum, Firma
    # ------------------------------------------------------------------
    def _check_dashboard_access(self):
        if not self.env.user.has_group("plcs_dashboard.group_dashboard_user"):
            raise AccessError(self.env._("Das Dashboard ist nur für die Geschäftsführung freigegeben."))

    def _has_finance(self):
        return self.env.user.has_group("plcs_dashboard.group_dashboard_finance")

    def _company_id(self):
        return self.env.company.id

    def _period_range(self, period, date_from=None, date_to=None):
        today = fields.Date.context_today(self)
        if period == "custom":
            start, end = fields.Date.to_date(date_from), fields.Date.to_date(date_to)
            if not start or not end or start > end:
                raise UserError(self.env._("Bitte einen gültigen Zeitraum wählen (Von ≤ Bis)."))
            if (end - start).days + 1 > MAX_CUSTOM_DAYS:
                raise UserError(self.env._("Der Zeitraum darf höchstens zwei Jahre umfassen."))
        elif period in PERIODS:
            end = today
            start = today - timedelta(days=PERIODS[period] - 1)
        else:
            raise UserError(self.env._("Unbekannter Zeitraum."))
        days = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)
        return {
            "start": start, "end": end, "prev_start": prev_start, "prev_end": prev_end, "days": days,
            "dt": self._dt_bounds(start, end), "prev_dt": self._dt_bounds(prev_start, prev_end),
            "now": fields.Datetime.now(), "today": today,
        }

    def _dt_bounds(self, start, end):
        """Datumsgrenzen in der Zeitzone des Benutzers als UTC-Zeitpunkte [von, bis)."""
        tz = ZoneInfo(self.env.user.tz or "Europe/Berlin")

        def to_utc(day):
            return datetime.combine(day, time.min, tzinfo=tz).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        return to_utc(start), to_utc(end + timedelta(days=1))

    # ------------------------------------------------------------------
    # Ausführung mit Berechtigungs- und Verfügbarkeitsprüfung
    # ------------------------------------------------------------------
    def _run_kpi(self, key, label, section, unit, finance, compute, rng, drill):
        result = {
            "key": key, "label": self.env._(label), "section": section, "unit": unit,
            "value": None, "previous": None, "available": False, "reason": False,
            "breakdown": [], "drill": drill,
        }
        if finance and not self._has_finance():
            result.update(reason=self.env._("Nur für die Rolle „Dashboard: Finanzen“."), drill=False)
            return result
        try:
            values = compute(rng)
        except Unavailable as reason:
            result.update(reason=str(reason), drill=False)
            return result
        except AccessError:
            result.update(reason=self.env._("Keine Leseberechtigung für die zugrunde liegenden Daten."), drill=False)
            return result
        result.update(values, available=True)
        return result

    def _run_chart(self, key, title, section, kind, finance, compute, rng):
        result = {"key": key, "title": self.env._(title), "section": section, "type": kind, "available": False,
                  "reason": False, "labels": [], "datasets": []}
        if finance and not self._has_finance():
            result["reason"] = self.env._("Nur für die Rolle „Dashboard: Finanzen“.")
            return result
        try:
            result.update(compute(rng), available=True)
        except Unavailable as reason:
            result["reason"] = str(reason)
        except AccessError:
            result["reason"] = self.env._("Keine Leseberechtigung für die zugrunde liegenden Daten.")
        return result

    # ------------------------------------------------------------------
    # Domains (gemeinsam für Berechnung und Detailliste)
    # ------------------------------------------------------------------
    def _invoice_domain(self, start, end):
        return [("company_id", "=", self._company_id()), ("move_type", "in", ("out_invoice", "out_refund")),
                ("state", "=", "posted"), ("invoice_date", ">=", start), ("invoice_date", "<=", end)]

    def _order_domain(self, dt):
        return [("company_id", "=", self._company_id()), ("state", "=", "sale"),
                ("date_order", ">=", dt[0]), ("date_order", "<", dt[1])]

    def _open_orders_domain(self):
        return [("company_id", "=", self._company_id()), ("state", "=", "sale"),
                ("delivery_status", "in", ("pending", "started", "partial"))]

    def _overdue_domain(self, rng):
        return [("company_id", "=", self._company_id()), ("state", "in", OPEN_PICKING_STATES),
                ("scheduled_date", "<", rng["now"])]

    def _internal_quant_domain(self):
        return [("company_id", "=", self._company_id()), ("location_id.usage", "=", "internal"),
                ("quantity", ">", 0)]

    def _expiry_domain(self, rng, days_from, days_to):
        """Bestand mit Verfall im Band [heute + von, heute + bis); None = offen."""
        domain = self._internal_quant_domain() + [("lot_id.expiration_date", "!=", False)]
        now = rng["now"]
        if days_from is not None:
            domain.append(("lot_id.expiration_date", ">=", now + timedelta(days=days_from)))
        if days_to is not None:
            domain.append(("lot_id.expiration_date", "<", now + timedelta(days=days_to)))
        return domain

    def _quarantine_location(self):
        if not self.env["blistercenter.config"].has_access("read"):
            return self.env["stock.location"]
        config = self.env["blistercenter.config"].search([("company_id", "=", self._company_id())], limit=1)
        return config.quarantine_location_id

    def _quarantine_domain(self):
        if not self.env["blistercenter.config"].has_access("read"):
            raise AccessError(self.env._("Blistercenter-Einstellungen nicht lesbar."))
        location = self._quarantine_location()
        if not location:
            raise Unavailable(self.env._("Blistercenter-Quarantäne ist für diese Firma nicht eingerichtet."))
        return [("company_id", "=", self._company_id()), ("location_id", "child_of", location.id),
                ("quantity", ">", 0)]

    def _open_release_domain(self):
        return [("company_id", "=", self._company_id()), ("bc_kind", "=", "release"),
                ("state", "not in", ("done", "cancel", "draft"))]

    def _released_domain(self, dt, mode):
        return [("company_id", "=", self._company_id()), ("bc_kind", "=", "release"), ("state", "=", "done"),
                ("bc_release_mode", "=", mode), ("date_done", ">=", dt[0]), ("date_done", "<", dt[1])]

    def _receipt_deviation_domain(self, dt):
        return [("company_id", "=", self._company_id()), ("bc_kind", "=", "receipt"),
                ("bc_check_state", "=", "deviation"),
                ("bc_checked_date", ">=", dt[0]), ("bc_checked_date", "<", dt[1])]

    def _blocked_receipt_domain(self):
        return self._open_release_domain() + [("bc_receipt_id.bc_check_state", "=", "deviation")]

    def _open_return_picking_domain(self):
        return [("company_id", "=", self._company_id()), ("return_id", "!=", False),
                ("picking_type_code", "=", "incoming"), ("state", "not in", ("done", "cancel"))]

    def _drill_definitions(self, rng):
        """Kennzahl → (Modell, Domain, Titel, Finanzkennzahl)."""
        _ = self.env._
        drills = {
            "revenue": ("account.move", self._invoice_domain(rng["start"], rng["end"]), _("Umsatz: Rechnungen und Gutschriften"), True),
            "revenue_bc": ("account.move", self._invoice_domain(rng["start"], rng["end"])
                           + [("commercial_partner_id.bc_is_customer", "=", True)], _("Umsatz Blistercenter"), True),
            "order_intake": ("sale.order", self._order_domain(rng["dt"]), _("Auftragseingang"), True),
            "order_count": ("sale.order", self._order_domain(rng["dt"]), _("Aufträge im Zeitraum"), False),
            "open_orders": ("sale.order", self._open_orders_domain(), _("Offene Aufträge"), False),
            "overdue": ("stock.picking", self._overdue_domain(rng), _("Überfällige Lagervorgänge"), False),
            "stock_value": ("stock.quant", self._internal_quant_domain(), _("Bestand (intern)"), True),
            "products_in_stock": ("stock.quant", self._internal_quant_domain(), _("Bestand (intern)"), False),
            "expired": ("stock.quant", self._expiry_domain(rng, None, 0), _("Abgelaufene Ware im Bestand"), False),
            "expiry_30": ("stock.quant", self._expiry_domain(rng, 0, 30), _("MHD in 0–30 Tagen"), False),
            "expiry_60": ("stock.quant", self._expiry_domain(rng, 30, 60), _("MHD in 31–60 Tagen"), False),
            "expiry_90": ("stock.quant", self._expiry_domain(rng, 60, 90), _("MHD in 61–90 Tagen"), False),
            "blocked_lots": ("stock.quant", self._internal_quant_domain() + [("lot_id.bc_blocked", "=", True)],
                             _("Gesperrte Chargen mit Bestand"), False),
            "open_vp": ("stock.picking", self._open_release_domain(), _("Offene VP-Freigaben (Blistercenter)"), False),
            "auto_released": ("stock.picking", self._released_domain(rng["dt"], "auto"),
                              _("Automatisch freigegebene Wareneingänge"), False),
            "vp_released": ("stock.picking", self._released_domain(rng["dt"], "vp"), _("Durch die VP freigegeben"), False),
            "receipt_deviation": ("stock.picking", self._receipt_deviation_domain(rng["dt"]),
                                  _("Wareneingänge mit Abweichung"), False),
            "blocked_receipts": ("stock.picking", self._blocked_receipt_domain(),
                                 _("Gesperrte Wareneingänge (Abweichung, in Quarantäne)"), False),
        }
        location = self._quarantine_location()
        if location:
            drills["quarantine"] = ("stock.quant", self._quarantine_domain(), _("Ware in der Quarantäne"), False)
        return drills

    # ------------------------------------------------------------------
    # Hilfen
    # ------------------------------------------------------------------
    def _sum(self, model, domain, field):
        [(total,)] = self.env[model]._read_group(domain, [], [f"{field}:sum"])
        return total or 0.0

    def _count(self, model, domain):
        return self.env[model].search_count(domain)

    def _greenrx(self, model):
        """GreenRX-Modell, wenn das Modul installiert ist und der Benutzer lesen darf (sonst None)."""
        if model not in self.env or not self.env[model].has_access("read"):
            return None
        return self.env[model]

    def _greenrx_company(self, model):
        return [("company_id", "=", self._company_id())] if "company_id" in self.env[model]._fields else []

    def _order_amount(self, domain):
        """Summe der Netto-Auftragswerte in Firmenwährung (umgerechnet zum Tageskurs)."""
        company = self.env.company
        total = 0.0
        for currency, amount in self.env["sale.order"]._read_group(domain, ["currency_id"], ["amount_untaxed:sum"]):
            total += currency._convert(amount or 0.0, company.currency_id, company, fields.Date.context_today(self))
        return total

    # ------------------------------------------------------------------
    # Kennzahlen
    # ------------------------------------------------------------------
    def _kpi_definitions(self):
        """(Schlüssel, Bezeichnung, Bereich, Einheit, Finanzkennzahl, Berechnung)."""
        return [
            ("revenue", "Umsatz (netto, fakturiert)", "business", "monetary", True, self._kpi_revenue),
            ("order_intake", "Auftragseingang (netto)", "business", "monetary", True, self._kpi_order_intake),
            ("order_count", "Aufträge", "business", "count", False, self._kpi_order_count),
            ("avg_order_value", "Ø Auftragswert", "business", "monetary", True, self._kpi_avg_order_value),
            ("revenue_bc", "Umsatz Blistercenter", "business", "monetary", True, self._kpi_revenue_bc),
            ("revenue_greenrx", "Umsatz GreenRX", "business", "monetary", True, self._kpi_revenue_greenrx),
            ("open_orders", "Offene Aufträge", "operations", "count", False, self._kpi_open_orders),
            ("completed_orders", "Abgeschlossene Aufträge", "operations", "count", False, self._kpi_completed_orders),
            ("overdue", "Überfällige Lagervorgänge", "operations", "count", False, self._kpi_overdue),
            ("processing_days", "Ø Bearbeitungsdauer (Auftrag → Versand)", "operations", "days", False,
             self._kpi_processing_days),
            ("stock_value", "Bestandswert", "stock", "monetary", True, self._kpi_stock_value),
            ("products_in_stock", "Artikel mit Bestand", "stock", "count", False, self._kpi_products_in_stock),
            ("low_stock", "Artikel unter Meldebestand", "stock", "count", False, self._kpi_low_stock),
            ("expired", "Abgelaufene Chargen im Bestand", "stock", "count", False,
             lambda rng: self._kpi_expiry(rng, None, 0)),
            ("expiry_30", "MHD in 0–30 Tagen", "stock", "count", False, lambda rng: self._kpi_expiry(rng, 0, 30)),
            ("expiry_60", "MHD in 31–60 Tagen", "stock", "count", False, lambda rng: self._kpi_expiry(rng, 30, 60)),
            ("expiry_90", "MHD in 61–90 Tagen", "stock", "count", False, lambda rng: self._kpi_expiry(rng, 60, 90)),
            ("quarantine", "Ware in Quarantäne / gesperrt", "stock", "count", False, self._kpi_quarantine),
            ("blocked_lots", "Gesperrte Chargen mit Bestand", "stock", "count", False, self._kpi_blocked_lots),
            ("open_vp", "Offene VP-Freigaben", "quality", "count", False, self._kpi_open_vp),
            ("auto_released", "Automatisch freigegebene Wareneingänge", "quality", "count", False,
             self._kpi_auto_released),
            ("blocked_receipts", "Gesperrte Wareneingänge", "quality", "count", False, self._kpi_blocked_receipts),
            ("receipt_deviation", "Beschädigte Lieferungen / Qualitätsabweichungen", "quality", "count", False,
             self._kpi_receipt_deviation),
            ("open_returns", "Offene Retouren", "quality", "count", False, self._kpi_open_returns),
            ("complaints", "Offene Reklamationen", "quality", "count", False, self._kpi_complaints),
        ]

    def _kpi_revenue(self, rng):
        return {
            "value": self._sum("account.move", self._invoice_domain(rng["start"], rng["end"]), "amount_untaxed_signed"),
            "previous": self._sum("account.move", self._invoice_domain(rng["prev_start"], rng["prev_end"]),
                                  "amount_untaxed_signed"),
        }

    def _kpi_revenue_bc(self, rng):
        bc = [("commercial_partner_id.bc_is_customer", "=", True)]
        return {
            "value": self._sum("account.move", self._invoice_domain(rng["start"], rng["end"]) + bc,
                               "amount_untaxed_signed"),
            "previous": self._sum("account.move", self._invoice_domain(rng["prev_start"], rng["prev_end"]) + bc,
                                  "amount_untaxed_signed"),
        }

    def _kpi_revenue_greenrx(self, rng):
        raise Unavailable(self.env._(
            "GreenRX-Leistungen werden nicht in Odoo fakturiert. Die gespiegelten ATIV-Gutschriften sind "
            "Gutschriften von ATIV an Apotheken, kein PLCS-Umsatz, und werden deshalb nicht gezählt."))

    def _kpi_order_intake(self, rng):
        return {"value": self._order_amount(self._order_domain(rng["dt"])),
                "previous": self._order_amount(self._order_domain(rng["prev_dt"]))}

    def _kpi_order_count(self, rng):
        return {"value": self._count("sale.order", self._order_domain(rng["dt"])),
                "previous": self._count("sale.order", self._order_domain(rng["prev_dt"]))}

    def _kpi_avg_order_value(self, rng):
        def avg(dt):
            domain = self._order_domain(dt)
            count = self._count("sale.order", domain)
            return self._order_amount(domain) / count if count else None
        value = avg(rng["dt"])
        if value is None:
            raise Unavailable(self.env._("Keine bestätigten Aufträge im Zeitraum."))
        return {"value": value, "previous": avg(rng["prev_dt"])}

    def _kpi_open_orders(self, rng):
        return {"value": self._count("sale.order", self._open_orders_domain())}

    def _completed_order_ids(self, dt):
        """Aufträge, die im Zeitraum vollständig geliefert wurden (letzte Lieferung im Zeitraum)."""
        pickings = self.env["stock.picking"].search([
            ("company_id", "=", self._company_id()), ("picking_type_code", "=", "outgoing"), ("state", "=", "done"),
            ("sale_id", "!=", False), ("date_done", ">=", dt[0]), ("date_done", "<", dt[1]),
        ])
        orders = pickings.sale_id.filtered(lambda o: o.delivery_status == "full")
        result = []
        for order in orders:
            last = max(order.picking_ids.filtered(
                lambda p: p.picking_type_code == "outgoing" and p.state == "done").mapped("date_done"))
            if dt[0] <= last < dt[1]:
                result.append(order.id)
        return result

    def _kpi_completed_orders(self, rng):
        return {"value": len(self._completed_order_ids(rng["dt"])),
                "previous": len(self._completed_order_ids(rng["prev_dt"]))}

    def _kpi_overdue(self, rng):
        domain = self._overdue_domain(rng)
        Picking = self.env["stock.picking"]
        breakdown = [{"label": self.env._(label), "value": Picking.search_count(domain + [("picking_type_code", "=", code)])}
                     for code, label in (("incoming", "Eingänge"), ("outgoing", "Lieferungen"), ("internal", "Intern"))]
        return {"value": Picking.search_count(domain), "breakdown": breakdown}

    def _processing_days(self, dt):
        pickings = self.env["stock.picking"].search([
            ("company_id", "=", self._company_id()), ("picking_type_code", "=", "outgoing"), ("state", "=", "done"),
            ("sale_id", "!=", False), ("date_done", ">=", dt[0]), ("date_done", "<", dt[1]),
        ])
        durations = [(p.date_done - p.sale_id.date_order).total_seconds() / 86400 for p in pickings
                     if p.sale_id.date_order and p.date_done >= p.sale_id.date_order]
        return sum(durations) / len(durations) if durations else None

    def _kpi_processing_days(self, rng):
        value = self._processing_days(rng["dt"])
        if value is None:
            raise Unavailable(self.env._("Keine versendeten Aufträge im Zeitraum."))
        return {"value": value, "previous": self._processing_days(rng["prev_dt"])}

    def _kpi_stock_value(self, rng):
        # Das Feld „Wert“ der Bestände liest Odoo nur für Lager-Administratoren
        if not self.env.user.has_group("stock.group_stock_manager"):
            raise AccessError(self.env._("Bestandswert nur für Lager-Administratoren."))
        quants = self.env["stock.quant"].search(self._internal_quant_domain())
        return {"value": sum(quants.mapped("value"))}

    def _kpi_products_in_stock(self, rng):
        groups = self.env["stock.quant"]._read_group(self._internal_quant_domain(), ["product_id"], ["__count"])
        return {"value": len(groups)}

    def _kpi_low_stock(self, rng):
        orderpoints = self.env["stock.warehouse.orderpoint"].search([("company_id", "=", self._company_id())])
        if not orderpoints:
            raise Unavailable(self.env._("Keine Meldebestände gepflegt (Lager › Nachbestellregeln)."))
        below = orderpoints.filtered(
            lambda op: op.product_id.uom_id.compare(op.qty_on_hand, op.product_min_qty) < 0)
        return {"value": len(below)}

    def _kpi_expiry(self, rng, days_from, days_to):
        groups = self.env["stock.quant"]._read_group(
            self._expiry_domain(rng, days_from, days_to), ["lot_id"], ["quantity:sum"])
        return {"value": len(groups), "breakdown": [
            {"label": self.env._("Menge"), "value": sum(qty for _lot, qty in groups), "unit": "qty"}]}

    def _kpi_quarantine(self, rng):
        groups = self.env["stock.quant"]._read_group(self._quarantine_domain(), ["lot_id"], ["quantity:sum"])
        breakdown = [{"label": self.env._("Blistercenter-Quarantäne (Chargen)"), "value": len(groups)}]
        Box = self._greenrx("plcs.greenrx.box")
        if Box is not None:
            blocked = Box.search_count(self._greenrx_company("plcs.greenrx.box")
                                       + [("state", "=", "blocked"), ("archived", "=", False)])
            breakdown.append({"label": self.env._("GreenRX: gesperrte Boxen"), "value": blocked})
        return {"value": sum(item["value"] for item in breakdown), "breakdown": breakdown}

    def _kpi_blocked_lots(self, rng):
        groups = self.env["stock.quant"]._read_group(
            self._internal_quant_domain() + [("lot_id.bc_blocked", "=", True)], ["lot_id"], ["quantity:sum"])
        return {"value": len(groups)}

    def _kpi_open_vp(self, rng):
        breakdown = [{"label": self.env._("Blistercenter-Wareneingänge"),
                      "value": self._count("stock.picking", self._open_release_domain())}]
        Box = self._greenrx("plcs.greenrx.box")
        if Box is not None:
            breakdown.append({"label": self.env._("GreenRX-Boxen"), "value": Box.search_count(
                self._greenrx_company("plcs.greenrx.box") + [("state", "=", "to_release")])})
        return {"value": sum(item["value"] for item in breakdown), "breakdown": breakdown}

    def _kpi_auto_released(self, rng):
        auto = self._count("stock.picking", self._released_domain(rng["dt"], "auto"))
        manual = self._count("stock.picking", self._released_domain(rng["dt"], "vp"))
        share = round(100.0 * auto / (auto + manual)) if auto + manual else 0
        return {
            "value": auto,
            "previous": self._count("stock.picking", self._released_domain(rng["prev_dt"], "auto")),
            "breakdown": [{"label": self.env._("durch die VP"), "value": manual},
                          {"label": self.env._("Anteil automatisch (%)"), "value": share}],
        }

    def _kpi_blocked_receipts(self, rng):
        return {"value": self._count("stock.picking", self._blocked_receipt_domain())}

    def _kpi_receipt_deviation(self, rng):
        breakdown = [{"label": self.env._("Blistercenter-Wareneingänge"),
                      "value": self._count("stock.picking", self._receipt_deviation_domain(rng["dt"]))}]
        Box = self._greenrx("plcs.greenrx.box")
        if Box is not None:
            breakdown.append({"label": self.env._("GreenRX-Boxen gelb/rot"), "value": Box.search_count(
                self._greenrx_company("plcs.greenrx.box") + [
                    ("ampel", "in", ("gelb", "rot")),
                    ("checked_at", ">=", rng["dt"][0]), ("checked_at", "<", rng["dt"][1])])})
        result = {"value": sum(item["value"] for item in breakdown), "breakdown": breakdown}
        if Box is None:
            # Vergleich nur, wenn die Kennzahl aus einer Quelle stammt
            result["previous"] = self._count("stock.picking", self._receipt_deviation_domain(rng["prev_dt"]))
        return result

    def _kpi_open_returns(self, rng):
        breakdown = [{"label": self.env._("Kundenrücksendungen (Lager)"),
                      "value": self._count("stock.picking", self._open_return_picking_domain())}]
        Ticket = self._greenrx("plcs.greenrx.ticket")
        if Ticket is not None:
            breakdown.append({"label": self.env._("GreenRX-Retourenvorgänge"), "value": Ticket.search_count(
                self._greenrx_company("plcs.greenrx.ticket") + [("state", "in", ("open", "in_progress"))])})
        return {"value": sum(item["value"] for item in breakdown), "breakdown": breakdown}

    def _kpi_complaints(self, rng):
        raise Unavailable(self.env._(
            "Reklamationen werden in Odoo nicht als eigener Vorgang geführt. Dafür wäre ein "
            "Reklamations-/CAPA-Prozess (z. B. Modul Qualität) nötig."))

    # ------------------------------------------------------------------
    # Diagramme
    # ------------------------------------------------------------------
    def _chart_definitions(self):
        """(Schlüssel, Titel, Bereich, Diagrammart, Finanzkennzahl, Berechnung)."""
        return [
            ("revenue_trend", "Umsatzentwicklung", "business", "line", True, self._chart_revenue_trend),
            ("revenue_segments", "Umsatz nach Geschäftsbereich", "business", "doughnut", True,
             self._chart_revenue_segments),
            ("orders_by_state", "Blistercenter-Aufträge nach Bearbeitungsstatus", "operations", "bar", False,
             self._chart_orders_by_state),
            ("movements", "Warenbewegungen (gebuchte Lagerbewegungen)", "stock", "bar", False, self._chart_movements),
            ("releases", "Freigaben aus der Quarantäne", "quality", "doughnut", False, self._chart_releases),
        ]

    def _buckets(self, rng):
        """Zeitachse: Tage (bis 31 Tage), Wochen (bis 120 Tage), sonst 30-Tage-Abschnitte."""
        size = 1 if rng["days"] <= 31 else 7 if rng["days"] <= 120 else 30
        count = (rng["days"] + size - 1) // size
        labels = [(rng["start"] + timedelta(days=i * size)).strftime("%d.%m.") for i in range(count)]
        return size, count, labels

    @staticmethod
    def _as_date(value):
        return value.date() if isinstance(value, datetime) else value

    def _chart_revenue_trend(self, rng):
        size, count, labels = self._buckets(rng)

        def series(start, end):
            data = [0.0] * count
            for day, amount in self.env["account.move"]._read_group(
                    self._invoice_domain(start, end), ["invoice_date:day"], ["amount_untaxed_signed:sum"]):
                index = min((self._as_date(day) - start).days // size, count - 1)
                data[index] += amount or 0.0
            return data
        return {"labels": labels, "datasets": [
            {"label": self.env._("Zeitraum"), "data": series(rng["start"], rng["end"])},
            {"label": self.env._("Vergleichszeitraum"), "data": series(rng["prev_start"], rng["prev_end"])},
        ], "unit": "monetary"}

    def _chart_revenue_segments(self, rng):
        domain = self._invoice_domain(rng["start"], rng["end"])
        bc = self._sum("account.move", domain + [("commercial_partner_id.bc_is_customer", "=", True)],
                       "amount_untaxed_signed")
        other = self._sum("account.move", domain + [("commercial_partner_id.bc_is_customer", "!=", True)],
                          "amount_untaxed_signed")
        return {"labels": [self.env._("Blistercenter"), self.env._("Übrige Kunden")],
                "datasets": [{"label": self.env._("Umsatz"), "data": [bc, other]}],
                "unit": "monetary",
                "note": self.env._("GreenRX: nicht verfügbar (keine Fakturierung in Odoo).")}

    def _chart_orders_by_state(self, rng):
        orders = self.env["sale.order"].search([
            ("company_id", "=", self._company_id()), ("bc_is_blistercenter", "=", True),
            ("state", "in", ("draft", "sent", "sale")),
            "|", ("state", "!=", "sale"), ("delivery_status", "!=", "full"),
        ], limit=5000)
        counts = defaultdict(int)
        for order in orders:
            counts[order.bc_state] += 1
        states = [(key, label) for key, label in orders._fields["bc_state"].selection if key not in ("done", "cancel")]
        return {"labels": [self.env._(label) for _key, label in states],
                "datasets": [{"label": self.env._("Aufträge"), "data": [counts[key] for key, _label in states]}],
                "unit": "count"}

    def _chart_movements(self, rng):
        size, count, labels = self._buckets(rng)
        series = {code: [0] * count for code in ("incoming", "outgoing", "internal")}
        groups = self.env["stock.move"]._read_group([
            ("company_id", "=", self._company_id()), ("state", "=", "done"),
            ("date", ">=", rng["dt"][0]), ("date", "<", rng["dt"][1]),
        ], ["date:day", "picking_type_id"], ["__count"])
        for day, picking_type, number in groups:
            code = picking_type.code if picking_type.code in series else "internal"
            index = min((self._as_date(day) - rng["start"]).days // size, count - 1)
            series[code][index] += number
        names = {"incoming": self.env._("Eingänge"), "outgoing": self.env._("Ausgänge"), "internal": self.env._("Intern")}
        return {"labels": labels, "datasets": [{"label": names[code], "data": data} for code, data in series.items()],
                "unit": "count", "stacked": True}

    def _chart_releases(self, rng):
        auto = self._count("stock.picking", self._released_domain(rng["dt"], "auto"))
        manual = self._count("stock.picking", self._released_domain(rng["dt"], "vp"))
        if not auto + manual:
            raise Unavailable(self.env._("Keine Freigaben aus der Quarantäne im Zeitraum."))
        return {"labels": [self.env._("Automatisch (BC-AUTO-1)"), self.env._("Durch die VP")],
                "datasets": [{"label": self.env._("Freigaben"), "data": [auto, manual]}], "unit": "count"}
