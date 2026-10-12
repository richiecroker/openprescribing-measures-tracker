import html
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
from dateutil.relativedelta import relativedelta

st.set_page_config(layout="wide")
st.title("OpenPrescribing measures tracker")

ORG_TYPES = ["practice", "pcn", "sicbl", "icb", "regional-team", "national/england"]
HREF_RE = re.compile(r"href\s*=\s*['\"]([^'\"]+)['\"]", re.I)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


def twelve_month_range():
    today = date.today()
    return [(today - relativedelta(years=1)).isoformat(), today.isoformat()]


def review_months(review_date):
    if not review_date:
        return pd.NA
    try:
        if isinstance(review_date, datetime):
            review_date = review_date.date()
        delta = relativedelta(review_date, date.today())
        return max(delta.years * 12 + delta.months, 0)
    except (TypeError, ValueError):
        return pd.NA


def row_css(months):
    if pd.isna(months):
        return ""
    months = int(months)
    colour = "red" if months <= 0 else "orange" if months < 4 else "green" if months < 6 else "blue"
    return f"color:{colour};font-weight:bold;"


def email_to_name(email):
    if not isinstance(email, str) or not email:
        return ""
    return " ".join(part.capitalize() for part in email.split("@")[0].split(".") if part)


def extract_hrefs(value):
    if isinstance(value, str):
        yield from HREF_RE.findall(value)
    elif isinstance(value, list):
        for entry in value:
            yield from extract_hrefs(entry)


def looks_like_bot_block(response):
    return response.status_code in (403, 503) and (
        "cloudflare" in response.headers.get("Server", "").lower()
        or "cf-mitigated" in response.headers
        or "cf-ray" in response.headers
    )


@st.cache_data(ttl=86400, show_spinner=False)
def check_url(url, timeout=10):
    try:
        with requests.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True) as response:
            status = response.status_code
        if status >= 400:
            with requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True, stream=True) as response:
                if looks_like_bot_block(response):
                    return ("BLOCKED", "Bot/Cloudflare protection - verify manually")
                status = response.status_code
        return (str(status), "OK" if status < 400 else "Error")
    except requests.exceptions.SSLError as exc:
        return ("SSL_ERROR", str(exc))
    except requests.exceptions.ConnectionError as exc:
        return ("CONN_ERROR", str(exc))
    except requests.exceptions.Timeout:
        return ("TIMEOUT", "Request timed out")
    except requests.exceptions.RequestException as exc:
        return ("REQUEST_ERROR", str(exc))


def check_urls(urls, max_workers=10):
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(check_url, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                results[url] = future.result()
            except Exception as exc:
                results[url] = ("UNKNOWN_ERROR", str(exc))
    return results


@st.cache_data(ttl=1800, show_spinner="Fetching measure definitions from GitHub…")
def fetch_measures_and_links(github_token):
    headers = {"Authorization": f"token {github_token}"}
    endpoint = "https://api.github.com/repos/ebmdatalab/openprescribing/contents/openprescribing/measures/definitions"
    response = requests.get(endpoint, headers=headers, timeout=20)
    response.raise_for_status()
    rows, link_hits, measure_links = [], defaultdict(set), defaultdict(list)
    items = [item for item in response.json() if item.get("name", "").endswith(".json")]

    def fetch_item(item):
        response = requests.get(item["download_url"], timeout=15)
        response.raise_for_status()
        return item, response.json()

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_item, item): item for item in items}
        for future in as_completed(futures):
            try:
                item, data = future.result()
            except Exception:
                continue
            measure_id = os.path.splitext(item["name"])[0]
            name = data.get("name") or measure_id
            author = data.get("authored_by") or ""
            checker = data.get("checked_by") or ""
            if isinstance(author, list):
                author = author[0] if author else ""
            if isinstance(checker, list):
                checker = checker[0] if checker else ""
            next_review = data.get("next_review")
            if isinstance(next_review, list):
                next_review = next_review[0] if next_review else None
            if isinstance(next_review, str):
                try:
                    next_review = datetime.strptime(next_review, "%Y-%m-%d").date()
                except ValueError:
                    next_review = None
            for url in extract_hrefs(data.get("why_it_matters")):
                url = url.strip()
                if url:
                    link_hits[url].add(measure_id)
                    measure_links[measure_id].append(url)
            rows.append({
                "measure_name": name,
                "measure_id": measure_id,
                "github_url": item.get("html_url", ""),
                "authored_by": email_to_name(author),
                "checked_by": email_to_name(checker),
                "next_review": next_review,
                "next_review_months": review_months(next_review),
            })
    return rows, dict(link_hits), dict(measure_links)


