import hashlib
import re
from pathlib import Path

import pandas as pd

CERT_COLS = [
    "certificate_number", "uprn", "address", "postcode", "property_type", "built_form",
    "construction_age_band", "total_floor_area", "current_energy_rating",
    "current_energy_efficiency", "potential_energy_rating", "potential_energy_efficiency",
    "main_fuel", "mains_gas_flag", "tenure", "transaction_type",
    "inspection_date", "lodgement_date", "lodgement_datetime",
]
REC_COLS = ["certificate_number", "improvement_item", "improvement_summary_text", "indicative_cost"]

COMPLIANCE_DATES = [
    "tenancy_start", "gas_last_check", "gas_copy_to_tenant", "eicr_last_inspection",
    "eicr_copy_to_tenant", "deposit_received", "deposit_protected", "prescribed_info_served",
    "info_sheet_served",
]
COMPLIANCE_BOOLS = ["has_gas", "mees_exemption_registered"]
COMPLIANCE_REQUIRED = ["landlord_id", "tenancy_start"]


def _name(src) -> str:
    return str(getattr(src, "name", src)).lower()


def read_table(src, usecols=None) -> pd.DataFrame:
    """Read a CSV or Excel file from a path or an uploaded file object."""
    if _name(src).endswith((".xlsx", ".xlsm", ".xls")):
        try:
            df = pd.read_excel(src, dtype=str, engine="calamine")
        except ImportError:
            df = pd.read_excel(src, dtype=str)
        df.columns = df.columns.str.strip().str.lower()
        return df[[c for c in usecols if c in df.columns]] if usecols else df
    wanted = set(usecols) if usecols else None
    return pd.read_csv(src, dtype=str, low_memory=False, encoding_errors="replace",
                       usecols=(lambda c: c.strip().lower() in wanted) if wanted else None
                       ).rename(columns=lambda c: c.strip().lower())


def read_header(src) -> list[str]:
    if _name(src).endswith((".xlsx", ".xlsm", ".xls")):
        cols = pd.read_excel(src, nrows=0).columns
    else:
        cols = pd.read_csv(src, nrows=0, encoding_errors="replace").columns
    if hasattr(src, "seek"):
        src.seek(0)
    return [str(c).strip().lower() for c in cols]


def detect_kind(columns) -> str | None:
    cols = set(columns)
    if {"improvement_item", "indicative_cost"} <= cols:
        return "recommendations"
    if {"current_energy_rating", "lodgement_date"} <= cols:
        return "certificates"
    if {"tenancy_start", "landlord_id"} <= cols and cols & {"uprn", "property_id"}:
        return "compliance"
    return None


def find_inputs(root: Path) -> dict[str, Path]:
    """Map each input kind to a file in root or data/raw, preferring CSV (faster than Excel)."""
    found = {}
    files = [p for d in (root, root / "data" / "raw") for p in d.glob("*")
             if p.suffix.lower() in {".csv", ".xlsx"}]
    for p in sorted(files, key=lambda p: p.suffix.lower() != ".csv"):
        kind = detect_kind(read_header(p))
        if kind and kind not in found:
            found[kind] = p
    return found


def anon_id(key: str) -> str:
    return "P" + hashlib.sha256(str(key).strip().encode()).hexdigest()[:10].upper()


def _property_key(df: pd.DataFrame) -> pd.Series:
    fallback = "addr:" + df["address"].str.lower().str.strip() + "|" + df["postcode"].str.upper().str.strip()
    return df["uprn"].str.strip().fillna(fallback)


def load_certificates(src) -> pd.DataFrame:
    df = read_table(src, CERT_COLS)
    df["property_key"] = _property_key(df)
    return df


_NUM = re.compile(r"\d[\d,]*")


def parse_cost(text) -> tuple[float, float]:
    """'£4,000 - £6,000' -> (4000, 6000); '6,000' -> (6000, 6000). Tolerates mangled £ signs."""
    nums = [float(n.replace(",", "")) for n in _NUM.findall(str(text))]
    if not nums:
        return (float("nan"), float("nan"))
    return (nums[0], nums[-1])


