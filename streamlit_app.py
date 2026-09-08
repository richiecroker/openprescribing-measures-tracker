import streamlit as st
import requests
import pandas as pd
import re
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

# ----------------------------
# Page setup
# ----------------------------
st.set_page_config(layout="wide")
st.title("OpenPrescribing measures tracker")

#calculate date range for Plausible 
today = date.today()
year_ago = today.replace(year=today.year - 1)
last_12m = [str(year_ago), str(today)]

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
# Link-checking helpers
# ----------------------------
HREF_RE = re.compile(r"href\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)

def extract_hrefs(value):
    """Yield every href='...' URL found in a string or list of strings."""
    if isinstance(value, str):
        yield from HREF_RE.findall(value)
    elif isinstance(value, list):
        for v in value:
            yield from extract_hrefs(v)

LINK_CHECK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

def _looks_like_bot_block(resp):
    """Heuristic: is this a Cloudflare/WAF challenge page rather than a real 4xx/5xx?"""
    if resp.status_code not in (403, 503):
        return False
    server = resp.headers.get("Server", "").lower()
    if "cloudflare" in server:
        return True
    if "cf-mitigated" in resp.headers or "cf-ray" in resp.headers:
        return True
    return False

@st.cache_data(ttl=86400, show_spinner=False)
def check_url(url, timeout=10.0):
    """Check a single URL. Cached for 24h so reruns/filters don't re-hit the network."""
    headers = LINK_CHECK_HEADERS
    try:
        resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400 or resp.status_code == 405:
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, stream=True)
        if _looks_like_bot_block(resp):
            return ("BLOCKED", "Bot/Cloudflare protection - verify manually")
        return (str(resp.status_code), "OK" if resp.status_code < 400 else "Error")
    except requests.exceptions.SSLError as e:
        return ("SSL_ERROR", str(e))
    except requests.exceptions.ConnectionError as e:
        return ("CONN_ERROR", str(e))
    except requests.exceptions.Timeout:
        return ("TIMEOUT", "Request timed out")
    except requests.exceptions.RequestException as e:
        return ("REQUEST_ERROR", str(e))

def check_urls(urls, max_workers=10):
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(check_url, u): u for u in urls}
        for future in as_completed(future_to_url):
            u = future_to_url[future]
            try:
                results[u] = future.result()
            except Exception as e:
                results[u] = ("UNKNOWN_ERROR", str(e))
    return results

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

