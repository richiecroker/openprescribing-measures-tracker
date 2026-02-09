import streamlit as st
import requests
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from urllib.parse import urlparse
import os
import json
from typing import Optional

# ----------------------------
# Page setup
# ----------------------------
st.set_page_config(layout="wide")
st.title("OpenPrescribing measures tracker — Plausible (contains /id)")

# ----------------------------
# Constants
# ----------------------------
PLAUSIBLE_AGG_URL = "https://plausible.io/api/v1/stats/aggregate"
GITHUB_DEFINITIONS_API = (
    "https://api.github.com/repos/"
    "ebmdatalab/openprescribing/contents/"
    "openprescribing/measures/definitions"
)

# ----------------------------
# Helpers
# ----------------------------
def review_months(review_date):
    """Return integer months from now until review_date, or pd.NA."""
    if not review_date:
        return pd.NA
    try:
        if isinstance(review_date, datetime):
            review_date = review_date.date()
        now = datetime.now().date()
        delta = relativedelta(review_date, now)
        months = delta.years * 12 + delta.months
        return max(int(months), 0)
    except Exception:
        return pd.NA

def row_css(months):
    """Return inline CSS for row based on months to review."""
    if pd.isna(months):
        return ""
    try:
        m = int(months)
    except Exception:
        return ""
    if m <= 0:
        return "color: red; font-weight: bold;"
    if m < 4:
        return "color: orange; font-weight: bold;"
    if m < 6:
        return "color: green; font-weight: bold;"
    return "color: blue; font-weight: bold;"

def email_to_name(email):
    """Convert 'local.part@...' into 'Local Part' or return '' if missing."""
    if not email or not isinstance(email, str):
        return ""
    local = email.split("@")[0]
    parts = [p for p in local.split(".") if p]
    return " ".join(p.capitalize() for p in parts)

def measure_id_from_github_url(url: str) -> Optional[str]:
    """Extract the filename without extension from a GitHub html_url or download_url."""
    if not url or not isinstance(url, str):
        return None
    try:
        path = urlparse(url).path
        filename = os.path.basename(path)
        measure_id, _ = os.path.splitext(filename)
        return measure_id or None
    except Exception:
        return None

# ----------------------------
# Plausible querying utilities (cached)
# ----------------------------
plausible_api_key = st.secrets.get("plausible_api_key")
plausible_site_id = st.secrets.get("plausible_site_id")

