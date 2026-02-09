import streamlit as st
import requests
from dateutil.relativedelta import relativedelta
from datetime import datetime
import pandas as pd

st.set_page_config(layout="wide")
st.title("OpenPrescribing measures tracker")

def review_months(review_date):
    """Return full months from now until review_date. If review_date is None, return pd.NA."""
    if review_date is None or (isinstance(review_date, float) and pd.isna(review_date)):
        return pd.NA
    # normalize to date
    if isinstance(review_date, datetime):
        review_date = review_date.date()
    try:
        current_date = datetime.now().date()
        difference = relativedelta(review_date, current_date)
        total_months = difference.years * 12 + difference.months
        return max(int(total_months), 0)
    except Exception:
        return pd.NA

def style_css_for_months(next_review_months):
    """Return inline CSS string for the row based on next_review_months."""
    if pd.isna(next_review_months):
        return ""
    try:
        v = int(next_review_months)
    except Exception:
        return ""
    if v <= 0:
        return "color: red; font-weight: bold;"
    elif v < 4:
        return "color: orange; font-weight: bold;"
    elif v < 6:
        return "color: green; font-weight: bold;"
    else:
        return "color: blue; font-weight: bold;"

def email_to_name(email):
    """Convert local-part 'john.doe@...' to 'John Doe'. If empty or invalid, return empty string."""
    if not email or not isinstance(email, str):
        return ""
    # If email is actually a list (sometimes authored_by is a list), handle upstream; here assume string.
    local_part = email.split('@')[0]
    parts = local_part.split('.')
    capitalized_parts = [p.capitalize() for p in parts if p]
    return ' '.join(capitalized_parts)

# --- Get GitHub token safely ---
github_token = st.secrets.get("github_token")
if not github_token:
    st.error("GitHub token not found in Streamlit secrets (st.secrets['github_token']). Add it and reload.")
    st.stop()

headers = {'Authorization': f'token {github_token}'}
api_url = 'https://api.github.com/repos/ebmdatalab/openprescribing/contents/openprescribing/measures/definitions'

try:
    res = requests.get(api_url, headers=headers, timeout=15)
except Exception as e:
    st.error(f"Network error while contacting GitHub API: {e}")
    st.stop()

if res.status_code != 200:
    st.error(f"Failed to retrieve data from GitHub API (status code: {res.status_code}).")
    st.stop()

data = res.json()
if not isinstance(data, list):
    st.error("Unexpected data structure returned by the API.")
    st.stop()

normalized_data = []
for item in data:
    if not (isinstance(item, dict) and item.get('name', '').endswith('.json')):
        continue
    download_url = item.get('download_url')
    if not download_url:
        continue
    try:
        file_res = requests.get(download_url, timeout=15)
        file_res.raise_for_status()
        file_data = file_res.json()
    except Exception:
        # skip problematic files rather than failing the whole app
        continue

    table_id = item['name'].split('.')[0]
    # authored_by / checked_by may be strings, lists, or missing
    authored_by = file_data.get('authored_by', '')
    if isinstance(authored_by, list) and authored_by:
        authored_by = authored_by[0]
    checked_by = file_data.get('checked_by', '')
    if isinstance(checked_by, list) and checked_by:
        checked_by = checked_by[0]

    # Next review may be None, string, or list
    next_review = file_data.get('next_review', None)
    if isinstance(next_review, list) and next_review:
        next_review = next_review[0]
    if isinstance(next_review, str):
        try:
            # file uses YYYY-MM-DD per your earlier code
            next_review = datetime.strptime(next_review, '%Y-%m-%d').date()
        except Exception:
            # leave as-is (will be string) and handle later
            try:
                next_review = datetime.fromisoformat(next_review).date()
            except Exception:
                next_review = None
    elif isinstance(next_review, datetime):
        next_review = next_review.date()

    measure_name = file_data.get('name', '') or table_id
    github_url = item.get('html_url', '')

    months = review_months(next_review)
    row = {
        'measure_name': measure_name,
        'authored_by': email_to_name(authored_by),
        'checked_by': email_to_name(checked_by),
        'next_review': next_review,
        'github_url': github_url,
        'next_review_months': months
    }
    normalized_data.append(row)

# Sort by next_review, placing None at the end
def sort_key(r):
    nr = r.get('next_review')
    if nr is None:
        return datetime.max.date()
    return nr

normalized_data = sorted(normalized_data, key=sort_key)
df = pd.DataFrame(normalized_data)

if df.empty:
    st.info("No measure definitions found in the repository (or all were skipped).")
    st.stop()

# Provide a slider to filter by months until review.
# Determine safe min/max values for slider using numeric months, ignoring pd.NA
valid_months = df['next_review_months'][df['next_review_months'].notna()].astype(int) if 'next_review_months' in df.columns else pd.Series(dtype=int)
if valid_months.empty:
    # No numeric month values; show all and provide a disabled slider
    st.info("No upcoming review dates found for the measures. Showing all entries.")
    filtered_df = df.copy()
else:
    min_m = int(valid_months.min())
    max_m = int(valid_months.max())
    # If min == max, provide a single-value slider to show that point (Streamlit expects a tuple for range)
    months_filter = st.slider(
        'Select range of months until review',
        min_value=min_m,
        max_value=max_m,
        value=(min_m, max_m)
    )
    filtered_df = df[
        (df['next_review_months'].notna()) &
        (df['next_review_months'].astype(int) >= months_filter[0]) &
        (df['next_review_months'].astype(int) <= months_filter[1])
    ].copy()

# If slider filtered everything out, allow showing empty result message
if filtered_df.empty:
    st.warning("No measures match your filter. Try widening the months range or clear filters.")
    # Still show full table below (optional). Here we'll show nothing further.
    st.stop()

# Build HTML table where measure_name is clickable and opens in a new tab.
cols = [
    ("measure_name", "Measure"),
    ("authored_by", "Authored by"),
    ("checked_by", "Checked by"),
    ("next_review", "Next review"),
    ("next_review_months", "Months to review")
]

html_rows = []
# Header
header_cells = "".join(
    f"<th style='text-align:left; padding:8px 12px; border-bottom:1px solid #ddd'>{hdr}</th>"
    for _, hdr in cols
)
html_rows.append(f"<tr>{header_cells}</tr>")

for _, r in filtered_df.iterrows():
    css = style_css_for_months(r.get("next_review_months"))
    nr = r.get("next_review")
    nr_text = ""
    if pd.notna(nr) and nr is not None:
        try:
            nr_text = nr.strftime("%Y-%m-%d")
        except Exception:
            nr_text = str(nr)
    measure_html = (
        f'<a href="{r.get("github_url", "#")}" '
        f'target="_blank" rel="noopener noreferrer" '
        f'style="color: inherit; text-decoration: underline;">'
        f'{r.get("measure_name", "")}</a>'
    )
    cell_values = [
        measure_html,
        r.get("authored_by", "") or "",
        r.get("checked_by", "") or "",
        nr_text,
        "" if pd.isna(r.get("next_review_months")) else str(int(r.get("next_review_months")))
    ]
    row_cells = "".join(
        f"<td style='padding:8px 12px; border-bottom:1px solid #f2f2f2; {css}'>{val}</td>"
        for val in cell_values
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


