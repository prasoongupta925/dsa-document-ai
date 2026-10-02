# Lender branches and pincodes

Data of `app/branches.py`, behind `GET /projects/{id}/eligibility/branches` (the nearest
branches of each lender at a pincode). Every answer names the sources it used, with their
licences.

| File | What | Source | Licence |
| --- | --- | --- | --- |
| `pincode_directory.json.gz` | Every pincode's delivery post office, district, state and centre (latitude, longitude) | India Post, All India Pincode Directory | Government Open Data License - India |
| `bank_branches.json.gz` | The branches of HDFC Bank, ICICI Bank and Axis Bank, all India | RBI's list of bank branches (IFSC), as released by razorpay/ifsc | Public domain |
| `sample_nbfc_branches.json` | A few branches of Bajaj Finance and Tata Capital near the demo pincodes | Made up for the demo: **SAMPLE**, not real branches | Synthetic |

The two `.json.gz` files are built by `scripts/build_branch_data.py` (below); the SAMPLE
file is written by hand.

## Sources and attribution

### India Post pincode directory

Department of Posts, Ministry of Communications, Government of India, 2020, All India
Pincode Directory with contact details along with Latitude and Longitude, Open Government
Data (OGD) Platform India, file `dataurl03122020/pincode.csv`,
https://data.gov.in/files/ogdpv2dms/s3fs-public/dataurl03122020/pincode.csv. Published
under the Government Open Data License - India:
https://data.gov.in/government-open-data-license-india.

- Read on 2 October 2026: 157,126 post offices, SHA-256
  `84af12fa29adddedfa9addcf46546000d89d2e2899b2993dc88050fafc8861e2` (also stored in the
  file as `sha256`).
- Changed here: only the columns below are kept, names are re-cased, and each pincode gets
  one centre worked out from its offices' points (see "How the data is built"). The Department
  of Posts does not endorse this app or this use of its data.

### RBI bank branch list (IFSC)

RBI's lists of NEFT and RTGS bank branches (IFSC), as collected by
[razorpay/ifsc](https://github.com/razorpay/ifsc), release
[v2.0.62](https://github.com/razorpay/ifsc/releases/tag/v2.0.62) (1 September 2026), file
`IFSC.csv`. Its README: "The code in this repository is licensed under the MIT License ...
The dataset itself is under public domain." Only the dataset is used here, not the code.

- SHA-256 of `IFSC.csv`: `f03870a0ecb06f6c7606c90e4743fc63f9111f088a7175b871c2516bdb8f468e`
  (also stored in the file as `sha256`).
- Kept: the banks' own branches (IFSC `HDFC0`, `ICIC0`, `UTIB0` + six digits). Left out:
  co-operative banks that use these banks' codes, back-office units (service branches, card
  operations, treasury, currency chests ...), and branches whose published address has no
  pincode the directory knows, which cannot be placed:

  | Bank | Own branches | Kept | No known pincode in the address | Back-office units |
  | --- | ---: | ---: | ---: | ---: |
  | HDFC Bank | 9,866 | 8,518 | 1,343 | 5 |
  | ICICI Bank | 7,981 | 7,121 | 857 | 3 |
  | Axis Bank | 6,475 | 3,988 | 2,431 | 56 |

  A missing branch is not a closed one: upload your own branch list (below) for the full
  picture.

### SAMPLE NBFC branches

Bajaj Finance and Tata Capital publish no open branch list. `sample_nbfc_branches.json` has a
few rows for the demo pincodes (Thane, Ghodbunder Road, Mumbai, Pune, and the demo applicants'
Baner, Kothrud and Chinchwad) so that the demo can show them: every name ends in "(sample)",
no address is real, and the app labels them SAMPLE wherever they are shown. The pincodes and
towns are real, so that distances can be shown. Replace them with your own branch list.

## What an answer means

- **Distance** is pincode centre to pincode centre (great circle), so "about N km"; a branch
  in the applicant's own pincode is 0 km and comes first. At most 3 branches per lender,
  nearest first, none over 200 km away. A distance to or from a pincode without a point of
  its own (`approx`, below) is marked `approximate`.
- **Serviceable** comes from your uploaded serviceability list when it covers the lender,
  else from the SAMPLE pincode list of the eligibility calculation (`app/data/pincodes.json`),
  else it is unknown (`null`).
- **Branches** come from your uploaded branch list when it covers the lender, else from the
  public data (banks) or the SAMPLE rows (NBFCs).
- `source` is `dsa_list` when any of a lender's answer came from your lists, else
  `public_data` or `sample`.

## Your own lists (CSV)

`POST /projects/{id}/eligibility/reference-data?kind=<kind>` with the CSV file as the body
(or as the `file` part of a form). Simple columns, in any order, headers in any case; common
names are understood too (Bank, Pin Code, Branch Name, Company Name ...). UTF-8 (as Excel's
"CSV UTF-8" saves it) or Windows-1252, separated by commas, semicolons or tabs, at most 4 MB.
A file with any bad row is refused with its problems listed, so a list is never half used.
A new upload replaces that kind's previous list; a list is deleted after 7 days (the app's
retention period) or with the project.

