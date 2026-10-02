"""Loan eligibility per lender: the CIBIL page of a loan file (profile, CIBIL, eligibility).

GET  /projects/{project_id}/eligibility/lenders             SAMPLE lender policies and the form's choices
GET  /projects/{project_id}/eligibility/pincodes/{pincode}  "Check Availability": the lenders serving a pincode
GET  /projects/{project_id}/eligibility/companies?name=     "Check Category": a company's category per lender
GET  /projects/{project_id}/eligibility/inputs?applicant=   saved inputs, else a draft filled from the documents
PUT  /projects/{project_id}/eligibility/inputs              save one applicant's inputs
POST /projects/{project_id}/eligibility/calculate           eligibility at every lender
POST /projects/{project_id}/eligibility/login               record a login request; notify the CRM webhook

Every number comes from app/eligibility.py (fixed formulas, never a model). Every lender
policy, pincode list and company category is SAMPLE data (app/data), labelled "sample
policy — replace with your lender grid" wherever it is returned, and every result is
indicative: the lender decides. The DSA's own serviceability and company lists, uploaded
as CSV (POST .../eligibility/reference-data, app/reference_data.py), come first for the
lenders they have, in the calculation, the login and both checks (source "dsa_list").
Choices (employment type, loan type, BT / Obligate / Close ...) are returned as ids
(private_limited, bt) and accepted as ids or labels in any case.

The CIBIL block is filled from the applicant's credit report (cibil.source and the
tradelines' source "credit_report") or by hand; a bureau pull can write the same shape
(source "bureau") with PUT .../inputs unchanged.

Storage (DynamoDB, deleted by TTL on expires_at, and with the project):
- inputs: PK = PROJ#{project_id}, SK = ELIG#{key}, where key is a SHA-256 of the
  applicant's PAN or normalised name (so the key does not show them; it is not
  anonymous: the item itself holds the PAN, name and the other inputs), with applicant,
  inputs, created_at, updated_at and expires_at = first save + the retention period
  (default 7 days); later saves do not extend it, so nothing is kept longer. Erasing
  the applicant (POST .../applicants/erase) deletes these items at once. The inputs of
  one applicant are found by PAN or by name (older saves used the name): a save under
  the other identifier moves them and keeps their expiry.
- login requests: PK = PROJ#{project_id}, SK = LOGINREQ#{timestamp}#{uuid} with the lender
  id and the webhook outcome only (no applicant data, no amounts), same TTL.

The file check (the file-check Lambda, as POST .../file-check) supplies the verified net
salary (the lower of the salary slips' median net pay and the bank salary credits'
median), the loan EMIs seen in the bank statement, the file's verdict (READY / NOT READY
with its reasons) and the applicant's documents, whose facts fill the draft: every field a
document holds (see "document values" below), each with its source (file and page). Saved
inputs are never changed by the documents: the answer gives the documents' values next to
them. Without the file check (not configured, failed, no or several matching applicants)
the entered values are used and the answer says so.

POST .../login recomputes the lender's figures from the saved inputs (never from the
request), refuses a lender that is not eligible (409), refuses a file the file check
finds NOT READY unless the request confirms it (428, with the open issues) and, when the
project's CRM webhook is enabled, sends event file_login.requested through the webhook
delivery Lambda, signed like every delivery, with results = [{applicant, lender,
eligible_amount, emi, tenure_months, roi, note}].
"""

import datetime as dt
import hashlib
import json
import math
import re
import threading
import time
import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import Annotated, Any, Literal

from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from app import eligibility, reference_data
from app.config import get_config
from app.ddb import get_project_item, get_table
from app.ddb.ask_usage import TTL_ATTRIBUTE
from app.ddb.facts import query_facts
from app.ddb.webhooks import get_webhook_settings
from app.ddb.workflows import _decimal_to_python
from app.file_check import (
    TOOL_RUN_FILE_CHECK,
    FileCheckNotConfiguredError,
    FileCheckServiceError,
    UnknownChecklistError,
    invoke_file_check_tool,
)
from app.routers.file_check import ErrorResponse, ProjectId, UserId
from app.safe_ids import safe_segment
from app.webhook_delivery import invoke_delivery

router = APIRouter(prefix="/projects/{project_id}/eligibility", tags=["eligibility"])

INPUTS_SK_PREFIX = "ELIG#"
LOGIN_SK_PREFIX = "LOGINREQ#"
EVENT_LOGIN = "file_login.requested"
# One login per applicant and lender every LOGIN_INTERVAL_S (a double click must not log the
# file in twice), and only a few webhook calls (up to ~25 s each) at a time per process.
LOGIN_INTERVAL_S = 10
MAX_CONCURRENT_LOGINS = 3
MAX_AMOUNT = 1_000_000_000

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_MASKED_PAN_RE = re.compile(r"^X{6}[0-9]{3}[A-Z]$")
_LINE = r"^[^\x00-\x1f\x7f]*$"
_MULTILINE = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$"

# The page's choices beyond the engine's own (the engine never calculates with them).
HOUSE_OWNERSHIP = {**eligibility.HOUSE_OWNERSHIP, "parental": "Parental", "company_provided": "Company provided"}
HOUSE_OWNERSHIP_ALIASES = {
    **eligibility.HOUSE_OWNERSHIP_ALIASES,
    "parents": "parental",
    "family": "parental",
    "family_owned": "parental",
    "company": "company_provided",
    "company_lease": "company_provided",
    "employer_provided": "company_provided",
}
TRADELINE_SOURCES = {**eligibility.TRADELINE_SOURCES, "credit_report": "Credit report"}
CIBIL_SOURCES = {**eligibility.CIBIL_SOURCES, "credit_report": "Credit report"}
# The sheet's legend, plus the values read from the documents.
SOURCE_LABELS = {**eligibility.SOURCE_LABELS, "document": "From Document"}

# Replaced in tests.
_monotonic = time.monotonic
_login_lock = threading.Lock()
_last_login_at: dict[tuple[str, str, str], float] = {}
_running_logins = 0


# ------------------------------------------------------------------ input types
def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _vocabulary(choices: dict[str, str], aliases: dict[str, str] | None = None, suffixes: tuple[str, ...] = ()):
    """Accept an id or a label (any case): 'Private Limited' -> 'private_limited'."""

    def parse(value: Any) -> Any:
        value = _blank_to_none(value)
        if value is None:
            return None
        found = eligibility.enum_id(value, choices, aliases, suffixes)
        if found is None:
            raise ValueError(f"must be one of: {', '.join(choices)}")
        return found

    return BeforeValidator(parse)


def _pan(value: Any) -> Any:
    value = _blank_to_none(value)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be a string")
    pan = re.sub(r"\s", "", value).upper()
    if not (_PAN_RE.match(pan) or _MASKED_PAN_RE.match(pan)):
        raise ValueError("must be a PAN such as ABCDE1234F (or the masked PAN of a pre-fill, XXXXXX234F)")
    return pan


def _mobile(value: Any) -> Any:
    value = _blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError("must be a string")
    digits = re.sub(r"[\s-]", "", value)
    if digits.startswith("+91"):
        digits = digits[3:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9][0-9]{9}", digits):
        raise ValueError("must be a 10-digit Indian mobile number")
    return digits


def _pincode(value: Any) -> Any:
    value = _blank_to_none(value)
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{5}", re.sub(r"\s", "", value)):
        raise ValueError("must be a 6-digit pincode")
    return re.sub(r"\s", "", value)


def _past_date(value: dt.date | None) -> dt.date | None:
    """Not after today (in the user's time zone: UTC + 1 day of slack, so today in IST passes)."""
    latest = (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).date()
    if value is not None and not dt.date(1900, 1, 1) <= value <= latest:
        raise ValueError("must not be a future date")
    return value


