from datetime import date
from io import BytesIO

import altair as alt
import pandas as pd
import streamlit as st

from radar import loader, pipeline, rules

st.set_page_config(page_title="Compliance Radar", page_icon="📡", layout="wide")

STATUS_ORDER = ["breach", "action_needed", "due_soon", "at_risk", "ok", "n/a"]
STATUS_COLOURS = ["#d62728", "#ff7f0e", "#f2b701", "#4c78a8", "#59a14f", "#c7c7c7"]
STATUS_LABEL = {"breach": "Breach", "action_needed": "Action needed", "due_soon": "Due soon",
                "at_risk": "At risk (2030)", "ok": "OK", "n/a": "N/A"}
QUEUE_COLS = ["property_id", "landlord_id", "postcode_district", "rule_id", "status", "due_date",
              "days_to_due", "detail", "blocks_possession", "law"]


def _file(data: bytes, name: str) -> BytesIO:
    f = BytesIO(data)
    f.name = name
    return f


@st.cache_data(show_spinner="Loading Hull demo properties…")
def demo_properties() -> pd.DataFrame:
    return pipeline.load_demo()


@st.cache_data(show_spinner="Reading EPC register files… (large Excel files can take a minute)")
def uploaded_properties(certs: tuple[bytes, str], recs: tuple[bytes, str] | None) -> pd.DataFrame:
    return pipeline.build_properties(_file(*certs), _file(*recs) if recs else None)


@st.cache_data(show_spinner="Reading compliance records…")
def uploaded_compliance(comp: tuple[bytes, str]) -> pd.DataFrame:
    return loader.load_compliance(_file(*comp))


@st.cache_data(show_spinner="Running the rules…")
def run_radar(props: pd.DataFrame, as_of: date, compliance: pd.DataFrame | None, seed: int) -> dict:
    res = pipeline.run(props, pd.Timestamp(as_of), compliance, seed)
    district = res["records"][["property_id", "postcode_district"]].drop_duplicates("property_id")
    for key in ("findings", "queue"):
        res[key] = res[key].merge(district, on="property_id", how="left")
    return res


@st.cache_data
def config() -> dict:
    return rules.load_config(pipeline.CONFIG_PATH)


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("📡 Compliance Radar")
    as_of = st.date_input("Evaluate as of", value=date.today())
    source = st.radio("Data", ["Hull demo", "Upload my files"],
                      captions=["Real EPC register data (Hull private rentals) with synthetic safety & deposit records",
                                "EPC register export plus your own compliance records (Excel or CSV)"])

    props, compliance, seed = None, None, 42
    if source == "Hull demo":
        props = demo_properties()
        seed = st.number_input("Synthetic data seed", value=42, step=1,
                               help="Changes the randomly generated gas/EICR/deposit records")
    else:
        uploads = st.file_uploader("Drop files: EPC certificates, recommendations (optional), compliance records (optional)",
                                   type=["xlsx", "csv"], accept_multiple_files=True)
        kinds: dict[str, tuple[bytes, str]] = {}
        for up in uploads or []:
            kind = loader.detect_kind(loader.read_header(up))
            if kind is None:
                st.warning(f"Couldn't recognise **{up.name}**. Check its column headers.")
            else:
                kinds[kind] = (up.getvalue(), up.name)
                st.caption(f"✅ {up.name} → {kind}")
        st.download_button("Download compliance template (.xlsx)",
                           pipeline.compliance_template(demo_properties()["property_id"].tolist()),
                           file_name="compliance_template.xlsx")
        if "certificates" in kinds:
            try:
                props = uploaded_properties(kinds["certificates"], kinds.get("recommendations"))
                if "compliance" in kinds:
                    compliance = uploaded_compliance(kinds["compliance"])
            except (ValueError, KeyError) as e:
                st.error(str(e))
                props = None

st.title("Compliance Radar")
if props is None:
    st.info("Upload an EPC certificates export (from get-energy-performance-data.communities.gov.uk) to start. "
            "Add the recommendations file for 2030 upgrade cost estimates, and a compliance records file "
            "(see the template) for your gas, EICR, deposit and information-sheet dates. Without one, "
            "synthetic records are generated.")
    st.stop()

res = run_radar(props, as_of, compliance, seed)
findings, summary, queue = res["findings"], res["summary"], res["queue"]
if res["synthetic"]:
    st.caption("⚠️ EPC data is real (public register). Gas, EICR, deposit and tenancy records are **synthetic** "
               "with known defect rates. Rules as of " + as_of.strftime("%d %b %Y") + ". Not legal advice.")

