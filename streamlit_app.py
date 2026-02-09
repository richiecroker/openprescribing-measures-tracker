import streamlit as st
import requests
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from urllib.parse import urlparse
import os

# ----------------------------
# Page setup (UNCHANGED)
# ----------------------------
st.set_page_config(layout="wide")
st.title("OpenPrescribing measures tracker")

# ----------------------------
# Helpers (UNCHANGED)
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
# Plausible helper (WITH DEBUG)
# ----------------------------
def plausible_pageviews(measure_id, period, site_id, api_key):
    """
    Uses CONTAINS filter with leading slash: event:page~=/steve
    This avoids collisions and prevents identical counts.
    """
    if not measure_id:
        return None

    url = "https://plausible.io/api/v1/stats/aggregate"
    headers = {"Authorization": f"Bearer {api_key}"}

    params = {
        "site_id": site_id,
        "metrics": "pageviews",
        "period": period,
        "filters": f"event:page~=/{measure_id}",
    }

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        value = r.json()["results"]["pageviews"]["value"]
        result = int(float(value)) if value is not None else 0
        st.write(f"DEBUG: measure_id={measure_id}, period={period}, result={result}")
        return result
    except Exception:
        return None

# ----------------------------
# Secrets (UNCHANGED)
# ----------------------------
github_token = st.secrets.get("github_token")
plausible_api_key = st.secrets.get("plausible_api_key")
plausible_site_id = st.secrets.get("plausible_site_id")

if not github_token:
    st.error("Missing GitHub token")
    st.stop()

# ----------------------------
# Fetch measures from GitHub (UNCHANGED)
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
