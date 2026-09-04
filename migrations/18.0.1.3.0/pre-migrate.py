# -*- coding: utf-8 -*-
"""
Convierte ghl_sede_vendor.shift (morning/afternoon/both) a los nuevos
campos booleanos works_morning / works_afternoon antes de que el ORM
elimine la columna vieja.
"""


def migrate(cr, version):
    cr.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ghl_sede_vendor' AND column_name = 'shift'
        """
    )
    if not cr.fetchone():
        return

    cr.execute(
        """
        ALTER TABLE ghl_sede_vendor
            ADD COLUMN IF NOT EXISTS works_morning boolean,
            ADD COLUMN IF NOT EXISTS works_afternoon boolean;

        UPDATE ghl_sede_vendor SET
            works_morning = (shift IN ('morning', 'both')),
            works_afternoon = (shift IN ('afternoon', 'both'));
        """
    )
