# -*- coding: utf-8 -*-
##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
from odoo import models, _, fields, api, tools
from odoo.exceptions import UserError,ValidationError
from odoo.tools.safe_eval import safe_eval
import datetime
from datetime import date

class ResPartner(models.Model):
    _inherit = "res.partner"

    def partner_update_percepciones(self):
        self.ensure_one()
        sql = "SELECT TIPO_REG,FECHA_DESDE,PORCENTAJE FROM ARBA_PADRON WHERE CUIT = '%s' LIMIT 2"%(self.vat)
        self.env.cr.execute(sql)
        res = self.env.cr.fetchall()
        for rec in res:
            if rec[0] == 'R':
                tax_id = self.env.ref('account.1_ri_tax_withholding_iibb_ba_applied').id
                percent = rec[2]
                cadena = rec[1]
                date_from = cadena[4:] + '-' + cadena[2:4] + '-' + cadena[:2]
            vals = {
                    'tax_id': tax_id,
                    'partner_id': self.id,
                    'percent': percent,
                    'date_from': date_from
                    }
            return_id = self.env['res.partner.perception'].create(vals)

    @api.model
    def update_all_arba(self):
        partners = self.env['res.partner'].search([('vat','!=',False)])
        for partner in partners:
            partner._update_arba(self)