# ---------------------------------------------------------------- KPIs
open_ = findings[findings["severity"] < 9]
due_90 = open_[(open_["status"] == "due_soon") & (open_["days_to_due"] <= 90)]
c2030 = findings[findings["rule_id"] == config()["mees_2030"]["id"]]
at_risk = c2030[c2030["status"] == "at_risk"]
k = st.columns(5)
k[0].metric("Private rentals", f"{len(summary):,}")
k[1].metric("With a breach", f"{(summary['breaches'] > 0).sum():,}",
            f"{(summary['breaches'] > 0).mean():.0%} of portfolio", delta_color="off", delta_arrow="off")
k[2].metric("Possession blocked", f"{(~summary['possession_ready']).sum():,}",
            "deposit / prescribed info", delta_color="off", delta_arrow="off")
k[3].metric("Deadlines in 90 days", f"{len(due_90):,}")
bill = at_risk["est_cost_to_target"].sum() if "est_cost_to_target" in at_risk else 0
k[4].metric("Below EPC C (2030)", f"{len(at_risk):,}",
            f"est. £{bill / 1e6:,.1f}m to fix" if bill else "add recommendations for costs", delta_color="off", delta_arrow="off")

tab_q, tab_o, tab_c, tab_p, tab_l = st.tabs(["Work queue", "Rule overview", "EPC C by 2030", "Property lookup", "Rules & law"])

# ---------------------------------------------------------------- work queue
with tab_q:
    f = st.columns([2, 2, 2, 1])
    rule_ids = sorted(queue["rule_id"].unique())
    pick_rules = f[0].multiselect("Rule", rule_ids, default=[r for r in rule_ids if r != "MEES-C-2030"])
    pick_status = f[1].multiselect("Status", STATUS_ORDER[:4], default=["breach", "action_needed", "due_soon"],
                                   format_func=STATUS_LABEL.get)
    pick_district = f[2].multiselect("Postcode district", sorted(queue["postcode_district"].dropna().unique()))
    only_blocking = f[3].toggle("Possession blockers only")

    q = queue[queue["rule_id"].isin(pick_rules) & queue["status"].isin(pick_status)]
    if pick_district:
        q = q[q["postcode_district"].isin(pick_district)]
    if only_blocking:
        q = q[q["blocks_possession"]]
    st.caption(f"{len(q):,} items. Sorted by possession impact, then severity, then most overdue.")
    st.dataframe(q[QUEUE_COLS], hide_index=True, width="stretch", height=520,
                 column_config={"due_date": st.column_config.DateColumn("Due"),
                                "days_to_due": st.column_config.NumberColumn("Days to due", help="Negative = overdue"),
                                "blocks_possession": st.column_config.CheckboxColumn("Blocks possession"),
                                "status": st.column_config.TextColumn("Status"),
                                "detail": st.column_config.TextColumn("Detail", width="large")})
    d = st.columns(2)
    d[0].download_button("Download queue (.xlsx)", pipeline.to_excel({"work_queue": q[QUEUE_COLS + ["consequence"]]}),
                         file_name=f"work_queue_{as_of}.xlsx")
    d[1].download_button("Download all findings (.csv)", findings.to_csv(index=False).encode(),
                         file_name=f"findings_{as_of}.csv")

# ---------------------------------------------------------------- overview
with tab_o:
    counts = findings.groupby(["rule_id", "status"]).size().reset_index(name="properties")
    chart = alt.Chart(counts).mark_bar().encode(
        y=alt.Y("rule_id:N", title=None, sort=list(counts["rule_id"].unique())),
        x=alt.X("properties:Q", stack="normalize", title="Share of properties", axis=alt.Axis(format="%")),
        color=alt.Color("status:N", scale=alt.Scale(domain=STATUS_ORDER, range=STATUS_COLOURS),
                        legend=alt.Legend(orient="bottom", title=None)),
        order=alt.Order("sort_key:Q"),
        tooltip=["rule_id", "status", alt.Tooltip("properties:Q", format=",")],
    ).transform_calculate(sort_key=f"indexof({STATUS_ORDER}, datum.status)").properties(height=320)
    st.altair_chart(chart, width="stretch")
    table = counts.pivot(index="rule_id", columns="status", values="properties").fillna(0).astype(int)
    st.dataframe(table[[s for s in STATUS_ORDER if s in table.columns]].rename(columns=STATUS_LABEL),
                 width="stretch")

    by_ll = summary.groupby("landlord_id").agg(properties=("property_id", "size"), breaches=("breaches", "sum"),
                                               blocked=("possession_ready", lambda s: (~s).sum()))
    st.subheader("Landlords with the most breaches")
    st.dataframe(by_ll.sort_values(["breaches", "properties"], ascending=False).head(15), width="stretch")