def plausible_query(site_id, api_key, period, filters=None, dimensions=None):
    """Fetch every result page (not just Plausible's first 10,000 rows)."""
    endpoint = "https://plausible.io/api/v2/query"
    results = []
    offset, limit = 0, 10000
    while True:
        payload = {
            "site_id": site_id,
            "metrics": ["pageviews"],
            "date_range": period,
            "filters": filters or [],
            "dimensions": dimensions or [],
            "pagination": {"limit": limit, "offset": offset},
            "include": {"total_rows": True},
        }
        response = requests.post(
            endpoint, headers={"Authorization": f"Bearer {api_key}"},
            json=payload, timeout=45,
        )
        response.raise_for_status()
        data = response.json()
        batch = data.get("results", [])
        results.extend(batch)
        total = data.get("meta", {}).get("total_rows")
        offset += len(batch)
        if not batch or (total is not None and offset >= total) or len(batch) < limit:
            break
    return results


def org_from_page(page, org_type):
    """Only include the org's /measures/ pages; national is its exact landing page."""
    path = urlparse(page).path
    if org_type == "national/england":
        return "england" if path in ("/national/england", "/national/england/") else None
    match = re.fullmatch(rf"/{re.escape(org_type)}/([^/]+)/measures(?:/.*)?", path)
    return match.group(1) if match else None


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_org_breakdowns(site_id, api_key, period, cache_date):
    """Returns {org_type: {org_code: views}} for one reporting period."""
    breakdowns = {}
    for org_type in ORG_TYPES:
        if org_type == "national/england":
            filters = [["is", "event:page", ["/national/england", "/national/england/"]]]
        else:
            filters = [
                ["contains", "event:page", [f"/{org_type}/"]],
                ["contains", "event:page", ["/measures"]],
            ]
        rows = plausible_query(site_id, api_key, period, filters, ["event:page"])
        counts = defaultdict(int)
        for row in rows:
            page = row["dimensions"][0]
            code = org_from_page(page, org_type)
            if code:
                counts[code] += int(row["metrics"][0] or 0)
        breakdowns[org_type] = dict(counts)
    return breakdowns


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_measure_pageviews(measure_ids, site_id, api_key, period, cache_date):
    """Preserves the original measure ID matching rule and retrieves all IDs."""
    results = {}
    for measure_id in measure_ids:
        rows = plausible_query(
            site_id, api_key, period,
            [["contains", "event:page", [f"/{measure_id}/"]]],
        )
        results[measure_id] = int(rows[0]["metrics"][0] or 0) if rows else 0
    return results


def link_status_urls(urls, link_results):
    broken, unverified = [], []
    for url in urls:
        status, _ = link_results.get(url, ("?", ""))
        if status == "BLOCKED":
            unverified.append(url)
        elif not (status.isdigit() and status.startswith(("2", "3"))):
            broken.append(url)
    return broken, unverified


def links_popup(urls, colour):
    if not urls:
        return "0"
    items = "".join(
        f'<div><a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        f'{html.escape(url)}</a></div>' for url in urls
    )
    return (
        f'<details><summary style="color:{colour};font-weight:bold;cursor:pointer">{len(urls)}</summary>'
        f'<div style="text-align:left;font-weight:normal;margin-top:4px">{items}</div></details>'
    )


# Configuration
try:
    github_token = st.secrets.get("github_token")
    plausible_api_key = st.secrets.get("plausible_api_key")
    plausible_site_id = st.secrets.get("plausible_site_id")
except Exception:
    github_token = plausible_api_key = plausible_site_id = None

if not github_token:
    st.error("Missing GitHub token (github_token in Streamlit secrets).")
    st.stop()