def _finite(value: Any) -> Any:
    """NaN and infinity (json.loads accepts them) become strings, so that their 422 error renders as JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


Money = Annotated[Annotated[float, Field(ge=0, le=MAX_AMOUNT, allow_inf_nan=False)], BeforeValidator(_finite)]
Count = Annotated[Annotated[int, Field(ge=0, le=999)], BeforeValidator(_finite)]
Score = Annotated[Annotated[int, Field(ge=300, le=900)], BeforeValidator(_finite)]
InstalmentCount = Annotated[Annotated[int, Field(ge=0, le=600)], BeforeValidator(_finite)]
TenureMonths = Annotated[Annotated[int, Field(ge=1, le=480)], BeforeValidator(_finite)]
OptionalName = Annotated[
    Annotated[str, StringConstraints(max_length=200, pattern=_LINE)] | None, BeforeValidator(_blank_to_none)
]
OptionalLender = Annotated[
    Annotated[str, StringConstraints(max_length=100, pattern=_LINE)] | None, BeforeValidator(_blank_to_none)
]
OptionalAddress = Annotated[
    Annotated[str, StringConstraints(max_length=300, pattern=_MULTILINE)] | None, BeforeValidator(_blank_to_none)
]
OptionalDate = Annotated[dt.date | None, BeforeValidator(_blank_to_none)]
AccountNumber = Annotated[
    Annotated[str, StringConstraints(max_length=30, pattern=r"^[A-Za-z0-9 ./-]+$")] | None,
    BeforeValidator(_blank_to_none),
]

EmploymentTypeId = Literal[
    "defence",
    "government",
    "grade_4",
    "llp",
    "merchant_navy",
    "partnership_proprietorship",
    "private_limited",
    "public_limited",
]
LoanTypeId = Literal["personal", "home", "mortgage", "car", "education", "application", "consumer", "credit_card"]
TradelineActionId = Literal["bt", "obligate", "close"]
TradelineStatusId = Literal[
    "active", "closed", "settled", "written_off", "suit_filed", "wilful_default", "restructured"
]
TradelineSourceId = Literal["manual", "bureau", "bank_statement", "credit_report"]
CibilSourceId = Literal["manual", "bureau", "credit_report"]
OtherIncomeTypeId = Literal["rented", "bonus", "incentive", "pension"]
IncomeFrequencyId = Literal["yearly", "half_yearly", "quarterly", "monthly"]
RentAgreementId = Literal["notary", "registered"]
HouseOwnershipId = Literal["owned", "rented", "parental", "company_provided"]
LenderStatus = Literal["eligible", "not_serviceable", "not_eligible"]
SourceId = Literal["policy", "formula", "table"]
FieldSourceId = Literal["document", "table"]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OtherIncome(_Input):
    type: Annotated[
        OtherIncomeTypeId,
        _vocabulary(eligibility.OTHER_INCOME_TYPES, eligibility.OTHER_INCOME_ALIASES, ("_income",)),
    ] = Field(description="rented, bonus, incentive or pension (labels such as 'Rented Income' are accepted)")
    amount: Money | None = Field(
        default=None, description="Rupees per `frequency` (rented income and pension: per month unless given)"
    )
    frequency: Annotated[
        IncomeFrequencyId | None, _vocabulary(eligibility.INCOME_FREQUENCIES, eligibility.INCOME_FREQUENCY_ALIASES)
    ] = Field(
        default=None,
        description="yearly, half_yearly, quarterly or monthly. Bonus and incentive without it count as yearly "
        "(the lowest); rented income and pension as monthly.",
    )
    agreement: Annotated[
        RentAgreementId | None, _vocabulary(eligibility.RENT_AGREEMENTS, eligibility.RENT_AGREEMENT_ALIASES)
    ] = Field(
        default=None,
        description="Rented income only: notary or registered agreement (without it: notary, the lower share)",
    )

    @model_validator(mode="after")
    def _agreement_only_for_rent(self):
        if self.type != "rented":
            self.agreement = None
        return self


class Profile(_Input):
    pan: Annotated[str | None, BeforeValidator(_pan)] = Field(
        default=None, description="PAN (ABCDE1234F), or the masked PAN of a pre-fill (XXXXXX234F)"
    )
    name: OptionalName = Field(default=None, description="Name as per PAN")
    mobile: Annotated[str | None, BeforeValidator(_mobile)] = Field(
        default=None, description="10-digit mobile number (+91 / 0 prefix accepted)"
    )
    dob: OptionalDate = Field(default=None, description="Date of birth, YYYY-MM-DD")
    house_ownership: Annotated[HouseOwnershipId | None, _vocabulary(HOUSE_OWNERSHIP, HOUSE_OWNERSHIP_ALIASES)] = Field(
        default=None, description="owned, rented, parental or company_provided"
    )
    pincode: Annotated[str | None, BeforeValidator(_pincode)] = Field(
        default=None, description="6-digit pincode of the current address (checked against each lender's list)"
    )
    current_address: OptionalAddress = None
    permanent_address: OptionalAddress = None
    company: OptionalName = Field(default=None, description="Employer (its category is looked up per lender)")
    employment_type: Annotated[
        EmploymentTypeId | None, _vocabulary(eligibility.EMPLOYMENT_TYPES, eligibility.EMPLOYMENT_TYPE_ALIASES)
    ] = None
    net_income: Money | None = Field(
        default=None, description="Net monthly salary; a salary verified by the file check is used instead"
    )
    other_income: list[OtherIncome] = Field(default=[], max_length=10)

    @field_validator("dob")
    @classmethod
    def _dob_in_past(cls, value: dt.date | None) -> dt.date | None:
        return _past_date(value)


class Enquiries(_Input):
    """Cumulative bureau enquiries: enquiries in the last 30, 60, 90 and 120 days."""

    d30: Count | None = None
    d60: Count | None = None
    d90: Count | None = Field(default=None, description="Checked against each lender's limit")
    d120: Count | None = None

    @model_validator(mode="after")
    def _cumulative(self):
        known = [(k, getattr(self, k)) for k in ("d30", "d60", "d90", "d120") if getattr(self, k) is not None]
        for (first, a), (second, b) in zip(known, known[1:], strict=False):
            if a > b:
                raise ValueError(f"enquiries are cumulative: {first} ({a}) cannot be more than {second} ({b})")
        return self


class Tradeline(_Input):
    loan_type: Annotated[
        LoanTypeId | None, _vocabulary(eligibility.LOAN_TYPES, eligibility.LOAN_TYPE_ALIASES, ("_loan",))
    ] = None
    lender: OptionalLender = Field(default=None, description="Lender name (manual entry; a bureau pull fills it)")
    sanction_amount: Money | None = None
    outstanding: Money | None = Field(default=None, description="Needed for BT: the balance-transfer amount")
    emi: Money | None = Field(default=None, description="Monthly EMI; needed for Obligate")
    status: Annotated[
        TradelineStatusId | None,
        _vocabulary(eligibility.TRADELINE_STATUSES, eligibility.TRADELINE_STATUS_ALIASES),
    ] = None
    account_number: AccountNumber = None
    overdue: Money | None = None
    emis_paid: InstalmentCount | None = None
    emis_pending: InstalmentCount | None = None
    open_date: OptionalDate = None
    last_payment_date: OptionalDate = None
    action: Annotated[
        TradelineActionId, _vocabulary(eligibility.TRADELINE_ACTIONS, eligibility.TRADELINE_ACTION_ALIASES)
    ] = Field(
        default="obligate",
        description="bt: the new lender takes over the outstanding; obligate: it keeps running (its EMI is an "
        "obligation); close: closed before disbursal",
    )
    source: Annotated[TradelineSourceId, _vocabulary(TRADELINE_SOURCES)] = Field(
        default="manual",
        description="manual, bureau (a bureau pull), credit_report (read from the uploaded credit report) or "
        "bank_statement (a loan EMI of the bank statement, suggested by the draft)",
    )

    @field_validator("open_date", "last_payment_date")
    @classmethod
    def _dates_in_past(cls, value: dt.date | None) -> dt.date | None:
        return _past_date(value)

    @model_validator(mode="after")
    def _date_order(self):
        if self.open_date and self.last_payment_date and self.last_payment_date < self.open_date:
            raise ValueError("last_payment_date is before open_date")
        return self


class Cibil(_Input):
    score: Score | None = Field(default=None, description="CIBIL score, 300-900")
    enquiries: Enquiries = Field(default_factory=Enquiries)
    tradelines: list[Tradeline] = Field(default=[], max_length=50)
    source: Annotated[CibilSourceId, _vocabulary(CIBIL_SOURCES)] = Field(
        default="manual",
        description="manual, credit_report (read from the uploaded credit report) or bureau (a bureau pull)",
    )
    report_date: OptionalDate = Field(default=None, description="Date of the credit report, YYYY-MM-DD")

    @field_validator("report_date")
    @classmethod
    def _report_in_past(cls, value: dt.date | None) -> dt.date | None:
        return _past_date(value)


class Loan(_Input):
    amount: Money | None = Field(default=None, description="Loan amount requested, rupees")
    tenure_months: TenureMonths | None = Field(
        default=None, description="Requested tenure; each lender calculates at most at its max tenure"
    )


class EligibilityInputs(_Input):
    profile: Profile = Field(default_factory=Profile)
    cibil: Cibil = Field(default_factory=Cibil)
    loan: Loan = Field(default_factory=Loan)


ApplicantName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        description="The applicant: PAN (preferred) or name as the file check shows it; the same value finds "
        "the saved inputs again",
    ),
]


class SaveInputsRequest(EligibilityInputs):
    applicant: ApplicantName

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "applicant": "Rahul Vijay Deshmukh",
                    "profile": {
                        "name": "Rahul Vijay Deshmukh",
                        "pincode": "411045",
                        "company": "Konkan Softworks Pvt Ltd",
                        "employment_type": "private_limited",
                        "net_income": 82500,
                        "other_income": [{"type": "rented", "agreement": "registered", "amount": 12000}],
                    },
                    "cibil": {
                        "score": 772,
                        "enquiries": {"d30": 0, "d60": 1, "d90": 1, "d120": 2},
                        "tradelines": [
                            {
                                "loan_type": "car",
                                "lender": "Mulshi Auto Finance",
                                "sanction_amount": 450000,
                                "outstanding": 210000,
                                "emi": 8200,
                                "status": "active",
                                "action": "obligate",
                            }
                        ],
                    },
                    "loan": {"amount": 600000, "tenure_months": 48},
                }
            ]
        }
    )


class CalculateRequest(_Input):
    applicant: ApplicantName
    inputs: EligibilityInputs | None = Field(
        default=None, description="Calculate these (not saved); default: the applicant's saved inputs"
    )


class LoginRequest(_Input):
    applicant: ApplicantName
    lender: str = Field(min_length=1, max_length=100, description="Lender id (icici_bank) or name (ICICI Bank)")
    confirm_not_ready: bool = Field(
        default=False,
        description="The user saw the file check's open issues and logs a NOT READY file in anyway "
        "(without it such a file is refused with 428)",
    )


# ------------------------------------------------------------------ responses
class Option(BaseModel):
    id: str
    label: str


class FormOptions(BaseModel):
    employment_types: list[Option]
    house_ownership: list[Option]
    other_income_types: list[Option]
    income_frequencies: list[Option]
    rent_agreements: list[Option]
    loan_types: list[Option]
    tradeline_actions: list[Option]
    tradeline_statuses: list[Option]
    sources: list[Option] = Field(
        description="The legend: From Policy, Formula Calculation, From Table (the sheet's) and From Document"
    )


class CategoryPolicyOut(BaseModel):
    foir: float
    multiplier: float


class UnlistedCompanyOut(BaseModel):
    accepted: bool
    foir: float | None = None
    multiplier: float | None = None


class LenderPolicyOut(BaseModel):
    id: str
    name: str
    product: str | None = None
    roi: float = Field(description="Annual rate of interest, percent")
    foir: float = Field(description="Base FOIR (fraction of the monthly income); a company category may override it")
    multiplier: float = Field(description="Base income multiplier; a company category may override it")
    min_tenure_months: int
    max_tenure_months: int
    min_amount: float
    max_amount: float
    min_cibil_score: int
    max_enquiries_90d: int
    employment_types: list[str]
    company_categories: dict[str, CategoryPolicyOut]
    unlisted_company: UnlistedCompanyOut
    income_consideration_pct: dict[str, float] = Field(
        description="Percent of each other income counted: rented_notary, rented_registered, bonus, incentive, pension"
    )
    serviceable_regions: list[str]
    sample: bool
    label: str | None = Field(description='"sample policy — replace with your lender grid" for SAMPLE data')


class LendersResponse(BaseModel):
    sample: bool
    label: str | None
    version: str
    lenders: list[LenderPolicyOut]
    options: FormOptions
    disclaimers: list[str]


class PincodeLender(BaseModel):
    lender_id: str
    lender: str
    serviceable: bool
    source: Literal["dsa_list", "sample"] = Field(
        default="sample", description="dsa_list: your uploaded serviceability list; sample: the SAMPLE pincode list"
    )


class PincodeResponse(BaseModel):
    pincode: str
    region: str | None = Field(description="SAMPLE region the pincode is in, if any")
    serviceable_by: int
    lenders: list[PincodeLender]
    sample: bool
    label: str | None


class CompanyCategory(BaseModel):
    lender_id: str
    lender: str
    category: str | None = Field(description="The company's category with this lender; null when unlisted")
    listed: bool
    accepted: bool = Field(description="Listed, or unlisted and the lender takes unlisted companies")
    foir: float | None
    multiplier: float | None
    foir_range: list[float] | None = Field(
        default=None,
        description="A lender with a FOIR grid: the category's lowest and highest FOIR over its net salary slabs "
        "(foir is the lowest slab's; the calculation takes the applicant's slab)",
    )
    source: Literal["dsa_list", "sample"] = Field(
        default="sample", description="dsa_list: your uploaded company list; sample: the SAMPLE company list"
    )


class CompanyMatch(BaseModel):
    name: str
    aliases: list[str]
    employment_type: str | None
    synthetic: bool = Field(description="A fictional employer of the demo applicants")


class CompanyResponse(BaseModel):
    query: str
    match: CompanyMatch | None = Field(description="The listed company the name is (legal suffixes ignored)")
    categories: list[CompanyCategory] = Field(description="Per lender: its category, or its unlisted-company policy")
    suggestions: list[str] = Field(description="Listed companies whose name contains the query")
    sample: bool
    label: str | None


class Prefill(BaseModel):
    available: bool = Field(description="The file check found exactly one matching applicant")
    detail: str
    applicant_name: str | None = None
    pan_masked: str | None = None
    employer: str | None = None
    verified_net_income: float | None = None
    income_source: str | None = None
    dob: dt.date | None = None
    suggested_tradelines: int = 0
    credit_report: str | None = Field(default=None, description="The credit report the CIBIL block was read from")
    documents: int = 0


class DocumentRef(BaseModel):
    document_id: str | None = None
    file: str = Field(description="The document's file name")
    page: int | None = Field(default=None, description="1-based page the value is printed on, when known")
    doc_type: str | None = None


class FieldSource(BaseModel):
    source: FieldSourceId = Field(
        description="document = From Document (read from the files below); table = From Table (the company list)"
    )
    value: Any = Field(description="The value the documents give: the field shows its source while it holds it")
    documents: list[DocumentRef] = []
    detail: str | None = Field(default=None, description='e.g. "verified: salary slips, median net pay"')
    unverified: bool = Field(default=False, description="An amount not found in the document's text: check it")


class RowSources(BaseModel):
    """Per row of the returned inputs: where it came from (null: typed by hand)."""

    other_income: list[FieldSource | None] = []
    tradelines: list[FieldSource | None] = []


class DocumentRows(BaseModel):
    """The rows the documents give (value = an other-income row or a tradeline), saved or not."""

    other_income: list[FieldSource] = []
    tradelines: list[FieldSource] = []


class StillNeeded(BaseModel):
    field: str = Field(description='e.g. "pincode", "score", "enquiries.d90", "tradelines.2.emi" (1-based row)')
    label: str
    required: bool = Field(description="Every lender's check needs it (else a field of the sheet left empty)")
    from_documents: bool = Field(description="A document holds a value for it (the saved inputs left it empty)")


class InputsResponse(BaseModel):
    applicant: str
    saved: bool = Field(description="The inputs were saved (else a draft, filled from the documents when possible)")
    inputs: EligibilityInputs
    from_documents: list[str] = Field(
        description="Fields the draft took from the documents: name, pan, mobile, dob, house_ownership, pincode, "
        "current_address, permanent_address, company, employment_type, net_income, other_income, loan_amount, "
        "tenure_months, score, enquiries, tradelines"
    )
    prefill: Prefill | None = Field(description="How the draft was filled; null for saved inputs")
    sources: dict[str, FieldSource] = Field(
        default={},
        description="Per field (as in from_documents, plus report: the credit report's date), the value the "
        "documents give and where it was read; saved inputs keep their own values",
    )
    row_sources: RowSources = Field(default_factory=RowSources)
    document_rows: DocumentRows = Field(default_factory=DocumentRows)
    still_needed: list[StillNeeded] = Field(
        default=[], description='"Still needed before Check eligibility": the fields that are empty, in sheet order'
    )
    created_at: str | None = None
    updated_at: str | None = None
    expires_at: str | None = Field(default=None, description="When the saved inputs are deleted (DynamoDB TTL)")
    notes: list[str] = []
    label: str | None = None


class OtherIncomeConsidered(BaseModel):
    type: str
    label: str
    agreement: str | None
    frequency: str
    monthly_amount: float
    consideration_pct: float
    considered: float


class LenderEligibility(BaseModel):
    lender: str
    lender_id: str
    status: LenderStatus
    status_label: str = Field(description="Eligible, Not serviceable or Not eligible")
    reasons: list[str] = Field(description="Why not eligible / not serviceable (empty when eligible)")
    notes: list[str] = Field(description="Remarks that do not change the status")
    eligible_amount: float = Field(
        description="min(FOIR eligibility, multiplier eligibility, max_amount), to the paisa; 0 unless eligible"
    )
    computed_amount: float | None = Field(description="What the formulas give, whatever the status")
    tenure_months: int = Field(description="The lender's max tenure: the EMI is shown at it")
    roi: float = Field(description="Annual rate, percent")
    emi: float = Field(description="EMI of eligible_amount at roi over tenure_months; 0 unless eligible")
    calculation_tenure_months: int = Field(
        description="The per-lakh EMI's tenure: the lender's calculation tenure, or a shorter requested tenure "
        "(at least the lender's min)"
    )
    emi_at_calculation_tenure: float = Field(description="EMI of eligible_amount over calculation_tenure_months")
    per_lakh_emi: float
    foir_eligibility: float | None
    multiplier_eligibility: float | None
    income_considered: float | None = Field(description="Net salary + other income at this lender's consideration %")
    other_income_considered: list[OtherIncomeConsidered]
    obligations: float
    foir: float
    multiplier: float
    company_category: str | None
    company_policy: Literal["category", "unlisted", "base"]
    max_amount: float
    min_amount: float
    bt_amount: float
    covers_bt: bool | None
    covers_requested: bool | None
    serviceable: bool | None
    region: str | None
    sources: dict[str, SourceId] = Field(
        description="policy = From Policy, formula = Formula Calculation, table = From Table"
    )
    label: str | None


class OtherIncomeRow(BaseModel):
    type: str
    label: str
    agreement: str | None
    frequency: str
    amount: float
    monthly_amount: float


class EligibilityIncome(BaseModel):
    net_salary: float | None
    net_salary_source: Literal["verified", "entered"] | None
    net_salary_source_label: str
    entered_net_income: float | None
    verified_net_income: float | None
    other_income: list[OtherIncomeRow]
    other_income_monthly: float


class ObligationRow(BaseModel):
    index: int | None = Field(description="1-based tradeline number; null for a bank-statement EMI")
    lender: str | None
    loan_type: str | None
    action: str
    emi: float | None
    outstanding: float | None
    source: str | None = None
    flag: str | None = None
    note: str | None = None


class BankStatementEmi(BaseModel):
    payee: str | None
    lender: str | None
    amount: float
    months_seen: int | None
    months_total: int | None
    matched_tradeline: int | None
    counted: bool


class ObligationDetails(BaseModel):
    counted: list[ObligationRow]
    bt: list[ObligationRow]
    closed: list[ObligationRow]
    not_counted: list[ObligationRow]
    bank_statement_emis: list[BankStatementEmi]


class FileCheckUse(BaseModel):
    used: bool
    detail: str
    applicant: str | None = None
    verdict: str | None = Field(default=None, description="READY or NOT READY; null without the file check")
    ready: bool | None = None
    issues: list[str] = Field(default=[], description="The file check's reasons for NOT READY, in order")


class RequestedLoan(BaseModel):
    amount: float | None
    tenure_months: int | None


class CalculateResponse(BaseModel):
    applicant: str
    calculated_at: str
    sample: bool
    policy_label: str | None
    label: str
    income_considered: float | None = Field(description="Net monthly salary used (verified, else entered)")
    income: EligibilityIncome
    obligations: float = Field(description="Monthly obligations counted")
    obligation_details: ObligationDetails
    bt_amount: float
    requested: RequestedLoan
    per_lender: list[LenderEligibility]
    best_lender: str | None
    best_lender_id: str | None
    best_lender_reason: str | None
    file_check: FileCheckUse
    notes: list[str]
    disclaimers: list[str]


class LoginDelivery(BaseModel):
    delivery_id: str | None
    status: Literal["delivered", "failed"]
    http_status: int | None = None
    error: str | None = None


class LoginResponse(BaseModel):
    status: Literal["recorded"]
    applicant: str
    lender: str
    lender_id: str
    eligible_amount: float
    emi: float
    tenure_months: int
    roi: float
    requested_at: str
    webhook: Literal["delivered", "failed", "not_enabled", "not_configured", "skipped"]
    webhook_detail: str | None = None
    delivery: LoginDelivery | None = Field(description="The CRM webhook delivery; null when none was attempted")
    file_ready: bool | None = Field(
        default=None, description="The file check's verdict at login (false: logged in NOT READY, confirmed)"
    )
    open_issues: int = Field(default=0, description="The file check's open issues at login")
    label: str
    policy_label: str | None


_NOT_FOUND = {404: {"model": ErrorResponse, "description": "Project not found"}}
_INVALID_APPLICANT = {400: {"model": ErrorResponse, "description": "Invalid applicant"}}


# ------------------------------------------------------------------ helpers
def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.UTC).isoformat(timespec="microseconds")


def _require_project(project_id: str) -> None:
    if not get_project_item(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


def _applicant(value: str) -> str:
    """The applicant as given (stripped); 400 unless it is safe like an id (app/safe_ids.py)."""
    return safe_segment((value or "").strip(), "applicant")


def is_full_pan(value: Any) -> bool:
    return isinstance(value, str) and bool(_PAN_RE.match(re.sub(r"\s", "", value).upper()))


def applicant_key(applicant: str) -> str:
    """ELIG# key of an applicant: SHA-256 of the PAN (any case / spacing) or the name (case and spacing ignored)."""
    compact = re.sub(r"\s", "", applicant).upper()
    basis = f"pan:{compact}" if _PAN_RE.match(compact) else "name:" + " ".join(applicant.split()).casefold()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:40]


