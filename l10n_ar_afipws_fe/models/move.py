##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
from .pyi25 import PyI25
from odoo import fields, models, api, _
from odoo.exceptions import UserError,ValidationError
import base64
from io import BytesIO
import logging
import sys
import traceback
from datetime import datetime, date

import qrcode
import json


_logger = logging.getLogger(__name__)

TYPE_REVERSE_MAP = {
    'entry': 'entry',
    'out_invoice': 'out_refund',
    'out_refund': 'entry',
    'in_invoice': 'in_refund',
    'in_refund': 'entry',
    'out_receipt': 'entry',
    'in_receipt': 'entry',
}

try:
    from pysimplesoap.client import SoapFault
except ImportError:
    _logger.debug('Can not `from pyafipws.soap import SoapFault`.')

class AccountJournal(models.Model):
    _inherit = 'account.journal'

class IrSequence(models.Model):
    _inherit = 'ir.sequence'

    journal_id = fields.Many2one('account.journal','Diario facturacion')

class AccountMove(models.Model):
    _inherit = "account.move"

    afip_mypyme_sca_adc = fields.Selection(selection=[('SCA','Sistema Circulacion Abierta'),('ADC','Agente Deposito Colectivo')],string='SCA o ADC',default='SCA')
    afip_auth_verify_type = fields.Selection(
        related='company_id.afip_auth_verify_type',
    )
    document_number = fields.Char(
        copy=False,
        string='Document Number',
        readonly=True
    )
    afip_batch_number = fields.Integer(
        copy=False,
        string='Batch Number',
        readonly=True
    )
    afip_auth_verify_result = fields.Selection([
        ('A', 'Aprobado'), ('O', 'Observado'), ('R', 'Rechazado')],
        string='AFIP authorization verification result',
        copy=False,
        readonly=True,
    )
    afip_auth_verify_observation = fields.Char(
        string='AFIP authorization verification observation',
        copy=False,
        readonly=True,
    )
    afip_auth_mode = fields.Selection([
        ('CAE', 'CAE'), ('CAI', 'CAI'), ('CAEA', 'CAEA')],
        string='AFIP authorization mode',
        copy=False,
        readonly=True,
        states={'draft': [('readonly', False)]},
    )
    afip_auth_code = fields.Char(
        copy=False,
        string='CAE/CAI/CAEA Code',
        readonly=True,
        oldname='afip_cae',
        size=24,
        states={'draft': [('readonly', False)]},
    )
    afip_auth_code_due = fields.Date(
        copy=False,
        readonly=True,
        oldname='afip_cae_due',
        string='CAE/CAI/CAEA due Date',
        states={'draft': [('readonly', False)]},
    )
    # for compatibility
    afip_cae = fields.Char(
        related='afip_auth_code',
        readonly=False,
        string='CAE (only for backward compatibility)'
    )
    afip_cae_due = fields.Date(
        related='afip_auth_code_due',
        readonly=False,
        string='CAE due date (only for backward compatibility)'
    )

    afip_barcode = fields.Char(
        compute='_compute_barcode',
        string='AFIP Barcode',
        #store=True
    )
    # backport of v13 for qweb report
    l10n_ar_afip_barcode = fields.Char(
        compute='_compute_barcode',
        string='AFIP Barcode',
        #store=True
    )
    afip_barcode_img = fields.Binary(
        compute='_compute_barcode',
        string='AFIP Barcode Image',
        #store=True
    )
    afip_message = fields.Text(
        string='AFIP Message',
        copy=False,
    )
    afip_xml_request = fields.Text(
        string='AFIP XML Request',
        copy=False,
    )
    afip_xml_response = fields.Text(
        string='AFIP XML Response',
        copy=False,
    )
    afip_result = fields.Selection([
        ('', 'n/a'),
        ('A', 'Aceptado'),
        ('R', 'Rechazado'),
        ('O', 'Observado')],
        'Resultado',
        readonly=True,
        states={'draft': [('readonly', False)]},
        copy=False,
        help="AFIP request result"
    )
    validation_type = fields.Char(
        'Validation Type',
        compute='_compute_validation_type',
        store=True
    )
    afip_fce_es_anulacion = fields.Boolean(
        string='FCE: Es anulacion?',
        help='Solo utilizado en comprobantes MiPyMEs (FCE) del tipo débito o crédito. Debe informar:\n'
        '- SI: sí el comprobante asociado (original) se encuentra rechazado por el comprador\n'
        '- NO: sí el comprobante asociado (original) NO se encuentra rechazado por el comprador'
    )
    fe_qr_url = fields.Char('Data QR',compute='_compute_qrcode')
    qr_code   = fields.Binary('AFIP QR Image',compute='_compute_qrcode')

    def unlink(self):
        for rec in self:
            if rec.move_type in ['out_invoice','out_refund'] and rec.afip_auth_code and rec.state == 'posted':
                raise ValidationError('No se puede borrar una factura con CAE')
        return super(AccountMove, self).unlink()

    def _compute_show_credit_button(self):
        for rec in self:
            res = True
            if rec.move_type in ['in_invoice','out_invoice']:
                if rec.state == 'posted':
                    if rec.payment_state not in ['paid','reversed']:
                        res = True
                    else:
                        res = False
                else:
                    res = False
            else:
                res = False
            rec.show_credit_button = res

    show_credit_button = fields.Boolean('show_credit_button',compute=_compute_show_credit_button)

    @api.depends('journal_id', 'afip_auth_code')
    def _compute_validation_type(self):
        for rec in self:
            if rec.journal_id.afip_ws and not rec.afip_auth_code:
                validation_type = self.env[
                    'res.company']._get_environment_type()
                # if we are on homologation env and we dont have certificates
                # we validate only locally
                if validation_type == 'homologation':
                    try:
                        rec.company_id.get_key_and_certificate(validation_type)
                    except Exception:
                        validation_type = False
                rec.validation_type = validation_type

    #@api.depends('afip_auth_code')
    def _compute_barcode(self):
        for rec in self:
            barcode = False
            if rec.afip_auth_code:
                cae_due = ''.join(
                    [c for c in str(
                        rec.afip_auth_code_due or '') if c.isdigit()])
                barcode = ''.join(
                    [str(rec.company_id.vat),
                        "%03d" % int(rec.l10n_latam_document_type_id.code),
                        "%05d" % int(rec.journal_id.l10n_ar_afip_pos_number),
                        str(rec.afip_auth_code), cae_due])
                rec.l10n_ar_afip_barcode = barcode
                barcode = barcode + rec.verification_digit_modulo10(barcode)
            rec.afip_barcode = barcode
            rec.afip_barcode_img = rec._make_image_I25(barcode)

    @api.model
    def _make_image_I25(self, barcode):
        "Generate the required barcode Interleaved of 7 image using PIL"
        image = False
        if barcode:
            # create the helper:
            pyi25 = PyI25()
            output = BytesIO()
            # call the helper:
            bars = ''.join([c for c in barcode if c.isdigit()])
            if not bars:
                bars = "00"
            pyi25.GenerarImagen(bars, output, extension="PNG")
            # get the result and encode it for openerp binary field:
            image = base64.b64encode(output.getvalue())
            output.close()
        return image

    @api.model
    def verification_digit_modulo10(self, code):
        "Calculate the verification digit 'modulo 10'"
        # Step 1: sum all digits in odd positions, left to right
        code = code.strip()
        if not code or not code.isdigit():
            return ''
        etapa1 = sum([int(c) for i, c in enumerate(code) if not i % 2])
        # Step 2: multiply the step 1 sum by 3
        etapa2 = etapa1 * 3
        # Step 3: start from the left, sum all the digits in even positions
        etapa3 = sum([int(c) for i, c in enumerate(code) if i % 2])
        # Step 4: sum the results of step 2 and 3
        etapa4 = etapa2 + etapa3
        # Step 5: the minimun value that summed to step 4 is a multiple of 10
        digito = 10 - (etapa4 - (int(etapa4 // 10) * 10))
        if digito == 10:
            digito = 0
        return str(digito)

    def get_related_invoices_data(self):
        """
        List related invoice information to fill CbtesAsoc.
        """
        self.ensure_one()
        # for now we only get related document for debit and credit notes
        # because, for eg, an invoice can not be related to an invocie and
        # that happens if you choose the modify option of the credit note
        # wizard. A mapping of which documents can be reported as related
        # documents would be a better solution
        if self.l10n_latam_document_type_id.internal_type in ['debit_note', 'credit_note'] \
                and self.invoice_origin:
            return self.search([
                ('commercial_partner_id', '=', self.commercial_partner_id.id),
                ('company_id', '=', self.company_id.id),
                ('document_number', '=', self.invoice_origin),
                ('id', '!=', self.id),
                ('l10n_latam_document_type_id.l10n_ar_letter', '=', self.l10n_latam_document_type_id.l10n_ar_letter),
                ('l10n_latam_document_type_id', '!=', self.l10n_latam_document_type_id.id),
                ('state', 'not in', ['draft', 'cancel'])],
                limit=1)
        else:
            return self.browse()

    def action_post(self):
        """
        The last thing we do is request the cae because if an error occurs
        after cae requested, the invoice has been already validated on afip
        """
        for rec in self:
            rec.compute_taxes()
        res = super(AccountMove, self).action_post()
        self.check_afip_auth_verify_required()
        self.do_pyafipws_request_cae()
        return res

    # para cuando se crea, por ej, desde ventas o contratos
    @api.constrains('partner_id')
    # para cuando se crea manualmente la factura
    @api.onchange('partner_id')
    def _set_afip_journal(self):
        """
        Si es factura electrónica y es del exterior, buscamos diario
        para exportación
        TODO: implementar similar para elegir bono fiscal o factura con detalle
        """
        for rec in self.filtered(lambda x: (
                x.journal_id.l10n_ar_afip_pos_system == 'RLI_RLM' and
                x.journal_id.type == 'sale')):

            res_code = rec.commercial_partner_id.l10n_ar_afip_responsibility_type_id.\
                code
            ws = rec.journal_id.afip_ws
            journal = self.env['account.journal']
            domain = [
                ('company_id', '=', rec.company_id.id),
                ('point_of_sale_type', '=', 'electronic'),
                ('type', '=', 'sale'),
            ]
            # TODO mejorar que aca buscamos por codigo de resp mientras que
            # el mapeo de tipo de documentos es configurable por letras y,
            # por ejemplo, si se da letra e de RI a RI y se genera factura
            # para un RI queriendo forzar diario de expo, termina dando error
            # porque los ws y los res_code son incompatibles para esta logica.
            # El error lo da el metodo check_journal_document_type_journal
            # porque este metodo trata de poner otro diario sin actualizar
            # el tipo de documento
            if ws == 'wsfe' and res_code in ['8', '9', '10']:
                domain.append(('afip_ws', '=', 'wsfex'))
                journal = journal.search(domain, limit=1)
            elif ws == 'wsfex' and res_code not in ['8', '9', '10']:
                domain.append(('afip_ws', '=', 'wsfe'))
                journal = journal.search(domain, limit=1)

            if journal:
                rec.journal_id = journal.id

    def check_afip_auth_verify_required(self):
        verify_codes = [
            "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
            "13", "15", "19", "20", "21", "49", "51", "52", "53",
            "54", "60", "61", "63", "64"
        ]
        verification_required = self.filtered(
            lambda inv: inv.move_type in ['in_invoice', 'in_refund'] and
            inv.afip_auth_verify_type == 'required' and
            (inv.document_type_id and
             inv.document_type_id.code in verify_codes) and
            not inv.afip_auth_verify_result)
        if verification_required:
            raise UserError(_(
                'You can not validate invoice as AFIP authorization '
                'verification is required'))

    def verify_on_afip(self):
        """
cbte_modo = "CAE"                    # modalidad de emision: CAI, CAE,
CAEA
cuit_emisor = "20267565393"          # proveedor
pto_vta = 4002                       # punto de venta habilitado en AFIP
cbte_tipo = 1                        # 1: factura A (ver tabla de parametros)
cbte_nro = 109                       # numero de factura
cbte_fch = "20131227"                # fecha en formato aaaammdd
imp_total = "121.0"                  # importe total
cod_autorizacion = "63523178385550"  # numero de CAI, CAE o CAEA
doc_tipo_receptor = 80               # CUIT (obligatorio Facturas A o M)
doc_nro_receptor = "30628789661"     # numero de CUIT del cliente

ok = wscdc.ConstatarComprobante(
    cbte_modo, cuit_emisor, pto_vta, cbte_tipo,
    cbte_nro, cbte_fch, imp_total, cod_autorizacion,
    doc_tipo_receptor, doc_nro_receptor)

print "Resultado:", wscdc.Resultado
print "Mensaje de Error:", wscdc.ErrMsg
print "Observaciones:", wscdc.Obs
        """
        afip_ws = "wscdc"
        ws = self.company_id.get_connection(afip_ws).connect()
        for inv in self:
            cbte_modo = inv.afip_auth_mode
            cod_autorizacion = inv.afip_auth_code
            if not cbte_modo or not cod_autorizacion:
                raise UserError(_(
                    'AFIP authorization mode and Code are required!'))

            # get issuer and receptor depending on supplier or customer invoice
            if inv.type in ['in_invoice', 'in_refund']:
                issuer = inv.commercial_partner_id
                receptor = inv.company_id.partner_id
            else:
                issuer = inv.company_id.partner_id
                receptor = inv.commercial_partner_id

            cuit_emisor = issuer.cuit_required()

            receptor_doc_code = str(receptor.main_id_category_id.afip_code)
            doc_tipo_receptor = receptor_doc_code or '99'
            doc_nro_receptor = (
                receptor_doc_code and receptor.main_id_number or "0")
            doc_type = inv.document_type_id
            if (
                    doc_type.document_letter_id.name in ['A', 'M'] and
                    doc_tipo_receptor != '80' or not doc_nro_receptor):
                raise UserError(_(
                    'Para Comprobantes tipo A o tipo M:\n'
                    '*  el documento del receptor debe ser CUIT\n'
                    '*  el documento del Receptor es obligatorio\n'
                ))

            cbte_nro = inv.invoice_number
            pto_vta = inv.journal_id.l10n_ar_afip_pos_number
            cbte_tipo = doc_type.code
            if not pto_vta or not cbte_nro or not cbte_tipo:
                raise UserError(_(
                    'Point of sale and document number and document type '
                    'are required!'))
            cbte_fch = inv.invoice_date
            if not cbte_fch:
                raise UserError(_('Invoice Date is required!'))
            cbte_fch = cbte_fch.strftime('%Y%m%d')
            imp_total = str("%.2f" % inv.amount_total)

            _logger.info('Constatando Comprobante en afip')

            # atrapado de errores en afip
            msg = False
            try:
                ws.ConstatarComprobante(
                    cbte_modo, cuit_emisor, pto_vta, cbte_tipo, cbte_nro,
                    cbte_fch, imp_total, cod_autorizacion, doc_tipo_receptor,
                    doc_nro_receptor)
            except SoapFault as fault:
                msg = 'Falla SOAP %s: %s' % (
                    fault.faultcode, fault.faultstring)
            except Exception as e:
                msg = e
            except Exception:
                if ws.Excepcion:
                    # get the exception already parsed by the helper
                    msg = ws.Excepcion
                else:
                    # avoid encoding problem when raising error
                    msg = traceback.format_exception_only(
                        sys.exc_type,
                        sys.exc_value)[0]
            if msg:
                raise UserError(_('AFIP Verification Error. %s' % msg))

            inv.write({
                'afip_auth_verify_result': ws.Resultado,
                'afip_auth_verify_observation': '%s%s' % (ws.Obs, ws.ErrMsg)
            })

    def do_pyafipws_request_cae(self):
        "Request to AFIP the invoices' Authorization Electronic Code (CAE)"
        for inv in self:
            # Ignore invoices with cae (do not check date)
            if inv.afip_auth_code:
                continue

            if inv.journal_id.l10n_ar_afip_pos_system not in ['RLI_RLM','FEERCEL']:
                continue
            if inv.journal_id.l10n_ar_afip_pos_system != 'FEERCEL':
                afip_ws = inv.journal_id.afip_ws
            else:
                afip_ws = 'wsfex'
            # Ignore invoice if not ws on point of sale
            if not afip_ws:
                raise UserError(_(
                    'If you use electronic journals (invoice id %s) you need '
                    'configure AFIP WS on the journal') % (inv.id))

            # if no validation type and we are on electronic invoice, it means
            # that we are on a testing database without homologation
            # certificates
            if not inv.validation_type and afip_ws != 'wsfex':
                msg = (
                    'Factura validada solo localmente por estar en ambiente '
                    'de homologación sin claves de homologación')
                inv.write({
                    'afip_auth_mode': 'CAE',
                    'afip_auth_code': '68448767638166',
                    'afip_auth_code_due': inv.invoice_date,
                    'afip_result': '',
                    'afip_message': msg,
                })
                inv.message_post(body=msg)
                continue

            # get the electronic invoice type, point of sale and afip_ws:
            # import pdb;pdb.set_trace()
            commercial_partner = inv.commercial_partner_id
            country = commercial_partner.country_id
            journal = inv.journal_id
            pos_number = journal.l10n_ar_afip_pos_number
            doc_afip_code = inv.l10n_latam_document_type_id.code

            # authenticate against AFIP:
            ws = inv.company_id.get_connection(afip_ws).connect()

            if afip_ws == 'wsfex':
                if not country:
                    raise UserError(_(
                        'For WS "%s" country is required on partner' % (
                            afip_ws)))
                elif not country.code:
                    raise UserError(_(
                        'For WS "%s" country code is mandatory'
                        'Country: %s' % (
                            afip_ws, country.name)))
                elif not country.l10n_ar_afip_code:
                    raise UserError(_(
                        'For WS "%s" country afip code is mandatory'
                        'Country: %s' % (
                            afip_ws, country.name)))
            #ws_next_invoice_number = int(
            #    inv.journal_document_type_id.get_pyafipws_last_invoice(
            #    )['result']) + 1
            ws_next_invoice_number = int(
                inv.l10n_latam_document_type_id.get_pyafipws_last_invoice(inv)['result']) + 1
            # verify that the invoice is the next one to be registered in AFIP
            #if inv.invoice_number != ws_next_invoice_number:
            #    raise UserError(_(
            #        'Error!'
            #        'Invoice id: %i'
            #        'Next invoice number should be %i and not %i' % (
            #            inv.id,
            #            ws_next_invoice_number,
            #            inv.invoice_number)))

            partner_id_code = commercial_partner.l10n_latam_identification_type_id.l10n_ar_afip_code
            tipo_doc = partner_id_code or '99'
            nro_doc = \
                partner_id_code and commercial_partner.vat or "0"
            #cbt_desde = cbt_hasta = cbte_nro = inv.invoice_number
            cbt_desde = cbt_hasta = cbte_nro = ws_next_invoice_number
            concepto = tipo_expo = int(inv.l10n_ar_afip_concept)

            fecha_cbte = inv.invoice_date
            if afip_ws != 'wsmtxca':
                fecha_cbte = inv.invoice_date.strftime('%Y%m%d')

            mipyme_fce = int(doc_afip_code) in [201, 206, 211]
            # due date only for concept "services" and mipyme_fce
            if int(concepto) != 1 and int(doc_afip_code) not in [202, 203, 207, 208, 212, 213] or mipyme_fce:
                fecha_venc_pago = inv.invoice_date_due or inv.invoice_date
                if afip_ws != 'wsmtxca':
                    fecha_venc_pago = fecha_venc_pago.strftime('%Y%m%d')
            else:
                fecha_venc_pago = None

            # fecha de servicio solo si no es 1
            if int(concepto) != 1:
                fecha_serv_desde = inv.l10n_ar_afip_service_start
                fecha_serv_hasta = inv.l10n_ar_afip_service_end
                if afip_ws != 'wsmtxca':
                    fecha_serv_desde = fecha_serv_desde.strftime('%Y%m%d')
                    fecha_serv_hasta = fecha_serv_hasta.strftime('%Y%m%d')
            else:
                fecha_serv_desde = fecha_serv_hasta = None

            # invoice amount totals:
            amount_total = inv.amount_untaxed
            for move_tax in inv.move_tax_ids:
                amount_total += move_tax.tax_amount

            imp_total = str("%.2f" % amount_total)
            # ImpTotConc es el iva no gravado
            imp_tot_conc = str("%.2f" % inv.vat_untaxed_base_amount)
            # imp_tot_conc = str("%.2f" % inv.amount_untaxed)
            # tal vez haya una mejor forma, la idea es que para facturas c
            # no se pasa iva. Probamos hacer que vat_taxable_amount
            # incorpore a los imp cod 0, pero en ese caso termina reportando
            # iva y no lo queremos
            if inv.l10n_latam_document_type_id.l10n_ar_letter == 'C':
                imp_neto = str("%.2f" % inv.amount_untaxed)
            else:
                #imp_neto = str("%.2f" % inv.vat_taxable_amount)
                imp_neto = str("%.2f" % inv.vat_taxable_amount)
            imp_trib = str("%.2f" % inv.other_taxes_amount)
            # imp_iva = str("%.2f" % (inv.amount_total - (inv.amount_untaxed + inv.other_taxes_amount)))
            imp_iva = str("%.2f" % (inv.vat_amount))
            # se usaba para wsca..
            # imp_subtotal = str("%.2f" % inv.amount_untaxed)
            imp_op_ex = str("%.2f" % inv.vat_exempt_base_amount)
            moneda_id = inv.currency_id.l10n_ar_afip_code
            #moneda_ctz = round(1/inv.currency_id.rate,2)
            moneda_ctz = inv.currency_id.rate
            if not moneda_id:
                raise ValidationError('No esta definido el codigo AFIP en la moneda')


            CbteAsoc = inv.get_related_invoices_data()

            # create the invoice internally in the helper
            if afip_ws == 'wsfe':
                moneda_ctz = 1 / moneda_ctz
                inv.l10n_ar_currency_rate = moneda_ctz
                ws.CrearFactura(
                    concepto, tipo_doc, nro_doc, doc_afip_code, pos_number,
                    cbt_desde, cbt_hasta, imp_total, imp_tot_conc, imp_neto,
                    imp_iva,
                    imp_trib, imp_op_ex, fecha_cbte, fecha_venc_pago,
                    fecha_serv_desde, fecha_serv_hasta,
                    moneda_id, round(moneda_ctz,2)
                )
                if inv.other_taxes_amount > 0:
                    for move_tax in inv.move_tax_ids:
                        if move_tax.tax_id.tax_group_id.tax_type != 'vat':
                            tributo_id = move_tax.tax_id.tax_group_id.l10n_ar_tribute_afip_code
                            base_imp = str("%.2f" % move_tax.base_amount)
                            desc = move_tax.tax_id.name
                            importe = str("%.2f" % move_tax.tax_amount)
                            alic = None
                            ws.AgregarTributo(tributo_id, desc, base_imp, alic, importe)

            elif afip_ws == 'wsfex':
                # # foreign trade data: export permit, country code, etc.:
                if inv.invoice_incoterm_id:
                    incoterms = inv.invoice_incoterm_id.code
                    incoterms_ds = inv.invoice_incoterm_id.name
                    # máximo de 20 caracteres admite
                    incoterms_ds = incoterms_ds and incoterms_ds[:20]
                else:
                    incoterms = incoterms_ds = None
                # por lo que verificamos, se pide permiso existente solo
                # si es tipo expo 1 y es factura (codigo 19), para todo el
                # resto pasamos cadena vacia
                if int(doc_afip_code) == 19 and tipo_expo == 1:
                    # TODO investigar si hay que pasar si ("S")
                    permiso_existente = "N"
                else:
                    permiso_existente = ""
                obs_generales = inv.narration

                if inv.invoice_payment_term_id:
                    forma_pago = inv.invoice_payment_term_id.name
                    obs_comerciales = inv.invoice_payment_term_id.name
                else:
                    forma_pago = obs_comerciales = None

                idioma_cbte = 1     # invoice language: spanish / español

                # TODO tal vez podemos unificar este criterio con el del
                # citi que pide el cuit al partner
                # customer data (foreign trade):
                nombre_cliente = commercial_partner.name
                # se debe informar cuit pais o id_impositivo
                if nro_doc:
                    id_impositivo = nro_doc
                    cuit_pais_cliente = None
                elif country.code != 'AR' and nro_doc:
                    id_impositivo = None
                    if commercial_partner.is_company:
                        cuit_pais_cliente = country.cuit_juridica
                    else:
                        cuit_pais_cliente = country.cuit_fisica
                    if not cuit_pais_cliente:
                        raise UserError(_(
                            'No vat defined for the partner and also no CUIT '
                            'set on country'))

                domicilio_cliente = " - ".join([
                    commercial_partner.name or '',
                    commercial_partner.street or '',
                    commercial_partner.street2 or '',
                    commercial_partner.zip or '',
                    commercial_partner.city or '',
                ])
                pais_dst_cmp = commercial_partner.country_id.l10n_ar_afip_code
                ws.CrearFactura(
                    doc_afip_code, pos_number, cbte_nro, fecha_cbte,
                    imp_total, tipo_expo, permiso_existente, pais_dst_cmp,
                    nombre_cliente, cuit_pais_cliente, domicilio_cliente,
                    id_impositivo, moneda_id, round(1/moneda_ctz,2), obs_comerciales,
                    obs_generales, forma_pago, incoterms,
                    idioma_cbte, incoterms_ds
                )
            elif afip_ws == 'wsbfe':
                zona = 1  # Nacional (la unica devuelta por afip)
                # los responsables no inscriptos no se usan mas
                impto_liq_rni = 0.0
                imp_iibb = sum(inv.tax_line_ids.filtered(lambda r: (
                    r.tax_id.tax_group_id.type == 'perception' and
                    r.tax_id.tax_group_id.application == 'provincial_taxes')
                ).mapped('amount'))
                imp_perc_mun = sum(inv.tax_line_ids.filtered(lambda r: (
                    r.tax_id.tax_group_id.type == 'perception' and
                    r.tax_id.tax_group_id.application == 'municipal_taxes')
                ).mapped('amount'))
                imp_internos = sum(inv.tax_line_ids.filtered(
                    lambda r: r.tax_id.tax_group_id.application == 'others'
                ).mapped('amount'))
                imp_perc = sum(inv.tax_line_ids.filtered(lambda r: (
                    r.tax_id.tax_group_id.type == 'perception' and
                    # r.tax_id.tax_group_id.tax != 'vat' and
                    r.tax_id.tax_group_id.application == 'national_taxes')
                ).mapped('amount'))

                ws.CrearFactura(
                    tipo_doc, nro_doc, zona, doc_afip_code, pos_number,
                    cbte_nro, fecha_cbte, imp_total, imp_neto, imp_iva,
                    imp_tot_conc, impto_liq_rni, imp_op_ex, imp_perc, imp_iibb,
                    imp_perc_mun, imp_internos, moneda_id, round(moneda_ctz,2),
                    fecha_venc_pago
                )

            if afip_ws in ['wsfe', 'wsbfe']:
                if mipyme_fce:
                    # agregamos cbu para factura de credito electronica
                    if not inv.company_id.partner_id.bank_ids \
                            or not inv.company_id.partner_id.bank_ids[0].cbu:
                        raise ValidationError('La empresa no tiene declarado el CBU de su cuenta')
                    ws.AgregarOpcional(
                        opcional_id=2101,
                        valor=inv.company_id.partner_id.bank_ids[0].cbu)
                    ws.AgregarOpcional(
                        opcional_id=27,
                        valor=inv.afip_mypyme_sca_adc)
                elif int(doc_afip_code) in [202, 203, 207, 208, 212, 213]:
                    valor = inv.afip_fce_es_anulacion and 'S' or 'N'
                    ws.AgregarOpcional(
                        opcional_id=22,
                        valor=valor)

            # TODO ver si en realidad tenemos que usar un vat pero no lo
            # subimos
            if afip_ws not in ['wsfex', 'wsbfe']:
                #for vat in inv.move_tax_ids:vat_taxable_ids:
                for vat in inv.move_tax_ids:
                    if vat.tax_id.tax_group_id.tax_type == 'vat' and vat.tax_id.tax_group_id.l10n_ar_vat_afip_code != '2':
                            _logger.info('Adding VAT %s' % vat.tax_id.tax_group_id.name)
                            ws.AgregarIva(
                                vat.tax_id.tax_group_id.l10n_ar_vat_afip_code,
                                "%.2f" % vat.base_amount,
                                # "%.2f" % abs(vat.base_amount),
                                "%.2f" % vat.tax_amount,
                            )


            if CbteAsoc:
                # fex no acepta fecha
                doc_number = CbteAsoc.document_number.split('-')[1]
                invoice_date = str(CbteAsoc.invoice_date).replace('-','')
                if afip_ws == 'wsfex':
                    ws.AgregarCmpAsoc(
                        CbteAsoc.l10n_latam_document_type_id.document_type_id.code,
                        CbteAsoc.journal_id.l10n_ar_afip_pos_number,
                        doc_number,
                        self.company_id.vat,
                    )
                else:
                    ws.AgregarCmpAsoc(
                        CbteAsoc.l10n_latam_document_type_id.code,
                        CbteAsoc.journal_id.l10n_ar_afip_pos_number,
                        doc_number,
                        self.company_id.vat,
                        invoice_date,
                    )
            # Notas de debito
            if inv.l10n_latam_document_type_id.code in ['2','7','12','3','8','13'] and not CbteAsoc:
                year = date.today().year
                month = date.today().month
                day = date.today().day
                fecha_desde = str(year) + str(month).zfill(2) + '01'
                fecha_hasta = str(year) + str(month).zfill(2) + str(day).zfill(2)
                ws.AgregarPeriodoComprobantesAsociados(fecha_desde,fecha_hasta)



            # analize line items - invoice detail
            # wsfe do not require detail
            if afip_ws != 'wsfe':
                for line in inv.invoice_line_ids:
                    codigo = line.product_id.default_code
                    # unidad de referencia del producto si se comercializa
                    # en una unidad distinta a la de consumo
                    # uom is not mandatory, if no UOM we use "unit"
                    if not line.product_uom_id:
                        umed = '7'
                    elif not line.product_uom_id.l10n_ar_afip_code:
                        raise UserError(_(
                            'Not afip code con producto UOM %s' % (
                                line.product_uom_id.name)))
                    else:
                        umed = line.product_uom_id.l10n_ar_afip_code
                    # cod_mtx = line.uom_id.afip_code
                    ds = line.name
                    qty = line.quantity
                    precio = line.price_unit
                    importe = line.price_subtotal
                    # calculamos bonificacion haciendo teorico menos importe
                    bonif = line.discount and str(
                        "%.2f" % (precio * qty - importe)) or None
                    if afip_ws in ['wsmtxca', 'wsbfe']:
                        # TODO No lo estamos usando. Borrar?
                        # if not line.product_id.uom_id.afip_code:
                        #     raise UserError(_(
                        #         'Not afip code con producto UOM %s' % (
                        #             line.product_id.uom_id.name)))
                        # u_mtx = (
                        #     line.product_id.uom_id.afip_code or
                        #     line.uom_id.afip_code)
                        iva_id = line.vat_tax_id.tax_group_id.afip_code
                        vat_taxes_amounts = line.vat_tax_id.compute_all(
                            line.price_unit, inv.currency_id, line.quantity,
                            product=line.product_id,
                            partner=inv.partner_id)
                        imp_iva = sum(
                            [x['amount'] for x in vat_taxes_amounts['taxes']])
                        if afip_ws == 'wsmtxca':
                            raise UserError(
                                _('WS wsmtxca Not implemented yet'))
                            # ws.AgregarItem(
                            #     u_mtx, cod_mtx, codigo, ds, qty, umed,
                            #     precio, bonif, iva_id, imp_iva,
                            #     importe + imp_iva)
                        elif afip_ws == 'wsbfe':
                            sec = ""  # Código de la Secretaría (TODO usar)
                            ws.AgregarItem(
                                codigo, sec, ds, qty, umed, precio, bonif,
                                iva_id, importe + imp_iva)
                    elif afip_ws == 'wsfex':
                        ws.AgregarItem(
                            codigo, ds, qty, umed, precio, "%.2f" % importe,
                            bonif)

            # Request the authorization! (call the AFIP webservice method)
            vto = None
            msg = False
            try:
                if afip_ws == 'wsfe':
                    ws.CAESolicitar()
                    vto = ws.Vencimiento
                elif afip_ws == 'wsmtxca':
                    ws.AutorizarComprobante()
                    vto = ws.Vencimiento
                elif afip_ws == 'wsfex':
                    ws.Authorize(inv.id)
                    vto = ws.FchVencCAE
                elif afip_ws == 'wsbfe':
                    ws.Authorize(inv.id)
                    vto = ws.Vencimiento
            except SoapFault as fault:
                msg = 'Falla SOAP %s: %s' % (
                    fault.faultcode, fault.faultstring)
            except Exception as e:
                msg = e
            except Exception:
                if ws.Excepcion:
                    # get the exception already parsed by the helper
                    msg = ws.Excepcion
                else:
                    # avoid encoding problem when raising error
                    msg = traceback.format_exception_only(
                        sys.exc_type,
                        sys.exc_value)[0]
            if msg:
                _logger.info(_('AFIP Validation Error. %s' % msg)+' XML Request: %s XML Response: %s' % (
                    ws.XmlRequest, ws.XmlResponse))
                raise UserError(_('AFIP Validation Error. %s' % msg))

            msg = u"\n".join([ws.Obs or "", ws.ErrMsg or ""])
            if not ws.CAE or ws.Resultado != 'A':
                raise UserError(_('AFIP Validation Error. %s' % msg))
            # TODO ver que algunso campos no tienen sentido porque solo se
            # escribe aca si no hay errores
            _logger.info('CAE solicitado con exito. CAE: %s. Resultado %s' % (
                ws.CAE, ws.Resultado))
            if afip_ws == 'wsbfe':
                vto = datetime.strftime(
                    datetime.strptime(vto, '%d/%m/%Y'), '%Y%m%d')
            vto = vto[:4]+'-'+vto[4:6]+'-'+vto[6:8]
            inv.write({
                'afip_auth_mode': 'CAE',
                'afip_auth_code': ws.CAE,
                'afip_auth_code_due': vto,
                'afip_result': ws.Resultado,
                'afip_message': msg,
                'afip_xml_request': ws.XmlRequest,
                'afip_xml_response': ws.XmlResponse,
                'document_number': str(pos_number).zfill(5) + '-' + str(cbte_nro).zfill(8),
                'name': inv.l10n_latam_document_type_id.doc_code_prefix + ' ' + str(pos_number).zfill(5) + '-' + str(cbte_nro).zfill(8),
            })
            # si obtuvimos el cae hacemos el commit porque estoya no se puede
            # volver atras
            # otra alternativa seria escribir con otro cursor el cae y que
            # la factura no quede validada total si tiene cae no se vuelve a
            # solicitar. Lo mismo podriamos usar para grabar los mensajes de
            # afip de respuesta
            inv._cr.commit()


    def _compute_qrcode(self):
        for rec in self:
            if rec.afip_auth_code:
                #rec.qr_code = base64.b64encode(qrcode.make(rec.fe_qr_url))a
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                vals_qr = {
                    "ver": 1,
                    "fecha": str(rec.invoice_date),
                    "cuit": int(rec.company_id.partner_id.vat),
                    "ptoVta": rec.journal_id.l10n_ar_afip_pos_number,
                    "tipoCmp": int(rec.l10n_latam_document_type_id.code),
                    "nroCmp": int(rec.name.split('-')[2]),
                    "importe": rec.amount_total,
                    "moneda": rec.currency_id.l10n_ar_afip_code,
                    "ctz": rec.l10n_ar_currency_rate,
                    "tipoDocRec": int(rec.partner_id.l10n_latam_identification_type_id.l10n_ar_afip_code),
                    "nroDocRec": int(rec.partner_id.vat),
                    "tipoCodAut": 'E',
                    "codAut": rec.afip_auth_code,
                }
                rec.fe_qr_url = vals_qr
                qr.add_data(rec.fe_qr_url)
                qr.make(fit=True)
                img = qr.make_image()
                temp = BytesIO()
                img.save(temp, format="PNG")
                qr_image = base64.b64encode(temp.getvalue())
                rec.qr_code = qr_image
            else:
                rec.fe_qr_url = ''
                rec.qr_code = None

