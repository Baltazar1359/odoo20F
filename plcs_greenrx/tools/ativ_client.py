"""Lesender Zugriff auf das GreenRX-ERP von ATIV (Odoo Online) über die externe API.

Standard ist die JSON-2-API von Odoo 19 (``/json/2/<Modell>/<Methode>``, API-Schlüssel als
Bearer-Token). XML-RPC bleibt als Ausweichweg einstellbar, weil Odoo es noch anbietet.

Der Client kennt nur lesende Methoden. Schreibende Aufrufe lehnt er selbst ab, damit eine
falsch konfigurierte Feldzuordnung niemals Daten bei ATIV ändert.
"""
import json
import socket
import xmlrpc.client
from urllib.parse import urlparse

READ_METHODS = frozenset({"search", "search_read", "search_count", "read", "fields_get", "name_search"})


class AtivError(Exception):
    """Fehler beim Abruf. ``transient`` = später erneut versuchen (Netz, Zeitüberschreitung, 5xx)."""

    def __init__(self, message, transient=False, status=None):
        super().__init__(message)
        self.transient = transient
        self.status = status


class _TimeoutTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout):
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class AtivClient:
    """Dünne Hülle um die API. ``call(model, method, **kwargs)`` gibt das Ergebnis zurück."""

    def __init__(self, url, db, login, api_key, protocol="json2", timeout=60, lang="en_US", session=None):
        url = (url or "").strip().rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise AtivError("Die URL muss mit https:// beginnen (verschlüsselte Übertragung ist Pflicht).")
        if not (db and api_key):
            raise AtivError("Datenbank und API-Schlüssel ausfüllen.")
        self.url = url
        self.db = db
        self.login = login
        self._key = api_key
        self.protocol = protocol or "json2"
        self.timeout = timeout or 60
        self.lang = lang
        self._session = session
        self._uid = None

    # ------------------------------------------------------------------
    def call(self, model, method, **kwargs):
        if method not in READ_METHODS:
            raise AtivError("Nur lesende Aufrufe sind erlaubt (%s.%s abgelehnt)." % (model, method))
        context = dict(kwargs.pop("context", None) or {})
        if self.lang:
            context.setdefault("lang", self.lang)
        context.setdefault("active_test", False)
        if self.protocol == "xmlrpc":
            return self._call_xmlrpc(model, method, kwargs, context)
        return self._call_json2(model, method, kwargs, context)

    # JSON-2 ----------------------------------------------------------
    def _http(self):
        if self._session is None:
            import requests  # noqa: PLC0415 – erst bei Bedarf laden
            self._session = requests.Session()
        return self._session

    def _call_json2(self, model, method, kwargs, context):
        import requests  # noqa: PLC0415
        body = dict(kwargs)
        body["context"] = context
        try:
            res = self._http().post(
                "%s/json/2/%s/%s" % (self.url, model, method),
                data=json.dumps(body),
                headers={
                    "Authorization": "Bearer %s" % self._key,
                    "X-Odoo-Database": self.db,
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "PLCS-GreenRX-Sync/1",
                },
                timeout=self.timeout,
            )
        except requests.Timeout as e:
            raise AtivError("Zeitüberschreitung bei %s.%s" % (model, method), transient=True) from e
        except requests.RequestException as e:
            raise AtivError("Verbindung zu ATIV fehlgeschlagen: %s" % type(e).__name__, transient=True) from e
        if res.status_code == 200:
            try:
                return res.json()
            except ValueError as e:
                raise AtivError("Antwort von ATIV ist kein JSON (%s.%s)." % (model, method), transient=True) from e
        message = ""
        try:
            data = res.json()
            message = data.get("message") or data.get("name") or ""
        except ValueError:
            message = (res.text or "")[:200]
        status = res.status_code
        if status == 401:
            raise AtivError("Anmeldung bei ATIV abgelehnt. API-Schlüssel prüfen.", status=status)
        if status == 403:
            raise AtivError("Keine Leserechte bei ATIV für %s: %s" % (model, message), status=status)
        if status == 404:
            raise AtivError("Modell oder Methode bei ATIV nicht gefunden: %s.%s" % (model, method), status=status)
        transient = status >= 500 or status == 429
        raise AtivError("ATIV meldet Fehler %s bei %s.%s: %s" % (status, model, method, message),
                        transient=transient, status=status)

    # XML-RPC ---------------------------------------------------------
    def _proxy(self, endpoint):
        return xmlrpc.client.ServerProxy("%s/xmlrpc/2/%s" % (self.url, endpoint), allow_none=True,
                                         transport=_TimeoutTransport(self.timeout))

    def _call_xmlrpc(self, model, method, kwargs, context):
        try:
            if self._uid is None:
                self._uid = self._proxy("common").authenticate(self.db, self.login, self._key, {})
                if not self._uid:
                    raise AtivError("Anmeldung bei ATIV abgelehnt. Benutzer/API-Schlüssel prüfen.")
            args = []
            kw = dict(kwargs)
            if "ids" in kw:
                args.append(kw.pop("ids"))
            elif "domain" in kw and method in ("search", "search_read", "search_count"):
                args.append(kw.pop("domain"))
            kw["context"] = context
            return self._proxy("object").execute_kw(self.db, self._uid, self._key, model, method, args, kw)
        except xmlrpc.client.Fault as e:
            raise AtivError("ATIV meldet Fehler bei %s.%s: %s" % (model, method, str(e.faultString)[:300])) from e
        except (OSError, socket.timeout, xmlrpc.client.ProtocolError) as e:
            raise AtivError("Verbindung zu ATIV fehlgeschlagen: %s" % type(e).__name__, transient=True) from e


def binary_content(value):
    """Binärwert aus der API als base64-Text (JSON-2 liefert ab Odoo 20 ein dict)."""
    if isinstance(value, dict):
        return value.get("content") or False
    return value or False
