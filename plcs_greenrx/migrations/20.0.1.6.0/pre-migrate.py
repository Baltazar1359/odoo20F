"""1.6.0: Vorhandene Schnittstellen behalten die alte Feldzuordnung (Helpdesk-Tickets).

Neue Schnittstellen werden als ATIV-Gutschriften (Datenvertrag v1) angelegt.
"""


def migrate(cr, version):
    cr.execute("ALTER TABLE plcs_greenrx_connector ADD COLUMN IF NOT EXISTS mode varchar")
    cr.execute("UPDATE plcs_greenrx_connector SET mode = 'legacy' WHERE mode IS NULL")
