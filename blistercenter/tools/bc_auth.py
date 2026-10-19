"""Interne Freigaben für Blistercenter-Nachweisfelder (Prüfbericht 19.10.2026, R01–R03, R05, R07).

Nachweisfelder (wer hat kommissioniert, freigegeben, abgestimmt; Status der Sammelbestellung)
setzt nur der Modulcode in den geprüften Aktionen. Ein Kontextwert aus einem RPC-Aufruf ist
immer JSON (Text, Zahl, Liste). Die Freigabe hier ist ein Python-Objekt, das ein Aufrufer von
außen nicht erzeugen kann. Superuser-Aufrufe (nur aus Modulcode, Migration, Cron) sind erlaubt.
Gleiches Muster wie plcs_greenrx/tools/grx_auth.py.
"""
from odoo.exceptions import AccessError

KEY = "bc_internal_auth"


class Auth:
    """Freigabe für einen benannten internen Schritt."""

    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "<Blistercenter-Freigabe %s>" % self.name


def grant(records, name):
    """Recordset mit Freigabe für den Schritt `name` (nur im Modulcode verwenden)."""
    return records.with_context(**{KEY: Auth(name)})


def has(env, name=None):
    auth = env.context.get(KEY)
    return isinstance(auth, Auth) and (name is None or auth.name == name)


def check_protected(records, vals, fields, name=None):
    """Direktes Schreiben geschützter Felder ablehnen (nur Superuser oder interne Freigabe)."""
    touched = set(vals) & set(fields)
    if not touched or records.env.su or has(records.env, name):
        return
    labels = ", ".join(sorted(records._fields[f].string for f in touched))
    raise AccessError(records.env._(
        "Diese Felder setzt nur der geprüfte Ablauf in Odoo, nicht ein direkter Schreibzugriff: %(fields)s",
        fields=labels))
