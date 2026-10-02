"""Tests for the eligibility engine (app/eligibility.py) and its SAMPLE data files (no AWS, no model).

The worked example is the client's "Cibil Page Function" sheet: income 98,000, FOIR 0.70,
multiplier 21, obligations 15,000; ICICI Bank at 11% with a calculation tenure of 60 months (From
Policy) and a max tenure of 72 -> per-lakh EMI 2,174.24, FOIR eligibility 24,65,226.61, multiplier
eligibility 20,58,000; ICICI Bank 20,58,000 over 72 months (EMI 39,172.13); HDFC Bank capped at
15,00,000 over 60 months at 12% (EMI 33,366.67). Applicants and values are synthetic.
"""

import ast
import copy
import dataclasses
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from app import eligibility as el

BOOK = el.load_policy_book()
THANE_WEST = "400606"  # Thane-Kasarvadavali: every SAMPLE lender but Axis Bank serves it
PUNE = "411045"


def example(**changes):
    """The sheet's worked example with a synthetic applicant (employer in CAT A at HDFC and ICICI)."""
    inputs = {
        "profile": {
            "name": "Rahul Vijay Deshmukh",
            "pincode": THANE_WEST,
            "company": "Konkan Softworks Pvt Ltd",
            "employment_type": "private_limited",
            "net_income": 98000,
            "other_income": [],
        },
        "cibil": {
            "score": 765,
            "enquiries": {"d30": 0, "d60": 1, "d90": 2, "d120": 3},
            "tradelines": [
                {
                    "loan_type": "personal",
                    "lender": "Tamrapur Finance (sample)",
                    "sanction_amount": 500000,
                    "outstanding": 250000,
                    "emi": 15000,
                    "status": "active",
                    "action": "obligate",
                }
            ],
        },
        "loan": {"amount": 1500000, "tenure_months": 60},
    }
    for path, value in changes.items():
        section, _, field = path.partition("__")
        if field:
            inputs[section][field] = value
        else:
            inputs[section] = value
    return inputs


def at(result, lender_id):
    return next(r for r in result["per_lender"] if r["lender_id"] == lender_id)


def tradeline(**fields):
    base = {"loan_type": "personal", "lender": "Sample Lender", "emi": 1000, "status": "active", "action": "obligate"}
    return {**base, **fields}


def rupees(value):
    """To the rupee, half up: how the sheet shows its figures (2,174.24 -> 2,174)."""
    return int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------ the worked example
class TestWorkedExample:
    def test_per_lakh_emi_is_excel_pmt(self):
        per_lakh = el.emi(100000, 11, 60)

        assert abs(per_lakh - Decimal("2174.242307264331")) < Decimal("1e-9")  # the sheet's PMT(E13/12,F19,-100000)
        assert el.money(per_lakh) == 2174.24

    def test_emis_are_excel_pmt(self):
        assert abs(el.emi(2058000, 11, 72) - Decimal("39172.134583452542")) < Decimal("1e-7")
        assert abs(el.emi(1500000, 12, 60) - Decimal("33366.671527352657")) < Decimal("1e-7")

    def test_icici_bank(self):
        icici = at(el.calculate(example()), "icici_bank")

        assert icici["status"] == "eligible"
        assert icici["status_label"] == "Eligible"
        assert icici["reasons"] == []
        assert icici["per_lakh_emi"] == 2174.24
        assert icici["foir_eligibility"] == 2465226.61
        assert icici["multiplier_eligibility"] == 2058000.0
        assert icici["eligible_amount"] == 2058000.0
        assert icici["computed_amount"] == 2058000.0
        assert (icici["tenure_months"], icici["roi"], icici["emi"]) == (72, 11.0, 39172.13)
        assert icici["calculation_tenure_months"] == 60
        assert icici["sources"]["calculation_tenure_months"] == "policy"  # the sheet's grey "Tenure Calculation"
        assert icici["emi_at_calculation_tenure"] == el.money(el.emi(2058000, 11, 60))
        assert (icici["foir"], icici["multiplier"], icici["company_category"]) == (0.7, 21.0, "CAT A")
        assert (icici["income_considered"], icici["obligations"], icici["bt_amount"]) == (98000.0, 15000.0, 0.0)
        assert icici["label"] == "sample policy — replace with your lender grid"

    def test_the_sheets_figures_to_the_rupee(self):
        """The sheet shows ICICI Bank's 2,174 / 24,65,227 / 20,58,000 / 20,58,000 / 39,172."""
        icici = at(el.calculate(example()), "icici_bank")

        figures = ("per_lakh_emi", "foir_eligibility", "multiplier_eligibility", "eligible_amount", "emi")
        assert [rupees(icici[f]) for f in figures] == [2174, 2465227, 2058000, 2058000, 39172]

    def test_the_policy_alone_gives_the_sheets_numbers(self):
        """No tenure requested: the per-lakh EMI uses ICICI Bank's calculation tenure (60), the EMI its max (72)."""
        icici = at(el.calculate(example(loan__tenure_months=None)), "icici_bank")

        assert (icici["calculation_tenure_months"], icici["tenure_months"]) == (60, 72)
        assert (icici["per_lakh_emi"], icici["foir_eligibility"], icici["eligible_amount"], icici["emi"]) == (
            2174.24,
            2465226.61,
            2058000.0,
            39172.13,
        )
        assert icici["notes"] == []

    def test_hdfc_bank_is_capped_at_its_max_amount(self):
        hdfc = at(el.calculate(example()), "hdfc_bank")

        assert hdfc["status"] == "eligible"
        assert hdfc["eligible_amount"] == 1500000.0
        assert hdfc["max_amount"] == 1500000.0
        assert (hdfc["tenure_months"], hdfc["roi"], hdfc["emi"]) == (60, 12.0, 33366.67)
        # The cap, not the formulas, is the limit here.
        assert hdfc["foir_eligibility"] > 1500000 and hdfc["multiplier_eligibility"] > 1500000

    def test_sources_follow_the_sheets_legend(self):
        icici = at(el.calculate(example()), "icici_bank")

        assert icici["sources"] == {
            "income": "table",
            "obligations": "table",
            "bt_amount": "table",
            "pincode": "table",
            "company_category": "table",
            "foir": "policy",
            "multiplier": "policy",
            "roi": "policy",
            "tenure_months": "policy",
            "max_amount": "policy",
            "calculation_tenure_months": "policy",
            "per_lakh_emi": "formula",
            "foir_eligibility": "formula",
            "multiplier_eligibility": "formula",
            "eligible_amount": "formula",
            "emi": "formula",
        }

    def test_top_level_summary(self):
        result = el.calculate(example())

        assert result["income_considered"] == 98000.0
        assert result["obligations"] == 15000.0
        assert result["income"]["net_salary_source"] == "entered"
        assert result["sample"] is True
        assert result["policy_label"] == "sample policy — replace with your lender grid"
        assert result["label"] == "indicative — the lender decides"
        assert any(d.startswith("Indicative — the lender decides") for d in result["disclaimers"])
        assert any(d.startswith("Sample policy — replace with your lender grid") for d in result["disclaimers"])
        assert [r["lender"] for r in result["per_lender"]] == [
            "HDFC Bank",
            "ICICI Bank",
            "Axis Bank",
            "Bajaj Finance",
            "Tata Capital",
        ]

    def test_formula_with_an_explicit_policy(self):
        """min(FOIR eligibility, multiplier eligibility, cap), exact like the sheet's MIN, on a policy of its own."""
        policies = el.PolicyFile.model_validate(
            {
                "sample": False,
                "version": "test",
                "lenders": [
                    {
                        "id": "test_bank",
                        "name": "Test Bank",
                        "roi": 11,
                        "foir": 0.5,
                        "multiplier": 30,
                        "max_tenure_months": 60,
                        "max_amount": 10000000,
                        "min_cibil_score": 700,
                        "max_enquiries_90d": 5,
                        "employment_types": ["private_limited"],
                        "company_categories": {},
                        "unlisted_company": {"accepted": True, "foir": 0.5, "multiplier": 30},
                        "income_consideration_pct": {
                            "rented_notary": 0,
                            "rented_registered": 0,
                            "bonus": 0,
                            "incentive": 0,
                            "pension": 0,
                        },
                    }
                ],
            }
        )
        pincodes = el.PincodeFile.model_validate(
            {
                "sample": False,
                "version": "test",
                "regions": [{"id": "all", "name": "All", "ranges": [[100000, 999999]]}],
                "lenders": {"test_bank": ["all"]},
            }
        )
        companies = el.CompanyFile.model_validate({"sample": False, "version": "test", "companies": []})
        book = el.PolicyBook(policies, pincodes, companies)

        (row,) = el.calculate(example(), book=book)["per_lender"]

        # FOIR: (98,000 x 0.5 - 15,000) / 2,174.2423 x 1 lakh = 15,63,763.15 < 30 x 98,000
        assert row["foir_eligibility"] == 1563763.15
        assert row["multiplier_eligibility"] == 2940000.0
        assert row["eligible_amount"] == 1563763.15  # the sheet's MIN, not rounded to a round figure
        # At the calculation tenure the EMI of a FOIR-limited amount is exactly the FOIR headroom.
        assert row["emi_at_calculation_tenure"] == 98000 * 0.5 - 15000
        assert row["company_policy"] == "unlisted"
        assert row["label"] is None  # not SAMPLE data: no sample label
        assert el.calculate(example(), book=book)["policy_label"] is None


