"""1.8.0: Eindeutige Annahme je Artikel + Seriennummer (Code-Review R07).

Vorhandene Doppelungen werden nicht verändert (Daten werden nicht erfunden oder gelöscht),
sondern im Log genannt. Solange sie bestehen, legt Odoo den eindeutigen Index nicht an;
die Prüfung beim Scan gilt trotzdem.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT product_id, serial, array_agg(id ORDER BY id)
          FROM plcs_greenrx_scan
         WHERE accepted IS TRUE AND serial IS NOT NULL
      GROUP BY product_id, serial
        HAVING count(*) > 1
    """)
    rows = cr.fetchall()
    for product_id, serial, scan_ids in rows:
        _logger.warning("GreenRX: Seriennummer %s (Artikel %s) mehrfach angenommen, Scans %s – bitte klären.",
                        serial, product_id, scan_ids)
    if rows:
        _logger.warning("GreenRX: %s doppelte Seriennummern gefunden; eindeutiger Index erst nach Klärung.", len(rows))
