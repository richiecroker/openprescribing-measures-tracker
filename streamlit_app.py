import streamlit as st
import requests
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from urllib.parse import urlparse
import os

# ----------------------------
# Page setup
# ----------------------------
st.set_page_config(layout="wide")
st.title("OpenPrescribing measures tracker")

# ----------------------------
# Helpers
# ----------------------------
def review_months(review_date):
    if not review_date:
        return pd.NA
    try:
        if isinstance(review_date, datetime):
            review_date = review_date.date()
        delta = relativedelta(review_date, datetime.now().date())
        months = delta.years * 12 + delta.months
        return max(int(months), 0)
    except Exception:
        return pd.NA

def row_css(months):
    if pd.isna(months):
        return ""
    m = int(months)
    if m <= 0:
        return "color:red;font-weight:bold;"
    elif m < 4:
        return "color:orange;font-weight:bold;"
    elif m < 6:
        return "color:green;font-weight:bold;"
    else:
        return "color:blue;font-weight:bold;"

def email_to_name(email):
    if not email or not isinstance(email, str):
        return ""
    local = email.split("@")[0]
    return " ".join(p.capitalize() for p in local.split(".") if p)

def measure_id_from_github_url(url):
    if not url:
        return None
    try:
        path = urlparse(url).path
        filename = os.path.basename(path)
        return os.path.splitext(filename)[0]
    except Exception:
        return None

# ----------------------------
# Plausible helper (FIXED)
# ----------------------------
def plausible_pageviews(measure_id, period, site_id, api_key):
    """
    Fetches pageviews for a specific measure page and all its subpages.
    Uses contains filter to match /{measure_id}/ and children.
    """
    if not measure_id:
        return None

    url = "https://plausible.io/api/v1/stats/aggregate"
    headers = {"Authorization": f"Bearer {api_key}"}

    params = {
        "site_id": site_id,
        "metrics": "pageviews",
        "period": period,
        "filters": f"event:page~=/{measure_id}/",
    }

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        value = r.json()["results"]["pageviews"]["value"]
        return int(float(value)) if value is not None else 0
    except Exception:
        return None

# ----------------------------
# Secrets
# ----------------------------
github_token = st.secrets.get("github_token")
plausible_api_key = st.secrets.get("plausible_api_key")
plausible_site_id = st.secrets.get("plausible_site_id")

if not github_token:
    st.error("Missing GitHub token")
    st.stop()

# ----------------------------
# Fetch measures from GitHub
# ----------------------------
headers = {"Authorization": f"token {github_token}"}
repo_url = (
    "https://api.github.com/repos/"
    "ebmdatalab/openprescribing/contents/"
    "openprescribing/measures/definitions"
)

res = requests.get(repo_url, headers=headers, timeout=15)
if res.status_code != 200:
    st.error("Failed to fetch measure definitions")
    st.stop()

rows = []
for item in res.json():
    if not item.get("name", "").endswith(".json"):
        continue

    github_url = item.get("html_url")
    measure_id = measure_id_from_github_url(github_url)

    try:
        data = requests.get(item["download_url"], timeout=10).json()
    except Exception:
        continue

    authored_by = data.get("authored_by", "")
    if isinstance(authored_by, list):
        authored_by = authored_by[0] if authored_by else ""

    checked_by = data.get("checked_by", "")
    if isinstance(checked_by, list):
        checked_by = checked_by[0] if checked_by else ""

    next_review = data.get("next_review")
    if isinstance(next_review, list):
        next_review = next_review[0]
    if isinstance(next_review, str):
        try:
            next_review = datetime.strptime(next_review, "%Y-%m-%d").date()
        except Exception:
            next_review = None

    rows.append({
        "measure_name": data.get("name", measure_id),
        "measure_id": measure_id,
        "github_url": github_url,
        "authored_by": email_to_name(authored_by),
        "checked_by": email_to_name(checked_by),
        "next_review": next_review,
        "next_review_months": review_months(next_review),
    })

df = pd.DataFrame(rows)

# ----------------------------
# Slider filter
# ----------------------------
valid_months = df["next_review_months"].dropna().astype(int)
if not valid_months.empty:
    min_m, max_m = valid_months.min(), valid_months.max()
    rng = st.slider("Months until review", min_m, max_m, (min_m, max_m))
    df = df[
        df["next_review_months"].notna()
        & (df["next_review_months"].astype(int) >= rng[0])
        & (df["next_review_months"].astype(int) <= rng[1])
    ]

# ----------------------------
# Plausible enrichment
# ----------------------------
if plausible_api_key and plausible_site_id:
    with st.spinner("Fetching Plausible pageviews…"):
        df["views_30d"] = df["measure_id"].apply(
            lambda m: plausible_pageviews(m, "30d", plausible_site_id, plausible_api_key)
        )
        df["views_12m"] = df["measure_id"].apply(
            lambda m: plausible_pageviews(m, "12mo", plausible_site_id, plausible_api_key)
        )
else:
    df["views_30d"] = None
    df["views_12m"] = None

# ----------------------------
# Render HTML table
# ----------------------------
cols = [
    ("measure_name", "Measure"),
    ("authored_by", "Authored by"),
    ("checked_by", "Checked by"),
    ("next_review", "Next review"),
    ("next_review_months", "Months to review"),
    ("views_30d", "Views (30d)"),
    ("views_12m", "Views (12m)"),
]

html = []
html.append("<tr>" + "".join(f"<th>{label}</th>" for _, label in cols) + "</tr>")

for _, r in df.iterrows():
    css = row_css(r["next_review_months"])
    link = (
        f'<a href="{r["github_url"]}" target="_blank" '
        f'style="color:inherit;text-decoration:underline;">'
        f'{r["measure_name"]}</a>'
    )
    html.append(
        "<tr>"
        f'<td style="{css}">{link}</td>'
        f'<td style="{css}">{r["authored_by"]}</td>'
        f'<td style="{css}">{r["checked_by"]}</td>'
        f'<td style="{css}">{r["next_review"] or ""}</td>'
        f'<td style="{css}">{"" if pd.isna(r["next_review_months"]) else int(r["next_review_months"])}</td>'
        f'<td style="{css}">{r["views_30d"] or ""}</td>'
        f'<td style="{css}">{r["views_12m"] or ""}</td>'
        "</tr>"
    )

st.markdown(
    f"""
    <div style="overflow-x:auto">
    <table style="border-collapse:collapse;width:100%">
    {''.join(html)}
    </table>
    </div>
    """,
    unsafe_allow_html=True,
)
