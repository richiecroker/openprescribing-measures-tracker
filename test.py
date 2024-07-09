import streamlit as st
import pandas as pd

# Sample data
data = {
    'Name': ['John', 'Jane', 'Doe', 'Smith'],
    'Age': [28, 34, 29, 42],
    'Occupation': ['Engineer', 'Doctor', 'Artist', 'Scientist']
}

# Convert data to a DataFrame
df = pd.DataFrame(data)

# Display the table
st.write("## Sample Data Table")
st.table(df)

# Custom styling
styled_df = df.style.format({'Age': "{:.0f}"}) \
                    .highlight_min(subset=['Age'], color='red') \
                    .highlight_max(subset=['Age'], color='green')

# Display the styled dataframe
st.write("## Styled Data Table")
st.dataframe(styled_df)

# Widgets to filter data
age_filter = st.slider('Select Age Range', min_value=int(df['Age'].min()), max_value=int(df['Age'].max()), value=(int(df['Age'].min()), int(df['Age'].max())))

# Filter data based on the slider
filtered_df = df[(df['Age'] >= age_filter[0]) & (df['Age'] <= age_filter[1])]

# Display filtered data
st.write("## Filtered Data Table")
st.table(filtered_df)