def _inputs_key(project_id: str, applicant: str) -> dict[str, str]:
    return {"PK": f"PROJ#{project_id}", "SK": f"{INPUTS_SK_PREFIX}{applicant_key(applicant)}"}


def _mask_pan(pan: Any) -> str | None:
    """'BQXPD4821K' -> 'XXXXXX821K' (as the file check masks it)."""
    if not isinstance(pan, str):
        return None
    compact = re.sub(r"\s", "", pan).upper()
    return "X" * 6 + compact[-4:] if _PAN_RE.match(compact) else None


def _to_dynamo(value: Any) -> Any:
    """JSON-safe inputs with floats as Decimal (DynamoDB has no float)."""
    return json.loads(json.dumps(value), parse_float=Decimal)


def _positive(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _str(value: Any, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


# ------------------------------------------------------------------ storage
def _load_saved(project_id: str, applicant: str, now: dt.datetime) -> dict[str, Any] | None:
    """The saved item, or None (also when past expires_at but not yet removed by TTL)."""
    item = get_table().get_item(Key=_inputs_key(project_id, applicant), ConsistentRead=True).get("Item")
    if not item:
        return None
    item = _decimal_to_python(item)
    expires_at = item.get(TTL_ATTRIBUTE)
    if not isinstance(expires_at, int) or expires_at <= int(now.timestamp()):
        return None
    return item


def _saved_inputs(item: dict[str, Any], project_id: str) -> EligibilityInputs | None:
    try:
        return EligibilityInputs.model_validate(item.get("inputs") or {})
    except ValidationError as e:
        print(f"eligibility: saved inputs unreadable project={project_id} at {[err['loc'] for err in e.errors()][:5]}")
        return None


def _identities(*values: Any) -> tuple[set[str], set[str]]:
    """(names, PANs) of an applicant: casefolded single-spaced names, compact upper-case full PANs."""
    names: set[str] = set()
    pans: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        compact = re.sub(r"\s", "", value).upper()
        if _PAN_RE.match(compact):
            pans.add(compact)
        elif not _MASKED_PAN_RE.match(compact):
            names.add(" ".join(value.split()).casefold())
    return names, pans


def _pan_compatible(saved_pan: Any, pans: set[str]) -> bool:
    """A saved PAN (full or masked) that does not name another applicant than `pans`."""
    compact = re.sub(r"\s", "", saved_pan).upper() if isinstance(saved_pan, str) else ""
    if not compact or not pans:
        return True
    if _PAN_RE.match(compact):
        return compact in pans
    if _MASKED_PAN_RE.match(compact):
        return any(pan[-4:] == compact[-4:] for pan in pans)
    return True


def _item_identities(item: dict[str, Any]) -> tuple[set[str], set[str]]:
    """(names, PANs) a saved item is known by: its identifiers (now and before a move), name and PAN."""
    profile = (item.get("inputs") or {}).get("profile") or {}
    aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
    return _identities(item.get("applicant"), *aliases, profile.get("name"), profile.get("pan"))


def _is_applicants(item: dict[str, Any], names: set[str], pans: set[str]) -> bool:
    """A saved item whose PAN, or whose name with a PAN that does not contradict it, is the applicant's."""
    item_names, item_pans = _item_identities(item)
    if item_pans & pans:
        return True
    pan = ((item.get("inputs") or {}).get("profile") or {}).get("pan")
    return bool(item_names & names) and _pan_compatible(pan, pans)


def _saved_items(project_id: str, now: dt.datetime) -> list[dict[str, Any]]:
    """Every unexpired ELIG# item of the project."""
    table = get_table()
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"PROJ#{project_id}") & Key("SK").begins_with(INPUTS_SK_PREFIX)
    }
    items = []
    while True:
        page = table.query(**kwargs)
        for item in page.get("Items", []):
            item = _decimal_to_python(item)
            expires_at = item.get(TTL_ATTRIBUTE)
            if isinstance(expires_at, int) and expires_at > int(now.timestamp()):
                items.append(item)
        if not page.get("LastEvaluatedKey"):
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _find_saved(project_id: str, applicant: str, now: dt.datetime, also: Iterable[Any] = ()) -> dict[str, Any] | None:
    """The applicant's saved item: under its own key; else under the key of its other identifier
    (`also`: the PAN or name the file check or the inputs give; older saves used the name), unless
    that item's PAN names someone else; else the one saved item whose PAN or name is the applicant's."""
    item = _load_saved(project_id, applicant, now)
    if item:
        return item
    names, pans = _identities(applicant, *also)
    tried = {applicant_key(applicant)}
    for other in [*sorted(pans), *sorted(names)]:
        if applicant_key(other) in tried:
            continue
        tried.add(applicant_key(other))
        item = _load_saved(project_id, other, now)
        if item and _pan_compatible(((item.get("inputs") or {}).get("profile") or {}).get("pan"), pans):
            return item
    matches = [i for i in _saved_items(project_id, now) if _is_applicants(i, names, pans)]
    return matches[0] if len(matches) == 1 else None


