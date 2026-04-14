# SentIQ

SentIQ is a brand sentiment monitoring pipeline. It collects live news articles about a target brand, classifies each article as positive or negative using an AI model, runs a statistical comparison against a competitor, and outputs results to a Power BI dashboard.

The current demo tracks **MediaTek vs Snapdragon**. To monitor a different brand, update the `brands` list in the notebook — nothing else needs to change.

---

## Table of Contents

- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Running the Pipeline](#running-the-pipeline)
- [Dashboard](#dashboard)
- [Configuration](#configuration)
- [Known Limitations](#known-limitations)
- [Contributing](#contributing)

---

## Project Structure

```
sentiq/
├── sentiq_data_collection.ipynb   # Main pipeline — run this in Databricks
├── SentIQ_dashboard.pbix          # Power BI dashboard file
└── README.md
```

---

## Prerequisites

Before you start, make sure you have the following:

| Tool | Where to get it | Notes |
|---|---|---|
| Databricks account | [community.cloud.databricks.com](https://community.cloud.databricks.com) | Free Community Edition is sufficient |
| NewsAPI key | [newsapi.org/register](https://newsapi.org/register) | Free tier: 100 articles per query |
| Power BI Desktop | [powerbi.microsoft.com/desktop](https://powerbi.microsoft.com/desktop) | Free download |

---

## Setup

**1. Clone the repository**
```bash
git clone https://github.com/ValentineV-webarc/sentiq.git
```

**2. Import the notebook into Databricks**
- Go to **Workspace** → click the three dots (**...**) next to your username
- Select **Import** → upload `sentiq_data_collection.ipynb`

**3. Add your NewsAPI key**

Open the notebook and find this line in the Data Collection cell:
```python
API_KEY = 'YOUR_NEWSAPI_KEY_HERE'
```
Replace it with your actual key. Do not commit your key to GitHub.

**4. Install dependencies**

The first cell of the notebook handles this automatically:
```python
%pip install newsapi-python transformers torch
```

---

## Running the Pipeline

Run all cells in order using **Run → Run All** in Databricks.

The pipeline does the following in sequence:

| Step | What happens |
|---|---|
| Data Collection | Fetches up to 100 articles per brand from NewsAPI |
| Delta Storage | Saves raw articles to a Databricks Delta table (`sentiq_raw_news`) |
| SQL Analysis | Queries article counts, sources, and date range per brand |
| Sentiment Scoring | Runs each article through DistilBERT, labels as POSITIVE or NEGATIVE |
| KPI Summary | Calculates positive/negative percentage per brand |
| A/B Test | Runs an independent t-test to check if sentiment difference is significant |
| Visualisation | Plots sentiment distribution and daily trend |
| Export | Saves three CSV files to your Databricks workspace for Power BI |

**Expected output files** (saved to `/Workspace/Users/<your-email>/`):
- `sentiq_sentiment_results.csv` — full article-level data with sentiment labels
- `sentiq_summary.csv` — aggregated sentiment counts and percentages per brand
- `sentiq_trend_pct.csv` — daily average sentiment score per brand (as percentage)

---

## Dashboard

Open `SentIQ_dashboard.pbix` in Power BI Desktop.

If loading for the first time, go to **Home → Get data → Text/CSV** and load each of the three CSV files exported above. The dashboard contains:

- **Sentiment trend** — daily positive sentiment rate over time per brand
- **Sentiment distribution** — positive vs negative article count per brand
- **Article table** — raw headlines with brand, sentiment label, and source

> **Note:** When loading CSVs in Power BI, set File Origin to `1252: Western European (Windows)` to ensure decimal values are read correctly.

---

## Configuration

To monitor different brands, update this line in the notebook:

```python
brands = ['MediaTek', 'Snapdragon']
```

Any two brand names work. The rest of the pipeline — sentiment scoring, A/B test, visualisation, and export — runs without any other changes.

---

## Known Limitations

- **Data volume:** NewsAPI free tier caps at 100 articles per brand per request and covers only the past 30 days. For longer time windows or higher volume, a paid NewsAPI plan or additional data sources (Reddit API, financial news feeds) would be needed.
- **Sentiment model:** DistilBERT is a general-purpose model trained on movie reviews. It performs reasonably on tech news but a domain-specific fine-tuned model would improve accuracy.
- **A/B test scope:** The t-test compares overall positive rates between brands. It does not account for time effects or source bias. A time-sliced experiment (e.g. sentiment before vs after a product launch) would enable stronger causal conclusions.

---

## Contributing

1. Create a new branch from `main`
2. Make your changes
3. Open a pull request with a clear description of what you changed and why
4. Do not commit API keys or credentials — use environment variables or Databricks secrets in production
