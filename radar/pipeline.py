from io import BytesIO
from pathlib import Path

import pandas as pd

from radar import loader, rules, synth

ROOT = Path(__file__).parents[1]
CONFIG_PATH = ROOT / "config" / "rules.toml"
DEMO_PATH = ROOT / "data" / "demo" / "hull_prs.csv.gz"

TEMPLATE_GUIDE = [
    ("uprn", "Unique Property Reference Number. Used to match the EPC register. Give this or property_id."),
    ("property_id", "Alternative to uprn: the radar's anonymised ID (as shown in the demo)."),
    ("landlord_id", "Required. Any landlord reference."),
    ("tenancy_start", "Required. Start date of the current tenancy (DD/MM/YYYY or YYYY-MM-DD)."),
    ("has_gas", "Y/N. If blank, taken from the EPC (mains gas)."),
    ("gas_last_check", "Date of the last gas safety check."),
    ("gas_copy_to_tenant", "Date the gas safety record was given to the tenant."),
    ("eicr_last_inspection", "Date of the last EICR."),
    ("eicr_copy_to_tenant", "Date the EICR was given to the tenant."),
    ("deposit_received", "Date the deposit was received (blank = no deposit)."),
    ("deposit_protected", "Date the deposit was protected in an authorised scheme."),
    ("prescribed_info_served", "Date the prescribed information was served."),
    ("info_sheet_served", "Date the Renters' Rights Act Information Sheet was served."),
    ("mees_exemption_registered", "Y/N. A valid PRS Exemptions Register entry exists."),
    ("estimated_value", "Property value in £ (for the 10%-of-value cost cap). Optional."),
]


def build_properties(certs_src, recs_src=None) -> pd.DataFrame:
    certs = loader.load_certificates(certs_src)
    recs = loader.read_table(recs_src, loader.REC_COLS) if recs_src is not None else None
    return loader.latest_private_rentals(certs, recs)


def load_demo() -> pd.DataFrame:
    return pd.read_csv(DEMO_PATH, parse_dates=["lodgement_date", "inspection_date"])


def run(props: pd.DataFrame, as_of, compliance: pd.DataFrame | None = None, seed: int = 42,
        config: dict | None = None) -> dict:
    config = config or rules.load_config(CONFIG_PATH)
    if compliance is None:
        records = props.merge(synth.generate(props, str(as_of), seed), on="property_id")
    else:
        records = loader.join_portfolio(props, compliance)
    findings = rules.evaluate(records, config, as_of)
    return {
        "records": records,
        "findings": findings,
        "summary": rules.property_summary(findings),
        "queue": rules.work_queue(findings),
        "synthetic": compliance is None,
    }


def compliance_template(example_ids: list[str]) -> bytes:
    cols = [c for c, _ in TEMPLATE_GUIDE if c != "uprn"]
    example = pd.DataFrame({"property_id": example_ids[:3], "landlord_id": "L00001",
                            "tenancy_start": "01/09/2025", "has_gas": "Y",
                            "gas_last_check": "15/08/2025", "gas_copy_to_tenant": "20/08/2025",
                            "eicr_last_inspection": "01/06/2022", "eicr_copy_to_tenant": "10/06/2022",
                            "deposit_received": "25/08/2025", "deposit_protected": "05/09/2025",
                            "prescribed_info_served": "05/09/2025", "info_sheet_served": "",
                            "mees_exemption_registered": "N", "estimated_value": ""})[cols]
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        example.to_excel(xw, sheet_name="compliance", index=False)
        pd.DataFrame(TEMPLATE_GUIDE, columns=["column", "meaning"]).to_excel(xw, sheet_name="guide", index=False)
    return buf.getvalue()


def to_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
    return buf.getvalue()