def recommendation_costs(recs: pd.DataFrame) -> pd.DataFrame:
    costs = recs["indicative_cost"].map(parse_cost)
    recs = recs.assign(cost_low=[c[0] for c in costs], cost_high=[c[1] for c in costs],
                       item=pd.to_numeric(recs["improvement_item"], errors="coerce"))
    recs = recs.sort_values(["certificate_number", "item"])
    g = recs.groupby("certificate_number")
    return pd.DataFrame({
        "n_recommendations": g.size(),
        "upgrade_cost_low": g["cost_low"].sum(),
        "upgrade_cost_high": g["cost_high"].sum(),
        "recommended_measures": g["improvement_summary_text"].agg(lambda s: "; ".join(s.dropna().astype(str))),
    }).reset_index()


def latest_private_rentals(certs: pd.DataFrame, recs: pd.DataFrame | None = None) -> pd.DataFrame:
    """Latest certificate per property, kept only where that certificate says private rented.

    Filtering on the latest certificate (not any certificate) avoids counting homes that
    were rented once and have since been sold to owner-occupiers.
    """
    latest = (certs.sort_values("lodgement_datetime")
                   .groupby("property_key", as_index=False).tail(1))
    prs = latest[latest["tenure"].str.lower().str.contains("rented (private)", regex=False, na=False)
                 & latest["current_energy_rating"].isin(list("ABCDEFG"))].copy()

    prs["property_id"] = prs["property_key"].map(anon_id)
    # Postcode district only: full address/postcode carry Royal Mail licensing restrictions.
    prs["postcode_district"] = prs["postcode"].str.upper().str.split().str[0]
    out = prs[["property_id", "postcode_district"] + CERT_COLS].drop(
        columns=["uprn", "address", "postcode", "lodgement_datetime"])

    for col in ["lodgement_date", "inspection_date"]:
        out[col] = pd.to_datetime(out[col], errors="coerce", format="mixed")
    for col in ["total_floor_area", "current_energy_efficiency", "potential_energy_efficiency"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    if recs is not None:
        out = out.merge(recommendation_costs(recs[recs["certificate_number"].isin(out["certificate_number"])]),
                        on="certificate_number", how="left")
        out["n_recommendations"] = out["n_recommendations"].fillna(0).astype(int)
    return out.drop(columns="certificate_number").reset_index(drop=True)


def _to_bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin({"y", "yes", "true", "1"})


def load_compliance(src) -> pd.DataFrame:
    """Portfolio compliance records keyed by uprn (hashed to property_id) or property_id."""
    df = read_table(src)
    missing = [c for c in COMPLIANCE_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Compliance file is missing required columns: {', '.join(missing)}")
    if "property_id" not in df.columns:
        df["property_id"] = df["uprn"].map(anon_id)
    for col in COMPLIANCE_DATES:
        df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=True) if col in df.columns else pd.NaT
    for col in COMPLIANCE_BOOLS:
        df[col] = _to_bool(df[col]) if col in df.columns else pd.NA
    df["estimated_value"] = pd.to_numeric(df.get("estimated_value"), errors="coerce")
    return df.drop(columns=["uprn"], errors="ignore")


def join_portfolio(props: pd.DataFrame, compliance: pd.DataFrame) -> pd.DataFrame:
    """Compliance records drive the portfolio; properties with no EPC on the register stay in."""
    out = compliance.merge(props, on="property_id", how="left", suffixes=("", "_epc"))
    epc_gas = out["mains_gas_flag"].eq("Y") | out["main_fuel"].fillna("").str.lower().str.contains("mains gas")
    out["has_gas"] = out["has_gas"].fillna(epc_gas).astype(bool)
    out["mees_exemption_registered"] = out["mees_exemption_registered"].fillna(False).astype(bool)
    area = out["total_floor_area"].fillna(80)
    out["estimated_value"] = out["estimated_value"].fillna((area * 1650).round(-3))
    return out
