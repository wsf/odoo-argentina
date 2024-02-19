# -*- coding: utf-8 -*-
##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import time


class account_vat_ledger(models.Model):

    _name = "account.vat.ledger"
    _description = "Account VAT Ledger"
    _inherit = ['mail.thread']
    _order = 'date_from desc'

    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        default=lambda self: self.env[
            'res.company']._company_default_get('account.vat.ledger')
    )
    type = fields.Selection(
        [('sale', 'Sale'), ('purchase', 'Purchase')],
        "Type",
    )
    date_from = fields.Date(
        string='Fecha Desde',
    )
    date_to = fields.Date(
        string='Fecha Hasta',
    )

    journal_ids = fields.Many2many(
        'account.journal', 'account_vat_ledger_journal_rel',
        'vat_ledger_id', 'journal_id',
        string='Diarios',
    )
    presented_ledger = fields.Binary(
        "Presented Ledger",
    )
    presented_ledger_name = fields.Char(
    )
    state = fields.Selection(
        [('draft', 'Draft'), ('presented', 'Presented'), ('cancel', 'Cancel')],
        'State',
        default='draft'
    )
    note = fields.Html(
        "Notas"
    )
# Computed fields
    name = fields.Char(
        'Nombre',
        compute='_get_name'
    )
    reference = fields.Char(
        'Referencia',
    )
    invoice_ids = fields.Many2many(
        'account.move',
        string="Facturas",
        compute="_get_data"
    )

    # Sacamos el depends por un error con el cache en esqume multi cia al
    # cambiar periodo de una cia hija con usuario distinto a admin
    # @api.depends('journal_ids', 'period_id')
    def _get_data(self):
        #self.afip_responsability_type_ids = self.env[
        #    'l10n_ar.afip.responsibility.type'].search([])

        if self.type == 'sale':
            invoices_domain = [
                # cancel invoices with internal number are invoices
                ('state', '!=', 'draft'),
                ('document_number', '!=', False),
                # ('internal_number', '!=', False),
                ('journal_id', 'in', self.journal_ids.ids),
                ('date', '>=', self.date_from),
                ('date', '<=', self.date_to),
            ]
            invoices = self.env['account.move'].search(
                # TODO, tal vez directamente podemos invertir el orden, como?
                invoices_domain,
                order='invoice_date asc, document_number asc, id asc')
        else:
            invoices_domain = [
                # cancel invoices with internal number are invoices
                ('state', '!=', 'draft'),
                ('name', '!=', False),
                # ('internal_number', '!=', False),
                ('journal_id', 'in', self.journal_ids.ids),
                ('date', '>=', self.date_from),
                ('date', '<=', self.date_to),
            ]
            invoices = self.env['account.move'].search(
                # TODO, tal vez directamente podemos invertir el orden, como?
                invoices_domain,
                order='invoice_date asc, name asc, id asc')


        #self.document_type_ids = invoices.mapped('l10n_latam_document_type_id')
        self.invoice_ids = invoices

    def _get_name(self):
        for rec in self:
            if rec.type == 'sale':
                ledger_type = _('Ventas')
            elif rec.type == 'purchase':
                ledger_type = _('Compras')

            lang = self.env['res.lang']

            name = _("%s Libro de IVA %s - %s") % (
                ledger_type,
                rec.date_from and fields.Date.from_string(
                    rec.date_from).strftime("%d-%m-%Y") or '',
                rec.date_to and fields.Date.from_string(
                    rec.date_to).strftime("%d-%m-%Y") or '',
            )
            if rec.reference:
                name = "%s - %s" % (name, rec.reference)
            rec.name = name

    @api.onchange('company_id')
    def change_company(self):
        now = time.strftime('%Y-%m-%d')
        company_id = self.company_id.id
        domain = [('company_id', '=', company_id),
                  ('date_start', '<', now), ('date_stop', '>', now)]
        if self.type == 'sale':
            domain = [('type', '=', 'sale')]
        elif self.type == 'purchase':
            domain = [('type', '=', 'purchase')]
        domain += [
            ('l10n_latam_use_documents', '=', True),
            ('company_id', '=', self.company_id.id),
        ]
        journals = self.env['account.journal'].search(domain)
        self.journal_ids = journals

    def action_present(self):
        self.state = 'presented'

    def action_cancel(self):
        self.state = 'cancel'

    def action_to_draft(self):
        self.state = 'draft'

class AccountVatLedgerXlsx(models.AbstractModel):
    _name = 'report.l10n_ar_account_vat_ledger.account_vat_ledger_xlsx'
    #_name = 'account_vat_ledger_xlsx'
    _inherit = 'report.report_xlsx.abstract'

    def generate_xlsx_report(self, workbook, data, vat_ledger):
        if vat_ledger.invoice_ids:
            report_name = 'IVA Ventas'
            sheet = workbook.add_worksheet(report_name[:31])
            money_format = workbook.add_format({'num_format': '$#,##0'})
            bold = workbook.add_format({'bold': True})
            sheet.write(1, 0, vat_ledger.display_name, bold)
            titles = ['Fecha','Cliente','CUIT','Tipo Comprobante','Responsabilidad AFIP','Nro Comprobante','Neto gravado','Neto Exento','IVA 21','IVA 10.5','Otros Impuestos','Total gravado']
            for i,title in enumerate(titles):
                sheet.write(3, i, title, bold)
            row = 4
            index = 0
            sheet.set_column('A:F', 30)
            for i,obj in enumerate(vat_ledger.invoice_ids):
                # One sheet by partner
                #if obj.qty_available < 1:
                #    continue
                sheet.write(row + index, 0, obj.invoice_date.strftime("%Y-%m-%d"))
                sheet.write(row + index, 1, obj.partner_id.display_name)
                sheet.write(row + index, 2, obj.partner_id.vat)
                sheet.write(row + index, 3, obj.l10n_latam_document_type_id.display_name)
                sheet.write(row + index, 4, obj.partner_id.l10n_ar_afip_responsibility_type_id.name)
                sheet.write(row + index, 5, obj.display_name)
                vat_taxable_amount = 0
                vat_exempt_base_amount = 0
                other_taxes_amount = 0
                vat_amount = 0
                for move_tax in obj.move_tax_ids:
                    if move_tax.tax_id.tax_group_id.tax_type == 'vat' and move_tax.tax_id.tax_group_id.l10n_ar_vat_afip_code != '2':
                        vat_taxable_amount += move_tax.base_amount
                        vat_amount += move_tax.tax_amount
                    elif move_tax.tax_id.tax_group_id.tax_type == 'vat' and move_tax.tax_id.tax_group_id.l10n_ar_vat_afip_code == '2':
                        vat_exempt_base_amount += move_tax.base_amount
                    else:
                        other_taxes_amount += move_tax.tax_amount

                sheet.write(row + index, 6, vat_taxable_amount,money_format)
                sheet.write(row + index, 7, vat_exempt_base_amount,money_format)
                for tax_line in obj.move_tax_ids:
                    if tax_line.tax_id.amount == 21:
                        sheet.write(row + index, 8, tax_line.tax_amount,money_format)
                    if tax_line.tax_id.amount == 10.5:
                        sheet.write(row + index, 9, tax_line.tax_amount,money_format)
                sheet.write(row + index, 10, other_taxes_amount,money_format)
                sheet.write(row + index, 11, obj.amount_total,money_format)
                index += 1

