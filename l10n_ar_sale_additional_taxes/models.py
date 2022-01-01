# -*- coding: utf-8 -*-
##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
from odoo import models, _, fields, api, tools
from odoo.exceptions import UserError,ValidationError
from odoo.tools.safe_eval import safe_eval
import datetime


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
	date_from = fields.Date('Fecha Desde')
	partner_id = fields.Many2one('res.partner', 'Cliente')

class ResPartner(models.Model):
        _name = "res.partner"
        _inherit = "res.partner"

        @api.model
        def update_percepciones(self):
            partners = self.env['res.partner'].search([])
            for partner in partners:
                for perception in partner.perception_ids:
                    perception.unlink()
                padron_ids = self.env['account.padron'].search([('cuit','=',partner.vat)])
                for padron in padron_ids:
                    tax_id = self.env['account.tax'].search([('padron_prefix','=',padron.tax)])
                    if not tax_id:
                        raise ValidationError('Impuesto no determinado %s'%(padron.tax))
                    perception_ids = self.env['res.partner.perception'].search([('partner_id','=',partner.id),('tax_id','=',tax_id.id)],order='date_from desc')
                    if not perception_ids:
                        vals = {'partner_id': partner.id,'percent': padron.percent,'tax_id': tax_id.id,'date_from': padron.date_from}
                        perception_id = self.env['res.partner.perception'].create(vals)

        def partner_update_percepciones(self):
            self.ensure_one()
            for partner in self:
                for perception in partner.perception_ids:
                    perception.unlink()
                padron_ids = self.env['account.padron'].search([('cuit','=',partner.vat)],order='date_from desc')
                for padron in padron_ids:
                    tax_id = self.env['account.tax'].search([('padron_prefix','=',padron.tax)])
                    if not tax_id:
                        raise ValidationError('Impuesto no determinado %s'%(padron.tax))
                    perception_ids = self.env['res.partner.perception'].search([('partner_id','=',partner.id),('tax_id','=',tax_id.id)])
                    if not perception_ids:
                        vals = {'partner_id': partner.id,'percent': padron.percent,'tax_id': tax_id.id,'date_from': padron.date_from}
                        perception_id = self.env['res.partner.perception'].create(vals)

        perception_ids = fields.One2many('res.partner.perception', 'partner_id', 'Percepciones Definidas')


class AccountMoveLine(models.Model):
        _inherit = "account.move.line"

        def _get_amount_updated_values(self,tax_amount=0):
            debit = credit = 0
            if self.move_id.move_type == "in_invoice":
                if tax_amount > 0:
                    debit = tax_amount
                elif tax_amount < 0:
                    credit = -tax_amount
            else:  # For refund
                if tax_amount > 0:
                    credit = tax_amount
                elif tax_amount < 0:
                    debit = -tax_amount

            # If multi currency enable
            move_currency = self.move_id.currency_id
            company_currency = self.move_id.company_currency_id
            if move_currency and move_currency != company_currency:
                return {'amount_currency': tax_amount if debit else -tax_amount,
                        'debit': move_currency._convert(debit, company_currency, self.move_id.company_id, self.move_id.invoice_date),
                        'credit': move_currency._convert(credit, company_currency, self.move_id.company_id, self.move_id.invoice_date)}

            return {'debit': debit, 'credit': credit, 'balance': tax_amount}


