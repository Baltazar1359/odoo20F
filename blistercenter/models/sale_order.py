from markupsafe import Markup

from odoo.http import request
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import is_html_empty

from ..tools import bc_auth

BC_STATES = [
    ("new", "Eingegangen"),
    ("blocked", "Zahlungssperre"),
    ("to_procure", "Zu beschaffen"),
    ("procuring", "In Beschaffung"),
    ("ready", "Lieferbereit"),
    ("partial", "Teilweise geliefert"),
    ("done", "Geliefert"),
    ("cancel", "Storniert"),
]

ORDER_CHANNELS = [
    ("shop", "Shop"),
    ("phone", "Telefon"),
    ("email", "E-Mail"),
    ("fax", "Fax"),
    ("other", "Sonstiges"),
]
# Bestellwege, bei denen der Besteller Pflicht ist (keine Anmeldung im Shop)
CHANNELS_WITH_ORDERER = ("phone", "email", "fax", "other")
# Nachweis der Teillieferung nur über den Assistenten (Prüfbericht 19.10.2026 R07)
ORDER_PROTECTED_FIELDS = ("bc_partial_allowed", "bc_partial_note", "bc_partial_by", "bc_partial_date")
LINE_PROTECTED_FIELDS = ("bc_from_stock", "bc_from_stock_by")


