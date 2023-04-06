# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, models


class AccountPaymentMethod(models.Model):
    _inherit = 'account.payment.method'

    @api.model
    def _get_payment_method_information(self):
        res = super()._get_payment_method_information()
        res['in_third_party_checks'] = {'mode': 'multi', 'domain': [('type', '=', 'bank')]}
        res['out_third_party_checks'] = {'mode': 'multi', 'domain': [('type', '=', 'bank')]}
        return res
