"""Simuliertes GreenRX-ERP von ATIV (Odoo 19 Online) für die Tests der Schnittstelle.

Struktur wie in der ATIV-Testkopie geprüft (14.10.2026): Gutschrift = purchase.order mit Vorgang
x_vorgang, Positionen mit x_studio_ticket, Tickets helpdesk.ticket (ticket_ref, Charge = name),
Kundennummer = x_studio_vendor_number am Kontakt. Alle Daten sind erfunden.
"""
import base64
import copy
import hashlib

from ..tools.ativ_client import READ_METHODS, AtivError

T0 = "2026-10-01 10:00:00"


def _sha1(raw):
    return hashlib.sha1(raw).hexdigest()


class FakeAtiv:
    def __init__(self):
        self.calls = []
        self.fail = {}  # (model, method) -> AtivError, wird bei Aufruf ausgelöst
        self.clock = T0
        self.files = {}
        p = lambda i, n: [i, n]  # noqa: E731
        self.db = {
            "res.partner": {
                9: {"name": "Stern-Apotheke", "display_name": "Stern-Apotheke", "x_studio_vendor_number": 70105,
                    "ref": False, "street": "Hauptstr. 1", "street2": False, "zip": "50667", "city": "Köln",
                    "country_id": p(57, "Deutschland"), "email": "info@stern.example", "phone": "0221 1"},
                10: {"name": "Alliance Healthcare NL Meerane", "display_name": "Alliance Healthcare NL Meerane",
                     "x_studio_vendor_number": 70106, "ref": False, "street": "Ring 5", "street2": False,
                     "zip": "08393", "city": "Meerane", "country_id": p(57, "Deutschland"), "email": False,
                     "phone": False},
            },
            "product.product": {
                33: {"display_name": "[12345678] BYETTA 250", "name": "BYETTA 250", "default_code": "12345678",
                     "barcode": False, "x_studio_storage_conditions": p(2, "2–8 °C")},
                34: {"display_name": "[7560038] BRILIQUE 90 mg", "name": "BRILIQUE 90 mg", "default_code": "7560038",
                     "barcode": False, "x_studio_storage_conditions": p(1, "Raumtemperatur")},
            },
            "helpdesk.stage": {1: {"name": "New"}, 2: {"name": "In Progress"}, 4: {"name": "Approved"},
                               11: {"name": "Picked Up"}, 9: {"name": "Stock"}, 5: {"name": "Rejected"},
                               7: {"name": "Canceled"}},
            "x_vorgang": {},
            "helpdesk.ticket": {},
            "purchase.order": {},
            "purchase.order.line": {},
            "ir.attachment": {},
            "mail.message": {},
            "mail.tracking.value": {},
            "account.tax": {23: {"name": "19 % Vorsteuer"}},
        }
        self._seed()

    # ------------------------------------------------------------------
    def _seed(self):
        self.add_vorgang(100, "V900", "R-1", 9)
        self.add_vorgang(101, "V901", "R-2", 10)
        self.add_vorgang(102, "V902", False, 9)
        self.add_vorgang(103, "V903", "R-4", 9)
        self.add_vorgang(104, "V904", False, 10)
        self.add_ticket(501, "4711", "L-77", 100, 9, 33, 4, stage=4)
        self.add_ticket(502, "4712", "L-78", 100, 9, 34, 1, stage=11)
        self.add_ticket(503, "4713", "FPPV", 101, 10, 34, 2, stage=4)
        self.add_ticket(506, "4716", "FPPW", 101, 10, 33, 3, stage=11)
        self.add_ticket(504, "4714", "OLD1", 102, 9, 33, 5, stage=9)
        self.add_ticket(505, "4715", "REJ1", 103, 9, 33, 1, stage=5)
        self.add_ticket(507, "4717", "LEG1", 104, 10, 34, 6, stage=4)
        self.add_po(201, "20260001", 100, 9, [(301, 501, 33, 4), (302, 502, 34, 1)], ticket_1=501)
        self.add_po(202, "20260002", 101, 10, [(303, 503, 34, 2)], ticket_1=503)
        self.add_po(203, "20260003", 101, 10, [(304, 506, 33, 3)], ticket_1=506)
        self.add_po(204, "20260004", 102, 9, [(305, 504, 33, 5)], ticket_1=504, receipt_done=True, state="purchase")
        self.add_po(205, "20260005", 103, 9, [(306, 505, 33, 1)], ticket_1=505)
        # ältere Gutschrift: Position ohne Ticketverweis, Ticket nur am Kopf
        self.add_po(206, "20260006", 104, 10, [(307, False, 34, 6)], ticket_1=507)
        self.add_attachment(801, "helpdesk.ticket", 501, "Lieferschein 4711.pdf", "application/pdf", b"%PDF-1.4 LS")
        self.add_attachment(802, "helpdesk.ticket", 501, "Foto1.jpeg", "image/jpeg", b"\xff\xd8\xff JPEG")
        self.add_attachment(803, "purchase.order", 201, "Gutschrift_20260001.pdf", "application/pdf", b"%PDF GS")
        self.add_attachment(804, "helpdesk.ticket", 504, "Alt.pdf", "application/pdf", b"%PDF alt")
        self.add_message(901, 501, "comment", "Discussions", "<p>Charge korrigiert</p>", author="Stern-Apotheke")
        self.add_message(902, 501, "notification", "Stage Changed", "", tracking=[(951, "Neu", "Genehmigt")],
                         author="Carolina Strack")

    def add_vorgang(self, vid, name, complaint, partner):
        self.db["x_vorgang"][vid] = {"x_name": name, "x_complaint_number": complaint,
                                     "x_partner_id": [partner, self.db["res.partner"][partner]["name"]],
                                     "x_source": "portal", "create_date": T0, "write_date": T0}

    def add_ticket(self, tid, ref, batch, vorgang, partner, product, qty, stage):
        self.db["helpdesk.ticket"][tid] = {
            "name": batch, "ticket_ref": ref, "description": "<p>Retoure %s</p>" % ref,
            "partner_id": [partner, self.db["res.partner"][partner]["name"]],
            "partner_name": self.db["res.partner"][partner]["name"], "partner_email": "t@example.test",
            "partner_phone": False, "user_id": [6, "ATIV Service GmbH"], "create_uid": [1, "OdooBot"],
            "create_date": T0, "write_date": T0, "close_date": False,
            "stage_id": [stage, self.db["helpdesk.stage"][stage]["name"]],
            "x_studio_vorgang": [vorgang, self.db["x_vorgang"][vorgang]["x_name"]],
            "x_studio_complaint_number": self.db["x_vorgang"][vorgang]["x_complaint_number"],
            "x_studio_batch_number": batch,
            "x_studio_product": [product, self.db["product.product"][product]["display_name"]],
            "x_studio_pzn": self.db["product.product"][product]["default_code"],
            "x_studio_product_number": qty, "x_studio_product_price": 12.5,
            "x_studio_expiration_date": "2028-02-29",
            "x_studio_reason_for_product_return": [3, "3. Qualität"],
            "x_studio_confirmation_of_gdp_compliant_storage_1": "yes", "x_studio_rejection_reason": False,
            "x_studio_reason_for_rejection": False, "x_studio_abholadresse_kontaktadresse": True,
            "x_studio_contact_address_name": False, "x_studio_strae_hausnummer": "Hauptstr. 1",
            "x_studio_strae2": False, "x_studio_plz": "50667", "x_studio_stadt": "Köln",
            "x_studio_land": "Deutschland", "x_studio_storage_conditions": "2–8 °C",
            "x_studio_pick_up_done": stage == 11,
        }

    def add_po(self, pid, name, vorgang, partner, lines, ticket_1=False, receipt_done=False, state="draft"):
        line_ids = []
        for lid, tid, product, qty in lines:
            self.db["purchase.order.line"][lid] = {
                "order_id": [pid, name], "product_id": [product, self.db["product.product"][product]["display_name"]],
                "product_qty": qty, "write_date": T0, "name": self.db["product.product"][product]["display_name"],
                "sequence": 10, "display_type": False, "product_uom_id": [1, "Units"], "price_unit": 12.5,
                "tax_ids": [23], "price_subtotal": 12.5 * qty, "price_tax": 2.38 * qty, "price_total": 14.88 * qty,
                "x_studio_ticket": [tid, "x"] if tid else False, "x_studio_sellable_qty": 0,
                "x_studio_quarantine_qty": 0, "x_studio_blocked_qty": 0, "x_studio_destroyed_qty": 0,
                "x_studio_quarantne_grund": False,
            }
            line_ids.append(lid)
        self.db["purchase.order"][pid] = {
            "name": name, "partner_id": [partner, self.db["res.partner"][partner]["name"]], "state": state,
            "order_line": line_ids, "write_date": T0, "create_date": T0,
            "x_studio_vorgang": [vorgang, self.db["x_vorgang"][vorgang]["x_name"]],
            "x_studio_ticket_1": [ticket_1, "x"] if ticket_1 else False,
            "x_studio_complaint_number": self.db["x_vorgang"][vorgang]["x_complaint_number"],
            "x_studio_pick_up_done": False, "x_wareneingang_done": receipt_done, "date_order": "2026-09-29 09:19:58",
            "date_approve": False, "date_planned": "2026-10-15 08:00:00", "create_uid": [165, "Carolina Strack"],
            "user_id": [165, "Carolina Strack"], "amount_untaxed": 100.0, "amount_tax": 19.0, "amount_total": 119.0,
            "currency_id": [125, "EUR"], "receipt_status": False, "company_id": [1, "ATIV Service GmbH"],
            "x_studio_quarantne_grund": False,
        }

    def add_attachment(self, aid, res_model, res_id, name, mimetype, raw):
        self.files[aid] = raw
        self.db["ir.attachment"][aid] = {"name": name, "res_model": res_model, "res_id": res_id,
                                         "mimetype": mimetype, "file_size": len(raw), "checksum": _sha1(raw),
                                         "create_date": self.clock, "write_date": self.clock,
                                         "create_uid": [112, "Stern-Apotheke"], "type": "binary"}

    def add_message(self, mid, ticket, mtype, subtype, body, tracking=(), author="X"):
        tv_ids = []
        for tv_id, old, new in tracking:
            self.db["mail.tracking.value"][tv_id] = {"field_id": [11719, "Stage (Helpdesk Ticket)"],
                                                     "old_value_char": old, "new_value_char": new}
            tv_ids.append(tv_id)
        self.db["mail.message"][mid] = {"model": "helpdesk.ticket", "res_id": ticket, "date": self.clock,
                                        "write_date": self.clock, "author_id": [1, author],
                                        "message_type": mtype, "subtype_id": [1, subtype], "body": body,
                                        "attachment_ids": [], "tracking_value_ids": tv_ids, "email_from": False}

    def touch(self, model, rec_id, **vals):
        rec = self.db[model][rec_id]
        rec.update(vals)
        if "write_date" in rec:
            rec["write_date"] = self.clock

    # ------------------------------------------------------------------
    def _match(self, rec_id, rec, domain):
        stack = []
        for item in reversed(domain):
            if item == "|":
                a, b = stack.pop(), stack.pop()
                stack.append(a or b)
            elif item == "&":
                a, b = stack.pop(), stack.pop()
                stack.append(a and b)
            elif item == "!":
                stack.append(not stack.pop())
            else:
                fname, op, value = item
                current = rec_id if fname == "id" else rec.get(fname)
                if isinstance(current, list) and len(current) == 2 and isinstance(current[0], int) and \
                        fname not in ("order_line", "tracking_value_ids", "attachment_ids", "tax_ids"):
                    current = current[0]
                if op == "=":
                    res = current == value
                elif op == "!=":
                    res = current != value
                elif op == "in":
                    res = current in value
                elif op == "not in":
                    res = current not in value
                elif op == ">=":
                    res = bool(current) and str(current) >= str(value)
                elif op == ">":
                    res = bool(current) and str(current) > str(value)
                elif op == "<":
                    res = bool(current) and str(current) < str(value)
                else:
                    raise AssertionError("Operator %s nicht simuliert" % op)
                stack.append(res)
        return all(stack)

    def _search(self, model, domain, offset=0, limit=None, order=None):
        ids = sorted(i for i, r in self.db[model].items() if self._match(i, r, domain or []))
        ids = ids[offset or 0:]
        return ids[:limit] if limit else ids

    def call(self, model, method, **kw):
        assert method in READ_METHODS, (model, method)
        self.calls.append((model, method))
        err = self.fail.get((model, method))
        if err:
            raise err
        if model not in self.db:
            raise AtivError("Modell nicht gefunden: %s" % model, status=404)
        if method == "fields_get":
            names = set()
            for rec in self.db[model].values():
                names.update(rec)
            names.update({"id", "display_name", "write_date", "create_date"})
            info = {n: {"type": "char", "string": n} for n in names}
            if model == "purchase.order":
                info["state"]["selection"] = [["draft", "Angebotsanfrage"], ["purchase", "Bestellung"],
                                              ["cancel", "Abgebrochen"]]
            if model == "helpdesk.ticket":
                info["x_studio_confirmation_of_gdp_compliant_storage_1"]["selection"] = [["yes", "Ja"],
                                                                                         ["no", "Nein"]]
            return info
        if method == "search":
            return self._search(model, kw.get("domain"), kw.get("offset"), kw.get("limit"), kw.get("order"))
        if method == "search_count":
            return len(self._search(model, kw.get("domain")))
        if method in ("read", "search_read"):
            ids = kw.get("ids") if method == "read" else self._search(model, kw.get("domain"), kw.get("offset"),
                                                                      kw.get("limit"))
            fields_ = kw.get("fields") or []
            out = []
            for i in ids:
                rec = self.db[model].get(i)
                if rec is None:
                    continue
                row = {"id": i}
                for f in fields_:
                    if f == "datas" and model == "ir.attachment":
                        row[f] = base64.b64encode(self.files[i]).decode()
                    elif f == "name" and model == "helpdesk.stage":
                        lang = (kw.get("context") or {}).get("lang")
                        row[f] = {"Approved": "Genehmigt", "Picked Up": "Abgeholt"}.get(rec["name"], rec["name"]) \
                            if lang == "de_DE" else rec["name"]
                    else:
                        row[f] = copy.deepcopy(rec.get(f, False))
                out.append(row)
            return out
        raise AssertionError((model, method))


class FakeClient:
    """Ersatz für AtivClient (gleiche Schnittstelle call())."""

    def __init__(self, fake):
        self.fake = fake

    def call(self, model, method, **kw):
        return self.fake.call(model, method, **kw)
