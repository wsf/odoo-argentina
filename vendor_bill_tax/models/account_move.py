from odoo import tools, models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import date,datetime

class AccountMove(models.Model):
    _inherit = 'account.move'

    def btn_add_purchase_tax(self):
        self.ensure_one()
        vals = {
            'move_id': self.id,
            }
        wizard_id = self.env['account.invoice.tax'].create(vals)
        res = {
            'name': _('Account Invoice Tax'),
            'res_model': 'account.invoice.tax',
            'view_mode': 'form',
            'res_id': wizard_id.id,
            'type': 'ir.actions.act_window',
            'target': 'new',
        }
        return res
