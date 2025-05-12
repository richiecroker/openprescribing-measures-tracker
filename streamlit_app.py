import streamlit as st
import requests
from dateutil.relativedelta import relativedelta
from datetime import datetime
import pandas as pd

#set page details
st.set_page_config(layout="wide")
st.title("OpenPrescribing measures tracker")

#define functions

#calculate number of months until review
def review_months(review_date):
    current_date = datetime.now()
    difference = relativedelta(review_date, current_date)
    total_months = difference.years * 12 + difference.months
    return max(int(total_months), 0)

#set text colour depending on review distance
def style_based_on_next_review(row):
    next_review = row['next_review_months']
    if pd.notna(next_review):
        if next_review <= 0:
            return ['color: red; font-weight: bold;'] * len(row)
        elif next_review < 4:
            return ['color: orange; font-weight: bold;'] * len(row)
        elif next_review < 6:
            return ['color: green; font-weight: bold;'] * len(row)
        else:
            return ['color: blue; font-weight: bold;'] * len(row)
    return [''] * len(row)

#turn phc email address to name
def email_to_name(email):
    local_part = email.split('@')[0]
    parts = local_part.split('.')
    capitalized_parts = [part.capitalize() for part in parts]
    return ' '.join(capitalized_parts)

six_months = datetime.now() + relativedelta(months=6)
six_months = six_months.date()

# Read the GitHub token from Streamlit secrets
github_token = st.secrets["github_token"] 

if github_token is None:
    st.error("GitHub token not found in Streamlit secrets.")
else:
    headers = {'Authorization': f'token {github_token}'}
    res = requests.get('https://api.github.com/repos/ebmdatalab/openprescribing/contents/openprescribing/measures/definitions', headers=headers)

    if res.status_code == 200:
        data = res.json()
        if isinstance(data, list):
            normalized_data = []
            for item in data:
                if isinstance(item, dict) and item.get('name', '').endswith('.json'):
                    url = item['download_url']
                    file_data = requests.get(url).json()
                    table_id = item['name'].split('.')[0]
                    authored_by = file_data.get('authored_by', '')
                    if isinstance(authored_by, list):
                        authored_by = file_data['authored_by'][0]
                    checked_by = file_data.get('checked_by', '')
                    if isinstance(checked_by, list):
                        checked_by = file_data['checked_by'][0] 

                    measure_name = file_data.get('name', '')
                    github_url = item['html_url']
                    next_review = file_data.get('next_review', None)
                    if isinstance(next_review, list):
                        next_review = file_data['next_review'][0]
                    if next_review is not None:
                        next_review = datetime.strptime(next_review, '%Y-%m-%d').date()
                    row = {
                        'measure_name': measure_name,
                        'authored_by': email_to_name(authored_by),
                        'checked_by': email_to_name(checked_by),
                        'next_review': next_review,
                        'github_url': github_url,
                        'next_review_months': review_months(next_review)
                    }
                    normalized_data.append(row)
            normalized_data = sorted(normalized_data, key=lambda x: (x['next_review'] if x['next_review'] is not None else datetime.min.date()))
            df = pd.DataFrame(normalized_data)
            

            base_url = "https://github.com/ebmdatalab/openprescribing/blob/main/openprescribing/measures/definitions/"
            df['github_url'] = df['measure_name'].apply(lambda x: f"{base_url}{x}")


            months_filter = st.slider('Select number of months before review date', min_value=int(df['next_review_months'].min()), max_value=int(df['next_review_months'].max()), value=(int(df['next_review_months'].min()), int(df['next_review_months'].max())))
            filtered_df = df[(df['next_review_months'] >= months_filter[0]) & (df['next_review_months'] <= months_filter[1])]
            styled_df = filtered_df.style.apply(style_based_on_next_review, axis=1)
            # Display the dataframe with the LinkColumn configuration
            st.dataframe(
                styled_df, 
                hide_index=True, 
                use_container_width=True, 
                height=2500, 
                column_config={
                    "github_url": st.column_config.LinkColumn(
                        "Github link",  # Column header 
                        display_text="name"  # Use 'name' column as display text
                    ),
                    "next_review_months": None
                }
            )
        else:
            st.error("Unexpected data structure returned by the API.")
    else:
        st.error(f"Failed to retrieve data. Status code: {res.status_code}")

