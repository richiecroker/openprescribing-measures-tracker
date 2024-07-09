import streamlit as st
import requests
from dateutil.relativedelta import relativedelta
from datetime import datetime
import pandas as pd

st.set_page_config(layout="wide")
st.title("OpenPrescribing measures tracker")
#st.write('')

def review_months (review_date):
    # Get the current date
    current_date = datetime.now()

    # Calculate the difference using relativedelta
    difference = relativedelta(review_date, current_date)

    # Calculate the total number of months
    total_months = difference.years * 12 + difference.months

    if total_months <0:
        total_months = 0

    return int(total_months)

def style_based_on_next_review(row):
    next_review = row['next_review_months']
    if pd.notna(next_review):
        if next_review <=0:
            return ['color: red; font-weight: bold;'] * len(row)
        elif next_review < 4:
            return ['color: orange; font-weight: bold;'] * len(row)
        elif next_review < 6:
            return ['color: green; font-weight: bold;'] * len(row)
        else:
            return ['color: blue; font-weight: bold;'] * len(row)
    return [''] * len(row)

def email_to_name(email):
    # Extract the part before the "@" symbol
    local_part = email.split('@')[0]
    # Split by "."
    parts = local_part.split('.')
    # Capitalize each part
    capitalized_parts = [part.capitalize() for part in parts]
    # Join with a space
    name = ' '.join(capitalized_parts)
    return name

# Calculate date 6 months from now, and convert to date format
six_months = datetime.now() + relativedelta(months=6)
six_months = six_months.date()

# Get data from OpenPrescribing measure definitions from Github API
res = requests.get('https://api.github.com/repos/ebmdatalab/openprescribing/contents/openprescribing/measures/definitions')
data = res.json()  # Convert the response to JSON

# Create a list to store the normalized JSON data
normalized_data = []

# Iterate through the files, process only .json files
for item in data:
    if item['name'].endswith('.json'):
        url = item['download_url']  # Get the download URL
        file_data = requests.get(url).json()  # Fetch the JSON data from the URL
        table_id = item['name'].split('.')[0]  # Add the 'table_id' field
        authored_by = file_data.get('authored_by', '')
        if isinstance(authored_by, list):
            authored_by = file_data['authored_by'][0]  # Take the first element if next_review is a list
        #    authored_by = email_to_name('authored_by')

        measure_name = file_data.get('name', '') # Get measure name
        github_url = item['html_url'] # Get GitHub URL
        next_review = file_data.get('next_review', None)  # Get review date
        if isinstance(next_review, list):
            next_review = file_data['next_review'][0]  # Take the first element if next_review is a list
        if next_review != None :
            next_review = datetime.strptime(next_review, '%Y-%m-%d').date() # turn into date if not blank
        row = { # get data for each row
            'measure_name': measure_name,
            'authored_by': email_to_name(authored_by),
            'next_review': next_review,
            'github_url': github_url,
            'next_review_months': review_months(next_review)
            }
        #if next_review is None:
        #    normalized_data.append(row) # add if blank review data
        #elif next_review <= six_months:
        #    normalized_data.append(row) # add if currently less than six months from review date
    normalized_data.append(row)
normalized_data = sorted(normalized_data, key=lambda x: 
                         (x['next_review'] if x['next_review'] is not None else datetime.min.date())) # sort by review date, putting blank dates first

# Convert data to a DataFrame
df = pd.DataFrame(normalized_data)

# Widgets to filter data
months_filter = st.slider('Select number of months before review date', min_value=int(df['next_review_months'].min()), max_value=int(df['next_review_months'].max()), value=(int(df['next_review_months'].min()), int(df['next_review_months'].max())))

# Filter data based on the slider
filtered_df = df[(df['next_review_months'] >= months_filter[0]) & (df['next_review_months'] <= months_filter[1])]

# Apply the function to style the DataFrame
styled_df = filtered_df.style.apply(style_based_on_next_review, axis=1)

st.dataframe(
    styled_df, 
    hide_index=True,
    column_config={"github_url":st.column_config.LinkColumn("Github link", display_text="https://github.com/ebmdatalab/openprescribing/blob/main/openprescribing/measures/definitions/(.*?)"), "next_review_months": None}
)