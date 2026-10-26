from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

from einvoice_reader.detect import FormatInfo

Amount = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class FormatOut(Model):
    syntax: Literal["CII", "UBL"]
    container: Literal["PDF", "XML"]
    document_type: Literal["Invoice", "CreditNote"]
    standard: str
    profile: str | None = None
    version: str | None = None
    customization_id: str | None = None
    embedded_file_name: str | None = None

    @classmethod
    def from_info(cls, info: FormatInfo) -> "FormatOut":
        return cls(**vars(info))


class Identifier(Model):
    value: str
    scheme: str | None = None


class Address(Model):
    line1: str | None = None
    line2: str | None = None
    line3: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country_subdivision: str | None = None
    country_code: str | None = None


class Contact(Model):
    name: str | None = None
    phone: str | None = None
    email: str | None = None


class Party(Model):
    name: str | None = None
    trading_name: str | None = None
    identifiers: list[Identifier] = []
    legal_registration_id: Identifier | None = None
    vat_id: str | None = None
    tax_number: str | None = None
    electronic_address: Identifier | None = None
    address: Address = Address()
    contact: Contact | None = None


class Payee(Model):
    name: str | None = None
    identifier: str | None = None
    legal_registration_id: Identifier | None = None


class TaxRepresentative(Model):
    name: str | None = None
    vat_id: str | None = None
    address: Address = Address()


class Delivery(Model):
    party_name: str | None = None
    location_id: str | None = None
    address: Address = Address()


class Account(Model):
    iban: str | None = None
    bic: str | None = None
    name: str | None = None


class Card(Model):
    last_digits: str | None = None
    holder: str | None = None


class DirectDebit(Model):
    mandate_reference: str | None = None
    creditor_id: str | None = None
    debited_account: str | None = None


class Payment(Model):
    means_code: str | None = None
    means_text: str | None = None
    remittance_information: str | None = None
    accounts: list[Account] = []
    card: Card | None = None
    direct_debit: DirectDebit | None = None


class Period(Model):
    start: str | None = None
    end: str | None = None


class Note(Model):
    subject_code: str | None = None
    text: str | None = None


class PrecedingInvoice(Model):
    number: str | None = None
    issue_date: str | None = None


class Totals(Model):
    line_net_total: Amount | None = None
    allowance_total: Amount | None = None
    charge_total: Amount | None = None
    net_total: Amount | None = None
    vat_total: Amount | None = None
    vat_total_tax_currency: Amount | None = None
    gross_total: Amount | None = None
    prepaid_amount: Amount | None = None
    rounding_amount: Amount | None = None
    amount_due: Amount | None = None


class VatBreakdown(Model):
    category_code: str | None = None
    rate: Amount | None = None
    taxable_amount: Amount | None = None
    tax_amount: Amount | None = None
    exemption_reason: str | None = None
    exemption_reason_code: str | None = None


class AllowanceCharge(Model):
    is_charge: bool
    amount: Amount | None = None
    base_amount: Amount | None = None
    percent: Amount | None = None
    reason: str | None = None
    reason_code: str | None = None
    vat_category_code: str | None = None
    vat_rate: Amount | None = None


class ItemAttribute(Model):
    name: str | None = None
    value: str | None = None


class Classification(Model):
    value: str
    scheme: str | None = None
    version: str | None = None


class Line(Model):
    id: str | None = None
    name: str | None = None
    description: str | None = None
    note: str | None = None
    quantity: Amount | None = None
    unit_code: str | None = None
    net_price: Amount | None = None
    gross_price: Amount | None = None
    price_base_quantity: Amount | None = None
    net_amount: Amount | None = None
    vat_category_code: str | None = None
    vat_rate: Amount | None = None
    seller_item_id: str | None = None
    buyer_item_id: str | None = None
    standard_item_id: str | None = None
    order_line_reference: str | None = None
    period: Period | None = None
    allowances_charges: list[AllowanceCharge] = []
    parent_line_id: str | None = None
    line_status_reason_code: str | None = None
    accounting_reference: str | None = None
    object_identifier: Identifier | None = None
    attributes: list[ItemAttribute] = []
    classifications: list[Classification] = []


class Attachment(Model):
    id: str | None = None
    description: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    uri: str | None = None


class Invoice(Model):
    number: str | None = None
    type_code: str | None = None
    type: Literal["invoice", "credit_note", "corrected", "prepayment", "self_billed", "other"]
    is_credit_note: bool = False
    issue_date: str | None = None
    due_date: str | None = None
    tax_point_date: str | None = None
    currency: str | None = None
    tax_currency: str | None = None
    buyer_reference: str | None = None
    order_reference: str | None = None
    sales_order_reference: str | None = None
    contract_reference: str | None = None
    project_reference: str | None = None
    buyer_accounting_reference: str | None = None
    invoiced_object_identifier: Identifier | None = None
    receiving_advice_reference: str | None = None
    despatch_advice_reference: str | None = None
    tender_reference: str | None = None
    business_process: str | None = None
    preceding_invoices: list[PrecedingInvoice] = []
    delivery_date: str | None = None
    invoicing_period: Period | None = None
    notes: list[Note] = []
    payment_terms: str | None = None
    payment: Payment = Payment()
    seller: Party = Party()
    buyer: Party = Party()
    payee: Payee | None = None
    tax_representative: TaxRepresentative | None = None
    delivery: Delivery | None = None
    totals: Totals = Totals()
    vat_breakdown: list[VatBreakdown] = []
    allowances_charges: list[AllowanceCharge] = []
    lines: list[Line] = []
    attachments: list[Attachment] = []


class Message(Model):
    severity: Literal["error", "warning", "info"]
    rule_id: str | None = None
    message: str
    location: str | None = None


class Validation(Model):
    performed: bool
    valid: bool | None = None
    is_e_invoice: bool | None = None
    rule_sets: list[str] = []
    messages: list[Message] = []


class Meta(Model):
    service_version: str


class Result(Model):
    format: FormatOut
    invoice: Invoice
    validation: Validation
    warnings: list[str] = []
    xml: str | None = None
    meta: Meta