def _save(
    project_id: str, applicant: str, inputs: EligibilityInputs, now: dt.datetime, also: Iterable[Any] = ()
) -> dict[str, Any]:
    """Saves under the applicant's key; inputs saved under its other identifier move here with their
    expiry, and that identifier stays one of the item's aliases (so it still finds them)."""
    existing = _find_saved(project_id, applicant, now, also)
    if existing and isinstance(existing.get("created_at"), str):
        created_at, expires_at = existing["created_at"], int(existing[TTL_ATTRIBUTE])
    else:
        created_at = _iso(now)
        expires_at = int(now.timestamp()) + get_config().retention_days * 86400
    key = _inputs_key(project_id, applicant)
    item = {
        **key,
        "applicant": applicant,
        "inputs": _to_dynamo(inputs.model_dump(mode="json")),
        "created_at": created_at,
        "updated_at": _iso(now),
        TTL_ATTRIBUTE: expires_at,
    }
    if existing:
        before = [existing.get("applicant"), *(existing.get("aliases") or [])]
        aliases = [
            a for a in dict.fromkeys(before) if isinstance(a, str) and applicant_key(a) != applicant_key(applicant)
        ]
        if aliases:
            item["aliases"] = aliases[:10]
    get_table().put_item(Item=item)
    if existing and (existing["PK"], existing["SK"]) != (key["PK"], key["SK"]):
        get_table().delete_item(Key={"PK": existing["PK"], "SK": existing["SK"]})
    return item


def _expires_iso(item: dict[str, Any]) -> str | None:
    expires_at = item.get(TTL_ATTRIBUTE)
    if not isinstance(expires_at, int):
        return None
    return _iso(dt.datetime.fromtimestamp(expires_at, dt.UTC))


def erase_applicant_eligibility(project_id: str, identifiers: Iterable[str]) -> int:
    """Delete an applicant's saved eligibility inputs (for the erase-applicant flow).

    Deletes the ELIG# items whose key is one of `identifiers` (names or PANs), or one of their
    aliases (saved under it before a move), or whose saved name or PAN is; returns how many were
    deleted. Login-request items hold no applicant data.
    """
    wanted = {i.strip() for i in identifiers if isinstance(i, str) and i.strip()}
    keys = {applicant_key(i) for i in wanted}
    names = {" ".join(i.split()).casefold() for i in wanted if not is_full_pan(i)}
    pans = {re.sub(r"\s", "", i).upper() for i in wanted if is_full_pan(i)}
    table = get_table()
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"PROJ#{project_id}") & Key("SK").begins_with(INPUTS_SK_PREFIX)
    }
    deleted = 0
    while True:
        page = table.query(**kwargs)
        for item in page.get("Items", []):
            profile = (item.get("inputs") or {}).get("profile") or {}
            name = " ".join(str(profile.get("name") or item.get("applicant") or "").split()).casefold()
            aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
            match = (
                str(item["SK"])[len(INPUTS_SK_PREFIX) :] in keys
                or any(isinstance(a, str) and applicant_key(a) in keys for a in aliases)
                or (name and name in names)
                or (profile.get("pan") in pans)
            )
            if match:
                table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
                deleted += 1
        if not page.get("LastEvaluatedKey"):
            return deleted
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


# ------------------------------------------------------------------ file check
def _file_check_applicant(project_id: str, identifier: str) -> tuple[dict | None, str, dict]:
    """(the applicant's file-check result, what happened, the whole check)."""
    try:
        check = invoke_file_check_tool(TOOL_RUN_FILE_CHECK, {"project_id": project_id, "applicant": identifier})
    except FileCheckNotConfiguredError:
        return None, "the file check is not configured", {}
    except (FileCheckServiceError, UnknownChecklistError) as e:
        print(f"eligibility: file check failed project={project_id} ({type(e).__name__})")
        return None, "the file check failed", {}
    applicants = [a for a in check.get("applicants") or [] if isinstance(a, dict)]
    if not applicants:
        return None, "no applicant with this name or PAN in the analysed documents", check
    if len(applicants) > 1:
        return None, f"{len(applicants)} applicants in the documents match: use the PAN", check
    return applicants[0], "verified figures from the file check", check


def verified_income(applicant: dict | None) -> dict | None:
    """{amount, source}: the lower of the slips' median net pay and the bank salary credits' median."""
    income = (applicant or {}).get("income") or {}
    candidates = [
        (value, source)
        for value, source in (
            (income.get("slip_net"), "salary slips, median net pay"),
            (income.get("bank_salary_credit"), "bank salary credits, median"),
        )
        if _positive(value)
    ]
    if not candidates:
        return None
    amount, source = min(candidates, key=lambda c: c[0])
    return {"amount": amount, "source": source}


def bank_statement_emis(applicant: dict | None) -> list[dict]:
    """The loan EMIs the file check found in the bank statement (obligations.fixed_loan_emis)."""
    obligations = (applicant or {}).get("obligations") or {}
    if not isinstance(obligations, dict) or not obligations.get("available"):
        return []
    rows = []
    for view in obligations.get("fixed_loan_emis") or []:
        if not isinstance(view, dict) or not _positive(view.get("amount")):
            continue
        rows.append(
            {
                "amount": view["amount"],
                "payee": _str(view.get("payee"), 100),
                "lender": _str(view.get("declared_lender"), 100),
                "narration": _str(view.get("narration"), 300),
                "months_seen": _int(view.get("months_seen")),
                "months_total": _int(view.get("months_total")),
                "unverified": view.get("unverified") is True,
            }
        )
    return rows


_LOAN_TYPE_WORDS = (
    (r"\b(HOME|HOUSING|HL)\b", "home"),
    (r"\b(LAP|MORTGAGE|PROPERTY)\b", "mortgage"),
    (r"\b(CAR|AUTO|VEHICLE|TW|TWO ?WHEELER|BIKE)\b", "car"),
    (r"\b(EDU|EDUCATION)\b", "education"),
    (r"\b(CONSUMER|DURABLE)\b", "consumer"),
    (r"\bCARD\b", "credit_card"),
)


def _suggested_tradeline(bank: dict) -> dict:
    text = " ".join(str(bank.get(k) or "") for k in ("narration", "payee", "lender")).upper()
    loan_type = next((kind for pattern, kind in _LOAN_TYPE_WORDS if re.search(pattern, text)), "personal")
    lender = bank.get("lender") or (bank.get("payee") or "").title() or None
    return {
        "loan_type": loan_type,
        "lender": lender,
        "emi": bank["amount"],
        "status": "active",
        "action": "obligate",
        "source": "bank_statement",
    }


def _applicant_facts(project_id: str, applicant: dict, notes: list[str]) -> list[dict]:
    ids = {d.get("document_id") for d in applicant.get("documents") or [] if isinstance(d, dict)}
    try:
        facts = query_facts(project_id)
    except (ClientError, BotoCoreError) as e:
        print(f"eligibility: facts not read project={project_id} ({type(e).__name__})")
        notes.append("The documents' details could not be read: only the file check's figures are filled")
        return []
    return [facts[i] for i in sorted(ids, key=str) if i in facts and isinstance(facts[i].get("fields"), dict)]


# ------------------------------------------------------------------ document values (auto-fill)
# What the applicant's documents give each field of the page (their facts records): the application
# form first, then the ID / address proof (mobile, DOB, addresses, pincode, house ownership, company,
# employment type); the net income the file check verified; bonus and incentive from the salary
# slips; rented income from a rent agreement with the applicant as landlord, else the Form-16 / ITR;
# pension from the pension slips; the whole CAM block from the latest credit report. Each value keeps
# the file (and page, when the record gives it) it was read from.

FORM_TYPES = ("loan_application", "identity_details")
PROFILE_FIELDS = (
    "pan",
    "name",
    "mobile",
    "dob",
    "house_ownership",
    "pincode",
    "current_address",
    "permanent_address",
    "company",
    "employment_type",
    "net_income",
)
# from_documents names, in the order of the sheet.
FILLED_FIELDS = (
    "name",
    "pan",
    "mobile",
    "dob",
    "house_ownership",
    "pincode",
    "current_address",
    "permanent_address",
    "company",
    "employment_type",
    "net_income",
    "other_income",
    "loan_amount",
    "tenure_months",
    "score",
    "enquiries",
    "tradelines",
)
BUREAUS = {"cibil": "CIBIL", "experian": "Experian", "equifax": "Equifax", "crif": "CRIF"}
ENQUIRY_WINDOWS = ("d30", "d60", "d90", "d120")
_PINCODE_IN_TEXT = re.compile(r"(?<![0-9])[1-9][0-9]{5}(?![0-9])")
_SAME_ADDRESS = re.compile(r"^\W*same\s+as\s+(the\s+)?(current|present|above|communication|residential)\b", re.I)
_HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "dr", "shri", "smt", "kumari", "km"})


class _Fill:
    """What the documents give the page: per field its value and files, the rows, and notes."""

    def __init__(self) -> None:
        self.sources: dict[str, FieldSource] = {}
        self.other_income: list[FieldSource] = []
        self.tradelines: list[FieldSource] = []
        self.notes: list[str] = []
        self.credit_report: str | None = None


def _of(facts: list[dict], *doc_types: str) -> list[dict]:
    """The records of these document types, in this order."""
    return [record for doc_type in doc_types for record in facts if record.get("doc_type") == doc_type]


def _file(record: dict) -> str:
    return _str(record.get("document_name"), 300) or _str(record.get("document_id")) or "a document"


def _page(record: dict, field: str | None = None, item: Any = None) -> int | None:
    """1-based page of a value when the record gives it: the list item's, the field's (field_pages /
    pages, on the record or its fields), else the record's own page."""
    candidates = [item.get("page") if isinstance(item, dict) else None]
    for holder in (record, record.get("fields") or {}):
        for name in ("field_pages", "pages"):
            pages = holder.get(name)
            if field and isinstance(pages, dict):
                candidates.append(pages.get(field))
    candidates.append(record.get("page"))
    for page in candidates:
        if _positive(page) and float(page).is_integer() and page <= 9999:
            return int(page)
    return None


def _ref(record: dict, field: str | None = None, item: Any = None) -> DocumentRef:
    return DocumentRef(
        document_id=_str(record.get("document_id")),
        file=_file(record),
        page=_page(record, field, item),
        doc_type=_str(record.get("doc_type"), 50),
    )


def _unverified(record: dict, *names: str) -> bool:
    """An amount the grounding did not find in the document's text (grounding.unverified_fields)."""
    flagged = (record.get("grounding") or {}).get("unverified_fields") or []
    return any(isinstance(f, str) and any(f == n or f.startswith((f"{n}.", f"{n}[")) for n in names) for f in flagged)


def _source(
    value: Any,
    refs: Iterable[DocumentRef] = (),
    detail: str | None = None,
    unverified: bool = False,
    source: str = "document",
) -> FieldSource:
    return FieldSource(source=source, value=value, documents=list(refs), detail=detail, unverified=unverified)


def _checked(model: type[_Input], data: dict[str, Any]) -> dict[str, Any] | None:
    """`data` as `model` takes it (ids, normalised numbers and dates, JSON), every value it refuses
    dropped; None when nothing is left or a rule between fields fails."""
    data = {k: v for k, v in data.items() if v is not None}
    while data:
        try:
            return model.model_validate(data).model_dump(mode="json")
        except ValidationError as e:
            bad = {err["loc"][0] for err in e.errors() if err["loc"]} & data.keys()
            if not bad:
                return None
            data = {k: v for k, v in data.items() if k not in bad}
    return None


def _profile_value(field: str, value: Any) -> Any:
    """`value` as the profile's `field` takes it (spacing collapsed), else None."""
    if isinstance(value, str):
        value = " ".join(value.split())
    checked = _checked(Profile, {field: value})
    return checked.get(field) if checked else None