try:
    rows, link_hits, measure_links = fetch_measures_and_links(github_token)
except Exception as exc:
    st.error(f"Failed to fetch measure definitions: {exc}")
    st.stop()

if not rows:
    st.warning("No measure definitions were retrieved.")
    st.stop()

df = pd.DataFrame(rows)
with st.spinner(f"Checking {len(link_hits)} link(s)…"):
    link_results = check_urls(list(link_hits)) if link_hits else {}
statuses = df["measure_id"].apply(lambda mid: link_status_urls(measure_links.get(mid, []), link_results))
df["broken_urls"] = statuses.apply(lambda value: value[0])
df["unverified_urls"] = statuses.apply(lambda value: value[1])
df["broken_links"] = df["broken_urls"].apply(len)
df["unverified_links"] = df["unverified_urls"].apply(len)

# Fetch analytics BEFORE applying display filters so the organisation report is independent.
org_breakdowns_30d, org_breakdowns_12m = {}, {}
if plausible_api_key and plausible_site_id:
    with st.spinner("Fetching Plausible measure pageviews…"):
        try:
            ids = tuple(sorted(df["measure_id"].tolist()))
            # The date key prevents stale rolling windows from persisting across days.
            views_30 = fetch_measure_pageviews(ids, plausible_site_id, plausible_api_key, "30d", date.today().isoformat())
            views_12 = fetch_measure_pageviews(ids, plausible_site_id, plausible_api_key, twelve_month_range(), date.today().isoformat())
            df["views_30d"] = df["measure_id"].map(views_30)
            df["views_12m"] = df["measure_id"].map(views_12)
        except requests.RequestException as exc:
            st.error(f"Plausible measure pageviews failed: {exc}")
            df["views_30d"] = pd.NA
            df["views_12m"] = pd.NA

    with st.spinner("Fetching organisation-level pageviews…"):
        try:
            org_breakdowns_30d = fetch_org_breakdowns(
                plausible_site_id, plausible_api_key, "30d", date.today().isoformat()
            )
            org_breakdowns_12m = fetch_org_breakdowns(
                plausible_site_id, plausible_api_key, twelve_month_range(), date.today().isoformat()
            )
        except requests.RequestException as exc:
            st.error(f"Plausible organisation breakdown failed: {exc}")
            org_breakdowns_30d = {}
            org_breakdowns_12m = {}
else:
    st.info("Plausible API key or site ID missing; analytics will not be displayed.")
    df["views_30d"] = pd.NA
    df["views_12m"] = pd.NA

# Original measure filters
valid_months = df["next_review_months"].dropna().astype(int)
if not valid_months.empty:
    min_m, max_m = int(valid_months.min()), int(valid_months.max())
    if min_m == max_m:
        st.caption(f"Months until review: {min_m}")
        rng = (min_m, max_m)
    else:
        rng = st.slider("Months until review", min_m, max_m, (min_m, max_m))
    df = df.loc[df["next_review_months"].apply(lambda value: pd.notna(value) and rng[0] <= int(value) <= rng[1])].copy()

if st.checkbox("Only show measures with broken or unverified links"):
    df = df.loc[(df["broken_links"] > 0) | (df["unverified_links"] > 0)].copy()

sort_col = st.selectbox(
    "Sort by",
    ["next_review_months", "measure_name", "authored_by", "checked_by", "views_30d", "views_12m"],
    format_func=lambda key: {
        "next_review_months": "Months to review", "measure_name": "Measure name",
        "authored_by": "Authored by", "checked_by": "Checked by",
        "views_30d": "Views (30d)", "views_12m": "Views (12m)",
    }[key],
)
sort_order = st.radio("Order", ["Ascending", "Descending"], horizontal=True)
df = df.sort_values(by=sort_col, ascending=sort_order == "Ascending", na_position="last")

col1, col2, col3 = st.columns(3)
col1.metric("Total Measures", len(df))
col2.metric("Total Views (30 days)", f"{int(df['views_30d'].sum()):,}" if df["views_30d"].notna().any() else "N/A")
col3.metric("Total Views (12 months)", f"{int(df['views_12m'].sum()):,}" if df["views_12m"].notna().any() else "N/A")