# ------------------------------------------------------------------ rounding
class TestRounding:
    def test_eligible_amount_is_the_sheets_exact_minimum(self):
        """The sheet's C13 = MIN(D19, E19): no rounding to Rs 1,000, so the app matches the client's Excel."""
        icici = at(el.calculate(example(profile__net_income=98765)), "icici_bank")

        assert icici["multiplier_eligibility"] == 2074065.0
        assert icici["eligible_amount"] == 2074065.0

    def test_a_foir_limited_amount_keeps_its_paise(self):
        """Obligations of 30,000: (98,000 x 0.70 - 30,000) / 2,174.2423 x 1 lakh = 17,75,331.11 (Excel)."""
        icici = at(el.calculate(example(cibil__tradelines=[tradeline(emi=30000)])), "icici_bank")

        assert icici["foir_eligibility"] == 1775331.11
        assert icici["eligible_amount"] == 1775331.11
        assert icici["emi"] == 33791.79  # PMT(11%/12, 72, -17,75,331.11...)
        assert icici["emi_at_calculation_tenure"] == 38600.0  # exactly the FOIR headroom 68,600 - 30,000

    def test_full_precision_until_display(self):
        """A yearly bonus of 1,00,000 is 8,333.333... a month, never 8,333.33, inside the maths."""
        bonus = [{"type": "bonus", "frequency": "yearly", "amount": 100000}]
        icici = at(el.calculate(example(profile__other_income=bonus)), "icici_bank")

        # (98,000 + 1,00,000 / 12 x 50%) x 21 = 21,45,500 exactly
        assert icici["income_considered"] == 102166.67
        assert icici["multiplier_eligibility"] == 2145500.0

    def test_money_rounds_half_up_to_the_paisa(self):
        assert el.money(Decimal("0.005")) == 0.01
        assert el.money(Decimal("2.675")) == 2.68
        assert el.money(None) is None

    @pytest.mark.parametrize(
        ("value", "text"),
        [(2058000, "₹20,58,000"), (2465226.61, "₹24,65,226.61"), (999, "₹999"), (100000, "₹1,00,000"), (0, "₹0")],
    )
    def test_inr(self, value, text):
        assert el.inr(value) == text


# ------------------------------------------------------------------ income
class TestIncome:
    OTHER = [
        {"type": "rented", "agreement": "registered", "amount": 20000},
        {"type": "rented", "agreement": "notary", "amount": 10000},
        {"type": "bonus", "frequency": "yearly", "amount": 120000},
        {"type": "incentive", "frequency": "quarterly", "amount": 30000},
        {"type": "pension", "amount": 12000},
        {"type": "bonus", "frequency": "half_yearly", "amount": 60000},
        {"type": "incentive", "frequency": "monthly", "amount": 4000},
    ]

    def test_other_income_is_normalised_to_a_month(self):
        result = el.calculate(example(profile__other_income=self.OTHER))

        monthly = [(o["type"], o["frequency"], o["monthly_amount"]) for o in result["income"]["other_income"]]
        assert monthly == [
            ("rented", "monthly", 20000.0),
            ("rented", "monthly", 10000.0),
            ("bonus", "yearly", 10000.0),
            ("incentive", "quarterly", 10000.0),
            ("pension", "monthly", 12000.0),
            ("bonus", "half_yearly", 10000.0),
            ("incentive", "monthly", 4000.0),
        ]
        assert result["income"]["other_income_monthly"] == 76000.0
        # The top-level figure is the net salary, common to every lender.
        assert result["income_considered"] == 98000.0

    def test_each_lender_counts_its_consideration_percent(self):
        result = el.calculate(example(profile__other_income=self.OTHER))

        # ICICI: registered rent 60%, notary rent 25%, bonus 50%, incentive 60%, pension 80%
        icici = at(result, "icici_bank")
        assert icici["income_considered"] == 98000 + 12000 + 2500 + 5000 + 6000 + 9600 + 5000 + 2400
        # HDFC: registered rent 50%, notary rent 0%, bonus 50%, incentive 50%, pension 100%
        hdfc = at(result, "hdfc_bank")
        assert hdfc["income_considered"] == 98000 + 10000 + 0 + 5000 + 5000 + 12000 + 5000 + 2000
        considered = {(o["type"], o["agreement"]): o["consideration_pct"] for o in hdfc["other_income_considered"]}
        assert considered[("rented", "notary")] == 0.0
        assert considered[("rented", "registered")] == 50.0
        # FOIR and multiplier use the lender's own income figure.
        assert icici["multiplier_eligibility"] == icici["income_considered"] * 21

    def test_missing_frequency_agreement_or_amount(self):
        other = [
            {"type": "bonus", "amount": 120000},
            {"type": "rented", "amount": 20000},
            {"type": "pension", "amount": None},
        ]
        result = el.calculate(example(profile__other_income=other))

        rows = result["income"]["other_income"]
        assert [(r["type"], r["frequency"], r["agreement"], r["monthly_amount"]) for r in rows] == [
            ("bonus", "yearly", None, 10000.0),
            ("rented", "monthly", "notary", 20000.0),
        ]
        notes = " | ".join(result["notes"])
        assert "Bonus (other income 1) has no frequency: counted as yearly" in notes
        assert "Rented income (other income 2) has no agreement type: counted as notary" in notes
        assert "Pension (other income 3) has no amount: not counted" in notes

    def test_verified_income_is_used_instead_of_the_entered_one(self):
        verified = {"amount": 58000, "source": "salary slips, median net pay"}
        result = el.calculate(example(profile__net_income=65000), verified_income=verified)

        assert result["income_considered"] == 58000.0
        assert result["income"]["net_salary_source"] == "verified"
        assert result["income"]["entered_net_income"] == 65000.0
        assert result["income"]["verified_net_income"] == 58000.0
        assert at(result, "icici_bank")["multiplier_eligibility"] == 58000 * 21
        assert any("Entered net income ₹65,000 differs from the verified ₹58,000" in n for n in result["notes"])
        assert not any("not verified" in d for d in result["disclaimers"])

    def test_verified_income_used_when_nothing_was_entered(self):
        result = el.calculate(example(profile__net_income=None), verified_income={"amount": 82500, "source": "x"})

        assert result["income_considered"] == 82500.0
        assert not any("differs" in n for n in result["notes"])

    def test_no_income_at_all(self):
        result = el.calculate(example(profile__net_income=None))

        for row in result["per_lender"]:
            assert row["status"] in ("not_eligible", "not_serviceable")
            assert "Net monthly income not entered" in row["reasons"]
            assert row["foir_eligibility"] is None and row["computed_amount"] is None
            assert row["eligible_amount"] == 0.0 and row["emi"] == 0.0
            assert row["per_lakh_emi"] > 0  # depends only on the policy
        assert result["best_lender"] is None


