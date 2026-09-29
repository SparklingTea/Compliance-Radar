"""Rules engine: each rule takes one property record and returns a Finding.

Statuses, most to least serious:
  breach         - the legal requirement is not met right now
  action_needed  - no breach yet, but something has to happen before the next let/renewal
  due_soon       - a deadline falls inside the rule's warning window
  at_risk        - exposure to a proposed (not yet statutory) standard
  ok / n/a
"""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

BANDS = "ABCDEFG"
# SAP score at the bottom of each band.
BAND_FLOOR = {"A": 92, "B": 81, "C": 69, "D": 55, "E": 39, "F": 21, "G": 1}
SEVERITY = {"breach": 1, "action_needed": 2, "due_soon": 3, "at_risk": 4, "ok": 9, "n/a": 9}


@dataclass
class Finding:
    status: str
    due_date: pd.Timestamp | None = None
    detail: str = ""
    blocks_possession: bool = False
    extra: dict = field(default_factory=dict)


def load_config(path: str | Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _missing(v) -> bool:
    return v is None or pd.isna(v)


def _worse_than(rating: str, band: str) -> bool:
    return BANDS.index(rating) > BANDS.index(band)


def _deadline_status(due: pd.Timestamp, as_of: pd.Timestamp, warn_days: int) -> str:
    if due < as_of:
        return "breach"
    if (due - as_of).days <= warn_days:
        return "due_soon"
    return "ok"


def check_epc_validity(p, c, as_of):
    if _missing(p.get("lodgement_date")):
        return Finding("breach", detail="No private-rental EPC found on the register for this property")
    expiry = p["lodgement_date"] + pd.DateOffset(years=c["validity_years"])
    if expiry < as_of:
        # Validity is judged when the EPC is made available, so an EPC that expires
        # during a tenancy is only a breach if the current tenancy began after expiry.
        if p["tenancy_start"] >= expiry:
            return Finding("breach", expiry, f"Tenancy began {p['tenancy_start']:%Y-%m-%d} after the EPC expired on {expiry:%Y-%m-%d}")
        return Finding("action_needed", expiry, f"EPC expired {expiry:%Y-%m-%d}; a new one is needed before re-letting or renewal")
    return Finding(_deadline_status(expiry, as_of, c["warn_days"]), expiry, f"EPC valid until {expiry:%Y-%m-%d}")


def check_mees_current(p, c, as_of):
    rating = p.get("current_energy_rating")
    if _missing(rating):
        return Finding("n/a", detail="No EPC rating available")
    if not _worse_than(rating, c["minimum_band"]):
        return Finding("ok", detail=f"Rated {rating}")
    if p["mees_exemption_registered"]:
        return Finding("ok", detail=f"Rated {rating}; exemption registered (check its 5-year expiry)")
    return Finding("breach", detail=f"Rated {rating}, below band {c['minimum_band']}, with no registered exemption")


def _cost_to_target(p, target_sap):
    """Pro-rata share of the recommendation bundle cost needed to close the gap to the target.

    The open data gives indicative costs for the full set of recommendations (current ->
    potential rating) but not the SAP gain per measure, so assume points scale with spend.
    """
    low, high = p.get("upgrade_cost_low"), p.get("upgrade_cost_high")
    cur, pot = p["current_energy_efficiency"], p.get("potential_energy_efficiency")
    if _missing(low) or _missing(pot) or pot <= cur:
        return None
    share = min(1.0, (target_sap - cur) / (pot - cur))
    return share * (low + high) / 2


def check_mees_2030(p, c, as_of):
    rating, target = p.get("current_energy_rating"), c["target_band"]
    deadline = pd.Timestamp(c["deadline"])
    if _missing(rating):
        return Finding("n/a", deadline, "No EPC rating available")
    if not _worse_than(rating, target):
        return Finding("ok", deadline, f"Already {rating}")
    cap = min(c["cost_cap"], c["cost_cap_pct_of_value"] * p["estimated_value"])
    gap = BAND_FLOOR[target] - p["current_energy_efficiency"]
    detail = f"{rating} ({p['current_energy_efficiency']:.0f}) -> {target} needs +{gap:.0f} SAP pts; spend cap £{cap:,.0f}"
    est = _cost_to_target(p, BAND_FLOOR[target])
    potential = p.get("potential_energy_rating")
    if isinstance(potential, str) and _worse_than(potential, target):
        detail += f"; assessor's potential rating is only {potential}, so recommended measures won't reach {target} (possible exemption case)"
    elif est is None:
        detail += "; no recommendation costs available"
    else:
        detail += f"; est. £{est:,.0f} to reach {target} ({'within' if est <= cap else 'EXCEEDS'} cap)"
    return Finding("at_risk", deadline, detail, extra={"est_cost_to_target": est, "spend_cap": cap})


def check_gas_safety(p, c, as_of):
    if not p["has_gas"]:
        return Finding("n/a", detail="No gas supply")
    last = p["gas_last_check"]
    due = last + pd.DateOffset(months=c["interval_months"])
    copy_due = last + pd.Timedelta(days=c["copy_to_tenant_days"])
    if due < as_of:
        return Finding("breach", due, f"Last check {last:%Y-%m-%d}; overdue by {(as_of - due).days} days")
    if _missing(p["gas_copy_to_tenant"]) and copy_due < as_of:
        return Finding("breach", copy_due, f"Check done {last:%Y-%m-%d} but record not given to tenant within {c['copy_to_tenant_days']} days")
    return Finding(_deadline_status(due, as_of, c["warn_days"]), due, f"Next check due {due:%Y-%m-%d}")


def check_eicr(p, c, as_of):
    last = p["eicr_last_inspection"]
    due = last + pd.DateOffset(years=c["interval_years"])
    copy_due = last + pd.Timedelta(days=c["copy_to_tenant_days"])
    if due < as_of:
        return Finding("breach", due, f"Last inspection {last:%Y-%m-%d}; overdue by {(as_of - due).days} days")
    if _missing(p["eicr_copy_to_tenant"]) and copy_due < as_of:
        return Finding("breach", copy_due, f"Inspected {last:%Y-%m-%d} but report not given to tenant within {c['copy_to_tenant_days']} days")
    return Finding(_deadline_status(due, as_of, c["warn_days"]), due, f"Next inspection due {due:%Y-%m-%d}")


def check_deposit(p, c, as_of):
    received = p["deposit_received"]
    if _missing(received):
        return Finding("n/a", detail="No deposit taken")
    protect_by = received + pd.Timedelta(days=c["protect_days"])
    info_by = received + pd.Timedelta(days=c["prescribed_info_days"])
    blocks = c["blocks_possession"]
    if _missing(p["deposit_protected"]):
        if protect_by < as_of:
            return Finding("breach", protect_by, "Deposit not protected in an authorised scheme", blocks)
        return Finding("due_soon", protect_by, "Deposit awaiting protection")
    if _missing(p["prescribed_info_served"]) and info_by < as_of:
        return Finding("breach", info_by, "Deposit protected but prescribed information not served", blocks)
    if p["deposit_protected"] > protect_by:
        return Finding("breach", protect_by, f"Protected late on {p['deposit_protected']:%Y-%m-%d}: exposure to a 1-3x deposit claim under s214")
    return Finding("ok", protect_by, "Protected and prescribed information served")


def check_info_sheet(p, c, as_of):
    if p["tenancy_start"] >= pd.Timestamp(c["applies_to_tenancies_before"]):
        return Finding("n/a", detail="Tenancy started on or after 1 May 2026")
    deadline = pd.Timestamp(c["deadline"])
    served = p["info_sheet_served"]
    if _missing(served):
        status = "breach" if deadline < as_of else "due_soon"
        return Finding(status, deadline, "Information Sheet not served")
    if served > deadline:
        return Finding("breach", deadline, f"Served late on {served:%Y-%m-%d}")
    return Finding("ok", deadline, f"Served {served:%Y-%m-%d}")


RULES = {
    "epc_validity": check_epc_validity,
    "mees_current": check_mees_current,
    "mees_2030": check_mees_2030,
    "gas_safety": check_gas_safety,
    "eicr": check_eicr,
    "deposit": check_deposit,
    "info_sheet": check_info_sheet,
}


def evaluate(records: pd.DataFrame, config: dict, as_of) -> pd.DataFrame:
    as_of = pd.Timestamp(as_of)
    rows = []
    for p in records.to_dict("records"):
        for key, fn in RULES.items():
            c = config[key]
            f = fn(p, c, as_of)
            rows.append({
                "property_id": p["property_id"],
                "landlord_id": p["landlord_id"],
                "rule_id": c["id"],
                "rule": c["name"],
                "status": f.status,
                "due_date": f.due_date,
                "days_to_due": None if f.due_date is None else (f.due_date - as_of).days,
                "detail": f.detail,
                "blocks_possession": f.blocks_possession,
                "law": c["law"],
                "consequence": c["consequence"],
                **f.extra,
            })
    out = pd.DataFrame(rows)
    out["severity"] = out["status"].map(SEVERITY)
    return out


def property_summary(findings: pd.DataFrame) -> pd.DataFrame:
    open_ = findings[findings["severity"] < 9]
    g = open_.groupby("property_id")
    summary = pd.DataFrame({
        "breaches": g["status"].apply(lambda s: (s == "breach").sum()),
        "open_items": g.size(),
        "next_due": g["due_date"].min(),
        "possession_blocked": g["blocks_possession"].any(),
    })
    all_ids = findings[["property_id", "landlord_id"]].drop_duplicates().set_index("property_id")
    summary = all_ids.join(summary).fillna({"breaches": 0, "open_items": 0, "possession_blocked": False})
    summary["possession_ready"] = ~summary["possession_blocked"].astype(bool)
    return summary.drop(columns="possession_blocked").reset_index()


def work_queue(findings: pd.DataFrame) -> pd.DataFrame:
    q = findings[findings["severity"] < 9]
    return q.sort_values(["blocks_possession", "severity", "days_to_due"], ascending=[False, True, True])
