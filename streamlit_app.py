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