# ------------------------------------------------------------------ obligations
class TestObligations:
    def test_bt_obligate_and_close(self):
        tradelines = [
            tradeline(lender="Tamrapur Finance (sample)", emi=15000, action="obligate"),
            tradeline(lender="Axis Bank", emi=20000, outstanding=300000, action="bt"),
            tradeline(lender="Sample Consumer Finance", loan_type="consumer", emi=5000, action="close"),
            tradeline(lender="Old Lender", emi=3000, status="closed", action="obligate"),
        ]
        result = el.calculate(example(cibil__tradelines=tradelines))

        assert result["obligations"] == 15000.0  # only the Obligate EMI
        assert result["bt_amount"] == 300000.0
        details = result["obligation_details"]
        assert [(r["index"], r["source"]) for r in details["counted"]] == [(1, "tradeline")]
        assert [(r["index"], r["outstanding"]) for r in details["bt"]] == [(2, 300000.0)]
        assert [(r["index"], r["note"]) for r in details["closed"]] == [(3, "to be closed before disbursal")]
        assert [(r["index"], r["note"]) for r in details["not_counted"]] == [
            (4, "closed on the bureau report: not an obligation")
        ]
        assert any(
            "Tradeline 3 (Sample Consumer Finance, Consumer Loan): to be closed before disbursal" in n
            for n in result["notes"]
        )
        # BT and Close do not change the example's figures.
        icici = at(result, "icici_bank")
        assert (icici["eligible_amount"], icici["bt_amount"], icici["covers_bt"]) == (2058000.0, 300000.0, True)
        # A lender cannot take over its own loan.
        assert any("is with Axis Bank itself" in n for n in at(result, "axis_bank")["notes"])
        assert not any("itself" in n for n in icici["notes"])

    def test_obligate_counts_the_emi(self):
        no_loans = el.calculate(example(cibil__tradelines=[]))
        with_loan = el.calculate(example())

        assert no_loans["obligations"] == 0.0
        assert with_loan["obligations"] == 15000.0
        assert at(no_loans, "icici_bank")["foir_eligibility"] > at(with_loan, "icici_bank")["foir_eligibility"]

    def test_the_eligible_amount_must_cover_the_bt(self):
        tradelines = [
            tradeline(emi=15000),
            tradeline(lender="Sample Bank", emi=30000, outstanding=2000000, action="bt"),
        ]
        result = el.calculate(example(cibil__tradelines=tradelines))

        hdfc = at(result, "hdfc_bank")
        assert hdfc["status"] == "not_eligible"
        assert hdfc["covers_bt"] is False
        assert "Eligible amount ₹15,00,000 does not cover the balance transfer of ₹20,00,000" in hdfc["reasons"]
        assert hdfc["computed_amount"] == 1500000.0 and hdfc["eligible_amount"] == 0.0
        icici = at(result, "icici_bank")
        assert (icici["status"], icici["covers_bt"], icici["eligible_amount"]) == ("eligible", True, 2058000.0)

    def test_bt_without_outstanding_and_obligate_without_emi(self):
        tradelines = [
            tradeline(emi=15000),
            tradeline(lender="Sample Bank", emi=9000, outstanding=None, action="bt"),
            tradeline(lender="Card Issuer", loan_type="credit_card", emi=None, outstanding=80000),
        ]
        result = el.calculate(example(cibil__tradelines=tradelines))

        for row in result["per_lender"]:
            assert row["status"] != "eligible"
            reasons = " | ".join(row["reasons"])
            assert "Outstanding not entered for Tradeline 2 (Sample Bank, Personal Loan) marked BT" in reasons
            assert "EMI not entered for Tradeline 3 (Card Issuer, Credit Card) marked Obligate" in reasons
            assert "often 5% of the outstanding" in reasons

    def test_overdue_and_adverse_status_are_noted(self):
        tradelines = [tradeline(emi=15000, overdue=4500), tradeline(emi=0, status="written_off")]
        result = el.calculate(example(cibil__tradelines=tradelines))

        notes = " | ".join(result["notes"])
        assert "has an overdue of ₹4,500" in notes
        assert "is 'Written off' on the bureau report" in notes
        assert at(result, "icici_bank")["status"] == "eligible"  # noted, not decided here


# ------------------------------------------------------------------ bank-statement EMIs
class TestBankStatementEmis:
    CAR = {"amount": 8200.0, "payee": "MULSHI AUTO FINANCE", "lender": None, "months_seen": 6, "months_total": 6}

    def test_a_matching_tradeline_is_not_counted_twice(self):
        tradelines = [tradeline(loan_type="car", lender="Mulshi Auto Finance", emi=8200)]
        result = el.calculate(example(cibil__tradelines=tradelines), bank_emis=[self.CAR])

        assert result["obligations"] == 8200.0
        (bank,) = result["obligation_details"]["bank_statement_emis"]
        assert (bank["matched_tradeline"], bank["counted"]) == (1, False)
        assert not any("matches no tradeline" in n for n in result["notes"])

    @pytest.mark.parametrize("action", ["bt", "close"])
    def test_a_bank_emi_of_a_bt_or_closed_loan_is_not_counted(self, action):
        tradelines = [tradeline(lender="Mulshi Auto Finance", emi=8200, outstanding=100000, action=action)]
        result = el.calculate(example(cibil__tradelines=tradelines), bank_emis=[self.CAR])

        assert result["obligations"] == 0.0
        assert result["obligation_details"]["bank_statement_emis"][0]["matched_tradeline"] == 1

    def test_an_unmatched_bank_emi_is_counted_and_flagged(self):
        result = el.calculate(example(), bank_emis=[self.CAR])

        assert result["obligations"] == 15000.0 + 8200.0
        counted = result["obligation_details"]["counted"]
        assert [(c["source"], c["lender"], c["emi"]) for c in counted] == [
            ("tradeline", "Tamrapur Finance (sample)", 15000.0),
            ("bank_statement", "MULSHI AUTO FINANCE", 8200.0),
        ]
        assert "matches no tradeline" in counted[1]["flag"]
        assert any("Loan EMI ₹8,200 to 'MULSHI AUTO FINANCE' in the bank statement" in n for n in result["notes"])

    def test_within_two_percent_matches_beyond_does_not(self):
        near = el.calculate(example(cibil__tradelines=[tradeline(emi=8300)]), bank_emis=[self.CAR])
        far = el.calculate(example(cibil__tradelines=[tradeline(emi=8400)]), bank_emis=[self.CAR])

        assert near["obligations"] == 8300.0
        assert far["obligations"] == 8400.0 + 8200.0

    def test_the_same_lender_wins_a_tie(self):
        tradelines = [tradeline(lender="Other Finance", emi=8200), tradeline(lender="Mulshi Auto Finance", emi=8200)]
        result = el.calculate(example(cibil__tradelines=tradelines), bank_emis=[self.CAR])

        assert result["obligation_details"]["bank_statement_emis"][0]["matched_tradeline"] == 2
        assert result["obligations"] == 16400.0

    def test_each_tradeline_matches_one_bank_emi_at_most(self):
        result = el.calculate(example(cibil__tradelines=[tradeline(emi=8200)]), bank_emis=[self.CAR, dict(self.CAR)])

        assert [b["matched_tradeline"] for b in result["obligation_details"]["bank_statement_emis"]] == [1, None]
        assert result["obligations"] == 16400.0


