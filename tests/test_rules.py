from pathlib import Path

import pandas as pd
import pytest

from radar import rules

CFG = rules.load_config(Path(__file__).parents[1] / "config" / "rules.toml")
AS_OF = pd.Timestamp("2026-09-29")
T = pd.Timestamp


def prop(**overrides):
    base = {
        "property_id": "P1", "landlord_id": "L1",
        "current_energy_rating": "D", "current_energy_efficiency": 60, "potential_energy_rating": "B",
        "lodgement_date": T("2020-01-01"), "tenancy_start": T("2024-01-01"),
        "has_gas": True, "gas_last_check": T("2026-03-01"), "gas_copy_to_tenant": T("2026-03-05"),
        "eicr_last_inspection": T("2023-01-01"), "eicr_copy_to_tenant": T("2023-01-10"),
        "deposit_received": T("2024-01-01"), "deposit_protected": T("2024-01-10"),
        "prescribed_info_served": T("2024-01-11"), "info_sheet_served": T("2026-05-10"),
        "mees_exemption_registered": False, "estimated_value": 150000,
    }
    return {**base, **overrides}


def run(key, **overrides):
    return rules.RULES[key](prop(**overrides), CFG[key], AS_OF)


@pytest.mark.parametrize("key", list(rules.RULES))
def test_compliant_baseline(key):
    assert run(key).status in {"ok", "at_risk"}


def test_epc_expired_mid_tenancy_is_action_not_breach():
    assert run("epc_validity", lodgement_date=T("2015-06-01"), tenancy_start=T("2020-01-01")).status == "action_needed"


def test_epc_let_after_expiry_is_breach():
    assert run("epc_validity", lodgement_date=T("2015-06-01"), tenancy_start=T("2026-01-01")).status == "breach"


def test_epc_expiring_within_window():
    assert run("epc_validity", lodgement_date=T("2016-12-01")).status == "due_soon"


def test_mees_f_without_exemption_is_breach():
    assert run("mees_current", current_energy_rating="F").status == "breach"


def test_mees_f_with_exemption_is_ok():
    assert run("mees_current", current_energy_rating="F", mees_exemption_registered=True).status == "ok"


def test_mees_2030_cap_uses_10pct_of_value():
    f = run("mees_2030", estimated_value=80000)
    assert f.status == "at_risk" and "£8,000" in f.detail


def test_mees_2030_flags_low_potential():
    assert "possible exemption" in run("mees_2030", potential_energy_rating="D").detail


def test_gas_overdue_boundary():
    assert run("gas_safety", gas_last_check=T("2025-09-29")).status == "due_soon"  # due today
    assert run("gas_safety", gas_last_check=T("2025-09-28")).status == "breach"


def test_gas_copy_not_given():
    assert run("gas_safety", gas_copy_to_tenant=None).status == "breach"


def test_gas_not_applicable_without_supply():
    assert run("gas_safety", has_gas=False).status == "n/a"


def test_eicr_overdue():
    assert run("eicr", eicr_last_inspection=T("2021-09-01")).status == "breach"


def test_unprotected_deposit_blocks_possession():
    f = run("deposit", deposit_protected=None, prescribed_info_served=None)
    assert f.status == "breach" and f.blocks_possession


def test_missing_prescribed_info_blocks_possession():
    f = run("deposit", prescribed_info_served=None)
    assert f.status == "breach" and f.blocks_possession


def test_late_protection_is_breach_without_possession_block():
    f = run("deposit", deposit_protected=T("2024-03-01"))
    assert f.status == "breach" and not f.blocks_possession


def test_info_sheet_missing_and_late():
    assert run("info_sheet", info_sheet_served=None).status == "breach"
    assert run("info_sheet", info_sheet_served=T("2026-06-02")).status == "breach"


def test_info_sheet_not_applicable_to_new_tenancies():
    assert run("info_sheet", tenancy_start=T("2026-06-01"), info_sheet_served=None).status == "n/a"