class AccountMove(models.Model):
        _inherit = "account.move"

        def _prepare_tax_lines_data_for_totals_from_invoice(self, tax_line_id_filter=None, tax_ids_filter=None):
            """ Prepares data to be passed as tax_lines_data parameter of _get_tax_totals() from an invoice.

                NOTE: tax_line_id_filter and tax_ids_filter are used in l10n_latam to restrict the taxes with consider
                  in the totals.

                :param tax_line_id_filter: a function(aml, tax) returning true if tax should be considered on tax move line aml.
                :param tax_ids_filter: a function(aml, taxes) returning true if taxes should be considered on base move line aml.

                :return: A list of dict in the format described in _get_tax_totals's tax_lines_data's docstring.
            """
            self.ensure_one()

            tax_line_id_filter = tax_line_id_filter or (lambda aml, tax: True)
            tax_ids_filter = tax_ids_filter or (lambda aml, tax: True)

            balance_multiplicator = -1 if self.is_inbound() else 1
            tax_lines_data = []

            for line in self.line_ids:
                if line.tax_line_id and tax_line_id_filter(line, line.tax_line_id):
                    if not line.tax_line_id.is_padron:
                        tax_lines_data.append({
                            'line_key': 'tax_line_%s' % line.id,
                            'tax_amount': line.amount_currency * balance_multiplicator,
                            'tax': line.tax_line_id,
                        })

                if line.tax_ids:
                    for base_tax in line.tax_ids.flatten_taxes_hierarchy():
                        if tax_ids_filter(line, base_tax) or base_tax.is_padron:
                               tax_lines_data.append({
                                    'line_key': 'base_line_%s' % line.id,
                                    'base_amount': line.amount_currency * balance_multiplicator,
                                    'tax': base_tax,
                                    'tax_affecting_base': line.tax_line_id,
                                })
                        if base_tax.is_padron:
                            domain = [('move_id','=',line.move_id.id),('tax_id','=',base_tax.id)]
                            account_move_tax_line = self.env['account.move.tax'].search(domain)
                            if not account_move_tax_line:
                                raise ValidationError('Problemas con la configuracion de impuestos\nConsulte al administrador')
                            tax_lines_data.append({
                                'line_key': 'tax_line_%s' % line.id,
                                'tax_amount': account_move_tax_line.tax_amount,
                                'tax': base_tax,
                            })

            return tax_lines_data



        def compute_taxes(self):
            self.ensure_one()
            if self.state == 'draft':
                if self.move_type in ['out_invoice','out_refund']:
                    for move_tax in self.move_tax_ids:
                        move_tax.unlink()
                    if self.partner_id.perception_ids:
                        for perception in self.partner_id.perception_ids:
                            if perception.tax_id.type_tax_use != 'sale':
                                continue
                            for invoice_line in self.invoice_line_ids:
                                if perception.tax_id.id not in invoice_line.tax_ids.ids:
                                    invoice_line.tax_ids = [(4,perception.tax_id.id)] 
                                    invoice_line.recompute_tax_line = True
                    for invoice_line in self.invoice_line_ids:
                        if invoice_line.tax_ids:
                            for tax in invoice_line.tax_ids.ids:
                                account_tax = self.env['account.tax'].browse(tax)
                                move_tax_id = self.env['account.move.tax'].search([('move_id','=',self.id),('tax_id','=',tax)])
                                if not move_tax_id:
                                    vals = {
                                            'move_id': self.id,
                                            'tax_id': tax
                                            }
                                    move_tax_id = self.env['account.move.tax'].create(vals)
                                move_tax_id.base_amount = move_tax_id.base_amount + invoice_line.price_subtotal
                                tax_id = self.env['account.tax'].browse(tax)
                                tax_amount = 0
                                if not tax_id.is_padron:
                                    move_tax_id.tax_amount = move_tax_id.tax_amount + invoice_line.price_subtotal * (account_tax.amount / 100)
                                    tax_amount = move_tax_id.tax_amount
                                else:
                                    amount = 0
                                    for perception in self.partner_id.perception_ids:
                                        if perception.tax_id.id == tax_id.id:
                                            amount = perception.percent
                                    move_tax_id.tax_amount = move_tax_id.tax_amount + invoice_line.price_subtotal * (amount / 100)
                                    tax_amount = move_tax_id.tax_amount
                                if tax_id.tax_group_id.tax_type != 'vat':
                                    res = invoice_line._get_amount_updated_values(tax_amount)
                                    account_id = None
                                    for rep_tax in tax_id.invoice_repartition_line_ids:
                                        if rep_tax.account_id:
                                            account_id = rep_tax.account_id
                                    move_line = self.env['account.move.line'].search([('move_id','=',self.id),('account_id','=',account_id.id)])
                                    if not move_line:
                                        res['move_id'] = self.id
                                        res['account_id'] = account_id.id
                                        res['name'] = tax_id.display_name
                                        res['exclude_from_invoice_tab'] = True
                                        line_id = self.env['account.move.line'].with_context(check_move_validity=False).create(res)
                                    for move_line in self.line_ids:
                                        if move_line.account_id.reconcile:
                                            move_line.with_context(check_move_validity=False).debit = move_line.debit + tax_amount
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'reload',
                        }