# ------------------------------------------------------------------ rejection reasons
class TestReasons:
    def test_pincode_not_serviceable(self):
        result = el.calculate(example())

        axis = at(result, "axis_bank")
        assert axis["status"] == "not_serviceable"
        assert axis["status_label"] == "Not serviceable"
        assert axis["reasons"][0] == "Pincode 400606 is not serviceable by Axis Bank"
        assert axis["serviceable"] is False
        assert (axis["eligible_amount"], axis["emi"]) == (0.0, 0.0)
        assert axis["computed_amount"] > 0  # the working is still shown
        tata = at(el.calculate(example(profile__pincode=PUNE)), "tata_capital")
        assert tata["status"] == "not_serviceable"

    @pytest.mark.parametrize(
        ("changes", "lender_id", "reason"),
        [
            ({"profile__pincode": None}, "icici_bank", "Pincode not entered: serviceability cannot be checked"),
            (
                {"profile__employment_type": "merchant_navy"},
                "hdfc_bank",
                "Employment type Merchant Navy is not accepted by HDFC Bank",
            ),
            ({"profile__employment_type": None}, "icici_bank", "Employment type not entered"),
            (
                {"profile__company": "Sample Unlisted Traders Pvt Ltd"},
                "hdfc_bank",
                "'Sample Unlisted Traders Pvt Ltd' is not in HDFC Bank's company list and HDFC Bank does not "
                "accept unlisted companies",
            ),
            ({"profile__company": None}, "icici_bank", "Company not entered: its category is needed"),
            ({"cibil__score": 740}, "hdfc_bank", "CIBIL score 740 is below HDFC Bank's minimum 750"),
            ({"cibil__score": None}, "icici_bank", "CIBIL score not entered"),
            (
                {"cibil__enquiries": {"d30": 1, "d60": 3, "d90": 5, "d120": 6}},
                "hdfc_bank",
                "5 enquiries in the last 90 days: more than HDFC Bank's limit of 4",
            ),
            ({"cibil__enquiries": {}}, "icici_bank", "Enquiries in the last 90 days not entered"),
            (
                {"cibil__tradelines": [tradeline(emi=70000)]},
                "icici_bank",
                "Existing obligations ₹70,000 leave no room within FOIR 70% of ₹98,000 (₹68,600)",
            ),
            (
                {"profile__net_income": 4000, "cibil__tradelines": []},
                "bajaj_finance",
                "Eligible amount ₹80,000 is below Bajaj Finance's minimum loan ₹1,00,000",
            ),
        ],
    )
    def test_each_reason_makes_the_lender_not_eligible(self, changes, lender_id, reason):
        row = at(el.calculate(example(**changes)), lender_id)

        assert row["status"] == "not_eligible"
        assert row["status_label"] == "Not eligible"
        assert reason in row["reasons"]
        assert (row["eligible_amount"], row["emi"]) == (0.0, 0.0)

    def test_the_limits_are_per_lender(self):
        result = el.calculate(example(cibil__score=740, cibil__enquiries={"d90": 5}))

        assert at(result, "hdfc_bank")["status"] == "not_eligible"  # min 750, max 4 enquiries
        assert at(result, "icici_bank")["status"] == "eligible"  # min 725, max 5 enquiries

    def test_an_unlisted_company_gets_the_lenders_unlisted_policy(self):
        result = el.calculate(example(profile__company="Sample Unlisted Traders Pvt Ltd"))

        icici = at(result, "icici_bank")
        assert icici["status"] == "eligible"
        assert (icici["company_category"], icici["company_policy"]) == (None, "unlisted")
        assert (icici["foir"], icici["multiplier"]) == (0.5, 10.0)
        assert icici["multiplier_eligibility"] == 980000.0
        assert any("unlisted-company policy applies (FOIR 50%, multiplier 10)" in n for n in icici["notes"])

    def test_the_company_category_sets_foir_and_multiplier(self):
        result = el.calculate(example(profile__company="Deccan Retail"))  # CAT B at ICICI and HDFC

        icici = at(result, "icici_bank")
        assert (icici["company_category"], icici["foir"], icici["multiplier"]) == ("CAT B", 0.65, 18.0)
        assert icici["multiplier_eligibility"] == 98000 * 18
        hdfc = at(result, "hdfc_bank")  # its FOIR from its salary-slab grid's CAT B column
        assert (hdfc["company_category"], hdfc["foir"], hdfc["multiplier"]) == ("CAT B", 0.55, 16.0)

    def test_all_reasons_are_listed(self):
        row = at(el.calculate(example(cibil__score=700, profile__employment_type="grade_4")), "hdfc_bank")

        assert row["reasons"] == [
            "Employment type Grade 4 is not accepted by HDFC Bank",
            "CIBIL score 700 is below HDFC Bank's minimum 750",
        ]


# ------------------------------------------------------------------ FOIR grid
def with_hdfc(category=None, grid=None):
    """BOOK with the example's employer (Konkan Softworks) in `category` at HDFC Bank, and/or HDFC
    Bank's FOIR grid replaced by `grid` (False: no grid)."""
    companies = BOOK.companies
    if category is not None:
        rows = tuple(
            c.model_copy(update={"categories": {**c.categories, "hdfc_bank": category}})
            if c.name == "Konkan Softworks Pvt Ltd"
            else c
            for c in BOOK.companies.companies
        )
        companies = companies.model_copy(update={"companies": rows})
    policies = BOOK.policies
    if grid is not None:
        hdfc = BOOK.lender("hdfc_bank").model_copy(update={"foir_grid": grid or None})
        policies = policies.model_copy(
            update={"lenders": tuple(hdfc if lender.id == "hdfc_bank" else lender for lender in BOOK.lenders)}
        )
    return el.PolicyBook(policies, BOOK.pincodes, companies)


