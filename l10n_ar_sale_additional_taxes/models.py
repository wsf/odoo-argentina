# -*- coding: utf-8 -*-
##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
from odoo import models, _, fields, api, tools
from odoo.exceptions import UserError,ValidationError
from odoo.tools.safe_eval import safe_eval
import datetime


class AccountCheck(models.Model):
        _inherit = 'account.check'

        payment_date = fields.Date(readonly=True,states={'draft': [('readonly', False)],'holding': [('readonly', False)]},index=True,)


class AccountTax(models.Model):
	_inherit = 'account.tax'

	prefijo_padron = fields.Char('Prefijo padron')


class AccountPadron(models.Model):
	_name = 'account.padron'
	_description = 'account.padron'

	date_from = fields.Date('Fecha Desde')
	date_to = fields.Date('Fecha Hasta')
	cuit = fields.Char('CUIT',index=True)
	tax = fields.Char('Impuesto')
	percent = fields.Float('Porcentaje')

class ResPartnerPerception(models.Model):
	_name = "res.partner.perception"
	_description = "Perception Defined in Partner"

	tax_id = fields.Many2one('account.tax',string='Impuesto',required=True)
	percent = fields.Float('Percent', required=True)
	partner_id = fields.Many2one('res.partner', 'Cliente')

class ResPartner(models.Model):
        _name = "res.partner"
        _inherit = "res.partner"

        @api.multi
        def update_percepciones(self):
        	for partner in self:
        		if partner.main_id_number:
        			if partner.perception_ids:
        				for perception in partner.perception_ids:
        					perception.unlink()
        			padron_ids = self.env['account.padron'].search([('cuit','=',partner.main_id_number)],order='id desc',limit=2)
        			for padron in padron_ids[:2]:
        				tax_id = self.env['account.tax'].search([('prefijo_padron','=',padron.tax)])
        				if not tax_id:
        					continue
        				vals = {'partner_id': partner.id,'percent': padron.percent,'tax_id': tax_id.id}
        				perception_id = self.env['res.partner.perception'].create(vals)




        @api.multi
        def _compute_iibb_percent(self):
        	for rec in self:
        		if rec.perception_ids:
        			for perception in rec.perception_ids:
        				if perception.tax_id.prefijo_padron == 'RGSPer':
        					rec.iibb_percent = perception.percent


        @api.multi
        def _compute_iibb_ret_percent(self):
        	for rec in self:
        		if rec.perception_ids:
        			for perception in rec.perception_ids:
        				if perception.tax_id.prefijo_padron == 'RGSRet':
        					rec.iibb_ret_percent = perception.percent

        @api.model
        def update_additional_taxes(self):
        	partners = self.env['res.partner'].search([])
        	for partner in partners:
        		if partner.main_id_number:
        			if partner.perception_ids:
        				for perception in partner.perception_ids:
        					perception.unlink()
        			padron_ids = self.env['account.padron'].search([('cuit','=',partner.main_id_number)])
        			for padron in padron_ids:
        				if str(datetime.datetime.today())[:7] != padron.date_from[:7]:
        					continue
        				tax_id = self.env['account.tax'].search([('prefijo_padron','=',padron.tax)])
        				if not tax_id:
        					continue
        				vals = {'partner_id': partner.id,'percent': padron.percent,'tax_id': tax_id.id}
        				perception_id = self.env['res.partner.perception'].create(vals)


        perception_ids = fields.One2many('res.partner.perception', 'partner_id', 'Percepciones Definidas')
        iibb_percent = fields.Float('% IIBB',compute=_compute_iibb_percent)
        iibb_ret_percent = fields.Float('% IIBB',compute=_compute_iibb_ret_percent)
"""
class AccountInvoice(models.Model):
        _inherit = "account.move"

        @api.multi
        def compute_taxes(self):
	        """Function used in other module to compute the taxes on a fresh invoice created (onchanges did not applied)"""
        	account_invoice_tax = self.env['account.invoice.tax']
	        ctx = dict(self._context)
	        for invoice in self:
        	    # Delete non-manual tax lines
	            self._cr.execute("DELETE FROM account_invoice_tax WHERE invoice_id=%s AND manual is False", (invoice.id,))
        	    if self._cr.rowcount:
                	self.invalidate_cache()

	            # Generate one tax line per tax, however many invoice lines it's applied to
        	    tax_grouped = invoice.get_taxes_values()

	            # Create new tax lines
        	    for tax in tax_grouped.values():
                	account_invoice_tax.create(tax)

        	    if invoice.type in ['out_invoice','out_refund']:
        	    	if invoice.partner_id.perception_ids:
        	    		for perception in invoice.partner_id.perception_ids:
        	    			if perception.percent > 0:
        	    				vals = {'name': perception.tax_id.name,'tax_id': perception.tax_id.id,'account_id': perception.tax_id.account_id.id,'base': invoice.amount_untaxed,'amount': invoice.amount_untaxed * (perception.percent / 100),'invoice_id': invoice.id,'sequence': 100,'account_analytic_id': False,'manual': False}
        	    				account_invoice_tax.create(vals)

	        # dummy write on self to trigger recomputations
        	return self.with_context(ctx).write({'invoice_line_ids': []})
"""
