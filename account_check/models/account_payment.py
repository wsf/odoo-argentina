##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
from odoo import fields, models, _, api
from odoo.exceptions import UserError, ValidationError
import logging
# import odoo.addons.decimal_precision as dp
_logger = logging.getLogger(__name__)


class AccountPayment(models.Model):

    _inherit = 'account.payment'

    # we add this field for better usability on issue checks and received
    # checks. We keep m2m field for backward compatibility where we allow to
    # use more than one check per payment
    check_id = fields.Many2one(
        'account.check',
        string='Cheque',
    )
    check_type = fields.Selection(
        [('issue_check', 'Cheque Emitido'), ('third_check', 'Cheque de Terceros')],
        related='l10n_latam_check_id.check_id.type'
    )


    def action_post(self):
        res = super(AccountPayment, self).action_post()
        for rec in self:
            if rec.payment_method_code == 'new_third_party_checks':
                bank = rec.l10n_latam_check_bank_id
                check_id = rec.create_check('third_check','holding',bank)
                rec.check_id = check_id.id
            elif rec.payment_method_code == 'out_third_party_checks':
                #check_id = rec.l10n_latam_check_id.check_id
                rec.do_checks_operations()
            if rec.payment_method_code == 'check_printing':
                bank = rec.l10n_latam_check_bank_id
                check_id = rec.create_check('issue_check','handed',bank)
                rec.check_id = check_id.id
                #raise ValidationError('Estamos aca %s'%(check_id.number))
        return res


    def create_check(self, check_type, operation, bank):
        self.ensure_one()
        checkbook_id = None
        if check_type == 'issue_check':
            checkbook_id = self.journal_id.checkbook_ids.filtered(lambda l: l.state == 'active')
            if len(checkbook_id) != 1:
                raise ValidationError('No hay chequeras disponibles')
            checkbook_id.next_number = checkbook_id.next_number + 1
            self.check_number = checkbook_id.next_number

        check_vals = {
            'bank_id': bank.id,
            'owner_name': self.partner_id.name,
            'owner_vat': self.l10n_latam_check_issuer_vat,
            'number': self.check_number,
            'name': self.check_number,
            #'checkbook_id': self.checkbook_id.id,
            'issue_date': self.date,
            'type': check_type,
            'journal_id': self.journal_id.id,
            'amount': self.amount,
            'payment_date': self.l10n_latam_check_payment_date,
            'payment_id': self.id,
            'currency_id': self.currency_id.id,
            'amount_company_currency': self.amount_company_currency,
        }
        if checkbook_id:
            check_vals['checkbook_id'] = checkbook_id.id
        check = self.env['account.check'].create(check_vals)
        if operation:
            check._add_operation(
                operation, self, self.partner_id, date=self.date)
        return check

    def do_checks_operations(self, vals={}, cancel=False):
        """
        Check attached .ods file on this module to understand checks workflows
        This method is called from:
        * cancellation of payment to execute delete the right operation and
            unlink check if needed
        * from _get_liquidity_move_line_vals to add check operation and, if
            needded, change payment vals and/or create check and
        TODO si queremos todos los del operation podriamos moverlos afuera y
        simplificarlo ya que es el mismo en casi todos
        Tambien podemos simplficiar las distintas opciones y como se recorren
        los if
        """
        self.ensure_one()
        rec = self
        if not rec.check_type:
            # continue
            return vals
        if (
                rec.payment_method_code == 'received_third_check' and
                rec.payment_type == 'inbound'
                # el chequeo de partner type no seria necesario
                # un proveedor nos podria devolver plata con un cheque
                # and rec.partner_type == 'customer'
        ):
            operation = 'holding'
            if cancel:
                _logger.info('Cancel Receive Check')
                rec.check_ids._del_operation(self)
                rec.check_ids.unlink()
                return None

            _logger.info('Receive Check')
            check = self.create_check(
                    'third_check', operation, self.check_bank_id)
            if not vals:
                vals = {}
            vals['date_maturity'] = self.check_payment_date
            vals['account_id'] = check.get_third_check_account().id
            vals['name'] = _('Receive check %s') % check.name
        elif (
                rec.payment_method_code == 'delivered_third_check' and
                rec.payment_type == 'transfer'):
            # si el cheque es entregado en una transferencia tenemos tres
            # opciones
            # TODO we should make this method selectable for transfers
            inbound_method = (
                rec.destination_journal_id.inbound_payment_method_ids)
            # si un solo inbound method y es received third check
            # entonces consideramos que se esta moviendo el cheque de un diario
            # al otro
            if len(inbound_method) == 1 and (
                    inbound_method.code == 'received_third_check'):
                if cancel:
                    _logger.info('Cancel Transfer Check')
                    for check in rec.check_ids:
                        check._del_operation(self)
                        check._del_operation(self)
                        receive_op = check._get_operation('holding')
                        if receive_op.origin._name == 'account.payment':
                            check.journal_id = receive_op.origin.journal_id.id
                    return None

                _logger.info('Transfer Check')
                # get the account before changing the journal on the check
                vals['account_id'] = rec.check_ids.get_third_check_account().id
                rec.check_ids._add_operation(
                    'transfered', rec, False, date=rec.date)
                rec.check_ids._add_operation(
                    'holding', rec, False, date=rec.date)
                rec.check_ids.write({
                    'journal_id': rec.destination_journal_id.id})
                vals['name'] = _('Transfer checks %s') % ', '.join(
                    rec.check_ids.mapped('name'))
            elif rec.destination_journal_id.type == 'cash':
                if cancel:
                    _logger.info('Cancel Sell Check')
                    rec.check_ids._del_operation(self)
                    return None

                _logger.info('Sell Check')
                rec.check_ids._add_operation(
                    'selled', rec, False, date=rec.date)
                vals['account_id'] = rec.check_ids.get_third_check_account().id
                vals['name'] = _('Sell check %s') % ', '.join(
                    rec.check_ids.mapped('name'))
            # bank
            else:
                if cancel:
                    _logger.info('Cancel Deposit Check')
                    rec.check_ids._del_operation(self)
                    return None

                _logger.info('Deposit Check')
                rec.check_ids._add_operation(
                    'deposited', rec, False, date=rec.date)
                vals['account_id'] = rec.check_ids.get_third_check_account().id
                vals['name'] = _('Deposit checks %s') % ', '.join(
                    rec.check_ids.mapped('name'))
        elif (
                #rec.payment_method_code == 'delivered_third_check' and
                rec.payment_method_code == 'out_third_party_checks' and
                rec.payment_type == 'outbound'
                # el chequeo del partner type no es necesario
                # podriamos entregarlo a un cliente
                # and rec.partner_type == 'supplier'
        ):
            if cancel:
                _logger.info('Cancel Deliver Check')
                rec.check_ids._del_operation(self)
                return None

            _logger.info('Deliver Check')
            check = rec.l10n_latam_check_id.check_id
            operation = check._add_operation(
                'delivered', rec, rec.partner_id, date=rec.date)
            check.state = 'delivered'
            vals['account_id'] = rec.journal_id.default_account_id.id
            vals['name'] = _('Deliver checks %s') % ', '.join(rec.check_id.mapped('name'))
        elif (
                rec.payment_method_code == 'issue_check' and
                rec.payment_type == 'outbound'
                # el chequeo del partner type no es necesario
                # podriamos entregarlo a un cliente
                # and rec.partner_type == 'supplier'
        ):
            if cancel:
                _logger.info('Cancel Hand/debit Check')
                rec.check_ids._del_operation(self)
                rec.check_ids.unlink()
                return None

            _logger.info('Hand/debit Check')
            # if check is deferred, hand it and later debit it change account
            # if check is current, debit it directly
            # operation = 'debited'
            # al final por ahora depreciamos esto ya que deberiamos adaptar
            # rechazos y demas, deferred solamente sin fecha pero con cuenta
            # puente
            # if self.check_subtype == 'deferred':

            #raise ValidationError('estamos aca %s'%(self.company_id.deferred_check_account_id.code))
            if not self.company_id.deferred_check_account_id:
                raise ValidationError('No hay cuenta de cheques diferidos definida')
            vals = {}
            vals['account_id'] = self.company_id.deferred_check_account_id.id
            operation = 'handed'
            check = self.create_check(
                'issue_check', operation, self.check_bank_id)
            vals['date_maturity'] = self.check_payment_date
            vals['name'] = _('Hand check %s') % check.name
        elif (
                rec.payment_method_code == 'issue_check' and
                rec.payment_type == 'transfer' and
                rec.destination_journal_id.type == 'cash'):
            if cancel:
                _logger.info('Cancel Withdrawal Check')
                rec.check_ids._del_operation(self)
                rec.check_ids.unlink()
                return None

            _logger.info('Withdraw Check')
            self.create_check('issue_check', 'withdrawed', self.check_bank_id)
            vals['name'] = _('Withdraw with checks %s') % ', '.join(
                rec.check_ids.mapped('name'))
            vals['date_maturity'] = self.check_payment_date
            # if check is deferred, change account
            # si retiramos por caja directamente lo sacamos de banco
            # if self.check_subtype == 'deferred':
            #     vals['account_id'] = self.company_id._get_check_account(
            #         'deferred').id
        else:
            raise UserError(_(
                'This operatios is not implemented for checks:\n'
                '* Payment type: %s\n'
                '* Partner type: %s\n'
                '* Payment method: %s\n'
                '* Destination journal: %s\n' % (
                    rec.payment_type,
                    rec.partner_type,
                    rec.payment_method_code,
                    rec.destination_journal_id.type)))
        return vals

    def _get_liquidity_move_line_vals(self, amount):
        vals = super(AccountPayment, self)._get_liquidity_move_line_vals(
            amount)
        vals = self.do_checks_operations(vals=vals)
        return vals

    def _get_counterpart_move_line_vals(self, invoice=False):
        vals = super(AccountPayment, self)._get_counterpart_move_line_vals(
            invoice=invoice)
        force_account_id = self._context.get('force_account_id')
        if force_account_id:
            vals['account_id'] = force_account_id
        return vals

    def _split_aml_line_per_check(self, move):
        """ Take an account mvoe, find the move lines related to check and
        split them one per earch check related to the payment
        """
        self.ensure_one()
        res = self.env['account.move.line']
        move.button_cancel()
        checks = self.check_ids
        aml = move.line_ids.with_context(check_move_validity=False).filtered(
            lambda x: x.name != self.name)
        if len(aml) > 1:
            raise UserError(
                _('Seems like this move has been already splited'))
        elif len(aml) == 0:
            raise UserError(
                _('There is not move lines to split'))

        amount_field = 'credit' if aml.credit else 'debit'
        new_name = _('Deposit check %s') if aml.credit else \
            aml.name + _(' check %s')

        # if the move line has currency then we are delivering checks on a
        # different currency than company one
        currency = aml.currency_id
        currency_sign = amount_field == 'debit' and 1.0 or -1.0
        aml.write({
            'name': new_name % checks[0].name,
            amount_field: checks[0].amount_company_currency,
            'amount_currency': currency and currency_sign * checks[0].amount,
        })
        res |= aml
        checks -= checks[0]
        for check in checks:
            res |= aml.copy({
                'name': new_name % check.name,
                amount_field: check.amount_company_currency,
                'payment_id': self.id,
                'amount_currency': currency and currency_sign * check.amount,
            })
        move.post()
        return res