def hdfc_at(salary, book=None, **changes):
    """HDFC Bank's row for the example (no obligations) at a net monthly salary."""
    inputs = example(profile__net_income=salary, cibil__tradelines=[], **changes)
    return at(el.calculate(inputs, book=book), "hdfc_bank")


class TestFoirGrid:
    """HDFC Bank's SAMPLE grid: =INDEX(grid, MATCH(salary, slab_starts, 1), MATCH(category, categories, 0))."""

    def test_the_sample_grid(self):
        grid = BOOK.lender("hdfc_bank").foir_grid

        assert grid.label == "SAMPLE: confirm with Varunika Loan Partners' HDFC grid"
        assert grid.slab_starts == (35000, 50000, 75000, 100000)
        assert grid.categories == {
            "Super CAT A": (0.6, 0.65, 0.7, 0.75),  # the sample company list's extra category, as CAT A
            "CAT A": (0.6, 0.65, 0.7, 0.75),
            "CAT B": (0.55, 0.55, 0.55, 0.55),
            "CAT C": (0.5, 0.5, 0.5, 0.5),
            "CAT D": (0.45, 0.45, 0.45, 0.45),
        }
        assert [grid.slab_text(i) for i in range(4)] == [
            "35,000–49,999",
            "50,000–74,999",
            "75,000–99,999",
            "1,00,000 and above",
        ]
        assert [lender.id for lender in BOOK.lenders if lender.foir_grid] == ["hdfc_bank"]

    @pytest.mark.parametrize(
        ("salary", "foir", "slab"),
        [
            (35000, 0.6, "35,000–49,999"),
            (49999, 0.6, "35,000–49,999"),
            (50000, 0.65, "50,000–74,999"),
            (74999, 0.65, "50,000–74,999"),
            (75000, 0.7, "75,000–99,999"),
            (99999, 0.7, "75,000–99,999"),
            (99999.99, 0.7, "75,000–99,999"),
            (100000, 0.75, "1,00,000 and above"),
            (350000, 0.75, "1,00,000 and above"),
        ],
    )
    def test_each_slab_boundary(self, salary, foir, slab):
        hdfc = hdfc_at(salary)

        assert (hdfc["status"], hdfc["foir"], hdfc["company_category"]) == ("eligible", foir, "CAT A")
        assert hdfc["sources"]["foir"] == "policy"
        assert hdfc["notes"][0] == (
            f"FOIR {round(foir * 100)}% · slab {slab} · CAT A · From Policy "
            "(SAMPLE: confirm with Varunika Loan Partners' HDFC grid)"
        )
        assert hdfc["foir_eligibility"] == el.money(el.dec(salary) * el.dec(foir) / el.emi(100000, 12, 60) * 100000)
        assert hdfc["multiplier"] == 20.0  # the multiplier is still CAT A's

    def test_below_the_first_slab_is_below_the_minimum_income(self):
        hdfc = hdfc_at(34999)

        assert hdfc["status"] == "not_eligible"
        assert hdfc["reasons"] == [
            "Net monthly salary ₹34,999 is below HDFC Bank's minimum income of ₹35,000 "
            "(the first slab of its FOIR grid)"
        ]
        assert (hdfc["eligible_amount"], hdfc["emi"]) == (0.0, 0.0)
        # The formulas keep CAT A's own FOIR, the grid's lowest.
        assert hdfc["foir"] == 0.6 and hdfc["computed_amount"] > 0
        assert not any(n.startswith("FOIR ") for n in hdfc["notes"])

    @pytest.mark.parametrize(
        ("category", "foir", "multiplier"),
        [
            ("Super CAT A", 0.65, 22.0),
            ("CAT A", 0.65, 20.0),
            ("CAT B", 0.55, 16.0),
            ("CAT C", 0.5, 12.0),
            ("CAT D", 0.45, 10.0),
        ],
    )
    def test_each_category(self, category, foir, multiplier):
        hdfc = hdfc_at(60000, book=with_hdfc(category))

        assert (hdfc["company_category"], hdfc["foir"], hdfc["multiplier"]) == (category, foir, multiplier)
        assert hdfc["notes"][0].startswith(f"FOIR {round(foir * 100)}% · slab 50,000–74,999 · {category} · From Policy")

    @pytest.mark.parametrize(("category", "foir"), [("CAT B", 0.55), ("CAT C", 0.5), ("CAT D", 0.45)])
    @pytest.mark.parametrize("salary", [35000, 60000, 80000, 150000])
    def test_cat_b_c_and_d_are_one_foir_in_every_slab(self, category, foir, salary):
        assert hdfc_at(salary, book=with_hdfc(category))["foir"] == foir

    def test_a_category_the_grid_has_no_foir_for(self):
        grid = BOOK.lender("hdfc_bank").foir_grid
        no_cat_b = grid.model_copy(update={"categories": {k: v for k, v in grid.categories.items() if k != "CAT B"}})

        hdfc = hdfc_at(60000, book=with_hdfc("CAT B", grid=no_cat_b))

        assert hdfc["status"] == "not_eligible"
        assert hdfc["reasons"] == [
            "HDFC Bank's FOIR grid has no FOIR for CAT B (it covers Super CAT A, CAT A, CAT C, CAT D)"
        ]
        assert (hdfc["foir"], hdfc["company_category"]) == (0.55, "CAT B")  # CAT B's own FOIR for the formulas
        assert hdfc["eligible_amount"] == 0.0

    def test_the_salary_slab_is_the_net_salary(self):
        """Verified salary wins over the entered one; other income does not move the slab."""
        verified = el.calculate(
            example(profile__net_income=80000, cibil__tradelines=[]),
            verified_income={"amount": 58000, "source": "salary slips, median net pay"},
        )
        bonus = hdfc_at(49000, profile__other_income=[{"type": "bonus", "amount": 240000, "frequency": "yearly"}])

        assert at(verified, "hdfc_bank")["foir"] == 0.65  # 58,000: slab 50,000–74,999, not 75,000–99,999
        assert (bonus["foir"], bonus["income_considered"]) == (0.6, 59000.0)  # slab 35,000–49,999

    def test_no_salary_no_grid(self):
        hdfc = hdfc_at(None)

        assert "Net monthly income not entered" in hdfc["reasons"]
        assert hdfc["foir"] == 0.6 and not any(n.startswith("FOIR ") for n in hdfc["notes"])

    def test_an_unlisted_company_gets_the_unlisted_policy_not_the_grid(self):
        hdfc = BOOK.lender("hdfc_bank")
        accepting = hdfc.model_copy(
            update={"unlisted_company": el.UnlistedCompanyPolicy(accepted=True, foir=0.5, multiplier=10)}
        )
        policies = BOOK.policies.model_copy(
            update={"lenders": tuple(accepting if lender.id == "hdfc_bank" else lender for lender in BOOK.lenders)}
        )
        book = el.PolicyBook(policies, BOOK.pincodes, BOOK.companies)

        row = hdfc_at(80000, book=book, profile__company="Sample Unlisted Traders Pvt Ltd")

        assert (row["status"], row["company_policy"], row["foir"]) == ("eligible", "unlisted", 0.5)
        assert not any(n.startswith("FOIR ") for n in row["notes"])

    @pytest.mark.parametrize("salary", [30000, 34999, 35000, 50000, 99999, 100000, 250000])
    def test_lenders_without_a_grid_are_unchanged(self, salary):
        """Without its grid HDFC Bank prices CAT A at the category's FOIR again; every other lender's
        row is the same with or without HDFC Bank's grid."""
        inputs = example(profile__net_income=salary, cibil__tradelines=[])
        with_grid = el.calculate(inputs)
        without = el.calculate(inputs, book=with_hdfc(grid=False))

        assert [r for r in with_grid["per_lender"] if r["lender_id"] != "hdfc_bank"] == [
            r for r in without["per_lender"] if r["lender_id"] != "hdfc_bank"
        ]
        plain = at(without, "hdfc_bank")
        assert plain["foir"] == BOOK.lender("hdfc_bank").company_categories["CAT A"].foir
        assert not any(n.startswith("FOIR ") for n in plain["notes"])
        assert not any("minimum income" in r for r in plain["reasons"])
        for row in with_grid["per_lender"]:
            if row["lender_id"] != "hdfc_bank":
                lender = BOOK.lender(row["lender_id"])
                assert row["foir"] == lender.company_categories[row["company_category"]].foir
                assert not any(n.startswith("FOIR ") for n in row["notes"])

    @pytest.mark.parametrize(
        ("change", "problem"),
        [
            ({"slab_starts": [35000, 35000, 75000, 100000]}, "slab_starts must increase"),
            ({"slab_starts": [50000, 35000, 75000, 100000]}, "slab_starts must increase"),
            ({"slab_starts": [0, 50000, 75000, 100000]}, r"slab_starts\[0\]: must be at least 1"),
            ({"categories": {}}, "a FOIR grid needs at least one category"),
            ({"categories": {"CAT A": [0.6, 0.65, 0.7]}}, r"CAT A needs one FOIR per slab \(4\)"),
            ({"categories": {"CAT A": [0.6, 0.65, 0.7, 1.2]}}, r"categories.CAT A\[3\]: must be at most 1"),
            ({"categories": {"CAT Z": [0.6, 0.65, 0.7, 0.75]}}, r"foir_grid categories \['CAT Z'\] are not company"),
            ({"rows": []}, r"unknown field\(s\) rows"),
        ],
    )
    def test_the_grid_is_checked(self, change, problem):
        policy = BOOK.lender("hdfc_bank").model_dump(mode="json")
        policy["foir_grid"] = {**policy["foir_grid"], **change}

        with pytest.raises(ValueError, match=problem):
            el.LenderPolicy.model_validate(policy)