def _money(value: Any) -> float | None:
    """A positive amount within the API's limit, else None."""
    if _positive(value) and value <= MAX_AMOUNT:
        return float(value)
    return None


def _date(value: Any) -> dt.date | None:
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return dt.datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _same_person(a: str, b: str) -> bool:
    """Two names of one person: each word of the shorter is a word, or the initial, of the longer."""

    def words(name: str) -> list[str]:
        return [w for w in re.findall(r"[a-z]+", name.casefold()) if w not in _HONORIFICS]

    short, long = sorted((words(a), words(b)), key=len)
    return all(any(w == v or (min(len(w), len(v)) == 1 and w[0] == v[0]) for v in long) for w in short)


def _declared(facts: list[dict], field: str, names: tuple[str, ...] | None = None, order=FORM_TYPES):
    """(value, record, field name) of the first form (the application, then the ID / address proof)
    whose value the profile's `field` takes; None if none."""
    for record in _of(facts, *order):
        for name in names or (field,):
            value = _profile_value(field, record["fields"].get(name))
            if value is not None:
                return value, record, name
    return None


def _identity_values(fill: _Fill, found: dict, facts: list[dict]) -> None:
    others = [r for r in facts if r.get("doc_type") not in ("identity_details", "loan_application")]
    preferred = [*_of(facts, "identity_details", "loan_application"), *others]
    name = _str(found.get("applicant"))
    value = _profile_value("name", name) if name and name != "Unknown" else None
    if value:
        holders = [r for r in preferred if _identities(r["fields"].get("applicant_name"))[0] == _identities(name)[0]]
        fill.sources["name"] = _source(value, [_ref(r, "applicant_name") for r in holders[:1]])
    pan = re.sub(r"\s", "", found.get("pan") or "").upper() if isinstance(found.get("pan"), str) else ""
    masked = _mask_pan(pan)
    if masked:
        holders = [r for r in preferred if re.sub(r"\s", "", str(r["fields"].get("pan") or "")).upper() == pan]
        fill.sources["pan"] = _source(masked, [_ref(r, "pan") for r in holders[:1]])
    for field in ("mobile", "house_ownership"):
        hit = _declared(facts, field)
        if hit:
            fill.sources[field] = _source(hit[0], [_ref(hit[1], hit[2])])
    # The ID proof is the better source of a date of birth.
    dob = _declared(facts, "dob", ("dob", "date_of_birth"), ("identity_details", "loan_application"))
    if dob is None:
        for record in _of(facts, "identity_details", "loan_application"):
            for key in ("dob", "date_of_birth"):
                parsed = _date(record["fields"].get(key))
                if parsed and (value := _profile_value("dob", parsed.isoformat())):
                    dob = (value, record, key)
                    break
            if dob:
                break
    if dob and dob[0] < _now().date().isoformat():
        fill.sources["dob"] = _source(dob[0], [_ref(dob[1], dob[2])])


def _address_values(fill: _Fill, facts: list[dict]) -> None:
    current = _declared(facts, "current_address")
    if current:
        fill.sources["current_address"] = _source(current[0], [_ref(current[1], "current_address")])
    pincode = _declared(facts, "pincode", ("current_pincode",))
    if pincode:
        fill.sources["pincode"] = _source(pincode[0], [_ref(pincode[1], "current_pincode")])
    elif current and (printed := _PINCODE_IN_TEXT.findall(current[0])):
        value = _profile_value("pincode", printed[-1])
        if value:
            fill.sources["pincode"] = _source(
                value, [_ref(current[1], "current_address")], detail="read from the current address"
            )
    for record in _of(facts, *FORM_TYPES):
        text = record["fields"].get("permanent_address")
        if current and isinstance(text, str) and _SAME_ADDRESS.match(text):
            fill.sources["permanent_address"] = _source(
                current[0], [_ref(record, "permanent_address")], detail="the form says: same as the current address"
            )
            return
        value = _profile_value("permanent_address", text)
        if value:
            pin = _profile_value("pincode", record["fields"].get("permanent_pincode"))
            if pin and pin not in value:
                value = _profile_value("permanent_address", f"{value} - {pin}") or value
            fill.sources["permanent_address"] = _source(value, [_ref(record, "permanent_address")])
            return


def _employment_type_by_name(employer: str) -> str | None:
    text = employer.casefold()
    if re.search(r"\b(pvt|private)\b.*\b(ltd|limited)\b", text):
        return "private_limited"
    if re.search(r"\bllp\b", text):
        return "llp"
    if re.search(r"\b(ltd|limited)\b", text):
        return "public_limited"
    return None


def _employment_values(fill: _Fill, facts: list[dict]) -> None:
    """The company the application (else the ID proof) names, else the longest employer of the slips /
    Form-16 (never a bank statement's), and the employment type the forms give, else the company's."""
    company = None
    for record in _of(facts, *FORM_TYPES):
        for name in ("company", "employer"):
            value = _profile_value("company", record["fields"].get(name))
            if value:
                company = (value, record, name)
                break
        if company:
            break
    if company is None:
        others = [
            (value, record, "employer")
            for record in _of(facts, "salary_slip", "form16_itr", "other")
            if (value := _profile_value("company", record["fields"].get("employer")))
        ]
        company = max(others, key=lambda c: len(c[0])) if others else None
    if company:
        fill.sources["company"] = _source(company[0], [_ref(company[1], company[2])])
    listed = eligibility.load_policy_book().find_company(company[0]) if company else None
    declared = _declared(facts, "employment_type")
    if declared:
        fill.sources["employment_type"] = _source(declared[0], [_ref(declared[1], "employment_type")])
        if listed and listed.employment_type and listed.employment_type != declared[0]:
            fill.notes.append(
                f"{_file(declared[1])} gives the employment type {eligibility.EMPLOYMENT_TYPES[declared[0]]}; the "
                f"company list (sample) has {listed.name} as {eligibility.EMPLOYMENT_TYPES[listed.employment_type]}"
            )
    elif listed and listed.employment_type:
        fill.sources["employment_type"] = _source(
            listed.employment_type, detail=f"{listed.name} in the company list (sample)", source="table"
        )
    elif company and (kind := _employment_type_by_name(company[0])):
        fill.sources["employment_type"] = _source(
            kind, [_ref(company[1], company[2])], detail="from the company's name"
        )


def _other_income_rows(found: dict, facts: list[dict], notes: list[str]) -> list[FieldSource]:
    rows: list[FieldSource] = []
    applicant = _str(found.get("applicant"))
    today = _now().date()
    for record in _of(facts, "rent_agreement"):
        fields = record["fields"]
        landlord = _str(fields.get("landlord_name"))
        if landlord and applicant and applicant != "Unknown" and not _same_person(landlord, applicant):
            notes.append(f"{_file(record)}: the applicant is not the landlord, so its rent is not their income")
            continue
        ends = _date(fields.get("agreement_to"))
        if ends and ends < today:
            notes.append(f"The rent agreement {_file(record)} ended on {ends.isoformat()}: its rent is not counted")
            continue
        row = _checked(
            OtherIncome,
            {"type": "rented", "amount": _money(fields.get("monthly_rent")), "agreement": fields.get("registration")},
        )
        if row and row.get("amount"):
            rows.append(
                _source(
                    row,
                    [_ref(record, "monthly_rent")],
                    detail=None if landlord else "the agreement names no landlord: check that it is the applicant",
                    unverified=_unverified(record, "monthly_rent"),
                )
            )
    returns = sorted(_of(facts, "form16_itr"), key=lambda r: str(r["fields"].get("financial_year") or ""), reverse=True)
    if not rows:
        for record in returns:
            annual = _money(record["fields"].get("rental_income_annual"))
            row = _checked(OtherIncome, {"type": "rented", "amount": round(annual / 12, 2)}) if annual else None
            if row:
                rows.append(
                    _source(
                        row,
                        [_ref(record, "rental_income_annual")],
                        detail=f"{eligibility.inr(annual)} a year on the Form-16 / ITR, per month; without a rent "
                        "agreement it counts as notary (the lower share)",
                        unverified=_unverified(record, "rental_income_annual"),
                    )
                )
                break
    for record in returns:
        other = _money(record["fields"].get("other_income_annual"))
        if other:
            notes.append(
                f"{_file(record)} shows other income of {eligibility.inr(other)} a year: add it as an income "
                "row if a lender counts it"
            )
            break
    # One slip per month; a bonus or incentive on every slip is monthly, else yearly (the lowest).
    slips: dict[str, dict] = {}
    for record in _of(facts, "salary_slip"):
        slips.setdefault(_str(record["fields"].get("month"), 7) or str(record.get("document_id")), record)
    for kind in ("bonus", "incentive"):
        paid = [(r, a) for r in slips.values() if (a := _money(r["fields"].get(kind)))]
        if not paid:
            continue
        label = eligibility.OTHER_INCOME_TYPES[kind]
        if len(slips) >= 2 and len(paid) == len(slips):
            frequency, amount = "monthly", min(a for _, a in paid)
            detail = f"{label} on each of the {len(slips)} salary slips: counted monthly (the lowest month)"
        else:
            frequency, amount = "yearly", round(sum(a for _, a in paid), 2)
            detail = (
                f"{label} on {len(paid)} of {len(slips)} salary slips: counted as yearly (the lowest); "
                "change how often it is paid if it is more often"
            )
        row = _checked(OtherIncome, {"type": kind, "amount": amount, "frequency": frequency})
        if row:
            rows.append(
                _source(
                    row,
                    [_ref(r, kind) for r, _ in paid],
                    detail=detail,
                    unverified=any(_unverified(r, kind) for r, _ in paid),
                )
            )
    pensions = [(r, a) for r in _of(facts, "pension_slip") if (a := _money(r["fields"].get("monthly_pension")))]
    row = _checked(OtherIncome, {"type": "pension", "amount": min(a for _, a in pensions)}) if pensions else None
    if row:
        rows.append(
            _source(
                row,
                [_ref(r, "monthly_pension") for r, _ in pensions],
                detail="the lowest of the pension slips" if len(pensions) > 1 else None,
                unverified=any(_unverified(r, "monthly_pension") for r, _ in pensions),
            )
        )
    return rows[:10]


def _income_values(fill: _Fill, found: dict, facts: list[dict]) -> None:
    verified = verified_income(found)
    income = found.get("income") if isinstance(found.get("income"), dict) else {}
    if verified and (amount := _profile_value("net_income", verified["amount"])) is not None:
        kind, field = (
            ("salary_slip", "net_salary")
            if verified["source"].startswith("salary")
            else ("bank_statement", "salary_credits")
        )
        fill.sources["net_income"] = _source(
            amount, [_ref(r, field) for r in _of(facts, kind)], detail=f"verified: {verified['source']}"
        )
    elif _positive(income.get("declared_net")) and (amount := _profile_value("net_income", income["declared_net"])):
        holders = [r for r in _of(facts, "loan_application") if _positive(r["fields"].get("declared_net_salary"))]
        fill.sources["net_income"] = _source(
            amount,
            [_ref(r, "declared_net_salary") for r in holders[:1]],
            detail="declared on the loan application, not verified",
        )
    fill.other_income = _other_income_rows(found, facts, fill.notes)


def _loan_values(fill: _Fill, facts: list[dict]) -> None:
    for record in _of(facts, "loan_application"):
        fields = record["fields"]
        amount = _money(fields.get("loan_amount"))
        if amount and "loan_amount" not in fill.sources:
            fill.sources["loan_amount"] = _source(
                amount, [_ref(record, "loan_amount")], unverified=_unverified(record, "loan_amount")
            )
        tenure = fields.get("loan_tenure_months")
        if _positive(tenure) and 1 <= int(tenure) <= 480 and "tenure_months" not in fill.sources:
            fill.sources["tenure_months"] = _source(int(tenure), [_ref(record, "loan_tenure_months")])