@st.cache_data(ttl=3600)
def plausible_query(site_id: str, api_key: str, params: dict):
    """
    Low-level cached GET to Plausible aggregate endpoint.
    Returns parsed JSON or None on failure.
    """
    if not site_id or not api_key:
        return None
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        r = requests.get(PLAUSIBLE_AGG_URL, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None

@st.cache_data(ttl=3600)
def plausible_pageviews_contains(measure_id: str, period: str, site_id: str, api_key: str) -> Optional[int]:
    """
    Query Plausible using CONTAINS scoped by leading slash.
    Tries in order:
      1) contains token "/{measure_id}"  (matches /steve, /steve/, /foo/steve/bar)
      2) contains token "/{measure_id}/" (explicit trailing slash)
      3) optional exact fallback: event:page==/measure/{measure_id}/
    Returns integer pageviews or None on failure/no-data.
    """
    if not measure_id or not site_id or not api_key:
        return None

    # tokens to try (leading slash first — matches "/steve" and "/foo/steve")
    tokens = [f"/{measure_id}", f"/{measure_id}/"]

    for token in tokens:
        params = {
            "site_id": site_id,
            "metrics": "pageviews",
            "period": period,
            "filters": f"event:page~={token}",
        }
        payload = plausible_query(site_id, api_key, params)
        if payload:
            # safe navigation
            val = payload.get("results", {}).get("pageviews", {}).get("value")
            if val is not None:
                try:
                    return int(float(val))
                except Exception:
                    return None
    # fallback to exact match (useful if your site canonical paths always include /measure/<id>/)
    exact_path = f"/measure/{measure_id}/"
    params = {
        "site_id": site_id,
        "metrics": "pageviews",
        "period": period,
        "filters": f"event:page=={exact_path}",
    }
    payload = plausible_query(site_id, api_key, params)
    if payload:
        val = payload.get("results", {}).get("pageviews", {}).get("value")
        if val is not None:
            try:
                return int(float(val))
            except Exception:
                return None
    return None

def plausible_raw_for_debug(measure_id: str, site_id: str, api_key: str):
    """
    Non-cached raw request for debug display (used sparingly).
    Returns tuple (params, status_code, parsed_json_or_text) or exception text.
    """
    if not measure_id or not site_id or not api_key:
        return {"error": "missing credentials or measure_id"}

    # show the exact params we use (exact-match example)
    exact_path = f"/measure/{measure_id}/"
    params = {
        "site_id": site_id,
        "metrics": "pageviews",
        "period": "30d",
        "filters": f"event:page=={exact_path}",
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        r = requests.get(PLAUSIBLE_AGG_URL, headers=headers, params=params, timeout=10)
        try:
            parsed = r.json()
        except Exception:
            parsed = {"text": r.text}
        return {"params": params, "status_code": r.status_code, "response": parsed}
    except Exception as ex:
        return {"error": str(ex)}

# ----------------------------
# User controls
# ----------------------------
debug = st.checkbox("Show Plausible debug info (one sample request)", value=False)
# Option to choose whether to use contains (leading slash) or exact path priority
contains_first = st.checkbox("Prefer contains-style matching (leading slash) over exact match", value=True)

# ----------------------------
# Get GitHub token
# ----------------------------
github_token = st.secrets.get("github_token")
if not github_token:
    st.error("Missing GitHub token in Streamlit secrets (github_token).")
    st.stop()

# ----------------------------
# Fetch measure definitions from GitHub
# ----------------------------
with st.spinner("Fetching measure definitions from GitHub…"):
    try:
        headers = {"Authorization": f"token {github_token}"}
        r = requests.get(GITHUB_DEFINITIONS_API, headers=headers, timeout=20)
        r.raise_for_status()
        items = r.json()
    except Exception as ex:
        st.error(f"Error fetching from GitHub API: {ex}")
        st.stop()

rows = []
for item in items:
    # only process json files (def files)
    name = item.get("name", "")
    if not name.endswith(".json"):
        continue

    github_html = item.get("html_url")
    download_url = item.get("download_url")
    # get measure_id from filename
    measure_id = measure_id_from_github_url(github_html) or measure_id_from_github_url(download_url)

    # fetch file JSON
    try:
        file_json = requests.get(download_url, timeout=10).json()
    except Exception:
        # skip problematic file
        continue

    authored_by = file_json.get("authored_by", "")
    if isinstance(authored_by, list):
        authored_by = authored_by[0] if authored_by else ""
    checked_by = file_json.get("checked_by", "")
    if isinstance(checked_by, list):
        checked_by = checked_by[0] if checked_by else ""

    next_review = file_json.get("next_review", None)
    if isinstance(next_review, list) and next_review:
        next_review = next_review[0]
    if isinstance(next_review, str):
        try:
            next_review = datetime.strptime(next_review, "%Y-%m-%d").date()
        except Exception:
            next_review = None
    elif isinstance(next_review, datetime):
        next_review = next_review.date()

    rows.append({
        "measure_name": file_json.get("name") or measure_id or name.replace(".json", ""),
        "measure_id": measure_id,
        "github_url": github_html,
        "authored_by": email_to_name(authored_by),
        "checked_by": email_to_name(checked_by),
        "next_review": next_review,
        "next_review_months": review_months(next_review),
    })

df = pd.DataFrame(rows)

if df.empty:
    st.info("No measures found in repository.")
    st.stop()

# ----------------------------
# Slider filter for months until review (works only on numeric months)
# ----------------------------
valid_months = df["next_review_months"].dropna().astype(int) if "next_review_months" in df.columns else pd.Series(dtype=int)
if not valid_months.empty:
    min_m, max_m = int(valid_months.min()), int(valid_months.max())
    months_filter = st.slider("Select range: months until review", min_m, max_m, (min_m, max_m))
    df = df[
        df["next_review_months"].notna()
        & (df["next_review_months"].astype(int) >= months_filter[0])
        & (df["next_review_months"].astype(int) <= months_filter[1])
    ]

# ----------------------------
# Enrich with Plausible counts using contains ("/{id}" token)
# ----------------------------
if plausible_api_key and plausible_site_id:
    with st.spinner("Fetching Plausible pageviews (cached)…"):
        views_30 = []
        views_12 = []
        # We'll collect a sample debug payload for the first measure if debug=True
        sample_debug = None

        for i, row in df.iterrows():
            mid = row.get("measure_id")
            if not mid:
                views_30.append(None)
                views_12.append(None)
                continue

            # Primary behaviour: prefer contains vs exact as per user checkbox
            if contains_first:
                v30 = plausible_pageviews_contains(mid, "30d", plausible_site_id, plausible_api_key)
                v12 = plausible_pageviews_contains(mid, "12mo", plausible_site_id, plausible_api_key)
            else:
                # try exact first (useful if site always uses /measure/<id>/) then contains fallback
                v30 = None
                v12 = None
                # exact attempt
                exact_params_30 = {"site_id": plausible_site_id, "metrics": "pageviews", "period": "30d", "filters": f"event:page==/measure/{mid}/"}
                exact_params_12 = {"site_id": plausible_site_id, "metrics": "pageviews", "period": "12mo", "filters": f"event:page==/measure/{mid}/"}
                exact_30 = plausible_query(plausible_site_id, plausible_api_key, exact_params_30)
                exact_12 = plausible_query(plausible_site_id, plausible_api_key, exact_params_12)
                if exact_30 and exact_30.get("results", {}).get("pageviews", {}).get("value") is not None:
                    try:
                        v30 = int(float(exact_30["results"]["pageviews"]["value"]))
                    except Exception:
                        v30 = None
                if exact_12 and exact_12.get("results", {}).get("pageviews", {}).get("value") is not None:
                    try:
                        v12 = int(float(exact_12["results"]["pageviews"]["value"]))
                    except Exception:
                        v12 = None

                # fallback to contains
                if v30 is None:
                    v30 = plausible_pageviews_contains(mid, "30d", plausible_site_id, plausible_api_key)
                if v12 is None:
                    v12 = plausible_pageviews_contains(mid, "12mo", plausible_site_id, plausible_api_key)

            views_30.append(v30)
            views_12.append(v12)

            if debug and sample_debug is None:
                sample_debug = plausible_raw_for_debug(mid, plausible_site_id, plausible_api_key)

        df["views_30d"] = views_30
        df["views_12m"] = views_12

        if debug and sample_debug:
            st.subheader("Plausible debug (sample raw request)")
            st.json(sample_debug)
else:
    st.info("Plausible credentials not found in secrets — views columns will be empty.")
    df["views_30d"] = None
    df["views_12m"] = None

# ----------------------------
# Render HTML table (clickable measure_name inherits row colour)
# ----------------------------
columns_to_show = [
    ("measure_name", "Measure"),
    ("authored_by", "Authored by"),
    ("checked_by", "Checked by"),
    ("next_review", "Next review"),
    ("next_review_months", "Months to review"),
    ("views_30d", "Views (30d)"),
    ("views_12m", "Views (12m)"),
]

# Build header row
header_cells = "".join(
    f"<th style='text-align:left; padding:8px 12px; border-bottom:1px solid #ddd'>{label}</th>"
    for _, label in columns_to_show
)
html_rows = [f"<tr>{header_cells}</tr>"]

# Rows
for _, row in df.iterrows():
    css = row_css(row.get("next_review_months"))
    # format next_review
    nr = row.get("next_review")
    nr_text = ""
    if pd.notna(nr) and nr is not None:
        try:
            nr_text = nr.strftime("%Y-%m-%d")
        except Exception:
            nr_text = str(nr)

    measure_html = (
        f'<a href="{row.get("github_url", "#")}" target="_blank" rel="noopener noreferrer" '
        f'style="color: inherit; text-decoration: underline;">'
        f'{row.get("measure_name", "")}</a>'
    )

    values = [
        measure_html,
        row.get("authored_by", "") or "",
        row.get("checked_by", "") or "",
        nr_text,
        "" if pd.isna(row.get("next_review_months")) else str(int(row.get("next_review_months"))),
        "" if row.get("views_30d") is None else str(row.get("views_30d")),
        "" if row.get("views_12m") is None else str(row.get("views_12m")),
    ]

    row_cells = "".join(
        f"<td style='padding:8px 12px; border-bottom:1px solid #f2f2f2; {css}'>{v}</td>"
        for v in values
    )
    html_rows.append(f"<tr>{row_cells}</tr>")

html_table = f"""
<div style="overflow-x:auto;">
<table style="border-collapse:collapse; width:100%; font-family:Arial, sans-serif;">
{''.join(html_rows)}
</table>
</div>
"""

st.markdown(html_table, unsafe_allow_html=True)