# ------------------------------------------------------------------ tenure and requested amount
class TestTenure:
    def test_each_lender_calculates_at_its_calculation_tenure(self):
        """The SAMPLE policies: ICICI Bank 60 of 72 months (the sheet), Bajaj Finance 72 of 84."""
        result = el.calculate(example(loan__tenure_months=None))

        tenures = {r["lender_id"]: (r["calculation_tenure_months"], r["tenure_months"]) for r in result["per_lender"]}
        assert tenures == {
            "hdfc_bank": (60, 60),
            "icici_bank": (60, 72),
            "axis_bank": (60, 60),
            "bajaj_finance": (72, 84),
            "tata_capital": (60, 72),
        }
        for row in result["per_lender"]:
            assert row["sources"]["calculation_tenure_months"] == "policy"
            assert row["per_lakh_emi"] == el.money(el.emi(100000, row["roi"], row["calculation_tenure_months"]))

    def test_requested_tenure_is_capped_at_the_lenders_max(self):
        result = el.calculate(example(loan__tenure_months=84))

        hdfc = at(result, "hdfc_bank")
        assert hdfc["calculation_tenure_months"] == 60
        assert hdfc["sources"]["calculation_tenure_months"] == "policy"
        assert any("Requested tenure 84 months is more than HDFC Bank's max 60" in n for n in hdfc["notes"])
        icici = at(result, "icici_bank")
        assert (icici["calculation_tenure_months"], icici["tenure_months"], icici["emi"]) == (60, 72, 39172.13)
        assert icici["notes"] == [
            "Requested tenure 84 months is more than ICICI Bank's max 72: the EMI is shown at 72 months and "
            "eligibility is calculated at 60 months"
        ]
        # A long request never raises the calculation tenure.
        assert at(result, "bajaj_finance")["calculation_tenure_months"] == 72

    def test_a_requested_tenure_beyond_the_calculation_tenure_is_noted(self):
        icici = at(el.calculate(example(loan__tenure_months=66)), "icici_bank")

        assert (icici["calculation_tenure_months"], icici["tenure_months"], icici["emi"]) == (60, 72, 39172.13)
        assert icici["sources"]["calculation_tenure_months"] == "policy"
        assert icici["notes"] == [
            "ICICI Bank calculates eligibility at 60 months (its calculation tenure), not at the requested 66"
        ]

    def test_short_tenure_is_raised_to_the_min(self):
        short = at(el.calculate(example(loan__tenure_months=6)), "icici_bank")

        assert short["calculation_tenure_months"] == 12
        assert short["sources"]["calculation_tenure_months"] == "policy"
        assert any("Requested tenure 6 months is less than ICICI Bank's min 12" in n for n in short["notes"])

    def test_a_shorter_requested_tenure_is_used(self):
        """The EMI must fit the FOIR at the tenure the customer takes: 36 months, not ICICI Bank's 60."""
        icici = at(el.calculate(example(loan__tenure_months=36)), "icici_bank")

        assert icici["calculation_tenure_months"] == 36
        assert icici["sources"]["calculation_tenure_months"] == "table"
        assert icici["per_lakh_emi"] == el.money(el.emi(100000, 11, 36))
        assert icici["tenure_months"] == 72  # the EMI is still shown at the max tenure
        assert icici["notes"] == []

    def test_a_policy_without_a_calculation_tenure_calculates_at_its_max(self):
        policy = BOOK.lender("icici_bank").model_dump()
        del policy["calculation_tenure_months"]
        icici = el.LenderPolicy.model_validate(policy)
        lenders = tuple(icici if lender.id == "icici_bank" else lender for lender in BOOK.lenders)
        book = el.PolicyBook(BOOK.policies.model_copy(update={"lenders": lenders}), BOOK.pincodes, BOOK.companies)

        row = at(el.calculate(example(loan__tenure_months=None), book=book), "icici_bank")

        assert (icici.calculation_tenure_months, icici.calculation_tenure) == (None, 72)
        assert (row["calculation_tenure_months"], row["per_lakh_emi"]) == (72, el.money(el.emi(100000, 11, 72)))

    @pytest.mark.parametrize("months", [6, 84])
    def test_a_calculation_tenure_outside_the_lenders_limits_is_refused(self, months):
        policy = {**BOOK.lender("icici_bank").model_dump(), "calculation_tenure_months": months}

        with pytest.raises(ValueError, match="calculation_tenure_months is outside min_tenure_months"):
            el.LenderPolicy.model_validate(policy)

    def test_less_than_requested_is_noted(self):
        result = el.calculate(example(loan__amount=1800000))

        hdfc = at(result, "hdfc_bank")
        assert (hdfc["status"], hdfc["covers_requested"]) == ("eligible", False)
        assert "₹15,00,000 is less than the requested ₹18,00,000" in hdfc["notes"]
        assert at(result, "icici_bank")["covers_requested"] is True