def _cam_tradeline(item: Any) -> dict | None:
    """A credit-report tradeline as the CAM takes it: Obligate when active, Close when closed (an
    adverse account without an EMI too); the account number reduced to its last 4."""
    if not isinstance(item, dict):
        return None
    status = eligibility.enum_id(
        item.get("status"), eligibility.TRADELINE_STATUSES, eligibility.TRADELINE_STATUS_ALIASES
    )
    emi = item.get("emi")
    paying = _positive(emi)
    action = "close" if status == "closed" or (status in ("written_off", "settled") and not paying) else "obligate"
    last4 = re.sub(r"[^A-Za-z0-9]", "", str(item.get("account_last4") or ""))[-4:]
    opened, last = _date(item.get("open_date")), _date(item.get("last_payment_date"))
    row = _checked(
        Tradeline,
        {
            "loan_type": eligibility.enum_id(
                item.get("loan_type"), eligibility.LOAN_TYPES, eligibility.LOAN_TYPE_ALIASES, ("_loan",)
            ),
            "lender": _str(item.get("lender"), 100),
            "sanction_amount": item.get("sanction_amount"),
            "outstanding": item.get("outstanding"),
            "emi": emi,
            "status": status,
            "account_number": f"XXXX{last4.upper()}" if last4 else None,
            "overdue": item.get("overdue"),
            "emis_paid": item.get("emis_paid"),
            "emis_pending": item.get("emis_pending"),
            "open_date": opened.isoformat() if opened else None,
            "last_payment_date": last.isoformat() if last and not (opened and last < opened) else None,
            "action": action,
            "source": "credit_report",
        },
    )
    if not row or not (row["lender"] or row["emi"] is not None or row["outstanding"] is not None):
        return None
    return row


def _cam_values(fill: _Fill, found: dict, facts: list[dict]) -> None:
    """The latest credit report's score, enquiries and tradelines; the bank statement's loan EMIs that
    match none of them stay suggestions (a matched one would count the same loan twice)."""
    rows: list[FieldSource] = []
    reports = _of(facts, "credit_report")
    if reports:
        report = max(
            reports,
            key=lambda r: (_date(r["fields"].get("report_date")) or dt.date.min, str(r.get("document_name") or "")),
        )
        fields = report["fields"]
        fill.credit_report = _file(report)
        score = _checked(Cibil, {"score": fields.get("credit_score")})
        if score and score["score"] is not None:
            fill.sources["score"] = _source(
                score["score"], [_ref(report, "credit_score")], unverified=_unverified(report, "credit_score")
            )
        names = {w: f"enquiries_{w[1:]}d" for w in ENQUIRY_WINDOWS}
        windows = {w: fields.get(name) for w, name in names.items()}
        enquiries = _checked(Enquiries, windows)
        if enquiries and any(enquiries[w] is not None for w in ENQUIRY_WINDOWS):
            fill.sources["enquiries"] = _source(
                {w: enquiries[w] for w in ENQUIRY_WINDOWS},
                [_ref(report, "enquiries_90d")],
                unverified=_unverified(report, *names.values()),
            )
        elif any(v is not None for v in windows.values()):
            fill.notes.append(
                f"The enquiries on {fill.credit_report} do not add up over 30, 60, 90 and 120 days: enter them by hand"
            )
        date = _checked(Cibil, {"report_date": fields.get("report_date")})
        bureau = BUREAUS.get(str(fields.get("bureau") or "").strip().casefold())
        fill.sources["report"] = _source(
            date["report_date"] if date else None,
            [_ref(report, "report_date")],
            detail=f"{bureau} report" if bureau else None,
        )
        items = fields.get("tradelines") if isinstance(fields.get("tradelines"), list) else []
        for index, item in enumerate(items[:50]):
            row = _cam_tradeline(item)
            if row:
                rows.append(
                    _source(
                        row, [_ref(report, "tradelines", item)], unverified=_unverified(report, f"tradelines[{index}]")
                    )
                )
        when = f", report of {date['report_date']}" if date and date["report_date"] else ""
        fill.notes.append(
            f"CIBIL block read from the credit report {fill.credit_report} ({bureau or 'bureau not named'}{when}): "
            "active loans are marked Obligate and closed ones Close"
        )
        if len(reports) > 1:
            fill.notes.append(f"{len(reports)} credit reports: the latest ({fill.credit_report}) is used")
    bank = bank_statement_emis(found)[:50]
    pairs = eligibility.match_bank_emis([r.value for r in rows], bank) if rows else [(b, None) for b in bank]
    matched = sum(1 for _, index in pairs if index is not None)
    if matched:
        fill.notes.append(f"{matched} loan EMI(s) of the bank statement are on the credit report: not added twice")
    statements = _of(facts, "bank_statement")
    suggested = 0
    for bank_emi, index in pairs:
        row = _checked(Tradeline, _suggested_tradeline(bank_emi)) if index is None else None
        if row:
            suggested += 1
            seen = bank_emi.get("months_seen")
            total = bank_emi.get("months_total")
            rows.append(
                _source(
                    row,
                    [_ref(r, "recurring_debits") for r in statements[:1]],
                    detail=f"loan EMI in {seen} of {total} months of the bank statement" if seen and total else None,
                    unverified=bank_emi.get("unverified") is True,
                )
            )
    if suggested:
        fill.notes.append(
            f"{suggested} loan EMI(s) from the bank statement were added as tradelines: check the loan type, lender "
            "and action, and add the outstanding"
        )
    fill.tradelines = rows[:50]


def _document_values(found: dict | None, facts: list[dict]) -> _Fill:
    fill = _Fill()
    if found is None:
        return fill
    _identity_values(fill, found, facts)
    _address_values(fill, facts)
    _employment_values(fill, facts)
    _income_values(fill, found, facts)
    _loan_values(fill, facts)
    _cam_values(fill, found, facts)
    return fill


def _draft_inputs(fill: _Fill) -> dict[str, Any]:
    s = fill.sources
    profile: dict[str, Any] = {f: s[f].value for f in PROFILE_FIELDS if f in s}
    profile["other_income"] = [row.value for row in fill.other_income]
    cibil: dict[str, Any] = {"tradelines": [row.value for row in fill.tradelines]}
    if "score" in s:
        cibil["score"] = s["score"].value
    if "enquiries" in s:
        cibil["enquiries"] = s["enquiries"].value
    if fill.credit_report:
        cibil["source"] = "credit_report"
        if s.get("report") and s["report"].value:
            cibil["report_date"] = s["report"].value
    loan = {key: s[f].value for key, f in (("amount", "loan_amount"), ("tenure_months", "tenure_months")) if f in s}
    return {"profile": profile, "cibil": cibil, "loan": loan}


def _filled(fill: _Fill) -> list[str]:
    """from_documents: the fields the documents filled, in the sheet's order."""
    rows = {"other_income": bool(fill.other_income), "tradelines": bool(fill.tradelines)}
    return [f for f in FILLED_FIELDS if rows.get(f, f in fill.sources)]


# Rows of saved inputs are the documents' rows when these values are the same (one row each).
_ROW_IDENTITY = {"other_income": ("type",), "tradelines": ("source", "lender", "account_number", "open_date")}


def _row_sources(rows: list[dict], document_rows: list[FieldSource], keys: tuple[str, ...]) -> list[FieldSource | None]:
    used: set[int] = set()
    out: list[FieldSource | None] = []
    for row in rows:
        match = None
        if row.get("source") not in ("manual", "bureau"):
            match = next(
                (
                    i
                    for i, d in enumerate(document_rows)
                    if i not in used and all(row.get(k) == d.value.get(k) for k in keys)
                ),
                None,
            )
        if match is not None:
            used.add(match)
        out.append(document_rows[match] if match is not None else None)
    return out


def _saved_sources(inputs: EligibilityInputs, fill: _Fill, found: dict | None) -> tuple[dict, RowSources]:
    """The documents' values next to saved inputs (a full PAN only when the saved one is the documents')."""
    sources = dict(fill.sources)
    saved_pan = inputs.profile.pan
    if (
        "pan" in sources
        and is_full_pan(saved_pan)
        and saved_pan == re.sub(r"\s", "", (found or {}).get("pan") or "").upper()
    ):
        sources["pan"] = sources["pan"].model_copy(update={"value": saved_pan})
    dumped = inputs.model_dump(mode="json")
    rows = RowSources(
        other_income=_row_sources(dumped["profile"]["other_income"], fill.other_income, _ROW_IDENTITY["other_income"]),
        tradelines=_row_sources(dumped["cibil"]["tradelines"], fill.tradelines, _ROW_IDENTITY["tradelines"]),
    )
    return sources, rows


# "Still needed before Check eligibility", in the order of the sheet: (field, label, every lender needs it).
NEEDED_FIELDS = (
    ("pan", "PAN", False),
    ("name", "Name as per PAN", False),
    ("mobile", "Mobile number", False),
    ("dob", "Date of birth", False),
    ("house_ownership", "House ownership", False),
    ("pincode", "Pincode", True),
    ("current_address", "Current address", False),
    ("permanent_address", "Permanent address", False),
    ("company", "Company", True),
    ("employment_type", "Employment type", True),
    ("net_income", "Net income", True),
    ("loan_amount", "Loan amount", False),
    ("tenure_months", "Tenure", False),
    ("score", "CIBIL score", True),
    ("enquiries.d30", "Enquiries in the last 30 days", False),
    ("enquiries.d60", "Enquiries in the last 60 days", False),
    ("enquiries.d90", "Enquiries in the last 90 days", True),
    ("enquiries.d120", "Enquiries in the last 120 days", False),
)


def still_needed(inputs: EligibilityInputs, sources: dict[str, FieldSource] | None = None) -> list[StillNeeded]:
    """The empty fields (those no document filled and nobody typed), and the tradelines a lender cannot
    count yet; `from_documents` when a document holds a value the inputs do not have."""
    sources = sources or {}
    p, c, loan = inputs.profile, inputs.cibil, inputs.loan
    values = {f: getattr(p, f) for f in PROFILE_FIELDS}
    values.update(loan_amount=loan.amount, tenure_months=loan.tenure_months, score=c.score)
    values.update({f"enquiries.{w}": getattr(c.enquiries, w) for w in ENQUIRY_WINDOWS})

    def held(field: str) -> bool:
        if field.startswith("enquiries."):
            value = sources["enquiries"].value if "enquiries" in sources else None
            return isinstance(value, dict) and value.get(field.split(".")[1]) is not None
        return field in sources and sources[field].value is not None

    out = [
        StillNeeded(field=field, label=label, required=required, from_documents=held(field))
        for field, label, required in NEEDED_FIELDS
        if values[field] is None
    ]
    for index, row in enumerate(c.tradelines, 1):
        named = row.lender or (eligibility.LOAN_TYPES.get(row.loan_type) if row.loan_type else None)
        suffix = f" ({named})" if named else ""
        if row.status == "closed":
            continue
        if row.action == "obligate" and row.emi is None:
            out.append(
                StillNeeded(
                    field=f"tradelines.{index}.emi",
                    label=f"EMI of loan {index}{suffix}",
                    required=True,
                    from_documents=False,
                )
            )
        elif row.action == "bt" and row.outstanding is None:
            out.append(
                StillNeeded(
                    field=f"tradelines.{index}.outstanding",
                    label=f"Outstanding of loan {index}{suffix}",
                    required=True,
                    from_documents=False,
                )
            )
    return out


