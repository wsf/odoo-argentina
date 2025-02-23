# © 2016 ADHOC SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

import logging
_logger = logging.getLogger(__name__)


class AccountPayment(models.Model):
    _inherit = "account.payment"

    payment_group_id = fields.Many2one(
        'account.payment.group',
        'Recibo',
        ondelete='cascade',
        readonly=True,
    )
    # we add this field so company can be send in context when adding payments
    # before payment group is saved
    payment_group_company_id = fields.Many2one(
        related='payment_group_id.company_id',
        string='Payment Group Company',
    )
    # we make a copy without transfer option, we try with related but it
    # does not works
    payment_type_copy = fields.Selection(
        selection=[('outbound', 'Send Money'), ('inbound', 'Receive Money')],
        compute='_compute_payment_type_copy',
        inverse='_inverse_payment_type_copy',
        string='Payment Type (without transfer)'
    )
    signed_amount = fields.Monetary(
        string='Monto',
        compute='_compute_signed_amount',
    )
    signed_amount_company_currency = fields.Monetary(
        string='Monto del Pago en la Moneda de la Empresa',
        compute='_compute_signed_amount',
        currency_field='company_currency_id',
    )
    amount_company_currency = fields.Monetary(
        string='Monto en la Moneda de la Empresa',
        compute='_compute_amount_company_currency',
        inverse='_inverse_amount_company_currency',
        currency_field='company_currency_id',
    )
    other_currency = fields.Boolean(
        compute='_compute_other_currency',
    )
    force_amount_company_currency = fields.Monetary(
        string='Monto Forzado en la Moneda de la Empresa',
        currency_field='company_currency_id',
        copy=False,
    )
    exchange_rate = fields.Float(
        string='Tipo de Cambio',
        compute='_compute_exchange_rate',
        # readonly=False,
        #inverse='_inverse_exchange_rate',
        digits=(16, 4),
    )
    company_currency_id = fields.Many2one(
        related='company_id.currency_id',
        string='Company currency',
    )

    #@api.model
    #def default_get(self, fields):
    #    res = super(AccountPayment, self).default_get(fields)
    #    context = self.env.context
    #    import pdb;pdb.set_trace()
    #    if context.get('default_payment_type') == 'transfer':
    #        res['is_internal_transfer'] = True
    #    if context.get('default_journal_id'):
    #        res['journal_id'] = context.get('default_journal_id')
    #    return res

    @api.model
    def default_get(self, fields):
        res = super(AccountPayment, self).default_get(fields)
        if res.get('payment_type') == 'transfer':
            res['payment_type'] = 'outbound'
            res['partner_id'] = self.env.user.company_id.partner_id.id
            res['journal_id'] = self.env['account.journal'].search([('type','in',['bank','cash'])])
            res['destination_journal_id'] = self.env['account.journal'].search([('type','in',['bank','cash'])])
            res['payment_type'] = 'outbound'
        return res

    def _get_blocking_l10n_latam_warning_msg(self):
        msgs = []
        for rec in self.filtered('l10n_latam_check_id'):
            if rec.currency_id and not rec.currency_id.is_zero(rec.l10n_latam_check_id.amount - rec.amount):
                msgs.append(_(
                    'The amount of the payment (%s) does not match the amount of the selected check (%s). '
                    'Please try to deselect and select the check again.', rec.amount, rec.l10n_latam_check_id.amount))
            if not rec.currency_id and (rec.l10n_latam_check_id.amount - rec.amount) > 0:
                msgs.append(_(
                    'The amount of the payment (%s) does not match the amount of the selected check (%s). '
                    'Please try to deselect and select the check again.', rec.amount, rec.l10n_latam_check_id.amount))
            if rec.payment_method_line_id.code in ['in_third_party_checks', 'out_third_party_checks']:
                if rec.l10n_latam_check_id.state != 'posted':
                    msgs.append(_('Selected check "%s" is not posted', rec.l10n_latam_check_id.display_name))
                elif (rec.payment_type == 'outbound' and
                        rec.l10n_latam_check_id.l10n_latam_check_current_journal_id != rec.journal_id) or (
                        rec.payment_type == 'inbound' and rec.is_internal_transfer and
                        rec.l10n_latam_check_id.l10n_latam_check_current_journal_id != rec.destination_journal_id):
                    # check outbound payment and transfer or inbound transfer
                    msgs.append(_(
                        'Check "%s" is not anymore in journal "%s", it seems it has been moved by another payment.',
                        rec.l10n_latam_check_id.display_name, rec.journal_id.name
                        if rec.payment_type == 'outbound' else rec.destination_journal_id.name))
                elif rec.payment_type == 'inbound' and not rec.is_internal_transfer and \
                        rec.l10n_latam_check_id.l10n_latam_check_current_journal_id:
                    msgs.append(_("Check '%s' is on journal '%s', it can't be received it again",
                                rec.l10n_latam_check_id.display_name, rec.journal_id.name))
        return msgs


    @api.depends(
        'amount', 'payment_type', 'partner_type', 'amount_company_currency')
    def _compute_signed_amount(self):
        for rec in self:
            sign = 1.0
            if (
                    (rec.partner_type == 'supplier' and
                        rec.payment_type == 'inbound') or
                    (rec.partner_type == 'customer' and
                        rec.payment_type == 'outbound')):
                sign = -1.0
            rec.signed_amount = rec.amount and rec.amount * sign
            rec.signed_amount_company_currency = (
                rec.amount_company_currency and
                rec.amount_company_currency * sign)

    # TODO check why we get error with depend on company_id and fix it
    # (recursive dependency?). The error is on paymentrs tree/form view
    # @api.depends('currency_id', 'company_id')
    @api.depends('currency_id')
    def _compute_other_currency(self):
        for rec in self:
            rec.other_currency = False
            if rec.company_currency_id and rec.currency_id and \
               rec.company_currency_id != rec.currency_id:
                rec.other_currency = True

    @api.depends('amount', 'other_currency', 'amount_company_currency')
    def _compute_exchange_rate(self):
        for rec in self:
            if rec.other_currency:
                rec.exchange_rate = rec.amount and (
                    rec.amount_company_currency / rec.amount) or 0.0
            else:
                rec.exchange_rate = 1

    # this onchange is necesary because odoo, sometimes, re-compute
    # and overwrites amount_company_currency. That happends due to an issue
    # with rounding of amount field (amount field is not change but due to
    # rouding odoo believes amount has changed)
    @api.onchange('amount_company_currency')
    def _inverse_amount_company_currency(self):
        for rec in self:
            if rec.other_currency and rec.amount_company_currency != \
                    rec.currency_id._convert(
                        rec.amount, rec.company_id.currency_id,
                        rec.company_id, rec.date):
                force_amount_company_currency = rec.amount_company_currency
            else:
                force_amount_company_currency = False
            rec.force_amount_company_currency = force_amount_company_currency

    @api.depends('amount', 'other_currency', 'force_amount_company_currency')
    def _compute_amount_company_currency(self):
        """
        * Si las monedas son iguales devuelve 1
        * si no, si hay force_amount_company_currency, devuelve ese valor
        * sino, devuelve el amount convertido a la moneda de la cia
        """
        for rec in self:
            if not rec.other_currency:
                amount_company_currency = rec.amount
            elif rec.force_amount_company_currency:
                amount_company_currency = rec.force_amount_company_currency
            else:
                amount_company_currency = rec.currency_id._convert(
                    rec.amount, rec.company_id.currency_id,
                    rec.company_id, rec.date)
            rec.amount_company_currency = amount_company_currency

    @api.onchange('payment_type_copy')
    def _inverse_payment_type_copy(self):
        for rec in self:
            # if false, then it is a transfer
            rec.payment_type = (
                rec.payment_type_copy and rec.payment_type_copy or 'outbound')

    @api.depends('payment_type')
    def _compute_payment_type_copy(self):
        for rec in self:
            #if rec.payment_type == 'transfer':
            #    continue
            rec.payment_type_copy = rec.payment_type

    def get_journals_domain(self):
        domain = super(AccountPayment, self).get_journals_domain()
        if self.payment_group_company_id:
            domain.append(
                ('company_id', '=', self.payment_group_company_id.id))
        return domain

    @api.onchange('payment_type')
    def _onchange_payment_type(self):
        """
        we disable change of partner_type if we came from a payment_group
        but we still reset the journal
        """
        if not self._context.get('payment_group'):
            return super(AccountPayment, self)._onchange_payment_type()
        self.journal_id = False

    @api.constrains('payment_group_id', 'payment_type')
    def check_payment_group(self):
        # odoo tests don't create payments with payment gorups
        if self.env.registry.in_test_mode():
            return True
        for rec in self:
            receivable_payable = all([
                x['move_line'].account_id.account_type in [
                    'asset_receivable', 'liability_payable']
                for x in self._context.get('counterpart_aml_dicts', [])])
            #if rec.partner_type and rec.partner_id and receivable_payable and \
            #   not rec.payment_group_id:
            #    raise ValidationError(_(
            #        'Payments with partners must be created from '
            #        'payments groups'))
            ## transfers or payments from bank reconciliation without partners
            #elif not rec.partner_type and rec.payment_group_id:
            #    raise ValidationError(_(
            #        "Payments without partners (usually transfers) cant't "
            #        "have a related payment group"))

    @api.model
    def get_amls(self):
        """ Review parameters of process_reconciliation() method and transform
        them to amls recordset. this one is return to recompute the payment
        values
         context keys(
            'counterpart_aml_dicts', 'new_aml_dicts', 'payment_aml_rec')
         :return: account move line recorset
        """
        counterpart_aml_data = self._context.get('counterpart_aml_dicts', [])
        new_aml_data = self._context.get('new_aml_dicts', [])
        amls = self.env['account.move.line']
        if counterpart_aml_data:
            for item in counterpart_aml_data:
                amls |= item.get(
                    'move_line', self.env['account.move.line'])
        if new_aml_data:
            for aml_values in new_aml_data:
                amls |= amls.new(aml_values)
        return amls

    @api.model
    def infer_partner_info(self, vals):
        """ Odoo way to to interpret the partner_id, partner_type is not
        usefull for us because in some time they leave this ones empty and
        we need them in order to create the payment group.

        In this method will try to improve infer when it has a debt related
        taking into account the account type of the line to concile, and
        computing the partner if this ones is not setted when concile
        operation.

        return dictionary with keys (partner_id, partner_type)
        """
        res = {}
        # Get related amls
        amls = self.get_amls()
        if not amls:
            return res

        # odoo manda partner type segun si el pago es positivo o no, nosotros
        # mejoramos infiriendo a partir de que tipo de deuda se esta pagando
        partner_type = False
        internal_type = amls.mapped('account_id.account_type')
        if len(internal_type) == 1:
            if internal_type == ['liability_payable']:
                partner_type = 'supplier'
            elif internal_type == ['asset_receivable']:
                partner_type = 'customer'
            if partner_type:
                res.update({'partner_type': partner_type})

        # por mas que el usuario no haya selecccionado partner, si esta pagando
        # deuda usamos el partner de esa deuda
        partner_id = vals.get('partner_id', False)
        if not partner_id and len(amls.mapped('partner_id')) == 1:
            partner_id = amls.mapped('partner_id').id
            res.update({'partner_id': partner_id})

        return res

    @api.model
    def create(self, vals):
        """ When payments are created from bank reconciliation create the
        Payment group before creating payment to avoid raising error, only
        apply when the all the counterpart account are receivable/payable """
        # Si viene counterpart_aml entonces estamos viniendo de una
        # conciliacion desde el wizard
        import pdb;pdb.set_trace()
        new_aml_dicts = self._context.get('new_aml_dicts', [])
        counterpart_aml_data = self._context.get('counterpart_aml_dicts', [])
        if counterpart_aml_data or new_aml_dicts:
            vals.update(self.infer_partner_info(vals))

        create_from_statement = self._context.get(
            'create_from_statement', False) and vals.get('partner_type') \
            and vals.get('partner_id') and all([
                x['move_line'].account_id.account_type in [
                    'asset_receivable', 'liability_payable']
                for x in counterpart_aml_data])
        create_from_expense = self._context.get('create_from_expense', False)
        create_from_website = self._context.get('create_from_website', False)
        # NOTE: This is required at least from POS when we do not have
        # partner_id and we do not want a payment group in tha case.
        create_payment_group = \
            create_from_statement or create_from_website or create_from_expense
        if create_payment_group:
            company_id = self.env['account.journal'].browse(
                vals.get('journal_id')).company_id.id
            payment_group = self.env['account.payment.group'].create({
                'company_id': company_id,
                'partner_type': vals.get('partner_type'),
                'partner_id': vals.get('partner_id'),
                'payment_date': vals.get('date', fields.Date.context_today(self)),
                'communication': vals.get('communication'),
            })
            vals['payment_group_id'] = payment_group.id
        if not vals.get('currency_id'):
            vals['currency_id'] = self.env.user.company_id.currency_id.id
        #raise ValidationError('%s'%(vals))
        if 'payment_type_copy' in vals:
            vals['payment_type'] = vals['payment_type_copy']
            del vals['payment_type_copy']
        if 'destination_journal_id' in vals:
            del vals['destination_journal_id']
        payment = super(AccountPayment, self).create(vals)
        if payment.move_id and payment.currency_id.id != payment.company_id.currency_id.id \
                and abs(payment.amount_company_currency) > 0:
            for move_line in payment.move_id.line_ids:
                if move_line.debit > 0:
                    move_line.with_context({'check_move_validity': False}).write({'debit': abs(payment.amount_company_currency)})
                if move_line.credit > 0:
                    move_line.with_context({'check_move_validity': False}).write({'credit': abs(payment.amount_company_currency)})
        #if payment.move_id:
        #    for move_line in payment.move_id.line_ids:
                #if payment.is_internal_transfer and payment.payment_type == 'outbound':
                #    if move_line.account_id.account_type in ['liability_payable','asset_receivable']:
                #        move_line.with_context({'check_move_validity': False}).write({'account_id': payment.company_id.account_journal_payment_credit_account_id.id})
                #    if move_line.account_id.account_type in ['asset_cash']:
                #        move_line.with_context({'check_move_validity': False}).write({'account_id': payment.company_id.transfer_account_id.id})
               #if payment.is_internal_transfer and payment.payment_type == 'inbound':
                #    if move_line.account_id.account_type in ['liability_payable','asset_receivable']:
                #        move_line.with_context({'check_move_validity': False}).write({'account_id': payment.company_id.account_journal_payment_debit_account_id.id})
                #    if move_line.account_id.account_type in ['asset_cash']:
                #        move_line.with_context({'check_move_validity': False}).write({'account_id': payment.company_id.transfer_account_id.id})
        if create_payment_group:
            payment.payment_group_id.post()
        return payment

    @api.depends('invoice_line_ids', 'payment_type', 'partner_type', 'partner_id')
    def _compute_destination_account_id(self):
        """
        If we are paying a payment gorup with paylines, we use account
        of lines that are going to be paid
        """
        for rec in self:
            to_pay_account = rec.payment_group_id.to_pay_move_line_ids.mapped(
                'account_id')
            if len(to_pay_account) > 1:
                raise ValidationError(_(
                    'To Pay Lines must be of the same account!'))
            elif len(to_pay_account) == 1:
                rec.destination_account_id = to_pay_account[0]
            else:
                super(AccountPayment, rec)._compute_destination_account_id()

    def show_details(self):
        """
        Metodo para mostrar form editable de payment, principalmente para ser
        usado cuando hacemos ajustes y el payment group esta confirmado pero
        queremos editar una linea
        """
        return {
            'name': _('Payment Lines'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'account.payment',
            'target': 'new',
            'res_id': self.id,
            'context': self._context,
        }

    def _get_shared_move_line_vals(
            self, debit, credit, amount_currency, move_id, invoice_id=False):
        """
        Si se esta forzando importe en moneda de cia, usamos este importe
        para debito/credito
        """
        res = super(AccountPayment, self)._get_shared_move_line_vals(
            debit, credit, amount_currency, move_id, invoice_id=invoice_id)
        if self.force_amount_company_currency:
            if res.get('debit', False):
                res['debit'] = self.force_amount_company_currency
            if res.get('credit', False):
                res['credit'] = self.force_amount_company_currency
        return res

    def _get_move_vals(self, journal=None):
        """If we have a communication on payment group append it before
        payment communication
        """
        vals = super(AccountPayment, self)._get_move_vals(journal=journal)
        if self.payment_group_id.communication:
            vals['ref'] = "%s%s" % (
                self.payment_group_id.communication,
                self.communication and ": %s" % self.communication or "")
        return vals


    def _prepare_payment_moves(self):
        res = super(AccountPayment, self)._prepare_payment_moves()
        for i,rec in enumerate(self):
            if rec.currency_id.id != rec.company_id.currency_id.id and rec.payment_type == 'inbound':
                amount_debit = res[i]['line_ids'][0][2]['debit']
                amount_credit = res[i]['line_ids'][0][2]['credit']
                if amount_credit > 0 and rec.signed_amount_company_currency != amount_credit:
                    res[i]['line_ids'][0][2]['credit'] = rec.signed_amount_company_currency
                amount_debit = res[i]['line_ids'][1][2]['debit']
                amount_credit = res[i]['line_ids'][1][2]['credit']
                if amount_debit > 0 and rec.signed_amount_company_currency != amount_debit:
                    res[i]['line_ids'][1][2]['debit'] = rec.signed_amount_company_currency
        return res
