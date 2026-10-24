"""Geschäftsführungs-Dashboard: Berechnung, Zeitfilter, fehlende Daten, Rechte, Mandantentrennung, Performance."""
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.blistercenter.tests.test_blistercenter import BlistercenterCommon

MODEL = "plcs.management.dashboard"


@tagged("post_install", "-at_install", "plcs_dashboard")
class TestPlcsDashboard(BlistercenterCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        def user(login, groups):
            return new_test_user(cls.env, login=login, groups=groups, company_id=cls.company.id,
                                 company_ids=[Command.set(cls.company.ids)])

        common = ("sales_team.group_sale_salesman_all_leads,stock.group_stock_manager,account.group_account_readonly,"
                  "blistercenter.group_bc_user")
        cls.gf_finance = user("gf_finanzen", "plcs_dashboard.group_dashboard_finance," + common)
        cls.gf_ops = user("gf_operativ", "plcs_dashboard.group_dashboard_user," + common)
        cls.no_dashboard = user("ohne_dashboard", common)
        cls.service = cls.env["product.product"].create({
            "name": "Dienstleistung (Test)", "type": "service", "list_price": 100.0, "taxes_id": [Command.clear()],
            "invoice_policy": "order",
        })
        cls.today = fields.Date.today()

    # Hilfsfunktionen ------------------------------------------------------
    def _invoice(self, partner, amount, days_ago=0, refund=False, company=None):
        invoice = self.env["account.move"].with_company(company or self.company).create({
            "move_type": "out_refund" if refund else "out_invoice",
            "partner_id": partner.id,
            "invoice_date": self.today - timedelta(days=days_ago),
            "invoice_line_ids": [Command.create({
                "name": "Position", "quantity": 1, "price_unit": amount, "tax_ids": [Command.clear()]})],
        })
        invoice.action_post()
        return invoice

    def _service_order(self, partner, amount=100.0):
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [Command.create({"product_id": self.service.id, "product_uom_qty": 1, "price_unit": amount})],
        })
        order.action_confirm()
        return order

    def _data(self, user, period="30d", **kwargs):
        return self.env[MODEL].with_user(user).get_dashboard_data(period, **kwargs)

    def _kpi(self, data, key):
        return next(kpi for kpi in data["kpis"] if kpi["key"] == key)

    # Berechnungen -----------------------------------------------------------
    def test_revenue_and_orders(self):
        before = self._data(self.gf_finance)
        self._invoice(self.customer_invoice, 1000.0, days_ago=2)
        self._invoice(self.customer_other, 500.0, days_ago=5)
        self._invoice(self.customer_invoice, 200.0, days_ago=3, refund=True)
        self._invoice(self.customer_invoice, 300.0, days_ago=45)  # Vergleichszeitraum
        self._invoice(self.customer_invoice, 999.0, days_ago=200)  # außerhalb
        self._service_order(self.customer_other, 100.0)
        self._service_order(self.customer_other, 300.0)
        data = self._data(self.gf_finance)
        revenue = self._kpi(data, "revenue")
        self.assertTrue(revenue["available"])
        self.assertAlmostEqual(revenue["value"] - self._kpi(before, "revenue")["value"], 1300.0)
        self.assertAlmostEqual(revenue["previous"] - self._kpi(before, "revenue")["previous"], 300.0)
        self.assertAlmostEqual(self._kpi(data, "revenue_bc")["value"] - self._kpi(before, "revenue_bc")["value"], 800.0)
        orders = self._kpi(data, "order_count")
        self.assertEqual(orders["value"] - self._kpi(before, "order_count")["value"], 2)
        intake = self._kpi(data, "order_intake")["value"] - self._kpi(before, "order_intake")["value"]
        self.assertAlmostEqual(intake, 400.0)
        avg = self._kpi(data, "avg_order_value")
        self.assertAlmostEqual(avg["value"], self._kpi(data, "order_intake")["value"] / orders["value"])
        segments = next(c for c in data["charts"] if c["key"] == "revenue_segments")
        self.assertAlmostEqual(sum(segments["datasets"][0]["data"]), revenue["value"])
        trend = next(c for c in data["charts"] if c["key"] == "revenue_trend")
        self.assertEqual(len(trend["labels"]), 30)
        self.assertAlmostEqual(sum(trend["datasets"][0]["data"]), revenue["value"])

    def test_drill_down_matches_kpi(self):
        self._invoice(self.customer_invoice, 1000.0, days_ago=1)
        self._order(self.customer_invoice, qty=2)
        data = self._data(self.gf_finance)
        Dashboard = self.env[MODEL].with_user(self.gf_finance)
        for key, model in (("revenue", "account.move"), ("order_count", "sale.order"), ("open_orders", "sale.order")):
            action = Dashboard.action_open_kpi(key, "30d")
            self.assertEqual(action["res_model"], model)
            if key != "revenue":
                self.assertEqual(self.env[model].search_count(action["domain"]), self._kpi(data, key)["value"])
        moves = self.env["account.move"].search(Dashboard.action_open_kpi("revenue", "30d")["domain"])
        self.assertAlmostEqual(sum(moves.mapped("amount_untaxed_signed")), self._kpi(data, "revenue")["value"])
        with self.assertRaises(UserError):
            Dashboard.action_open_kpi("revenue_greenrx", "30d")
        with self.assertRaises(AccessError, msg="Finanzliste nur mit Rolle Finanzen"):
            self.env[MODEL].with_user(self.gf_ops).action_open_kpi("revenue", "30d")

    def test_stock_and_quality(self):
        self._stock(5, "CH-ABGELAUFEN", days=-3)
        self._stock(4, "CH-20", days=20)
        self._stock(3, "CH-45", days=45)
        self._stock(2, "CH-80", days=80)
        self._stock(1, "CH-400", days=400)
        blocked = self._stock(6, "CH-GESPERRT", days=500)
        blocked.write({"bc_blocked": True, "bc_block_reason": "Rückruf"})
        data = self._data(self.gf_ops)
        self.assertEqual(self._kpi(data, "expired")["value"], 1)
        self.assertEqual(self._kpi(data, "expiry_30")["value"], 1)
        self.assertEqual(self._kpi(data, "expiry_60")["value"], 1)
        self.assertEqual(self._kpi(data, "expiry_90")["value"], 1)
        self.assertEqual(self._kpi(data, "expiry_30")["breakdown"][0]["value"], 4)
        self.assertEqual(self._kpi(data, "blocked_lots")["value"], 1)
        self.assertGreaterEqual(self._kpi(data, "products_in_stock")["value"], 1)
        # Quarantäne und offene VP-Freigabe aus einem Blistercenter-Wareneingang
        order = self._order(self.customer_invoice, qty=2)
        _receipt, release = self._receive(self._collect(order), "CH-Q-DASH")
        data = self._data(self.gf_ops)
        self.assertEqual(self._kpi(data, "open_vp")["value"], 1)
        self.assertEqual(self._kpi(data, "quarantine")["breakdown"][0]["value"], 1)
        release.with_user(self.user_approver).button_validate()
        data = self._data(self.gf_ops)
        self.assertEqual(self._kpi(data, "open_vp")["value"], 0)
        self.assertEqual(self._kpi(data, "auto_released")["value"], 0)
        self.assertEqual(self._kpi(data, "auto_released")["breakdown"][0]["value"], 1, "durch die VP")
        releases = next(c for c in data["charts"] if c["key"] == "releases")
        self.assertEqual(releases["datasets"][0]["data"], [0, 1])

    def test_missing_data_marked_unavailable(self):
        data = self._data(self.gf_finance)
        for key in ("revenue_greenrx", "complaints", "low_stock"):
            kpi = self._kpi(data, key)
            self.assertFalse(kpi["available"], key)
            self.assertIsNone(kpi["value"], "Kein Platzhalterwert")
            self.assertTrue(kpi["reason"])
        self.env["stock.warehouse.orderpoint"].create({
            "product_id": self.product.id, "warehouse_id": self.warehouse.id,
            "location_id": self.stock_location.id, "product_min_qty": 10, "product_max_qty": 20,
        })
        low = self._kpi(self._data(self.gf_finance), "low_stock")
        self.assertTrue(low["available"])
        self.assertEqual(low["value"], 1)

    def test_periods_and_validation(self):
        Dashboard = self.env[MODEL].with_user(self.gf_ops)
        data = Dashboard.get_dashboard_data("7d")
        self.assertEqual(data["date_to"], fields.Date.to_string(self.today))
        self.assertEqual(data["date_from"], fields.Date.to_string(self.today - timedelta(days=6)))
        self.assertEqual(data["prev_to"], fields.Date.to_string(self.today - timedelta(days=7)))
        self.assertEqual(data["prev_from"], fields.Date.to_string(self.today - timedelta(days=13)))
        today = Dashboard.get_dashboard_data("today")
        self.assertEqual(today["date_from"], today["date_to"])
        custom = Dashboard.get_dashboard_data("custom", date_from="2026-01-01", date_to="2026-03-31")
        self.assertEqual(custom["prev_to"], "2025-12-31")
        self.assertEqual(custom["prev_from"], "2025-10-03", "90 Tage vor dem 01.01.2026")
        for args in (("custom", "2026-03-01", "2026-01-01"), ("custom", None, "2026-01-01"),
                     ("custom", "2020-01-01", "2026-01-01"), ("jahr", None, None)):
            with self.assertRaises(UserError):
                Dashboard.get_dashboard_data(args[0], date_from=args[1], date_to=args[2])

    def test_permissions(self):
        with self.assertRaises(AccessError):
            self._data(self.no_dashboard)
        with self.assertRaises(AccessError):
            self.env[MODEL].with_user(self.user_bc).get_dashboard_data("30d")
        ops = self._data(self.gf_ops)
        self.assertFalse(ops["finance"])
        for key in ("revenue", "order_intake", "avg_order_value", "revenue_bc", "stock_value"):
            kpi = self._kpi(ops, key)
            self.assertFalse(kpi["available"], key)
            self.assertIsNone(kpi["value"])
        self.assertFalse(next(c for c in ops["charts"] if c["key"] == "revenue_trend")["available"])
        self.assertTrue(self._kpi(ops, "order_count")["available"], "Anzahl ist keine Finanzkennzahl")
        # Finanzrolle ohne Lager-Administrator: Bestandswert nicht lesbar
        self.gf_finance.group_ids -= self.env.ref("stock.group_stock_manager")
        self.env.invalidate_all()
        stock_value = self._kpi(self._data(self.gf_finance), "stock_value")
        self.assertFalse(stock_value["available"])
        self.assertIn("berechtigung", stock_value["reason"].lower())
        # Ohne Blistercenter-Rolle: Quarantäne „keine Berechtigung“, nicht „nicht eingerichtet“
        gf_plain = new_test_user(
            self.env, login="gf_ohne_bc", company_id=self.company.id, company_ids=[Command.set(self.company.ids)],
            groups="plcs_dashboard.group_dashboard_user,stock.group_stock_user")
        quarantine = self._kpi(self._data(gf_plain), "quarantine")
        self.assertFalse(quarantine["available"])
        self.assertIn("berechtigung", quarantine["reason"].lower())

    def test_company_separation(self):
        other = self.setup_other_company(name="ATIV (Test)")["company"]
        self.env.user.company_ids |= other
        self.gf_finance.company_ids |= other
        partner = self.env["res.partner"].create({"name": "Fremdkunde", "company_id": other.id})
        before = self._kpi(self._data(self.gf_finance), "revenue")["value"]
        self._invoice(partner, 5000.0, days_ago=1, company=other)
        self.assertAlmostEqual(self._kpi(self._data(self.gf_finance), "revenue")["value"], before,
                               msg="Nur Daten der aktiven Firma")
        in_other = self.env[MODEL].with_user(self.gf_finance).with_company(other).get_dashboard_data("30d")
        self.assertAlmostEqual(self._kpi(in_other, "revenue")["value"], 5000.0)
        self.assertEqual(in_other["company"], "ATIV (Test)")

    def test_queries_do_not_grow_with_data(self):
        """Aggregation in der Datenbank: mehr Belege, gleich viele Abfragen (Toleranz für Cache-Effekte)."""
        for i in range(10):
            self._invoice(self.customer_other, 10.0 + i, days_ago=i)
        self._data(self.gf_finance)
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        self._data(self.gf_finance)
        small = self.cr.sql_log_count - start
        for i in range(60):
            self._invoice(self.customer_other, 20.0 + i, days_ago=i % 25)
        self.env.invalidate_all()
        start = self.cr.sql_log_count
        data = self._data(self.gf_finance)
        large = self.cr.sql_log_count - start
        self.assertTrue(self._kpi(data, "revenue")["available"])
        self.assertLessEqual(large, small + 5, f"{small} → {large} Abfragen")


@tagged("post_install", "-at_install", "plcs_dashboard")
class TestPlcsDashboardTour(HttpCase):

    def test_dashboard_tour(self):
        partner = self.env["res.partner"].create({"name": "Tour-Kunde"})
        invoice = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": partner.id, "invoice_date": fields.Date.today(),
            "invoice_line_ids": [Command.create({"name": "Position", "quantity": 1, "price_unit": 1000.0,
                                                 "tax_ids": [Command.clear()]})],
        })
        invoice.action_post()
        self.start_tour("/odoo/action-plcs_dashboard.action_management_dashboard", "plcs_dashboard_tour",
                        login="admin")