if org_breakdowns_30d and org_breakdowns_12m:
    st.subheader("Organisation type pageviews")
    summary_cols = st.columns(len(ORG_TYPES))
    for col, org_type in zip(summary_cols, ORG_TYPES):
        count_30 = sum(org_breakdowns_30d.get(org_type, {}).values())
        count_12 = sum(org_breakdowns_12m.get(org_type, {}).values())
        col.metric(org_type, f"{count_30:,}")
        col.metric("12m", f"{count_12:,}")

    st.subheader("Individual organisation pageviews")
    st.caption("Each organisation code aggregates its /measures/ page URLs. National/England is counted at its exact landing-page URL, as in the original tracker. These figures are independent of the measure filters above.")
    org_tables = []
    for org_type in ORG_TYPES:
        by_30 = org_breakdowns_30d.get(org_type, {})
        by_12 = org_breakdowns_12m.get(org_type, {})
        codes = set(by_30) | set(by_12)
        for code in codes:
            org_tables.append({
                "Organisation type": org_type,
                "Organisation": code,
                "Views (30d)": by_30.get(code, 0),
                "Views (12m)": by_12.get(code, 0),
            })
    org_df = pd.DataFrame(org_tables, columns=["Organisation type", "Organisation", "Views (30d)", "Views (12m)"])
    selected_type = st.selectbox("Organisation type breakdown", ORG_TYPES)
    selected = org_df.loc[org_df["Organisation type"] == selected_type].copy()
    selected = selected.sort_values(["Views (12m)", "Organisation"], ascending=[False, True])
    st.dataframe(selected[["Organisation", "Views (30d)", "Views (12m)"]], hide_index=True, use_container_width=True)
    st.download_button(
        "Download selected organisation breakdown (CSV)",
        selected.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"openprescribing_{selected_type.replace('/', '_')}_pageviews.csv",
        mime="text/csv",
    )
    st.download_button(
        "Download all organisation breakdowns (CSV)",
        org_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="openprescribing_all_organisation_pageviews.csv",
        mime="text/csv",
    )

st.markdown("---")
st.subheader("Measures")
columns = [
    ("measure_name", "Measure"), ("authored_by", "Authored by"),
    ("checked_by", "Checked by"), ("next_review", "Next review"),
    ("next_review_months", "Months to review"), ("views_30d", "Views (30d)"),
    ("views_12m", "Views (12m)"), ("broken_links", "Broken links"),
    ("unverified_links", "Unverified links"),
]
nowrap_columns = {"next_review", "authored_by", "checked_by"}
head = "<tr>" + "".join(f"<th>{html.escape(label)}</th>" for _, label in columns) + "</tr>"
body = []
for _, row in df.iterrows():
    css = row_css(row["next_review_months"])
    github_url = html.escape(str(row["github_url"]), quote=True)
    name = html.escape(str(row["measure_name"]))
    cells = {
        "measure_name": f'<a href="{github_url}" target="_blank" rel="noopener noreferrer" style="color:inherit;text-decoration:underline">{name}</a>',
        "authored_by": html.escape(str(row["authored_by"])),
        "checked_by": html.escape(str(row["checked_by"])),
        "next_review": html.escape(str(row["next_review"])) if row["next_review"] else "",
        "next_review_months": str(int(row["next_review_months"])) if pd.notna(row["next_review_months"]) else "",
        "views_30d": str(int(row["views_30d"])) if pd.notna(row["views_30d"]) else "",
        "views_12m": str(int(row["views_12m"])) if pd.notna(row["views_12m"]) else "",
        "broken_links": links_popup(row["broken_urls"], "red"),
        "unverified_links": links_popup(row["unverified_urls"], "orange"),
    }
    body.append("<tr>" + "".join(
        f'<td style="{css if key not in ("broken_links", "unverified_links") else ""}'
        f'{"white-space:nowrap;" if key in nowrap_columns else ""}">{cells[key]}</td>'
        for key, _ in columns
    ) + "</tr>")
st.markdown(
    '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%">'
    + head + "".join(body) + "</table></div>",
    unsafe_allow_html=True,
)
