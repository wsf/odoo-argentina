# -*- coding: utf-8 -*-
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
from . import models
from . import account_tax


from odoo import api, SUPERUSER_ID

def post_init_hook(env):
    domain = [
            ('model','=','account.tax'),
            ('name','ilike','percepcion'),
            ]
    external_taxes = env['ir.model.data'].search(domain)
    taxes = []
    for ext_tax in external_taxes:
        tax = env.ref(ext_tax.complete_name)
        if tax.type_tax_use == 'sale':
            taxes.append(tax)
    taxes_ids = []
    for tax in taxes:
        tax.amount_type = 'code'
        tax.is_padron = True
        tax.all_products = True
        tax.python_compute = 'result = base_amount * (partner and partner.get_tax_percent(tax) or 1)'