class TestBestLender:
    def test_lowest_roi_among_the_lenders_that_cover_the_request(self):
        result = el.calculate(example())

        assert (result["best_lender"], result["best_lender_id"]) == ("ICICI Bank", "icici_bank")
        assert result["best_lender_reason"] == "lowest ROI among the lenders that cover ₹15,00,000"

    def test_highest_amount_when_none_covers_it(self):
        result = el.calculate(example(loan__amount=5000000))

        assert result["best_lender"] == "ICICI Bank"
        assert result["best_lender_reason"] == "highest eligible amount"

    def test_the_bt_amount_counts_as_need(self):
        tradelines = [tradeline(emi=15000), tradeline(lender="Sample Bank", emi=9000, outstanding=1900000, action="bt")]
        result = el.calculate(example(loan__amount=None, cibil__tradelines=tradelines))

        # HDFC (15 L) cannot cover 19 L; ICICI (20.58 L, 11%) covers it at the lowest ROI.
        assert result["best_lender"] == "ICICI Bank"
        assert result["best_lender_reason"] == "lowest ROI among the lenders that cover ₹19,00,000"

    def test_none_when_no_lender_is_eligible(self):
        result = el.calculate(example(cibil__score=None))

        assert (result["best_lender"], result["best_lender_id"], result["best_lender_reason"]) == (None, None, None)


# ------------------------------------------------------------------ SAMPLE data files
class TestDataFiles:
    def test_five_sample_lenders(self):
        assert [lender.name for lender in BOOK.lenders] == [
            "HDFC Bank",
            "ICICI Bank",
            "Axis Bank",
            "Bajaj Finance",
            "Tata Capital",
        ]
        assert BOOK.sample is True
        assert BOOK.label == "sample policy — replace with your lender grid"
        for data in (BOOK.policies, BOOK.pincodes, BOOK.companies):
            assert data.sample is True
            assert data.label == "sample policy — replace with your lender grid"
            assert "SAMPLE" in data.note and "replace with your lender grid" in data.note.lower()

    def test_hdfc_and_icici_reproduce_the_sheet(self):
        hdfc, icici = BOOK.lender("hdfc_bank"), BOOK.lender("ICICI Bank")

        assert (icici.roi, icici.max_tenure_months, icici.foir, icici.multiplier) == (11.0, 72, 0.7, 21.0)
        assert icici.calculation_tenure_months == 60  # the sheet's "Tenure Calculation"
        assert icici.company_categories["CAT A"].model_dump() == {"foir": 0.7, "multiplier": 21.0}
        assert (hdfc.roi, hdfc.max_tenure_months, hdfc.max_amount) == (12.0, 60, 1500000.0)

    def test_every_lender_has_the_policy_fields(self):
        for lender in BOOK.lenders:
            assert lender.employment_types and lender.company_categories
            assert lender.min_cibil_score >= 300 and lender.max_enquiries_90d >= 0
            assert set(lender.income_consideration_pct.model_dump()) == {
                "rented_notary",
                "rented_registered",
                "bonus",
                "incentive",
                "pension",
            }
            assert BOOK.regions_of(lender.id)

    @pytest.mark.parametrize(
        ("pincode", "region"),
        [
            ("400001", "Mumbai (city and suburbs)"),
            ("400601", "Thane"),
            ("400606", "Thane (Ghodbunder Road)"),
            ("401404", "Palghar, Boisar, Dahanu"),
            ("411045", "Pune and Pimpri-Chinchwad"),
            ("560001", None),
        ],
    )
    def test_pincode_regions(self, pincode, region):
        found = BOOK.region_of(pincode)
        assert (found.name if found else None) == region

    def test_mumbai_thane_palghar_are_served(self):
        for pincode in ("400001", "400601", "400606", "401404"):
            assert BOOK.serviceable("hdfc_bank", pincode)
        assert not BOOK.serviceable("axis_bank", "400606")
        assert not BOOK.serviceable("tata_capital", "411045")
        assert not BOOK.serviceable("icici_bank", "560001")

    def test_about_forty_sample_companies_including_the_demo_employers(self):
        companies = BOOK.companies.companies
        assert 35 <= len(companies) <= 50
        synthetic = {c.name for c in companies if c.synthetic}
        assert synthetic == {"Konkan Softworks Pvt Ltd", "Deccan Retail Pvt Ltd", "Varad Logistics LLP"}

    @pytest.mark.parametrize(
        ("typed", "listed"),
        [
            ("konkan softworks private limited", "Konkan Softworks Pvt Ltd"),
            ("Varad Logistics", "Varad Logistics LLP"),
            ("TCS", "Tata Consultancy Services Ltd"),
            ("L & T", "Larsen & Toubro Ltd"),
            ("Sample Unlisted Traders", None),
            ("", None),
        ],
    )
    def test_company_lookup_ignores_case_and_legal_suffixes(self, typed, listed):
        found = BOOK.find_company(typed)
        assert (found.name if found else None) == listed

    def test_company_suggestions(self):
        names = [c.name for c in BOOK.company_suggestions("tata")]
        assert names[:3] == ["Tata Consultancy Services Ltd", "Tata Motors Ltd", "Tata Steel Ltd"]
        assert BOOK.company_suggestions("") == []

    def test_records_are_checked_field_by_field(self):
        policies = BOOK.policies.model_dump(mode="json")
        json.dumps(policies)  # lists, not tuples
        policies["lenders"][1]["roi"] = 75

        with pytest.raises(ValueError, match=r"lenders\[1\]: roi: must be at most 60"):
            el.PolicyFile.model_validate(policies)
        policies["lenders"][1] = {**policies["lenders"][1], "roi": 11, "teaser_rate": 9.5}
        with pytest.raises(ValueError, match=r"unknown field\(s\) teaser_rate"):
            el.PolicyFile.model_validate(policies)
        del policies["lenders"][1]["teaser_rate"]
        del policies["lenders"][1]["max_amount"]
        with pytest.raises(ValueError, match=r"missing field\(s\) max_amount"):
            el.PolicyFile.model_validate(policies)
        with pytest.raises(ValueError, match="must be a whole number"):
            el.Region.model_validate({"id": "x", "name": "X", "ranges": [[400001.5, 400100]]})
        with pytest.raises(ValueError, match="an accepted unlisted company needs foir and multiplier"):
            el.UnlistedCompanyPolicy(accepted=True)

    def test_records_are_frozen_and_dump_like_pydantic(self):
        icici = BOOK.lender("icici_bank")

        with pytest.raises(dataclasses.FrozenInstanceError):
            icici.roi = 9.0
        dumped = icici.model_dump(exclude={"company_categories", "unlisted_company"})
        assert dumped["employment_types"][0] == "government" and isinstance(dumped["employment_types"], tuple)
        assert dumped["income_consideration_pct"] == {
            "rented_notary": 25.0,
            "rented_registered": 60.0,
            "bonus": 50.0,
            "incentive": 60.0,
            "pension": 80.0,
        }
        assert "company_categories" not in dumped
        assert el.LenderPolicy.model_validate(icici.model_dump()) == icici
        assert icici.model_copy(update={"roi": 10.5}).roi == 10.5 and icici.roi == 11.0

    def test_broken_references_are_refused(self):
        companies = copy.deepcopy(BOOK.companies.model_dump())
        companies["companies"][0]["categories"]["hdfc_bank"] = "CAT Z"

        with pytest.raises(ValueError, match="has no category 'CAT Z'"):
            el.PolicyBook(BOOK.policies, BOOK.pincodes, el.CompanyFile.model_validate(companies))

        pincodes = copy.deepcopy(BOOK.pincodes.model_dump())
        pincodes["lenders"]["hdfc_bank"] = ["atlantis"]
        with pytest.raises(ValueError, match="unknown regions"):
            el.PolicyBook(BOOK.policies, el.PincodeFile.model_validate(pincodes), BOOK.companies)


