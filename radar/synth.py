"""Synthetic compliance records layered on top of real EPC properties.

Gas, EICR, deposit and tenancy records are not public, so they are generated here with
known defect rates. The rules engine should find roughly these rates back, which is a
simple check that the rules work.
"""
import numpy as np
import pandas as pd

DEFECT_RATES = {
    "gas_overdue": 0.05,
    "gas_copy_missing": 0.03,
    "eicr_overdue": 0.07,
    "eicr_copy_missing": 0.04,
    "no_deposit_taken": 0.05,
    "deposit_late": 0.03,
    "deposit_unprotected": 0.02,
    "prescribed_info_missing": 0.04,
    "info_sheet_late": 0.07,
    "info_sheet_missing": 0.08,
    "mees_exemption_registered": 0.30,
}


def _days(rng, lo, hi, n):
    return pd.to_timedelta(rng.integers(lo, hi + 1, n), unit="D")


def generate(props: pd.DataFrame, ref_date: str, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(props)
    ref = pd.Timestamp(ref_date)
    r = DEFECT_RATES
    df = pd.DataFrame({"property_id": props["property_id"].values})

    # Heavy-tailed portfolio sizes: many single-property landlords, a few large ones.
    n_landlords = max(1, n // 3)
    df["landlord_id"] = [f"L{i:05d}" for i in (rng.zipf(1.6, n) % n_landlords)]

    # Tenancy length: exponential, so most tenancies are fairly recent.
    df["tenancy_start"] = ref - pd.to_timedelta(
        np.minimum(rng.exponential(700, n), 3650).astype(int) + 10, unit="D")

    fuel = props["main_fuel"].fillna("").str.lower()
    df["has_gas"] = (props["mains_gas_flag"].eq("Y") | fuel.str.contains("mains gas")).values

    overdue = rng.random(n) < r["gas_overdue"]
    df["gas_last_check"] = ref - np.where(overdue, _days(rng, 366, 540, n), _days(rng, 0, 364, n))
    df["gas_copy_to_tenant"] = df["gas_last_check"] + _days(rng, 0, 21, n)
    df.loc[rng.random(n) < r["gas_copy_missing"], "gas_copy_to_tenant"] = pd.NaT
    df.loc[~df["has_gas"], ["gas_last_check", "gas_copy_to_tenant"]] = pd.NaT

    overdue = rng.random(n) < r["eicr_overdue"]
    df["eicr_last_inspection"] = ref - np.where(overdue, _days(rng, 1827, 2200, n), _days(rng, 0, 1825, n))
    df["eicr_copy_to_tenant"] = df["eicr_last_inspection"] + _days(rng, 0, 21, n)
    df.loc[rng.random(n) < r["eicr_copy_missing"], "eicr_copy_to_tenant"] = pd.NaT

    df["deposit_received"] = df["tenancy_start"] - _days(rng, 0, 7, n)
    df["deposit_protected"] = df["deposit_received"] + _days(rng, 0, 25, n)
    late = rng.random(n) < r["deposit_late"]
    df.loc[late, "deposit_protected"] = df.loc[late, "deposit_received"] + _days(rng, 31, 120, late.sum())
    df.loc[rng.random(n) < r["deposit_unprotected"], "deposit_protected"] = pd.NaT
    df["prescribed_info_served"] = df["deposit_protected"] + _days(rng, 0, 4, n)
    df.loc[rng.random(n) < r["prescribed_info_missing"], "prescribed_info_served"] = pd.NaT
    no_dep = rng.random(n) < r["no_deposit_taken"]
    df.loc[no_dep, ["deposit_received", "deposit_protected", "prescribed_info_served"]] = pd.NaT

    df["info_sheet_served"] = pd.Timestamp("2026-05-01") + _days(rng, 0, 30, n)
    late = rng.random(n) < r["info_sheet_late"]
    df.loc[late, "info_sheet_served"] = pd.Timestamp("2026-06-01") + _days(rng, 0, 90, late.sum())
    df.loc[rng.random(n) < r["info_sheet_missing"], "info_sheet_served"] = pd.NaT
    df.loc[df["tenancy_start"] >= "2026-05-01", "info_sheet_served"] = pd.NaT

    df["mees_exemption_registered"] = rng.random(n) < r["mees_exemption_registered"]

    # Rough Hull value: floor area x £/m2. Only used for the 10%-of-value cost cap.
    area = props["total_floor_area"].fillna(80).values
    df["estimated_value"] = (area * rng.uniform(1300, 2000, n)).round(-3)
    return df
