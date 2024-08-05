import streamlit as st
import requests
from dateutil.relativedelta import relativedelta
from datetime import datetime
import pandas as pd

st.set_page_config(layout="wide")
st.title("OpenPrescribing measures tracker")

def review_months(review_date):
    current_date = datetime.now()
    difference = relativedelta(review_date, current_date)
    total_months = difference.years * 12 + difference.months
    return max(int(total_months), 0)

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
            months_filter = st.slider('Select number of months before review date', min_value=int(df['next_review_months'].min()), max_value=int(df['next_review_months'].max()), value=(int(df['next_review_months'].min()), int(df['next_review_months'].max())))
            filtered_df = df[(df['next_review_months'] >= months_filter[0]) & (df['next_review_months'] <= months_filter[1])]
            styled_df = filtered_df.style.apply(style_based_on_next_review, axis=1)
            st.dataframe(styled_df, hide_index=True, column_config={"github_url": st.column_config.LinkColumn("Github link", display_text="https://github.com/ebmdatalab/openprescribing/blob/main/openprescribing/measures/definitions/(.*?)"), "next_review_months": None})
        else:
            st.error("Unexpected data structure returned by the API.")
    else:
        st.error(f"Failed to retrieve data. Status code: {res.status_code}")


import requests
import streamlit as st

# Function to check GitHub token validity and rate limits
def check_github_token(github_token):
    url = 'https://api.github.com/user'
    headers = {'Authorization': f'token {github_token}'}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        print("Token is valid and working.")
    else:
        print(f"Failed to authenticate. Status code: {response.status_code}")

    # Check rate limits
    url = 'https://api.github.com/rate_limit'
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    rate_limit_info = response.json()
    print("Rate Limit Info:", rate_limit_info)
    
# Function to get open pull requests
def get_open_pull_requests(repo_owner, repo_name, github_token):
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/pulls?state=open'
    headers = {'Authorization': f'token {github_token}'}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

# Function to filter pull requests by label
def filter_pull_requests_by_label(pulls, label):
    filtered_pulls = []
    for pr in pulls:
        pr_labels = [l['name'] for l in pr.get('labels', [])]
        if label in pr_labels:
            filtered_pulls.append(pr)
    return filtered_pulls

# Function to extract branch names from pull requests
def extract_branches_from_pull_requests(pulls):
    branches = set()
    for pr in pulls:
        branches.add(pr['head']['ref'])
    return branches

# Function to get commits from a branch
def get_commits(repo_owner, repo_name, branch, github_token):
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/commits?sha={branch}'
    headers = {'Authorization': f'token {github_token}'}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.json()

# Function to get files changed in a commit
def get_files_changed_in_commit(repo_owner, repo_name, commit_sha, github_token):
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/commits/{commit_sha}'
    headers = {'Authorization': f'token {github_token}'}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    commit_data = response.json()
    files_changed = [file['filename'] for file in commit_data.get('files', [])]
    return files_changed

# Function to get all files changed in a branch
def get_all_files_changed_in_branch(repo_owner, repo_name, base_branch, branch, github_token):
    base_commits = get_commits(repo_owner, repo_name, base_branch, github_token)
    branch_commits = get_commits(repo_owner, repo_name, branch, github_token)

    base_commit_sha = base_commits[0]['sha'] if base_commits else None
    branch_commit_shas = [commit['sha'] for commit in branch_commits]

    changed_files = set()
    for commit_sha in branch_commit_shas:
        files = get_files_changed_in_commit(repo_owner, repo_name, commit_sha, github_token)
        changed_files.update(files)
    
    return changed_files

# Main function to find files with changes in branches with open PRs having a specific label
def find_files_with_changes(repo_owner, repo_name, label, base_branch, github_token):
    pulls = get_open_pull_requests(repo_owner, repo_name, github_token)
    filtered_pulls = filter_pull_requests_by_label(pulls, label)
    branches = extract_branches_from_pull_requests(filtered_pulls)
    
    branch_changes = {}
    for branch in branches:
        changed_files = get_all_files_changed_in_branch(repo_owner, repo_name, base_branch, branch, github_token)
        branch_changes[branch] = changed_files

    return branch_changes

# Example usage
repo_owner = 'ebmdatalab'
repo_name = 'openprescribing'
label = 'amend-measure'
base_branch = 'main'  # or 'master'
#github_token = 'your_github_token_here'  # Replace with your actual token

# Check GitHub token
check_github_token(github_token)

# Find files with changes
files_with_changes = find_files_with_changes(repo_owner, repo_name, label, base_branch, github_token)
st.write("Files with changes in branches with open pull requests labeled '{}':".format(label))
for branch, files in files_with_changes.items():
    st.write(f"Branch '{branch}':")
    for file in files:
        st.write(f"  - {file}")