def plausible_pageviews_pattern(prefix, period, site_id, api_key, exact=False):
    """Fetch pageviews for URLs matching prefix AND containing /measures/"""
    url = "https://plausible.io/api/v2/query"
    headers = {"Authorization": f"Bearer {api_key}"}
    filter_op = "is" if exact else "contains"
    filters = [[filter_op, "event:page", [prefix]]]
    if not exact:
        filters.append(["contains", "event:page", ["/measures/"]])
    payload = {
        "site_id": site_id,
        "metrics": ["pageviews"],
        "date_range": period,
        "filters": filters,
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
    #calculate date range for Plausible 
    today = date.today()
    year_ago = today.replace(year=today.year - 1)
    date_range = [str(year_ago), str(today)]
    
    results = {}
    for measure_id in measure_ids:
        views_30d = plausible_pageviews(measure_id, "30d", site_id, api_key)
        views_12m = plausible_pageviews(measure_id, last_12m, site_id, api_key)
        results[measure_id] = (views_30d, views_12m)
    return results

ORG_TYPES = ["practice", "pcn", "sicbl", "icb", "regional-team", "national/england"]

@st.cache_data(ttl=2592000)  # Cache for 30 days
def fetch_orgtypes_pageviews(site_id, api_key):
    """
    Fetch pageviews for /{org_type}/{org}/measures/ URL patterns, grouped by org_type.
    national/england is matched exactly since it has no /measures/ subpath.
    Cached for 30 days.
    """
    results = {}
    for org_type in ORG_TYPES:
        if org_type == "national/england":
            views_30d = plausible_pageviews_pattern("/national/england/", "30d", site_id, api_key, exact=True)
            views_12m = plausible_pageviews_pattern("/national/england/", last_12m, site_id, api_key, exact=True)
        else:
            views_30d = plausible_pageviews_pattern(f"/{org_type}/", "30d", site_id, api_key)
            views_12m = plausible_pageviews_pattern(f"/{org_type}/", last_12m, site_id, api_key)
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
    st.error(f"Failed to fetch measure definitions: {res.status_code} — {res.text}")
    st.stop()
    
rows = []
link_hits = {}  # url -> set of measure names it was found in
measure_links = {}  # measure name -> list of urls found in its why_it_matters

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

    measure_name = data.get("name", measure_id)

    # Pull out any href='...' links from the why_it_matters field so we can
    # check them below.
    for url in extract_hrefs(data.get("why_it_matters")):
        url = url.strip()
        link_hits.setdefault(url, set()).add(measure_name)
        measure_links.setdefault(measure_name, []).append(url)

    rows.append({
        "measure_name": measure_name,
        "measure_id": measure_id,
        "github_url": github_url,
        "authored_by": email_to_name(authored_by),
        "checked_by": email_to_name(checked_by),
        "next_review": next_review,
        "next_review_months": review_months(next_review),
    })

df = pd.DataFrame(rows)

# ----------------------------
# Check links (needed for the broken/unverified count columns and filter below)
# ----------------------------
if link_hits:
    with st.spinner(f"Checking {len(link_hits)} link(s)…"):
        link_results = check_urls(list(link_hits.keys()))
else:
    link_results = {}

def _link_status_urls(urls):
    broken = []
    unverified = []
    for u in urls:
        status, _ = link_results.get(u, ("?", ""))
        if status == "BLOCKED":
            unverified.append(u)
        elif not (status.isdigit() and status.startswith(("2", "3"))):
            broken.append(u)
    return broken, unverified

_link_status = df["measure_name"].apply(lambda m: _link_status_urls(measure_links.get(m, [])))
df["broken_urls"] = _link_status.apply(lambda t: t[0])
df["unverified_urls"] = _link_status.apply(lambda t: t[1])
df["broken_links"] = df["broken_urls"].apply(len)
df["unverified_links"] = df["unverified_urls"].apply(len)

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

only_link_issues = st.checkbox("Only show measures with broken or unverified links")
if only_link_issues:
    df = df[(df["broken_links"] > 0) | (df["unverified_links"] > 0)]

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

    org_cols = st.columns(len(ORG_TYPES))
    for col, org_type in zip(org_cols, ORG_TYPES):
        v30, v12 = orgtype_views.get(org_type, (None, None))
        col.metric(
            org_type,
            f"{v30:,}" if v30 is not None else "N/A",
        )
        col.metric(
            "12m",
            f"{v12:,}" if v12 is not None else "N/A",
        )

st.markdown("---")

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
    ("broken_links", "Broken links"),
    ("unverified_links", "Unverified links"),
]

html = []
html.append("<tr>" + "".join(f"<th>{label}</th>" for _, label in cols) + "</tr>")

def _links_popup(urls, color):
    """Render a count that expands (native <details>/<summary>) to list the URLs on click."""
    if not urls:
        return "0"
    items = "".join(f'<div><a href="{u}" target="_blank">{u}</a></div>' for u in urls)
    return (
        f'<details><summary style="color:{color};font-weight:bold;cursor:pointer;">{len(urls)}</summary>'
        f'<div style="text-align:left;font-weight:normal;margin-top:4px;">{items}</div></details>'
    )

for _, r in df.iterrows():
    css = row_css(r["next_review_months"])
    link = (
        f'<a href="{r["github_url"]}" target="_blank" '
        f'style="color:inherit;text-decoration:underline;">'
        f'{r["measure_name"]}</a>'
    )
    broken_cell = _links_popup(r["broken_urls"], "red")
    unverified_cell = _links_popup(r["unverified_urls"], "orange")
    html.append(
        "<tr>"
        f'<td style="{css}">{link}</td>'
        f'<td style="{css}">{r["authored_by"]}</td>'
        f'<td style="{css}">{r["checked_by"]}</td>'
        f'<td style="{css}">{r["next_review"] or ""}</td>'
        f'<td style="{css}">{"" if pd.isna(r["next_review_months"]) else int(r["next_review_months"])}</td>'
        f'<td style="{css}">{int(r["views_30d"]) if pd.notna(r["views_30d"]) else ""}</td>'
        f'<td style="{css}">{int(r["views_12m"]) if pd.notna(r["views_12m"]) else ""}</td>'
        f'<td>{broken_cell}</td>'
        f'<td>{unverified_cell}</td>'
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