def _draft(
    found: dict | None, detail: str, check: dict, fill: _Fill, notes: list[str]
) -> tuple[EligibilityInputs, list[str], Prefill]:
    """Inputs filled from the documents: (inputs, fields taken from the documents, prefill)."""
    if found is None:
        return EligibilityInputs(), [], Prefill(available=False, detail=f"Not pre-filled: {detail}")
    notes.extend(fill.notes)
    if check.get("pending_documents"):
        notes.append(
            f"{len(check['pending_documents'])} document(s) still being analysed: reload to fill more fields from them"
        )
    try:
        inputs, fields = EligibilityInputs.model_validate(_draft_inputs(fill)), _filled(fill)
    except ValidationError as e:
        print(f"eligibility: pre-fill rejected at {[err['loc'] for err in e.errors()][:5]}")
        inputs, fields = EligibilityInputs(), []
        notes.append("The documents' values could not be used: fill the form by hand")
    verified = verified_income(found)
    count = len(found.get("documents") or [])
    prefill = Prefill(
        available=True,
        detail=f"Pre-filled from the file check ({count} documents)",
        applicant_name=_str(found.get("applicant")),
        pan_masked=_mask_pan(found.get("pan")),
        employer=fill.sources["company"].value if "company" in fill.sources else None,
        verified_net_income=verified["amount"] if verified else None,
        income_source=fill.sources["net_income"].detail if "net_income" in fill.sources else None,
        dob=fill.sources["dob"].value if "dob" in fill.sources else None,
        suggested_tradelines=sum(1 for row in fill.tradelines if row.value.get("source") == "bank_statement"),
        credit_report=fill.credit_report,
        documents=count,
    )
    return inputs, fields, prefill


def _checked_file(project_id: str, identifier: str) -> tuple[str, tuple[dict | None, str, dict]]:
    """The file check for `identifier`, kept with it so that a later run for the same one reuses it."""
    return identifier, _file_check_applicant(project_id, identifier)


def _of_found(found: dict | None) -> tuple[Any, ...]:
    """The applicant's name and PAN as the file check shows them."""
    return (found.get("applicant"), found.get("pan")) if found else ()


def file_check_use(found: dict | None, detail: str) -> dict[str, Any]:
    """The calculation's file_check: used or not (why), and the file's verdict with its open issues."""
    verdict = _str(found.get("verdict"), 20) if found else None
    issues = [r[:300] for r in (found or {}).get("reasons") or [] if isinstance(r, str) and r.strip()]
    return {
        "used": found is not None,
        "detail": detail,
        "applicant": _str(found.get("applicant")) if found else None,
        "verdict": verdict,
        "ready": verdict == "READY" if verdict else None,
        "issues": [] if verdict == "READY" else issues[:30],
    }


def _calculation_lists(project_id: str, notes: list[str] | None = None) -> reference_data.CalculationLists | None:
    """The DSA's uploaded serviceability and company lists; None when none is uploaded or they cannot
    be read (then the SAMPLE lists apply, and `notes` says so)."""
    try:
        return reference_data.calculation_lists(project_id, _now())
    except (ClientError, BotoCoreError) as e:
        print(f"eligibility: uploaded lists not read project={project_id} ({type(e).__name__})")
        if notes is not None:
            notes.append("Your uploaded serviceability and company lists could not be read: the sample lists are used")
        return None


def _run(
    project_id: str,
    applicant: str,
    inputs: EligibilityInputs,
    checked: tuple[str, tuple[dict | None, str, dict]] | None = None,
) -> dict[str, Any]:
    """The engine's calculation with the file check's verified salary and bank-statement EMIs
    (`checked`: a file check already made, used when it is for the same identifier)."""
    identifier = inputs.profile.pan if is_full_pan(inputs.profile.pan) else applicant
    if checked is None or checked[0] != identifier:
        checked = _checked_file(project_id, identifier)
    found, detail, check = checked[1]
    notes: list[str] = []
    if found is None:
        notes.append(
            f"File check not used ({detail}): the entered net income is used and no bank-statement EMIs are added"
        )
    elif check.get("pending_documents"):
        notes.append(
            f"{len(check['pending_documents'])} document(s) still being analysed: the verified figures may change"
        )
    lists = _calculation_lists(project_id, notes)
    book = eligibility.load_policy_book()
    result = eligibility.calculate(
        inputs.model_dump(mode="json"),
        verified_income=verified_income(found),
        bank_emis=bank_statement_emis(found),
        book=reference_data.CalculationBook(book, lists) if lists else book,
    )
    result["notes"] = notes + result["notes"] + (lists.notes() if lists else [])
    result["file_check"] = file_check_use(found, detail)
    result["applicant"] = applicant
    result["calculated_at"] = _iso(_now())
    return result


def _saved_for(
    project_id: str, applicant: str, checked: tuple[str, tuple[dict | None, str, dict]]
) -> EligibilityInputs | None:
    """The applicant's saved inputs, found by PAN or name (the file check gives the other one)."""
    stored = _find_saved(project_id, applicant, _now(), _of_found(checked[1][0]))
    return _saved_inputs(stored, project_id) if stored else None


# ------------------------------------------------------------------ login
def reset_login_limits() -> None:
    """Forget every login's time and count (tests)."""
    global _running_logins
    with _login_lock:
        _last_login_at.clear()
        _running_logins = 0


def _begin_login(key: tuple[str, str, str]) -> None:
    global _running_logins
    now = _monotonic()
    with _login_lock:
        last = _last_login_at.get(key)
        if last is not None and now - last < LOGIN_INTERVAL_S:
            wait = max(1, math.ceil(LOGIN_INTERVAL_S - (now - last)))
            raise HTTPException(
                status_code=429,
                detail=f"This file was just logged in with this lender: wait {wait} s",
                headers={"Retry-After": str(wait)},
            )
        if _running_logins >= MAX_CONCURRENT_LOGINS:
            raise HTTPException(
                status_code=429,
                detail="Too many login requests are being sent; try again in a few seconds",
                headers={"Retry-After": "5"},
            )
        if len(_last_login_at) > 512:
            for stale in [k for k, t in _last_login_at.items() if now - t >= LOGIN_INTERVAL_S]:
                del _last_login_at[stale]
        _last_login_at[key] = now
        _running_logins += 1


def _end_login(key: tuple[str, str, str], recorded: bool) -> None:
    """Free the slot; a request that was not recorded may be retried at once."""
    global _running_logins
    with _login_lock:
        _running_logins = max(0, _running_logins - 1)
        if not recorded:
            _last_login_at.pop(key, None)


def _failed(error: str) -> tuple[str, dict, None]:
    return "failed", {"delivery_id": None, "status": "failed", "http_status": None, "error": error}, None


def _notify_crm(project_id: str, login: dict[str, Any]) -> tuple[str, dict | None, str | None]:
    """(webhook outcome, delivery, detail): event file_login.requested through the delivery Lambda."""
    settings = get_webhook_settings(project_id)
    if settings is None or not (settings.enabled and settings.url and settings.secret_set):
        return "not_enabled", None, "the project's CRM webhook is not enabled"
    function_name = get_config().webhook_function_name
    if not function_name:
        return "not_configured", None, "webhook delivery is not configured"
    try:
        response, raw = invoke_delivery(function_name, {"project_id": project_id, "event": EVENT_LOGIN, "login": login})
    except ClientError as e:
        return _failed(f"invoke failed ({e.response.get('Error', {}).get('Code') or 'ClientError'})")
    except BotoCoreError as e:
        return _failed(f"invoke failed ({type(e).__name__})")
    if response.get("FunctionError"):
        return _failed(f"function error ({response['FunctionError']})")
    try:
        payload = json.loads(raw)
    except ValueError:
        return _failed("non-JSON response")
    if not isinstance(payload, dict):
        return _failed("non-object response")
    if payload.get("status") == "skipped":
        return "skipped", None, str(payload.get("reason") or "not sent")
    if payload.get("status") in ("delivered", "failed") and isinstance(payload.get("delivery_id"), str):
        http_status = payload.get("http_status")
        error = payload.get("error")
        return (
            payload["status"],
            {
                "delivery_id": payload["delivery_id"],
                "status": payload["status"],
                "http_status": http_status if isinstance(http_status, int) else None,
                "error": error if isinstance(error, str) and error else None,
            },
            None,
        )
    return _failed(str(payload.get("error") or "unexpected response")[:300])


def _write_login_audit(
    project_id: str, lender_id: str, at: dt.datetime, file_ready: bool | None = None, open_issues: int = 0
) -> dict[str, str]:
    key = {"PK": f"PROJ#{project_id}", "SK": f"{LOGIN_SK_PREFIX}{_iso(at)}#{uuid.uuid4().hex}"}
    item: dict[str, Any] = {
        **key,
        "lender_id": lender_id,
        "requested_at": _iso(at),
        "webhook_status": "pending",
        TTL_ATTRIBUTE: int(at.timestamp()) + get_config().retention_days * 86400,
    }
    if file_ready is False:
        # Logged in although the file check is NOT READY: the user confirmed it (counts only).
        item.update(file_ready=False, open_issues=open_issues)
    get_table().put_item(Item=item)
    return key


def _not_ready_detail(issues: list[str]) -> str:
    """The 428 of a NOT READY file: its open issues (the first five) and how to log it in anyway."""
    shown = "; ".join(issue[:200] for issue in issues[:5]) + ("; …" if len(issues) > 5 else "")
    return (
        f"The file check is NOT READY ({len(issues)} open issue{'s' if len(issues) != 1 else ''}): {shown}. "
        "Confirm to log it in anyway (confirm_not_ready)"
    )


def _update_login_audit(key: dict[str, str], webhook: str) -> None:
    try:
        get_table().update_item(
            Key=key,
            UpdateExpression="SET webhook_status = :webhook",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues={":webhook": webhook},
        )
    except (ClientError, BotoCoreError) as e:
        print(f"eligibility login: audit not updated ({type(e).__name__})")


def _display_name(applicant: str, inputs: EligibilityInputs) -> str:
    """The applicant's name for the CRM: the saved name, else the applicant; never a full PAN."""
    if inputs.profile.name:
        return inputs.profile.name
    if is_full_pan(applicant):
        return _mask_pan(applicant) or "applicant"
    return applicant


# ------------------------------------------------------------------ routes
@router.get("/lenders", responses=_NOT_FOUND, summary="SAMPLE lender policies and the form's choices")
def list_lenders(project_id: ProjectId, user_id: UserId) -> LendersResponse:
    """Every lender's SAMPLE policy (ROI, FOIR, multiplier, tenure, amounts, CIBIL and enquiry limits,
    employment types, company categories, income consideration, serviceable regions)."""
    _require_project(project_id)
    book = eligibility.load_policy_book()
    lenders = [
        LenderPolicyOut(
            **lender.model_dump(exclude={"employment_types", "company_categories", "unlisted_company"}),
            employment_types=list(lender.employment_types),
            company_categories={k: CategoryPolicyOut(**v.model_dump()) for k, v in lender.company_categories.items()},
            unlisted_company=UnlistedCompanyOut(**lender.unlisted_company.model_dump()),
            serviceable_regions=[region.name for region in book.regions_of(lender.id)],
            sample=book.sample,
            label=book.label,
        )
        for lender in book.lenders
    ]
    disclaimers = [f"Every result is {eligibility.INDICATIVE_LABEL}."]
    if book.sample:
        disclaimers.insert(0, f"All lender data here is SAMPLE data: {eligibility.SAMPLE_LABEL}.")
    options = eligibility.options()
    options["house_ownership"] = [{"id": k, "label": v} for k, v in HOUSE_OWNERSHIP.items()]
    options["sources"] = [{"id": k, "label": v} for k, v in SOURCE_LABELS.items()]
    return LendersResponse(
        sample=book.sample,
        label=book.label,
        version=book.policies.version,
        lenders=lenders,
        options=FormOptions(**options),
        disclaimers=disclaimers,
    )


@router.get(
    "/pincodes/{pincode}",
    responses=_NOT_FOUND,
    summary='"Check Availability": the lenders that serve a pincode (your list, else the SAMPLE lists)',
)
def check_pincode(
    project_id: ProjectId,
    user_id: UserId,
    pincode: Annotated[str, Path(pattern=r"^[1-9][0-9]{5}$", description="6-digit pincode")],
) -> PincodeResponse:
    _require_project(project_id)
    book = eligibility.load_policy_book()
    region = book.region_of(pincode)
    lists = _calculation_lists(project_id)
    lenders = []
    for lender in book.lenders:
        own = lists.serviceable(lender, pincode) if lists else None
        serviceable = book.serviceable(lender.id, pincode) if own is None else own
        source = "sample" if own is None else "dsa_list"
        lenders.append(PincodeLender(lender_id=lender.id, lender=lender.name, serviceable=serviceable, source=source))
    return PincodeResponse(
        pincode=pincode,
        region=region.name if region else None,
        serviceable_by=sum(1 for lender in lenders if lender.serviceable),
        lenders=lenders,
        sample=book.sample,
        label=book.label,
    )


