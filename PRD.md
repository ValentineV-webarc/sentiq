# Product Requirements Document — SentIQ v1.0

| | |
|---|---|
| **Product** | SentIQ — Brand Sentiment Intelligence |
| **Author** | Valentine Virgo |
| **Status** | Launched |
| **Last updated** | April 2026 |
| **Target release** | v1.0 — live on Railway |

---

## 1. Summary

SentIQ is a web-based dashboard that lets marketers, analysts, and founders compare how the news media is covering two or more brands, in real time. It answers a single question: **"Is my brand winning or losing in the news right now, and why?"** — in under 30 seconds.

The product pulls recent articles from NewsAPI, classifies sentiment for each, runs a statistical significance test between brands, and produces a plain-English AI summary so non-technical users can act on the results.

---

## 2. Problem

Brand managers and marketing teams currently answer "how is my brand doing in the news?" in one of three ways, all of them bad:

1. **Manually skimming Google News** — slow, biased by what appears on the first page, and impossible to compare two brands side-by-side.
2. **Enterprise tools like Brandwatch or Meltwater** — comprehensive, but cost $10,000+ per year and require onboarding and training.
3. **Social listening tools** — focused on Twitter/Reddit, not news journalism, which is where the more authoritative narrative lives.

There's a clear gap in the middle: a tool that's fast, visual, comparison-focused, and accessible to a solo founder or small marketing team without a five-figure budget.

---

## 3. Target users

**Primary persona — "Priya the Startup Marketer"**
- Marketing lead at a 20–100 person B2B startup
- Tracks her company's public perception against 1–2 direct competitors
- Reports to a CMO or founder who wants "the 10-second read" each Monday
- Currently uses Google Alerts + a spreadsheet; hates it
- Doesn't have budget for Brandwatch; doesn't have time to learn Python

**Secondary persona — "Marcus the Analyst"**
- Equity research or market intelligence analyst
- Monitors media sentiment as a leading indicator for stock/industry moves
- Needs exportable data (CSV, PDF) to drop into broader reports
- Cares about statistical rigor — wants to know if a sentiment gap is real or noise

**Out of scope for v1**
- Enterprise users with compliance requirements (SSO, SOC2, audit logs)
- Agencies managing 50+ client brands
- Real-time crisis monitoring (sub-hourly alerting)

---

## 4. Goals & success metrics

### Primary goals (v1.0)

| Goal | Metric | Target |
|---|---|---|
| Users can run their first analysis without reading docs | Time from landing → first result | < 30 seconds |
| Output is understandable to non-analysts | "I understood the result at a glance" | 80% in user interviews |
| The product is shippable on a free-tier stack | Monthly infrastructure cost | < $5 |

### Secondary goals

- User returns within 7 days: > 25%
- Users who register an account: > 10% of total sessions
- At least 5 brand comparisons generated per registered user per month

### Non-goals

- Monetization — no pricing, no paywalls, no premium tier
- Integration with CRM/BI tools
- Beating Brandwatch on coverage depth or accuracy

---

## 5. Scope (v1.0)

### Must-have (MVP)

- [x] Enter 2+ brand names, trigger analysis
- [x] Pull articles from NewsAPI (7-day default window)
- [x] Classify sentiment per article (positive / negative / neutral)
- [x] KPI cards showing positive-sentiment % per brand
- [x] Bar chart of sentiment distribution
- [x] Daily stacked bar chart of coverage × sentiment
- [x] Welch's t-test for A/B significance
- [x] AI-generated plain-English insight summary (Groq LLaMA 3.1)
- [x] Article list with confidence scores and source links

### Should-have (v1.0)

- [x] User accounts (email + password) with search history
- [x] Email alerts when positive % drops below threshold
- [x] CSV and PDF export
- [x] Password reset flow

### Could-have (v1.1+)

- [ ] Up to 6 brands in a single comparison
- [ ] Historical trend view (past 3 months)
- [ ] Weekly digest email with top findings
- [ ] Brand-alias matching ("Apple" matches "AAPL", "iPhone maker")
- [ ] Source credibility weighting

### Won't-have (explicit non-scope)

