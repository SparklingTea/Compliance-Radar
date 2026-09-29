import argparse
from datetime import date
from pathlib import Path

import pandas as pd

from radar import loader, pipeline

ROOT = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser(description="Compliance Radar: EPC + compliance records -> deadlines and work queue")
    ap.add_argument("--as-of", default=date.today().isoformat(), help="Evaluation date, YYYY-MM-DD")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--build-demo", action="store_true",
                    help=f"Write the anonymised property file the web app ships with ({pipeline.DEMO_PATH.relative_to(ROOT)})")
    args = ap.parse_args()

    inputs = loader.find_inputs(ROOT)
    if "certificates" not in inputs:
        raise SystemExit("No EPC certificates file (CSV or Excel) found in the project root or data/raw/")
    print("Inputs:", {k: p.name for k, p in inputs.items()})

    props = pipeline.build_properties(inputs["certificates"], inputs.get("recommendations"))
    if args.build_demo:
        pipeline.DEMO_PATH.parent.mkdir(parents=True, exist_ok=True)
        props.to_csv(pipeline.DEMO_PATH, index=False)
        print(f"Wrote {len(props):,} properties to {pipeline.DEMO_PATH}")

    compliance = loader.load_compliance(inputs["compliance"]) if "compliance" in inputs else None
    res = pipeline.run(props, args.as_of, compliance, args.seed)
    findings, summary, queue = res["findings"], res["summary"], res["queue"]

    out = ROOT / "output"
    out.mkdir(exist_ok=True)
    findings.to_csv(out / "findings.csv", index=False)
    queue.to_csv(out / "work_queue.csv", index=False)
    summary.to_csv(out / "property_summary.csv", index=False)

    source = "synthetic" if res["synthetic"] else "uploaded"
    print(f"\nAs of {args.as_of}: {len(summary):,} properties ({source} compliance records)\n")
    table = findings.pivot_table(index="rule_id", columns="status", values="property_id", aggfunc="count", fill_value=0)
    cols = [c for c in ["breach", "action_needed", "due_soon", "at_risk", "ok", "n/a"] if c in table.columns]
    print(table[cols].to_string(), "\n")
    print(f"Properties with >=1 breach:      {(summary['breaches'] > 0).sum():,}")
    print(f"Possession blocked (deposit):    {(~summary['possession_ready']).sum():,}")
    print(f"Work queue items:                {len(queue):,}  -> {out / 'work_queue.csv'}")


if __name__ == "__main__":
    pd.set_option("display.width", 140)
    main()