@router.get(
    "/companies",
    responses=_NOT_FOUND,
    summary='"Check Category": a company\'s category with every lender (your list, else the SAMPLE lists)',
)
def check_company(
    project_id: ProjectId,
    user_id: UserId,
    name: Annotated[str, Query(min_length=1, max_length=200, pattern=_LINE, description="Company name or part of it")],
) -> CompanyResponse:
    _require_project(project_id)
    book = eligibility.load_policy_book()
    query = name.strip()
    company = book.find_company(query)
    lists = _calculation_lists(project_id)
    names = [query, *([company.name, *company.aliases] if company else [])]
    categories = []
    for lender in book.lenders:
        own = bool(lists and lists.covers_companies(lender))
        if own:
            category = lists.company_category(lender, names)
        else:
            category = company.categories.get(lender.id) if company else None
        foir_range = None
        if category is not None:
            policy = lender.company_categories[category]
            foir, multiplier, accepted = policy.foir, policy.multiplier, True
            column = lender.foir_grid.categories.get(category) if lender.foir_grid else None
            if column and min(column) != max(column):
                foir_range = [min(column), max(column)]
        else:
            unlisted = lender.unlisted_company
            foir, multiplier, accepted = unlisted.foir, unlisted.multiplier, unlisted.accepted
        categories.append(
            CompanyCategory(
                lender_id=lender.id,
                lender=lender.name,
                category=category,
                listed=category is not None,
                accepted=accepted,
                foir=foir,
                multiplier=multiplier,
                foir_range=foir_range,
                source="dsa_list" if own else "sample",
            )
        )
    match = (
        CompanyMatch(
            name=company.name,
            aliases=list(company.aliases),
            employment_type=company.employment_type,
            synthetic=company.synthetic,
        )
        if company
        else None
    )
    return CompanyResponse(
        query=query,
        match=match,
        categories=categories,
        suggestions=[c.name for c in book.company_suggestions(query) if not company or c is not company],
        sample=book.sample,
        label=book.label,
    )


@router.get(
    "/inputs",
    responses={**_INVALID_APPLICANT, **_NOT_FOUND},
    summary="An applicant's saved inputs, else a draft pre-filled from the file check",
)
def get_inputs(
    project_id: ProjectId,
    user_id: UserId,
    applicant: Annotated[str, Query(min_length=1, max_length=200, description="PAN (preferred) or name")],
) -> InputsResponse:
    """Saved inputs (until they expire, found by PAN or name), else a draft filled from the documents:
    every field a document holds (profile, addresses, pincode, house ownership, company, employment
    type, verified net income, other income, loan amount and tenure, and the CIBIL block of a credit
    report), with the bank statement's other loan EMIs as suggested tradelines. `sources` gives each
    field's documents' value and files (next to saved inputs too: saved values are never changed),
    `still_needed` the fields that are still empty. Nothing is saved by this call."""
    _require_project(project_id)
    applicant = _applicant(applicant)
    book = eligibility.load_policy_book()
    found, detail, check = _file_check_applicant(project_id, applicant)
    notes: list[str] = []
    fill = _document_values(found, _applicant_facts(project_id, found, notes) if found else [])
    document_rows = DocumentRows(other_income=fill.other_income, tradelines=fill.tradelines)
    stored = _find_saved(project_id, applicant, _now(), _of_found(found))
    saved_inputs = _saved_inputs(stored, project_id) if stored else None
    if stored and saved_inputs is not None:
        sources, row_sources = _saved_sources(saved_inputs, fill, found)
        print(f"eligibility inputs user={user_id} project={project_id} saved=true documents={len(sources)}")
        return InputsResponse(
            applicant=applicant,
            saved=True,
            inputs=saved_inputs,
            from_documents=[],
            prefill=None,
            sources=sources,
            row_sources=row_sources,
            document_rows=document_rows,
            still_needed=still_needed(saved_inputs, sources),
            created_at=stored.get("created_at"),
            updated_at=stored.get("updated_at"),
            expires_at=_expires_iso(stored),
            label=book.label,
        )
    inputs, fields, prefill = _draft(found, detail, check, fill, notes)
    if stored:
        notes.insert(0, "The saved inputs could not be read (saved by an older version): check the form again")
    print(
        f"eligibility inputs user={user_id} project={project_id} saved=false prefill={prefill.available} "
        f"fields={len(fields)}"
    )
    sources = fill.sources if fields else {}
    return InputsResponse(
        applicant=applicant,
        saved=False,
        inputs=inputs,
        from_documents=fields,
        prefill=prefill,
        sources=sources,
        row_sources=RowSources(
            other_income=fill.other_income if fields else [], tradelines=fill.tradelines if fields else []
        ),
        document_rows=document_rows,
        still_needed=still_needed(inputs, sources),
        notes=notes,
        label=book.label,
    )


@router.put(
    "/inputs",
    responses={**_INVALID_APPLICANT, **_NOT_FOUND},
    summary="Save an applicant's inputs (deleted after the retention period)",
)
def put_inputs(project_id: ProjectId, user_id: UserId, request: SaveInputsRequest) -> InputsResponse:
    """Replaces the applicant's saved inputs (also when they were saved under its other identifier, the
    PAN or the name: they move here). They expire (DynamoDB TTL) the retention period (default 7 days)
    after the first save; later saves keep that date."""
    _require_project(project_id)
    applicant = _applicant(request.applicant)
    inputs = EligibilityInputs(profile=request.profile, cibil=request.cibil, loan=request.loan)
    item = _save(project_id, applicant, inputs, _now(), (inputs.profile.name, inputs.profile.pan))
    print(
        f"eligibility inputs saved user={user_id} project={project_id} tradelines={len(inputs.cibil.tradelines)} "
        f"other_income={len(inputs.profile.other_income)}"
    )
    return InputsResponse(
        applicant=applicant,
        saved=True,
        inputs=inputs,
        from_documents=[],
        prefill=None,
        still_needed=still_needed(inputs),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        expires_at=_expires_iso(item),
        label=eligibility.load_policy_book().label,
    )


@router.post(
    "/calculate",
    responses={
        **_INVALID_APPLICANT,
        404: {"model": ErrorResponse, "description": "Project not found, or no saved inputs (and none sent)"},
    },
    summary="Eligibility at every lender (fixed formulas; SAMPLE policies; indicative)",
)
def calculate(project_id: ProjectId, user_id: UserId, request: CalculateRequest) -> CalculateResponse:
    """Per lender: status (eligible / not_serviceable / not_eligible with reasons), eligible amount, EMI at
    the lender's max tenure and ROI, and the working: per-lakh EMI, FOIR and multiplier eligibility,
    income and obligations considered, with each parameter's source (policy / formula / table); and
    the file check's verdict with its open issues."""
    _require_project(project_id)
    applicant = _applicant(request.applicant)
    inputs = request.inputs
    checked = None
    if inputs is None:
        checked = _checked_file(project_id, applicant)
        inputs = _saved_for(project_id, applicant, checked)
        if inputs is None:
            raise HTTPException(
                status_code=404,
                detail="No saved eligibility inputs for this applicant: save them (PUT .../inputs) or send inputs",
            )
    result = _run(project_id, applicant, inputs, checked)
    eligible = sum(1 for r in result["per_lender"] if r["status"] == "eligible")
    print(
        f"eligibility calculate user={user_id} project={project_id} lenders={len(result['per_lender'])} "
        f"eligible={eligible} file_check={result['file_check']['used']} ready={result['file_check']['ready']}"
    )
    return CalculateResponse.model_validate(result)


@router.post(
    "/login",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid applicant or unknown lender"},
        404: {"model": ErrorResponse, "description": "Project not found, or no saved inputs for the applicant"},
        409: {"model": ErrorResponse, "description": "The lender is not eligible for this file (reasons given)"},
        428: {
            "model": ErrorResponse,
            "description": "The file check is NOT READY (open issues given) and confirm_not_ready was not sent",
        },
        429: {"model": ErrorResponse, "description": "Just logged in with this lender, or too many at once"},
        502: {"model": ErrorResponse, "description": "The login request could not be recorded"},
    },
    summary="Log the file in with a lender: record it and notify the CRM webhook",
)
def login(project_id: ProjectId, user_id: UserId, request: LoginRequest) -> LoginResponse:
    """Recomputes the lender's figures from the saved inputs, records the request (an audit item without
    applicant data, deleted after the retention period) and, when the CRM webhook is enabled, sends event
    file_login.requested {applicant, lender, eligible_amount, emi, tenure_months, roi} signed like every
    delivery. A file the file check finds NOT READY is logged in only with confirm_not_ready (the user saw
    its open issues); the event's note then says so. A CRM that is down makes `webhook` "failed", not an
    error."""
    _require_project(project_id)
    applicant = _applicant(request.applicant)
    book = eligibility.load_policy_book()
    lender = book.lender(request.lender.strip())
    if lender is None:
        known = ", ".join(policy.id for policy in book.lenders)
        raise HTTPException(status_code=400, detail=f"Unknown lender (known: {known})")
    checked = _checked_file(project_id, applicant)
    inputs = _saved_for(project_id, applicant, checked)
    if inputs is None:
        raise HTTPException(
            status_code=404, detail="No saved eligibility inputs for this applicant: save them (PUT .../inputs) first"
        )
    result = _run(project_id, applicant, inputs, checked)
    row = next(r for r in result["per_lender"] if r["lender_id"] == lender.id)
    if row["status"] != "eligible":
        raise HTTPException(
            status_code=409, detail=f"{lender.name}: {row['status_label']}: " + "; ".join(row["reasons"])
        )
    file_ready, issues = result["file_check"]["ready"], result["file_check"]["issues"]
    if file_ready is False and not request.confirm_not_ready:
        raise HTTPException(status_code=428, detail=_not_ready_detail(issues))

    limit_key = (project_id, applicant_key(applicant), lender.id)
    _begin_login(limit_key)
    recorded = False
    try:
        requested_at = _now()
        try:
            audit_key = _write_login_audit(project_id, lender.id, requested_at, file_ready, len(issues))
        except (ClientError, BotoCoreError) as e:
            print(f"eligibility login: not recorded user={user_id} project={project_id} ({type(e).__name__})")
            raise HTTPException(status_code=502, detail="The login request could not be recorded") from e
        recorded = True
        note = eligibility.INDICATIVE_LABEL + (f"; {book.label}" if book.label else "")
        if file_ready is False:
            note += f"; logged in while the file check is NOT READY ({len(issues)} open issues)"
        login_event = {
            "applicant": _display_name(applicant, inputs),
            "lender": lender.name,
            "eligible_amount": row["eligible_amount"],
            "emi": row["emi"],
            "tenure_months": row["tenure_months"],
            "roi": row["roi"],
            "note": note,
        }
        webhook, delivery, detail = _notify_crm(project_id, login_event)
        _update_login_audit(audit_key, webhook)
    finally:
        _end_login(limit_key, recorded)
    # Ids and outcome only: no applicant data, no amounts.
    print(
        f"eligibility login user={user_id} project={project_id} lender={lender.id} webhook={webhook} "
        f"delivery={(delivery or {}).get('delivery_id')} file_ready={file_ready}"
    )
    return LoginResponse(
        status="recorded",
        applicant=login_event["applicant"],
        lender=lender.name,
        lender_id=lender.id,
        eligible_amount=row["eligible_amount"],
        emi=row["emi"],
        tenure_months=row["tenure_months"],
        roi=row["roi"],
        requested_at=_iso(requested_at),
        webhook=webhook,
        webhook_detail=detail,
        delivery=LoginDelivery(**delivery) if delivery else None,
        file_ready=file_ready,
        open_issues=len(issues) if file_ready is False else 0,
        label=eligibility.INDICATIVE_LABEL,
        policy_label=book.label,
    )
