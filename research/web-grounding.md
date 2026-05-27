# Web Research Grounding

Query: 
Fact-check the following specific claims about Singapore property-agent regulation and transactions. For EACH claim:

1. State the claim verbatim.
2. Verdict: **Correct / Incorrect / Partially correct / Unverified**.
3. The actual current value (with the unit / percentage / number / date).
4. Citation: exact source name (page or document title), publisher (CEA, IRAS, HDB, PDPC, MAS, SLA, AGC, MND), URL, and date the page was last updated or the source was published.
5. If you genuinely cannot find a primary-source citation, mark it **Unverified** and explain what you searched. **Do NOT invent or estimate numbers** — this fact-check exists precisely because the original draft is suspected of fabrication.

Prefer primary sources (cea.gov.sg, iras.gov.sg, hdb.gov.sg, pdpc.gov.sg, mas.gov.sg, sla.gov.sg, sso.agc.gov.sg). Use industry sources (PropertyGuru, SRX, Edge, Stacked, 99.co) only when corroborating something already said by a primary source, never as the sole reference.

Be especially careful that you are reporting CURRENT rules (as of late 2025 / early 2026), not pre-2023 cooling-measure values — Singapore property rules change often.

## Claims to check

1. There exists an "Anti-Money Laundering and Other Matters (Estate Agents and Developers) Act 2025" passed in Singapore Parliament. (Verify: does this Act exist under that exact name? What was its commencement date? What did it actually amend?)

2. CEA Practice Circular reference numbers "19-25" and "24-25" — do these exist? What are their actual titles and dates? List the actual most-recently-issued CEA Practice Circulars from 2024 and 2025 with their reference numbers, titles, and dates.

3. The CEA CPD framework will change from "6 credits per cycle" to "16 training hours per year" effective 1 January 2026, with a 12-hour structured + 4-hour self-directed split. (Is this correct? Cite the CEA announcement.)

4. Total annual CEA salesperson registration renewal fee is approximately S$283.50 (S$230 + S$53.50, including GST). State the actual current fee.

5. The HDB Flat Eligibility (HFE) letter is valid for 9 months from issuance. (My instinct says it's 6 months. State the actual validity period and cite HDB.)

6. For an HDB resale OTP, the option fee paid by the buyer is S$1 to S$1,000. (Verify the current upper bound and any minimum.)

7. For an HDB resale OTP, the maximum total deposit (option fee + exercise fee) the seller can collect is S$5,000. (Verify.)

8. For new uncompleted private residential property purchased from a developer, if the buyer does not exercise the OTP within the cooling-off period, the developer forfeits 25% of the booking fee (≈1.25% of purchase price). (Verify the percentage and the legal basis under the Housing Developers (Control and Licensing) Act / Sale of Commercial Properties Act / HDR Rules.)

9. **CRITICAL:** Current Additional Buyer's Stamp Duty (ABSD) rates as of 2025 — for each of: Singapore Citizens (1st / 2nd / 3rd+), PRs (1st / 2nd / 3rd+), Foreigners, Entities, and Trustees. Cite IRAS. (My note: the 27 Apr 2023 cooling measures took foreigners to 60% and PRs 2nd to 30%; verify that no further change has happened.)

10. Current Seller's Stamp Duty (SSD) rates for residential property — confirm tiers: 12% if sold within 1 year, 8% within 2 years, 4% within 3 years, 0% after 3 years. State whether the 3-year holding period is still current (not 4 years).

11. For Singapore PDPA Do Not Call (DNC) Registry checks: how often must telephone numbers be re-screened before a marketing call/SMS, and what is the validity of a DNC search result? (My note: I believe the PDPC's stated validity is 30 days, NOT 21 days as the original draft says.)

12. Stamp Duty filing deadlines under the Stamp Duties Act: 14 days for documents executed in Singapore, 30 days if executed overseas. (Verify.)

13. CEA record-keeping retention period for transaction records — confirm 5 years from the date of the transaction. Cite the regulation.

14. CEA prescribed Estate Agency Agreement forms — list the actual prescribed forms (form numbers and full titles) under the Estate Agents (Estate Agency Work) Regulations 2010. Specifically: are there forms 1-6 (sale/purchase/lease) or forms 1-8 (separating landlord-side and tenant-side leases)?

15. Foreigner restrictions on landed residential property — name the Act and the approving authority (Singapore Land Authority Land Dealings Approval Unit, LDAU). Confirm Sentosa Cove differences if any.

16. CEA disciplinary process — name the body (Disciplinary Committee under EAA), maximum financial penalties currently in force, and where complaints are lodged.

## Output format

Markdown. For each of the 16 items, use this template:

### Item N — [short label]

**Claim:** <verbatim claim>
**Verdict:** <Correct | Incorrect | Partially correct | Unverified>
**Actual value:** <the corrected fact>
**Source:** <document title>, <publisher>, <URL>, <last-updated date>
**Notes:** <any caveats — recent changes, transition periods, common misconceptions>

After all 16 items, produce a "Net summary" section listing which items in the original draft need fixing, ordered by severity (claims that would mislead a working agent ranked first).

Save the result to /home/dev/projects/roost/research/property-agent-factcheck.md. Be ruthless about flagging unverifiable claims as Unverified rather than guessing.


---

### Item 1 — AML Act 2025

**Claim:** There exists an "Anti-Money Laundering and Other Matters (Estate Agents and Developers) Act 2025" passed in Singapore Parliament.
**Verdict:** **Correct**
**Actual value:** The **Anti-Money Laundering and Other Matters (Estate Agents and Developers) Act 2025** was passed by Parliament on 8 April 2025 and came into operation on **1 July 2025**. It amended the *Estate Agents Act 2010*, the *Housing Developers (Control and Licensing) Act 1965*, and the *Sale of Commercial Properties Act 1979*.
**Source:** [Legislation](https://www.cea.gov.sg/legislation), CEA; [Anti-Money Laundering and Other Matters (Estate Agents and Developers) Act 2025](https://sso.agc.gov.sg/Acts-Supp/14-2025), AGC, 15 April 2025.
**Notes:** This Act specifically introduced "per contravention" penalties for AML/CFT breaches (up to S$100,000 for salespersons) and expanded the framework to include Proliferation Financing (PF).

---

### Item 2 — CEA Practice Circulars

**Claim:** CEA Practice Circular reference numbers "19-25" and "24-25" — do these exist?
**Verdict:** **Partially correct