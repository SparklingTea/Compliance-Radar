# Compliance Radar

A rules engine that turns EPC register data and landlord compliance records into deadlines,
breach flags, a possession-readiness check and a prioritised work queue for private-rented homes
in England.

**Demo:** Kingston upon Hull private rentals (21,634 homes, latest certificate per property)
from the public EPC register. The gas, EICR, deposit and tenancy records are **synthetic** because
they aren't public.

## Rules

| ID | Requirement | Law |
|---|---|---|
| EPC-VALID | Valid EPC (≤10 years on register) when let | SI 2012/3118 reg 6(2), 6(5), 9(2)(a) |
| MEES-E | No letting below band E without a registered exemption | SI 2015/962 reg 22, 23 |
| MEES-C-2030 | Band C by 1 Oct 2030, cost cap £10k or 10% of value | **Proposed** (Warm Homes Plan, Jan 2026) |
| GAS | Annual gas safety check; copy to tenant within 28 days | SI 1998/2451 reg 36(3)(a), 36(6)(a) |
| EICR | 5-yearly electrical inspection; copy to tenant within 28 days | SI 2020/312 reg 3 |
| DEPOSIT | Protect and serve prescribed info within 30 days; **blocks possession** | Housing Act 2004 s213-215 (as amended by the Renters' Rights Act 2025) |
| INFO-SHEET | Information Sheet to pre-May-2026 tenants by 31 May 2026 | Renters' Rights Act 2025 Sch 6 para 7; SI 2026/324 |

Thresholds and legal references live in [`config/rules.toml`](config/rules.toml).
Not legal advice. Law checked 29 Sep 2026.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

In the app you can upload your own files (Excel or CSV). The file type is detected from its columns:

- **EPC certificates** export from [get-energy-performance-data.communities.gov.uk](https://get-energy-performance-data.communities.gov.uk/)
- **Recommendations** export (optional; gives the 2030 upgrade cost estimates)
- **Compliance records** (optional; download the template in the app). If you leave this out, synthetic records are generated.

Command-line version, reading raw files from the project root or `data/raw/`:

```bash
python run.py --as-of 2026-09-29 --build-demo
```

## Data notes

- Addresses are dropped. Properties are identified by a hashed UPRN plus the postcode district,
  because full address and postcode fields carry Royal Mail licensing restrictions.
- Cost to reach C = the recommended measures' indicative cost (midpoint), pro-rated by the SAP points
  needed compared with the points to the assessor's potential rating. This is an approximation.
