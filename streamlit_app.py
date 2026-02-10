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
# Plausible helpers
# ----------------------------
def plausible_pageviews(measure_id, period, site_id, api_key):
    """
    Fetches pageviews for pages containing the measure_id in the path using Plausible API v2.
    Matches patterns like /measure/{measure_id}/, /pcn/XXX/{measure_id}/, etc.
    """
    if not measure_id:
        return None

    url = "https://plausible.io/api/v2/query"
    headers = {"Authorization": f"Bearer {api_key}"}

    payload = {
        "site_id": site_id,
        "metrics": ["pageviews"],
        "date_range": period,
        "filters": [
            ["contains", "event:page", [f"/{measure_id}/"]]
        ]
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        response = r.json()
        result = response["results"][0]["metrics"][0] if response.get("results") else 0
        return int(result) if result is not None else 0
    except Exception:
        return None

def plausible_pageviews_pattern(prefix, period, site_id, api_key):
    """Fetch pageviews for URLs matching prefix AND containing /measures/"""
    url = "https://plausible.io/api/v2/query"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "site_id": site_id,
        "metrics": ["pageviews"],
        "date_range": period,
        "filters": [
            ["contains", "event:page", [prefix]],
            ["contains", "event:page", ["/measures/"]],
        ]
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        response = r.json()
        result = response["results"][0]["metrics"][0] if response.get("results") else 0
        return int(result) if result is not None else 0
    except Exception:
        return None

# ----------------------------
# Cached pageviews fetchers
# ----------------------------
@st.cache_data(ttl=2592000)  # Cache for 30 days
def fetch_all_pageviews(measure_ids, site_id, api_key):
    """
    Fetch pageviews for all measures. Cached for 30 days.
    Returns a dict with measure_id as key and tuple of (views_30d, views_12m) as value.
    """
    results = {}
    for measure_id in measure_ids:
        views_30d = plausible_pageviews(measure_id, "30d", site_id, api_key)
        views_12m = plausible_pageviews(measure_id, "12mo", site_id, api_key)
        results[measure_id] = (views_30d, views_12m)
    return results

ORG_TYPES = ["practice", "pcn", "sicbl", "icb", "regional-team", "england"]

@st.cache_data(ttl=2592000)  # Cache for 30 days
def fetch_orgtypes_pageviews(site_id, api_key):
    """
    Fetch pageviews for /{org_type}/{org}/measures/ URL patterns, grouped by org_type.
    Cached for 30 days.
    """
    results = {}
    for org_type in ORG_TYPES:
        views_30d = plausible_pageviews_pattern(f"/{org_type}/", "30d", site_id, api_key)
        views_12m = plausible_pageviews_pattern(f"/{org_type}/", "12mo", site_id, api_key)
        results[org_type] = (views_30d, views_12m)
    return results

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
# Plausible enrichment (CACHED)
# ----------------------------
if plausible_api_key and plausible_site_id:
    with st.spinner("Fetching Plausible pageviews…"):
        pageviews_dict = fetch_all_pageviews(
            df["measure_id"].tolist(),
            plausible_site_id,
            plausible_api_key
        )

        df["views_30d"] = df["measure_id"].apply(lambda m: int(pageviews_dict.get(m, (0, 0))[0]) if pageviews_dict.get(m, (0, 0))[0] is not None else None)
        df["views_12m"] = df["measure_id"].apply(lambda m: int(pageviews_dict.get(m, (0, 0))[1]) if pageviews_dict.get(m, (0, 0))[1] is not None else None)
else:
    df["views_30d"] = None
    df["views_12m"] = None

# ----------------------------
# Sort controls
# ----------------------------
sort_col = st.selectbox(
    "Sort by",
    options=["next_review_months", "measure_name", "authored_by", "checked_by", "views_30d", "views_12m"],
    format_func=lambda x: {
        "next_review_months": "Months to review",
        "measure_name": "Measure name",
        "authored_by": "Authored by",
        "checked_by": "Checked by",
        "views_30d": "Views (30d)",
        "views_12m": "Views (12m)"
    }[x],
    index=0
)

sort_order = st.radio("Order", options=["Ascending", "Descending"], horizontal=True)

df = df.sort_values(
    by=sort_col,
    ascending=(sort_order == "Ascending"),
    na_position="last"
)

# ----------------------------
# Display total pageviews + org_type breakdown
# ----------------------------
total_views_30d = df["views_30d"].sum() if "views_30d" in df.columns else 0
total_views_12m = df["views_12m"].sum() if "views_12m" in df.columns else 0

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Measures", len(df))
with col2:
    st.metric("Total Views (30 days)", f"{int(total_views_30d):,}" if pd.notna(total_views_30d) else "N/A")
with col3:
    st.metric("Total Views (12 months)", f"{int(total_views_12m):,}" if pd.notna(total_views_12m) else "N/A")

if plausible_api_key and plausible_site_id:
    with st.spinner("Fetching org-type pageviews…"):
        orgtype_views = fetch_orgtypes_pageviews(plausible_site_id, plausible_api_key)

    org_rows = []
    for org_type in ORG_TYPES:
        v30, v12 = orgtype_views.get(org_type, (None, None))
        org_rows.append(
            f"<tr>"
            f"<td><strong>{org_type}</strong></td>"
            f"<td>{f'{v30:,}' if v30 is not None else 'N/A'}</td>"
            f"<td>{f'{v12:,}' if v12 is not None else 'N/A'}</td>"
            f"</tr>"
        )

    st.markdown(
        f"""
        <table style="border-collapse:collapse;margin-bottom:1rem;">
        <tr><th style="text-align:left;padding-right:2rem;">Org type</th>
            <th style="text-align:right;padding-right:2rem;">Views (30d)</th>
            <th style="text-align:right;">Views (12m)</th></tr>
        {''.join(org_rows)}
        </table>
        """,
        unsafe_allow_html=True,
    )

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
        f'<td style="{css}">{int(r["views_30d"]) if pd.notna(r["views_30d"]) else ""}</td>'
        f'<td style="{css}">{int(r["views_12m"]) if pd.notna(r["views_12m"]) else ""}</td>'
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