class SaleOrder(models.Model):
    _inherit = "sale.order"

    bc_is_blistercenter = fields.Boolean(
        "Blistercenter-Bestellung", compute="_compute_bc_is_blistercenter", store=True, index=True,
        help="Wird beim Anlegen und bis zur Bestätigung aus dem Kunden übernommen und danach festgehalten. "
             "Eine spätere Änderung am Kunden hebt die Regeln laufender Bestellungen nicht auf.",
    )
    bc_payment_model = fields.Selection(related="partner_id.commercial_partner_id.bc_payment_model", string="Zahlungsart")
    bc_discount = fields.Selection(related="partner_id.commercial_partner_id.bc_discount", string="Rabatt auf HAP")
    bc_payment_block = fields.Boolean(related="partner_id.commercial_partner_id.bc_payment_block", string="Zahlungssperre")
    bc_payment_block_reason = fields.Char(related="partner_id.commercial_partner_id.bc_payment_block_reason", string="Grund der Zahlungssperre")
    bc_shop_enabled = fields.Boolean(related="partner_id.commercial_partner_id.bc_shop_enabled", string="Shop freigeschaltet")
    bc_overdue_amount = fields.Monetary(
        "Überfällig beim Kunden", compute="_compute_bc_overdue", currency_field="currency_id",
    )
    bc_overdue_count = fields.Integer("Überfällige Rechnungen", compute="_compute_bc_overdue")
    bc_state = fields.Selection(BC_STATES, string="Bearbeitungsstatus", compute="_compute_bc_state")
    bc_collective_ids = fields.Many2many(
        "blistercenter.collective.order", string="Sammelbestellungen", compute="_compute_bc_collective_ids",
    )
    bc_collective_count = fields.Integer(compute="_compute_bc_collective_ids")

    # Bestellweg (Shop, Telefon, ...) und Besteller
    bc_channel = fields.Selection(
        ORDER_CHANNELS, string="Bestellweg", tracking=True, copy=False,
        help="Wie die Bestellung bei PLCS eingegangen ist. Shop-Bestellungen werden automatisch gekennzeichnet.",
    )
    bc_commercial_partner_id = fields.Many2one(related="partner_id.commercial_partner_id", string="Firma des Kunden")
    bc_orderer_id = fields.Many2one(
        "res.partner", string="Besteller (Kontakt)", tracking=True, copy=False,
        domain="[('commercial_partner_id', '=', bc_commercial_partner_id), ('id', '!=', bc_commercial_partner_id)]",
        help="Ansprechpartner des Blistercenters, der bestellt hat.",
    )
    bc_orderer_name = fields.Char(
        "Besteller (Name)", tracking=True, copy=False,
        help="Name des Bestellers, wenn er nicht als Kontakt angelegt ist.",
    )
    bc_received_by = fields.Many2one("res.users", "Angenommen von", tracking=True, copy=False)
    bc_received_date = fields.Datetime("Angenommen am", tracking=True, copy=False)

    # Teillieferung nur nach Rücksprache
    bc_partial_allowed = fields.Boolean("Teillieferung erlaubt", readonly=True, tracking=True, copy=False)
    bc_partial_note = fields.Char("Vermerk Teillieferung", readonly=True, tracking=True, copy=False)
    bc_partial_by = fields.Many2one("res.users", "Teillieferung erlaubt von", readonly=True, copy=False)
    bc_partial_date = fields.Datetime("Teillieferung erlaubt am", readonly=True, copy=False)

    @api.depends("partner_id", "state")
    def _compute_bc_is_blistercenter(self):
        """Prüfbericht 19.10.2026 R04: Zuordnung bei bestätigten Aufträgen festhalten."""
        for order in self:
            if order.state in ("sale", "cancel") and order._origin.id and order.bc_is_blistercenter:
                continue
            order.bc_is_blistercenter = order.partner_id.commercial_partner_id.bc_is_customer

    @api.depends_context("uid")
    def _compute_bc_overdue(self):
        """Hinweis auf überfällige Rechnungen des Kunden. Sperrt nichts (Entscheidung PLCS 19.10.2026)."""
        for order in self:
            partner = order.partner_id.commercial_partner_id
            if order.bc_is_blistercenter and partner:
                order.bc_overdue_count = partner.bc_overdue_count
                order.bc_overdue_amount = partner.bc_overdue_amount
            else:
                order.bc_overdue_count = 0
                order.bc_overdue_amount = 0.0

    def _compute_bc_collective_ids(self):
        for order in self:
            collective = order.order_line.bc_collective_line_ids.order_id
            order.bc_collective_ids = collective
            order.bc_collective_count = len(collective)

    @api.depends("state", "delivery_status", "order_line.bc_qty_open")
    def _compute_bc_state(self):
        for order in self:
            if order.state == "cancel":
                state = "cancel"
            elif order.state in ("draft", "sent"):
                state = "new"
            elif order.bc_payment_block:
                state = "blocked"
            elif order.delivery_status == "full":
                state = "done"
            elif order.delivery_status in ("partial", "started"):
                state = "partial"
            elif any(
                p.state == "assigned" and p.picking_type_code == "outgoing" for p in order.picking_ids
            ):
                state = "ready"
            elif any(line.bc_qty_open > 0 for line in order.order_line):
                state = "to_procure"
            else:
                state = "procuring"
            order.bc_state = state

    # ------------------------------------------------------------------
    # Anlegen: Bestellweg, Komplettlieferung
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        Partner = self.env["res.partner"]
        for vals in vals_list:
            partner = Partner.browse(vals.get("partner_id")).commercial_partner_id
            bc_auth.check_protected(self, vals, ORDER_PROTECTED_FIELDS)
            if not partner.bc_is_customer:
                continue
            if vals.get("website_id") and not vals.get("bc_channel"):
                vals["bc_channel"] = "shop"
            # Standard bei Blistercentern: Komplettlieferung (Teillieferung nur nach Rücksprache)
            vals.setdefault("picking_policy", "one")
            channel = vals.get("bc_channel") or self.env.context.get("default_bc_channel")
            if channel and channel != "shop":
                vals.setdefault("bc_received_by", self.env.uid)
                vals.setdefault("bc_received_date", fields.Datetime.now())
        return super().create(vals_list)

    def write(self, vals):
        bc_auth.check_protected(self, vals, ORDER_PROTECTED_FIELDS)
        return super().write(vals)

    @api.onchange("partner_id")
    def _onchange_partner_id_bc(self):
        if self.partner_id.commercial_partner_id.bc_is_customer and self.state in ("draft", "sent"):
            self.picking_policy = "one"

    @api.onchange("bc_channel")
    def _onchange_bc_channel(self):
        if self.bc_channel and self.bc_channel != "shop" and not self.bc_received_by:
            self.bc_received_by = self.env.user
            self.bc_received_date = fields.Datetime.now()

    # ------------------------------------------------------------------
    # Prozessregeln
    # ------------------------------------------------------------------
    def _bc_mto_route(self):
        routes = self.env["stock.route"]
        route = self.env.ref("stock.route_warehouse0_mto", raise_if_not_found=False)
        if route:
            routes |= route
        routes |= self.env["stock.warehouse"].search([("company_id", "=", self.company_id.id)]).mto_pull_id.route_id
        return routes

    def _confirmation_error_message(self):
        message = super()._confirmation_error_message()
        if message:
            return message
        partner = self.partner_id.commercial_partner_id
        config = self.env["blistercenter.config"]._get(self.company_id)
        is_bc = self.bc_is_blistercenter or partner.bc_is_customer
        if is_bc and partner.bc_payment_block and config.rule_block_confirm:
            return self.env._(
                "%(customer)s hat eine Zahlungssperre (%(reason)s). Die Bestellung kann nicht bestätigt werden.",
                customer=partner.display_name, reason=partner.bc_payment_block_reason or "-",
            )
        if (self.website_id and config.rule_shop_requires_enable and not self._bc_loading_demo_data()
                and not (is_bc and partner.bc_shop_enabled)):
            return self.env._(
                "Shop-Bestellungen sind nur für freigeschaltete Blistercenter möglich (%(customer)s ist nicht freigeschaltet).",
                customer=partner.display_name,
            )
        if self.website_id and config.rule_shop_only_bc_products and not self._bc_loading_demo_data():
            outside = self.order_line.filtered(lambda sol: sol._bc_is_goods_line()).product_id.filtered(
                lambda p: not p.sudo().bc_shop_product)
            if outside:
                return self.env._(
                    "Im Shop gibt es nur das Blistercenter-Sortiment. Nicht im Shop: %(products)s.",
                    products=", ".join(outside.mapped("display_name")))
        if not is_bc:
            return False
        if config.rule_require_channel:
            channel = self.bc_channel or ("shop" if self.website_id else False)
            if not channel:
                return self.env._("%(order)s: Bitte den Bestellweg angeben (z. B. Telefon).", order=self.name)
            if channel in CHANNELS_WITH_ORDERER and not (self.bc_orderer_id or (self.bc_orderer_name or "").strip()):
                return self.env._(
                    "%(order)s: Bitte angeben, wer bestellt hat (Besteller als Kontakt oder Name).", order=self.name)
        if config.rule_require_payment_term and not self.payment_term_id:
            return self.env._(
                "%(order)s: Bitte am Blistercenter %(customer)s ein Zahlungsziel (z. B. 7 oder 14 Tage) hinterlegen "
                "und in der Bestellung übernehmen.", order=self.name, customer=partner.display_name)
        goods = self.order_line.filtered(lambda sol: sol._bc_is_goods_line())
        if config.rule_invoice_per_delivery:
            wrong = goods.product_id.filtered(lambda p: p.invoice_policy != "delivery")
            if wrong:
                return self.env._(
                    "Rechnung je Lieferung: %(products)s wird nach bestellter Menge fakturiert. Bitte am Artikel "
                    "„Fakturierung: gelieferte Mengen“ einstellen.",
                    products=", ".join(wrong.mapped("display_name")))
        if config.rule_customer_discount:
            expected = float(partner.bc_discount or 0)
            wrong = goods.filtered(lambda sol: sol.currency_id.compare_amounts(sol.discount, expected) != 0)
            if wrong:
                return self.env._(
                    "%(order)s: %(customer)s hat %(discount)s %% Rabatt auf den HAP. Abweichend in: %(products)s.",
                    order=self.name, customer=partner.display_name, discount=partner.bc_discount or "0",
                    products=", ".join(wrong.product_id.mapped("display_name")))
        if config.rule_forbid_mto_route:
            mto = self._bc_mto_route()
            for line in self.order_line.filtered(lambda sol: sol.product_id and not sol.display_type):
                product = line.product_id
                routes = line.route_ids | product.route_ids | product.categ_id.total_route_ids
                if routes & mto:
                    return self.env._(
                        "%(product)s hat die Route „Auf Auftrag beschaffen“. Bei Blistercentern bestellt PLCS über "
                        "Sammelbestellungen; bitte die Route am Artikel entfernen.",
                        product=product.display_name,
                    )
        return False

    def _bc_loading_demo_data(self):
        """Odoo lädt Demodaten (Installation mit Demodaten, „odoo-bin module force-demo“ auf Odoo.sh).

        Die Shop-Regel gilt dann nicht für Odoos eigene Demo-Shopbestellungen, sonst bricht das
        Laden der Demodaten ab. Nur ohne HTTP-Anfrage und im Superuser-Modus erkannt – über
        Web/RPC lässt sich das nicht auslösen, auch nicht mit einem Kontextwert.
        """
        return bool(self.env.context.get("install_demo") and self.env.su and not request)

    def _cart_add(self, product_id, quantity=1.0, **kwargs):
        self._bc_check_shop_access()
        self._bc_check_shop_product(product_id)
        return super()._cart_add(product_id, quantity, **kwargs)

    def _bc_check_shop_product(self, product_id):
        """Regel R40: in den Warenkorb nur Artikel des Blistercenter-Sortiments."""
        product = self.env["product.product"].sudo().browse(product_id).exists()
        for order in self:
            config = self.env["blistercenter.config"]._get(order.company_id)
            if config.rule_shop_only_bc_products and product and not product.bc_shop_product:
                raise UserError(self.env._(
                    "%s gibt es nicht im Blistercenter-Shop. Bitte wenden Sie sich an PLCS.", product.display_name))

    def _bc_check_shop_access(self):
        for order in self:
            config = self.env["blistercenter.config"]._get(order.company_id)
            if not config.rule_shop_requires_enable:
                continue
            partner = order.partner_id.commercial_partner_id
            if not (partner.bc_is_customer and partner.bc_shop_enabled):
                raise UserError(self.env._(
                    "Der Shop ist nur für freigeschaltete Blistercenter. Bitte melden Sie sich an "
                    "oder wenden Sie sich an PLCS für die Freischaltung."
                ))
            if partner.bc_payment_block and config.rule_block_confirm:
                raise UserError(self.env._(
                    "Für Ihr Kundenkonto ist eine Zahlungssperre hinterlegt. Bitte wenden Sie sich an PLCS."
                ))

    def _action_confirm(self):
        for order in self.filtered(lambda o: o.bc_is_blistercenter and not o.bc_channel and o.website_id):
            order.bc_channel = "shop"
        res = super()._action_confirm()
        for order in self.filtered("bc_is_blistercenter"):
            config = self.env["blistercenter.config"]._get(order.company_id)
            if config.notify_new_order and config.sales_user_id:
                # Aufgabe ohne E-Mail-Benachrichtigung (mail_activity_quick_update)
                order.sudo().with_context(mail_activity_quick_update=True).activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=self.env._("Neue Blistercenter-Bestellung bearbeiten"),
                    user_id=config.sales_user_id.id,
                )
        return res

    # ------------------------------------------------------------------
    # Proforma und Rechnung je Lieferung
    # ------------------------------------------------------------------
    def action_bc_print_proforma(self):
        """Proforma-Rechnung drucken: nur Information über Menge und Preis, keine Buchung, kein Versand."""
        for order in self:
            if not order.bc_is_blistercenter or order.state != "sale":
                raise UserError(self.env._("Proforma nur für bestätigte Blistercenter-Bestellungen."))
        return self.env.ref("blistercenter.action_report_bc_proforma").report_action(self)

    def _bc_direct_debit_note(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        if not (self.bc_is_blistercenter and partner.bc_payment_model == "direct_debit"):
            return False
        return self.env._(
            "Zahlung per SEPA-Lastschrift: Der Rechnungsbetrag wird zum Fälligkeitsdatum von Ihrem Konto "
            "eingezogen. Bitte nicht überweisen.")

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        note = self._bc_direct_debit_note()
        if note:
            narration = vals.get("narration")
            text = Markup("<p><strong>%s</strong></p>") % note
            vals["narration"] = text if is_html_empty(narration) else Markup("%s%s") % (text, narration)
        return vals

    def _bc_invoice_delivery(self, picking):
        """Rechnung über die mit dieser Lieferung gelieferte Menge als Entwurf (kein Buchen, kein Versand)."""
        self.ensure_one()
        config = self.env["blistercenter.config"]._get(self.company_id)
        order = self.sudo()
        if not order.order_line.filtered(lambda sol: sol._bc_is_goods_line() and sol.qty_to_invoice > 0):
            return self.env["account.move"]
        # Ohne Zuweisungs-E-Mail an den Verkäufer; die Buchhaltung bekommt eine Aufgabe in Odoo
        invoice = order.with_context(mail_auto_subscribe_no_notify=True)._create_invoices()
        if not invoice:
            return invoice
        delivery_note = Markup("<p>%s</p>") % self.env._("Lieferung: %(picking)s", picking=picking.name)
        invoice.write({
            "bc_picking_id": picking.id,
            "narration": delivery_note if is_html_empty(invoice.narration) else Markup("%s%s") % (
                invoice.narration, delivery_note),
        })
        self.message_post(body=self.env._(
            "Rechnung %(invoice)s zur Lieferung %(picking)s als Entwurf angelegt.",
            invoice=invoice.display_name or "-", picking=picking.name))
        picking.message_post(body=self.env._(
            "Rechnung %(invoice)s als Entwurf angelegt.", invoice=invoice.display_name or "-"))
        if config.accounting_user_id:
            invoice.with_context(mail_activity_quick_update=True).activity_schedule(
                "mail.mail_activity_data_todo",
                summary=self.env._("Rechnung zur Lieferung %(picking)s prüfen, buchen und an %(customer)s senden",
                                   picking=picking.name,
                                   customer=self.partner_id.commercial_partner_id.display_name),
                user_id=config.accounting_user_id.id,
            )
        return invoice

    # ------------------------------------------------------------------
    # Teillieferung
    # ------------------------------------------------------------------
    def _bc_set_partial(self, allowed, note=False):
        for order in self:
            if allowed and not (note or "").strip():
                raise UserError(self.env._("Bitte vermerken, mit wem die Teillieferung abgestimmt ist."))
            vals = {
                "bc_partial_allowed": allowed,
                "bc_partial_note": note if allowed else False,
                "bc_partial_by": self.env.uid if allowed else False,
                "bc_partial_date": fields.Datetime.now() if allowed else False,
            }
            order.sudo().write(vals)
            pickings = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
            pickings.sudo().write({"move_type": "direct" if allowed else "one"})
            order.message_post(body=self.env._(
                "Teillieferung erlaubt: %(note)s", note=note) if allowed else self.env._("Teillieferung zurückgenommen."))
        return True

    def action_bc_allow_partial(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Teillieferung erlauben"),
            "res_model": "blistercenter.partial.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_order_id": self.id},
        }

    def action_bc_revoke_partial(self):
        return self._bc_set_partial(False)

    def action_bc_open_collective(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("blistercenter.action_bc_collective_orders")
        action["domain"] = [("id", "in", self.bc_collective_ids.ids)]
        return action


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    bc_is_blistercenter = fields.Boolean(related="order_id.bc_is_blistercenter", store=True, index=True)
    bc_collective_line_ids = fields.One2many(
        "blistercenter.collective.order.line", "sale_line_id", string="Sammelbestellungs-Positionen",
    )
    bc_qty_allocated = fields.Float(
        "In Sammelbestellung", compute="_compute_bc_qty", store=True, digits="Product Unit",
    )
    bc_qty_open = fields.Float(
        "Offen zu bündeln", compute="_compute_bc_qty", store=True, digits="Product Unit",
    )
    bc_from_stock = fields.Boolean(
        "Aus Lagerbestand", readonly=True, copy=False,
        help="Diese Position wird aus dem freien Lagerbestand geliefert, nicht über eine Sammelbestellung.",
    )
    bc_from_stock_by = fields.Many2one("res.users", "Aus Lagerbestand von", readonly=True, copy=False)
    bc_supplier_id = fields.Many2one(
        "res.partner", string="Hersteller", compute="_compute_bc_supplier_id", store=True, index="btree_not_null",
        help="Hersteller aus der Einkaufskondition des Artikels (für die Liste „Zu bestellen“).",
    )

    @api.depends("product_id.seller_ids.partner_id", "product_id.seller_ids.partner_id.bc_is_supplier")
    def _compute_bc_supplier_id(self):
        for line in self:
            sellers = line.product_id.seller_ids.partner_id if line.bc_is_blistercenter else False
            if not sellers:
                line.bc_supplier_id = False
                continue
            line.bc_supplier_id = sellers.filtered("bc_is_supplier")[:1] or sellers[:1]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            bc_auth.check_protected(self, vals, LINE_PROTECTED_FIELDS)
        return super().create(vals_list)

    def write(self, vals):
        bc_auth.check_protected(self, vals, LINE_PROTECTED_FIELDS)
        return super().write(vals)

    @api.constrains("product_uom_qty", "product_uom_id")
    def _check_bc_qty_allocated(self):
        """Prüfbericht 19.10.2026 R06: Auftragsmenge nie unter die Menge in Sammelbestellungen senken."""
        for line in self.filtered("bc_collective_line_ids"):
            allocated = sum(c.qty for c in line.bc_collective_line_ids if c.order_id.state != "cancel")
            if line.product_uom_id.compare(allocated, line.product_uom_qty) > 0:
                raise ValidationError(self.env._(
                    "%(order)s / %(product)s: In Sammelbestellungen stehen %(alloc)s, der Auftrag hätte nur "
                    "noch %(qty)s. Erst die Sammelbestellung anpassen.",
                    order=line.order_id.name, product=line.product_id.display_name,
                    alloc=allocated, qty=line.product_uom_qty))

    @api.depends("order_id.partner_id")
    def _compute_discount(self):
        """Blistercenter: Rabatt des Kunden (0, 1 oder 2 %) auf den HAP für jede Warenposition."""
        super()._compute_discount()
        for line in self:
            partner = line.order_id.partner_id.commercial_partner_id
            if not ((line.order_id.bc_is_blistercenter or partner.bc_is_customer) and line._bc_is_goods_line()):
                continue
            config = self.env["blistercenter.config"]._get(line.order_id.company_id or self.env.company)
            if config.rule_customer_discount:
                line.discount = float(partner.bc_discount or 0)

    def _bc_is_goods_line(self):
        self.ensure_one()
        return bool(
            self.product_id and self.product_id.type == "consu" and not self.display_type
            and not self.is_downpayment and not getattr(self, "is_delivery", False)
        )

    @api.depends(
        "product_uom_qty", "state", "product_id", "display_type", "bc_from_stock",
        "bc_collective_line_ids.qty", "bc_collective_line_ids.order_id.state",
    )
    def _compute_bc_qty(self):
        for line in self:
            allocated = sum(
                c.qty for c in line.bc_collective_line_ids if c.order_id.state != "cancel"
            )
            line.bc_qty_allocated = allocated
            if line.state == "sale" and line._bc_is_goods_line() and not line.bc_from_stock:
                line.bc_qty_open = max(line.product_uom_qty - allocated, 0.0)
            else:
                line.bc_qty_open = 0.0

    def _bc_wait_moves(self):
        """Offene Lieferbewegungen, die auf Ware aus einer Sammelbestellung warten."""
        return self.move_ids.filtered(lambda m: m.bc_wait_collective and m.state not in ("done", "cancel"))

    def action_bc_from_stock(self):
        """Position aus freiem Lagerbestand liefern statt über eine Sammelbestellung."""
        for line in self:
            if not line.bc_is_blistercenter or line.state != "sale" or not line._bc_is_goods_line():
                raise UserError(self.env._("Nur Warenpositionen bestätigter Blistercenter-Bestellungen."))
            if line.bc_from_stock:
                continue
            if line.bc_qty_allocated:
                raise UserError(self.env._(
                    "%(order)s / %(product)s ist schon in einer Sammelbestellung. "
                    "Aus Lagerbestand geht nur ohne Sammelbestellung.",
                    order=line.order_id.name, product=line.product_id.display_name))
            moves = line._bc_wait_moves()
            line.sudo().write({"bc_from_stock": True, "bc_from_stock_by": self.env.uid})
            moves.sudo().write({"procure_method": "make_to_stock", "bc_wait_collective": False})
            moves.sudo()._action_assign()
            line.order_id.message_post(body=self.env._(
                "%(product)s wird aus Lagerbestand geliefert (nicht über eine Sammelbestellung).",
                product=line.product_id.display_name))
        return True

    def action_bc_add_to_collective(self):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Zur Sammelbestellung"),
            "res_model": "blistercenter.collective.add",
            "view_mode": "form",
            "target": "new",
            "context": {"active_model": "sale.order.line", "active_ids": self.ids},
        }