# ---------------------------------------------------------------- 2030
with tab_c:
    cfg = config()["mees_2030"]
    st.warning(f"**Proposed policy, not yet law.** {cfg['rule']}")
    records = res["records"]
    bands = records["current_energy_rating"].value_counts().reindex(list("ABCDEFG"), fill_value=0).reset_index()
    bands.columns = ["band", "properties"]
    bands["meets_c"] = bands["band"].isin(list("ABC"))
    c1, c2 = st.columns(2)
    c1.altair_chart(alt.Chart(bands).mark_bar().encode(
        x=alt.X("band:N", title="Current EPC band"), y=alt.Y("properties:Q", title="Properties"),
        color=alt.Color("meets_c:N", scale=alt.Scale(domain=[True, False], range=["#59a14f", "#4c78a8"]),
                        legend=alt.Legend(title="Meets C", orient="bottom")),
        tooltip=["band", alt.Tooltip("properties:Q", format=",")]).properties(title="Portfolio by EPC band", height=300),
        width="stretch")
    if "est_cost_to_target" in at_risk and at_risk["est_cost_to_target"].notna().any():
        costs = at_risk.dropna(subset=["est_cost_to_target"]).assign(
            over_cap=lambda d: d["est_cost_to_target"] > d["spend_cap"])
        c2.altair_chart(alt.Chart(costs).mark_bar().encode(
            x=alt.X("est_cost_to_target:Q", bin=alt.Bin(step=1000), title="Estimated cost to reach C (£)"),
            y=alt.Y("count():Q", title="Properties"),
            color=alt.Color("over_cap:N", scale=alt.Scale(domain=[False, True], range=["#4c78a8", "#d62728"]),
                            legend=alt.Legend(title="Exceeds cap", orient="bottom")),
        ).properties(title="Upgrade cost vs cap", height=300), width="stretch")
        m = st.columns(3)
        m[0].metric("With a cost estimate", f"{len(costs):,} / {len(at_risk):,}")
        m[1].metric("Estimate exceeds cap", f"{costs['over_cap'].sum():,}")
        m[2].metric("Median cost to C", f"£{costs['est_cost_to_target'].median():,.0f}")
        st.caption("Estimate = the recommended measures' indicative cost (midpoint), pro-rated by the SAP points "
                   "needed to reach C compared with the points to the assessor's potential rating. The register doesn't "
                   "give per-measure SAP gains, so this is an approximation.")
    else:
        c2.info("Upload the recommendations file to estimate upgrade costs against the cap.")
    st.dataframe(at_risk[["property_id", "postcode_district", "detail"]], hide_index=True, width="stretch")

# ---------------------------------------------------------------- property lookup
with tab_p:
    ids = queue["property_id"].drop_duplicates().tolist() + sorted(set(summary["property_id"]) - set(queue["property_id"]))
    pid = st.selectbox("Property (those with open items listed first)", ids)
    rec = res["records"].set_index("property_id").loc[pid]
    rec = rec.iloc[0] if isinstance(rec, pd.DataFrame) else rec
    row = summary.set_index("property_id").loc[pid]
    a = st.columns(4)
    a[0].metric("EPC", f"{rec.get('current_energy_rating', '–')} ({rec.get('current_energy_efficiency', float('nan')):.0f})"
                if pd.notna(rec.get("current_energy_rating")) else "none")
    a[1].metric("Type", f"{rec.get('property_type', '–')}")
    a[2].metric("Open breaches", int(row["breaches"]))
    a[3].metric("Possession ready", "Yes" if row["possession_ready"] else "No")
    st.caption(f"{rec.get('postcode_district', '')} · {rec.get('built_form', '')} · "
               f"{rec.get('construction_age_band', '')} · landlord {rec['landlord_id']}")
    pf = findings[findings["property_id"] == pid].sort_values("severity")
    for _, r in pf.iterrows():
        icon = {"breach": "🔴", "action_needed": "🟠", "due_soon": "🟡", "at_risk": "🔵", "ok": "🟢"}.get(r["status"], "⚪")
        with st.expander(f"{icon} **{r['rule']}**: {STATUS_LABEL[r['status']]}. {r['detail']}",
                         expanded=r["severity"] <= 2):
            st.markdown(f"**Law:** {r['law']}\n\n**Consequence:** {r['consequence']}")
            if pd.notna(r["due_date"]):
                st.markdown(f"**Due:** {pd.Timestamp(r['due_date']):%d %b %Y}")
    if isinstance(rec.get("recommended_measures"), str):
        st.markdown(f"**Assessor recommendations:** {rec['recommended_measures']}")

# ---------------------------------------------------------------- rules & law
with tab_l:
    st.markdown("Each rule's thresholds and legal references are set in `config/rules.toml`, so a change in the "
                "law is a config edit, not a code change. Checked against legislation.gov.uk on 29 Sep 2026. "
                "**Not legal advice.**")
    for key, c in config().items():
        with st.expander(f"**{c['id']}**: {c['name']}"):
            st.markdown(f"**Rule:** {c['rule']}\n\n**Law:** {c['law']}\n\n**Consequence:** {c['consequence']}")