`pincode_serviceability`: where each lender lends. A lender listed here is serviceable at
exactly the pincodes marked serviceable (yes, y, true, 1 or blank); a pincode marked no, or
not listed, is not serviceable.

```csv
lender,pincode,serviceable
HDFC Bank,400606,yes
HDFC Bank,400603,no
Bajaj Finance,400615,yes
```

`lender_branches`: your lenders' branches. A lender listed here shows these branches instead
of the public or SAMPLE ones. Optional columns: address, city, district, state, ifsc.

```csv
lender,branch,pincode,address,city,ifsc
Bajaj Finance,Thane West,400606,"Shop 4, Station Road",Thane,
HDFC Bank,Thane West,400606,,Thane,HDFC0003959
```

`company_categories`: the category a company has with each lender (the lender and its
category must be in `app/data/lender_policies.json`).

```csv
lender,company,category
HDFC Bank,Example Tech Private Limited,CAT A
ICICI Bank,Example Tech Private Limited,CAT B
```

The lists hold lender, branch, pincode and company names only, no applicant data.

## How the data is built

From `packages/backend` (downloads both inputs unless they are given):

```sh
uv run python scripts/build_branch_data.py [--pincode-csv pincode.csv] [--ifsc-csv IFSC.csv]
```

The same inputs give byte-for-byte the same files (about 0.3 MB and 0.8 MB gzip, 1 MB and
3 MB of JSON once loaded). To update, change `IFSC_RELEASE` in the script, run it, and
update the numbers above.

`pincode_directory.json.gz`: `{"columns": ["office", "district", "lat", "lon", "approx"],
"states": [...], "districts": [[name, state index], ...], "pincodes": {"400606": ["Jekegram",
394, 19.206, 72.9669, 0], ...}}`. Some offices have wrong points (a typo, a copy of
the head office's, the sea, or degrees-minutes-seconds written as a decimal), and whole
postal divisions gave all their offices one point: 1,250 points are shared by the offices of
3 or more pincodes (12 Pune offices sit 55 km west of Pune, 9 Mumbai ones at Malabar Hill).
So a shared point is nobody's own, and a pincode's centre is the median of its offices' own
points that lie near the neighbouring pincodes (same first four digits, else three, else
the district), moved to its head and sub offices when they agree, or when the points do not
agree at all (the town, where the branches are). `approx` = 1 for the 1,611 pincodes with no
usable point of their own: they take their shared point when it lies near the neighbours,
else their district's centre, and the app marks distances to or from them as approximate.

`bank_branches.json.gz`: `{"columns": ["ifsc", "name", "address", "city", "pincode"],
"lenders": {"hdfc_bank": {"name": "HDFC Bank", "ifsc_prefix": "HDFC", "branches": [[...],
...]}, ...}}`, the lender ids of `app/data/lender_policies.json`. The pincode is the last
one in the published address that the directory knows (one in the branch's state first).
