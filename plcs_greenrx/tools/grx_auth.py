"""Interne Freigaben für gesteuerte GreenRX-Buchungen.

Ein Kontextwert, der per RPC mitgeschickt wird, ist immer JSON (Text, Zahl, Liste, …).
Die Freigaben hier sind Python-Objekte. Ein Aufrufer von außen kann sie nicht erzeugen.
Damit steuert der Kontext nur den Ablauf; eine Berechtigung entsteht nur im Modulcode,
nachdem dort Rolle, 4-Augen-Prinzip und Inhalt geprüft wurden (Code-Review R01).
"""

CONTROLLED_MOVE_KEY = "plcs_grx_controlled_move"
FINISH_KEY = "plcs_grx_finish"


class ControlledMove:
    """Freigabe für genau eine Bewegung aus Quarantäne/Sperrlager (VP-Entscheidung)."""

    __slots__ = ("box_id", "lot_ids")

    def __init__(self, box_id, lot_ids):
        self.box_id = box_id
        self.lot_ids = frozenset(lot_ids)

    def __repr__(self):
        return "<GreenRX-Freigabe Box %s>" % self.box_id


class Finish:
    """Freigabe für das Buchen der Eingänge eines Vorgangs über „Paket fertig“."""

    __slots__ = ("ticket_id",)

    def __init__(self, ticket_id):
        self.ticket_id = ticket_id

    def __repr__(self):
        return "<GreenRX-Paket fertig %s>" % self.ticket_id


def controlled_move(env):
    """Gültige Bewegungsfreigabe aus dem Kontext oder None."""
    auth = env.context.get(CONTROLLED_MOVE_KEY)
    return auth if isinstance(auth, ControlledMove) else None


def finish(env):
    """Gültige „Paket fertig“-Freigabe aus dem Kontext oder None."""
    auth = env.context.get(FINISH_KEY)
    return auth if isinstance(auth, Finish) else None


# ----------------------------------------------------------------------
# Formblatt-PDF (1.9.0)
# ----------------------------------------------------------------------
PRINT_STAMP_KEY = "plcs_grx_print_stamp"
ARCHIVE_KEY = "plcs_grx_archive"


class PrintStamp:
    """Druckvermerk für genau einen gespeicherten Druck (Druck-Nr., Zeit, Benutzer).

    Nur der Modulcode in grx_print erzeugt ihn. Ein Aufruf von /report/pdf mit eigenem
    Kontext kann keinen gültigen Druckvermerk vortäuschen.
    """

    __slots__ = ("box_id", "print_no", "printed_at", "user_name")

    def __init__(self, box_id, print_no, printed_at, user_name):
        self.box_id = box_id
        self.print_no = print_no
        self.printed_at = printed_at
        self.user_name = user_name

    @property
    def reprint(self):
        return self.print_no > 1

    def __repr__(self):
        return "<GreenRX-Druck Box %s Nr. %s>" % (self.box_id, self.print_no)


class Archive:
    """Freigabe für das Archivieren einer Belegung (einziger Schreibzugriff auf archivierte Boxen)."""

    __slots__ = ("box_ids",)

    def __init__(self, box_ids):
        self.box_ids = frozenset(box_ids)

    def __repr__(self):
        return "<GreenRX-Archivierung %s>" % sorted(self.box_ids)


def print_stamp(env, box_id):
    """Gültiger Druckvermerk für diese Box oder None."""
    stamp = env.context.get(PRINT_STAMP_KEY)
    return stamp if isinstance(stamp, PrintStamp) and stamp.box_id == box_id else None


def archive(env):
    auth = env.context.get(ARCHIVE_KEY)
    return auth if isinstance(auth, Archive) else None