- Twitter / Reddit / social sentiment
- Competitor intelligence beyond news
- Team accounts or sharing
- White-label / API access for other apps
- Multilingual coverage (English only in v1)

---

## 6. Design principles

1. **Honest over impressive.** If NewsAPI only returned 2 days of data, the dashboard says so ("limited coverage") rather than pretending to have 7. Users trusting the tool matters more than it looking rich.
2. **One screen, zero clicks to understanding.** No tabs, no drill-downs. The headline verdict, the statistics, and the trend are all visible at once.
3. **Plain English over statistics.** Everything the tool shows must make sense to someone who has never heard of a p-value. The numbers are there for those who want them.
4. **Default to action.** "Last 7 days" analysis runs with default settings — no configuration required.

---

## 7. Technical approach (summary)

| Component | Choice | Why |
|---|---|---|
| Backend | Flask (Python) | Fast to build, team is Python-native |
| Data source | NewsAPI free tier | Free, covers major English publishers, good enough for MVP |
| Sentiment | Rule-based keyword classifier | Zero inference cost, runs on any free tier, good enough for headlines |
| LLM summary | Groq LLaMA 3.1 8B | Free tier is generous, fast, sufficient quality |
| Frontend | Vanilla JS + Chart.js | No build step, faster to iterate, smaller deploy |
| Hosting | Railway | Free tier, automatic deploys from GitHub |

Full technical spec: see `README.md`.

---

## 8. Launch plan

### Phase 1 — Internal alpha (Week 1)
- Deploy to Railway on a sandbox URL
- Dogfood: run daily analyses on 3 real brand pairs
- Success: no crashes, no silent failures, no embarrassing AI insight hallucinations

### Phase 2 — Portfolio launch (Week 2)
- Push to public GitHub with full README
- Share live URL in portfolio / LinkedIn / job applications
- Success: first 5 external users run at least one analysis

### Phase 3 — Public beta (Week 4+)
- Submit to Product Hunt / Indie Hackers
- Gather structured feedback from 20+ users
- Success: identify the top 3 requested features for v1.1

---

## 9. Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| NewsAPI daily rate limit (100/day) exhausted during demo | High | High | Cache results per (brand, date) pair; upgrade to paid tier if usage scales |
| NewsAPI's recency bias makes older days look empty | Certain | Medium | Surface honestly via "limited coverage" warnings rather than hiding; plan GDELT integration for v1.1 |
| Groq API outage removes the AI Insight feature | Low | Low | App degrades gracefully — insight panel simply doesn't render if the API fails |
| Keyword-based sentiment misclassifies sarcasm / negation | Medium | Low | Acknowledge limitation in README; plan fine-tuned DistilBERT for v2 |
| User hits password-reset rate limit from Gmail SMTP | Low | Low | Gmail allows 500 emails/day; document upgrade path to SendGrid if needed |
| Free tier hosting can't handle traffic spike | Low | Medium | Railway auto-scales within free tier; paid upgrade is one click |

---

## 10. Open questions

1. Should we default to 7 days or 14 days as the analysis window? *(Currently 7. May revisit after user feedback.)*
2. Is "positive %" the right headline metric, or should we show a composite "sentiment score"? *(Currently positive %. Simpler to explain.)*
3. For the AI insight — should we add a "confidence" indicator so users know when the data is thin? *(Not in v1.0 — revisit based on feedback.)*

---

## 11. Appendix — metrics instrumentation

To measure success post-launch, the following events need to be tracked:

| Event | Trigger | Use |
|---|---|---|
| `analysis_run` | User clicks Run Analysis | Count daily active usage |
| `analysis_completed` | Results render successfully | Funnel step — detect silent failures |
| `export_clicked` | CSV or PDF button clicked | Identifies users getting real value |
| `alert_created` | User saves an alert threshold | Engagement indicator |
| `session_returned` | Returning user (7-day window) | Retention metric |

**Note:** v1.0 ships *without* this instrumentation to stay within scope. Event tracking is v1.1 — we need to know the product is useful before investing in measurement infrastructure.

---

## Change log

| Version | Date | Change |
|---|---|---|
| 1.0 | Apr 2026 | Initial launch — all must-have and should-have features shipped |
