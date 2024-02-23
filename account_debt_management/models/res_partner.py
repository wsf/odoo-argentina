##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
from odoo import api, models, fields, _
# from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = 'res.partner'

    unreconciled_domain = [
        ('reconciled', '=', False), ('full_reconcile_id', '=', False)]
    receivable_domain = [('account_type', '=', 'asset_receivable')]
    payable_domain = [('account_type', '=', 'liability_payable')]

    receivable_debt_ids = fields.One2many(
        'account.debt.line',
        'partner_id',
        domain=unreconciled_domain + receivable_domain,
    )
    payable_debt_ids = fields.One2many(
        'account.debt.line',
        'partner_id',
        domain=unreconciled_domain + payable_domain,
    )
    debt_balance = fields.Monetary(
        compute='_compute_debt_balance',
        currency_field='currency_id',
    )

    # This computes makes fields to be computed upon partner creation where no
    # id exists yet and raise an erro because of partner where being empty on
    # _credit_debit_get method, ase debit and credit don't have depends, this
    # field neither
    # @api.depends('debit', 'credit')
    def _compute_debt_balance(self):
        for rec in self:
            rec.debt_balance = rec.credit - rec.debit
