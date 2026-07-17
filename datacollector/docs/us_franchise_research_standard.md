# U.S. franchise research standard for Planner v1

Checked on 2026-07-17. This document describes the research contract, not legal,
tax or investment advice.

## Scope

The federal FTC Franchise Rule requires a disclosure document with 23 specified
Items for covered U.S. franchise offers. It does not make those requirements
Polish law. For a Polish brand, this project uses the Items as a comparative
due-diligence standard and separately asks what Polish/EU rules apply.

The Planner must first identify the exact brand, legal franchisor, transaction
type, target country/regions, potentially applicable jurisdictions, document
versions and dates. It must not decide legal applicability itself.

## Formal FDD coverage

| Item | Information to collect |
| --- | --- |
| 1 | Franchisor, parents, predecessors and affiliates; structure, addresses, history, market, competition and industry rules |
| 2 | Covered managers and complete five-year employment histories with roles, employers, locations and dates |
| 3 | Required pending/recent proceedings, ten-year convictions/pleas, currently effective orders, case details and outcomes |
| 4 | Ten-year bankruptcy history for the full covered entity/person set, including the other-entity principal-officer/general-partner rule |
| 5 | Initial fees, calculation, timing, uniformity and refundability |
| 6 | Ongoing and event-driven fees, including royalty, marketing, IT, transfer, renewal, audit, remodeling and default fees |
| 7 | Low/high initial investment by line item, working-capital period, payment terms and estimation basis |
| 8 | Required purchases/suppliers, approval rules, affiliate interests, rebates and franchisor supplier income |
| 9 | Cross-referenced franchisee obligation matrix |
| 10 | Franchisor/affiliate-arranged financing and all material terms |
| 11 | Pre-opening and ongoing assistance, advertising funds, IT/data access, manual and training program |
| 12 | Location, territory, exclusivity, performance conditions, relocation and channel conflict |
| 13 | Principal trademarks, registrations, ownership, restrictions, disputes and defense |
| 14 | Patents, copyrights, software, know-how, trade secrets and confidentiality |
| 15 | Required owner participation and manager qualifications |
| 16 | Restrictions on products, services, customers and sales channels |
| 17 | Term, renewal, termination, cure, transfer, purchase rights, post-exit obligations, non-compete and dispute resolution |
| 18 | Public-figure endorsement, compensation, investment and management role |
| 19 | Financial-performance claim status, exact claims, population/sample, geography, attainment, assumptions, limits and substantiation |
| 20 | Three-year systemwide counts; openings, transfers, terminations, non-renewals, reacquisitions and closures; projections and franchisee references |
| 21 | Financial statement periods, audit status/opinion, guarantees, qualifications and startup phase-in |
| 22 | Every proposed agreement and cross-document conflicts |
| 23 | Issue and receipt metadata, exhibits, sellers and delivery evidence |

The canonical, field-level questions and evidence criteria live in
[`franchise_research_v1.yaml`](../catalogs/franchise_research_v1.yaml).

## U.S. process controls

For a U.S. target, Planner adds separate tasks for:

- current federal definition and any claimed exemption;
- current delivery, material-change, update and record-retention timing;
- state registration/notice/exemption, effective dates, addenda, seller and
  advertising filings, financial assurances and relationship-law protections;
- SBA Franchise Directory status when financing is relevant.

As of the check date, the federal baseline includes delivery at least 14 calendar
days before a binding agreement or payment, a separate seven-day review period
for specified unilateral material changes to the proposed agreement, an annual
update within 120 days after fiscal year-end, quarterly material-change updates,
and retention of disclosure/receipt records. Planner deliberately asks Searcher
to re-read the current rule instead of freezing changing requirements in code.
Receipt retention and the three-year retention of every materially different
disclosure version are tracked as separate controls.

Federal compliance and state compliance are separate conclusions. Directory
presence means SBA financing eligibility, not endorsement or probable success.

## Independent due diligence

FDD coverage alone is not the finish condition. Risk and unit depths add:

- target-market demand, saturation, pricing and competition;
- comparable unit economics and downside cases;
- non-cherry-picked current/former franchisee interviews;
- complaints, enforcement, reputable investigations and company responses;
- franchisor revenue incentives, financial health and support capacity;
- location-specific tax, permits, zoning, health/safety and employment questions;
- verified locations and, only through authorized methods, review signals.

## Evidence contract

Every future fact must carry:

```text
value + unit/currency + as_of date + source URL/file + publisher/source type
+ publication/effective date + collected_at + excerpt/document location
+ source reliability + claim confidence + verification status
```

Allowed absence/conflict states are `not_applicable`, `not_disclosed`,
`not_found`, and `conflicting`. An official source is authoritative for what the
company declares, but not automatically independent proof that the declaration
is true.

## Primary sources

- [16 CFR Part 436](https://www.ecfr.gov/current/title-16/chapter-I/subchapter-D/part-436)
- [FTC Franchise Rule](https://www.ftc.gov/legal-library/browse/rules/franchise-rule)
- [FTC Franchise Rule Compliance Guide](https://www.ftc.gov/business-guidance/resources/franchise-rule-compliance-guide)
- [FTC Consumer’s Guide to Buying a Franchise](https://www.ftc.gov/business-guidance/resources/consumers-guide-buying-franchise)
- [SBA: Buy an existing business or franchise](https://www.sba.gov/business-guide/plan-your-business/buy-existing-business-or-franchise)
- [SBA Franchise Directory](https://www.sba.gov/document/support-sba-franchise-directory)
- [SBA SOP 50 10](https://www.sba.gov/document/sop-50-10-lender-development-company-loan-programs)

For a U.S. state, follow the current statute and regulator instructions for that
state. NASAA can be used as an index, but not as a substitute for primary law.