class TestVocabulary:
    @pytest.mark.parametrize(
        ("value", "choices", "suffixes", "expected"),
        [
            ("Private Limited", el.EMPLOYMENT_TYPES, (), "private_limited"),
            ("Partnership/Proprietorship", el.EMPLOYMENT_TYPES, (), "partnership_proprietorship"),
            ("Grade 4", el.EMPLOYMENT_TYPES, (), "grade_4"),
            ("merchant_navy", el.EMPLOYMENT_TYPES, (), "merchant_navy"),
            ("Half Yearly", el.INCOME_FREQUENCIES, (), "half_yearly"),
            ("Rented Income", el.OTHER_INCOME_TYPES, ("_income",), "rented"),
            ("Personal Loan", el.LOAN_TYPES, ("_loan",), "personal"),
            ("Credit Card", el.LOAN_TYPES, ("_loan",), "credit_card"),
            ("BT", el.TRADELINE_ACTIONS, (), "bt"),
            ("Obligate", el.TRADELINE_ACTIONS, (), "obligate"),
            ("close", el.TRADELINE_ACTIONS, (), "close"),
            ("Written-off", el.TRADELINE_STATUSES, (), "written_off"),
            ("Owned", el.HOUSE_OWNERSHIP, (), "owned"),
            ("Sole trader", el.EMPLOYMENT_TYPES, (), None),
            (7, el.EMPLOYMENT_TYPES, (), None),
        ],
    )
    def test_ids_and_labels(self, value, choices, suffixes, expected):
        assert el.enum_id(value, choices, suffixes=suffixes) == expected

    def test_aliases(self):
        assert el.enum_id("Pvt Ltd", el.EMPLOYMENT_TYPES, el.EMPLOYMENT_TYPE_ALIASES) == "private_limited"
        assert el.enum_id("Rent", el.OTHER_INCOME_TYPES, el.OTHER_INCOME_ALIASES) == "rented"
        assert el.enum_id("Balance Transfer", el.TRADELINE_ACTIONS, el.TRADELINE_ACTION_ALIASES) == "bt"

    def test_options_list_every_choice(self):
        opts = el.options()
        assert [o["id"] for o in opts["tradeline_actions"]] == ["bt", "obligate", "close"]
        assert [o["label"] for o in opts["tradeline_actions"]] == ["BT", "Obligate", "Close"]
        assert len(opts["employment_types"]) == 8 and len(opts["loan_types"]) == 8
        assert {o["label"] for o in opts["sources"]} == {"From Policy", "Formula Calculation", "From Table"}


# ------------------------------------------------------------------ the seeded demo
class TestSeededDemo:
    """The demo's saved inputs (synthetic personas, as the seed script saves them): the calculation
    tenures do not change their results, as every requested tenure is shorter than the lenders'."""

    RAHUL = {
        "profile": {
            "name": "Rahul Vijay Deshmukh",
            "pincode": "411014",
            "company": "Konkan Softworks Pvt Ltd",
            "employment_type": "private_limited",
            "net_income": 82500,
            "other_income": [{"type": "bonus", "amount": 60000, "frequency": "yearly"}],
        },
        "cibil": {
            "score": 771,
            "enquiries": {"d30": 0, "d60": 1, "d90": 1, "d120": 2},
            "tradelines": [tradeline(loan_type="car", lender="Mulshi Auto Finance Ltd", outstanding=176000, emi=8200)],
        },
        "loan": {"amount": 600000, "tenure_months": 48},
    }
    SNEHA = {
        "profile": {
            "name": "Sneha Anil Kulkarni",
            "pincode": "400607",
            "company": "Deccan Retail Pvt Ltd",
            "employment_type": "private_limited",
            "net_income": 65000,
        },
        "cibil": {
            "score": 712,
            "enquiries": {"d30": 2, "d60": 4, "d90": 6, "d120": 7},
            "tradelines": [
                tradeline(
                    loan_type="consumer", lender="Deccan Consumer Finance", emi=0, status="closed", action="close"
                )
            ],
        },
        "loan": {"amount": 400000, "tenure_months": 36},
    }
    AMIT = {
        "profile": {
            "name": "Amit Suresh Patil",
            "pincode": "400606",
            "company": "Varad Logistics LLP",
            "employment_type": "llp",
            "net_income": 71200,
            "other_income": [{"type": "rented", "amount": 12000, "frequency": "monthly", "agreement": "notary"}],
        },
        "cibil": {
            "score": 748,
            "enquiries": {"d30": 0, "d60": 0, "d90": 1, "d120": 1},
            "tradelines": [
                tradeline(loan_type="car", lender="Indrayani Motor Finance Ltd", outstanding=48300, emi=3450)
            ],
        },
        "loan": {"amount": 500000, "tenure_months": 36},
    }

    @pytest.mark.parametrize(
        ("inputs", "verified", "best", "amount", "emi"),
        [
            (RAHUL, None, "icici_bank", 1785000.0, 33975.83),
            (SNEHA, {"amount": 58000, "source": "salary slips, median net pay"}, "bajaj_finance", 928000.0, 17390.73),
            (AMIT, None, "icici_bank", 1038800.0, 19772.6),
        ],
    )
    def test_best_lender_and_amount(self, inputs, verified, best, amount, emi):
        result = el.calculate(inputs, verified_income=verified)

        row = at(result, best)
        assert result["best_lender_id"] == best
        assert (row["eligible_amount"], row["emi"]) == (amount, emi)
        # Multiplier-limited, at the requested (shorter) tenure, as before.
        assert row["eligible_amount"] == row["multiplier_eligibility"]
        assert row["calculation_tenure_months"] == inputs["loan"]["tenure_months"]
        assert row["sources"]["calculation_tenure_months"] == "table"

    @pytest.mark.parametrize(
        ("inputs", "verified", "status", "foir", "computed"),
        [
            # 82,500 is in the 75,000–99,999 slab: CAT A 70%, the old CAT A FOIR.
            (RAHUL, None, "eligible", 0.7, 1500000.0),
            # 58,000 (verified) in 50,000–74,999: CAT B 55% (was 60%); still multiplier-limited, and HDFC
            # Bank is not eligible for her anyway (CIBIL 712, 6 enquiries in 90 days).
            (SNEHA, {"amount": 58000, "source": "salary slips, median net pay"}, "not_eligible", 0.55, 928000.0),
            # Varad Logistics is not on HDFC Bank's list, which takes no unlisted company: no grid.
            (AMIT, None, "not_eligible", 0.7, 1396687.16),
        ],
    )
    def test_hdfc_banks_grid(self, inputs, verified, status, foir, computed):
        hdfc = at(el.calculate(inputs, verified_income=verified), "hdfc_bank")

        assert (hdfc["status"], hdfc["foir"], hdfc["computed_amount"]) == (status, foir, computed)


def test_the_engine_needs_only_the_standard_library():
    """The file-check Lambda runs a copy of this module, and has no pydantic or other package."""
    tree = ast.parse(Path(el.__file__).read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}

    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
