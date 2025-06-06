#!/usr/bin/env python3

# TODO: The git blame is wrong. It seems to take the last commit instead.

import glob
import re
import os
from datetime import date
import json
import sys

# pip3 install PyGithub numpy
from github import Github
import numpy as np

# Only actually change things on master
# TODO: Make this a command line or env option
DRY_RUN = os.environ["DRY_RUN"] != "false"
print("DRY_RUN =", DRY_RUN)

# Git current commit id
commit_hash = None
with os.popen("git rev-parse HEAD") as process:
    commit_hash = process.read().strip()
print("Commit hash:", commit_hash)

# Connect to the GitHub repo
gh = Github(os.environ["GITHUB_TOKEN"])
repo = gh.get_repo(os.environ["REPO_NAME"])
print("Connected to", repo)

# GitHub labels for issues
labels = {
    "TODO": ["tracker", "to do"],
    "FEATURE": ["tracker", "feature"],
    "REFACTOR": ["tracker", "refactor"],
    "OPT": ["tracker", "optimize"],
    "HACK": ["tracker", "hack"],
}

# GitHub users for emails
users = {
    "<remco@wicked.ventures>": "recmo",
    "<james@prestwi.ch>": "prestwich",
}

# Translation from labels to PyGitHub `Label`s
repo_labels = {l.name: l for l in repo.get_labels()}

# Translate users ot PyGitHub `User`s
users = {k: gh.get_user(v) for k, v in users.items()}

# Collect existing tracker issues
open_issues = []
issue_body_json = re.compile("^<!--({.*})-->$", re.MULTILINE)
for gh_issue in repo.get_issues():
    if repo_labels["tracker"] in gh_issue.labels:
        issue = json.loads(issue_body_json.search(gh_issue.body).group(1))
        issue["github"] = gh_issue
        open_issues += [issue]
print("Found", len(open_issues), "tracked issues on GitHub.")

# Number of lines to give before and after the TODO comment.
CONTEXT_LINES = 5

# Rust like todos. For *.{rs}
rust_todo = re.compile(r"//\W*(TODO|HACK|OPT|FEATURE|REFACTOR)\W*(.*)$")
rust_continuation = re.compile(r"//\W*(?!(TODO|HACK|OPT|FEATURE|REFACTOR))(.*)$")
rust_todo_macro = re.compile(r"\btodo!\((.*)\)")

# Shell like todos. For *.{sh, yml, toml, py, Dockerfile, editorconfig, gitignore}
# TODO: `# TODO: {message}`

# Markdown like todos. For *.{md}
# TODO: `<!-- TODO: {message} -->`

# Markdown todo lists.
# TODO: `* [] {message}`


def git_blame(filename, line):
    # Git line numbers start at one
    command = "git blame {filename} --line-porcelain -L {line},{line}".format(
        filename=filename, line=line + 1
    )
    with os.popen(command) as process:
        lines = process.readlines()
        result = dict()
        result["commit-hash"] = lines[0].split(" ")[0]
        for line in lines[1:-1]:
            line = line.replace("\n", "")
            if " " in line:
                [key, value] = line.split(" ", 1)
                result[key] = value
        result["author-time"] = int(result["author-time"])
        result["committer-time"] = int(result["committer-time"])
        return result


def get_context(filename, start, end):
    with open(filename, "r") as file:
        lines = file.readlines()
        start = max(0, start)
        end = min(end, len(lines))
        return "".join(lines[start:end])


def prep_filename(filename):
    if filename.startswith("src/"):
        filename = filename[4:]
    if filename.endswith(".rs"):
        filename = filename[:-3]
    return f"`{filename}`: "


def issues_from_file(filename):
    with open(filename, "r") as file:
        line_number = 0
        issue = None
        kind = None
        issue_line = 0
        for line in file:
            match = rust_todo.search(line)
            continuation = rust_continuation.search(line)
            if match:
                issue = match.group(2)
                kind = match.group(1)
                issue_line = line_number
            elif issue and continuation:
                issue += "\n" + continuation.group(2)
            elif issue:
                result = git_blame(filename, issue_line)
                context = get_context(
                    filename, issue_line - CONTEXT_LINES, line_number + CONTEXT_LINES
                )
                result["filename"] = filename
                result["line"] = issue_line
                result["line_end"] = line_number
                result["kind"] = kind
                result["issue"] = issue
                result["head"] = prep_filename(filename) + issue.split("\n")[0]
                result["context"] = context
                result["repo"] = repo.full_name
                result["branch-hash"] = commit_hash
                yield result
                issue = None
                kind = None
                issue_line = 0
            else:
                match = rust_todo_macro.search(line)
                if match:
                    issue = match.group(1)
                    if issue == "":
                        issue = "todo!()"
                    kind = "TODO"
                    issue_line = line_number
                    result = git_blame(filename, issue_line)
                    context = get_context(
                        filename,
                        issue_line - CONTEXT_LINES,
                        line_number + CONTEXT_LINES,
                    )
                    result["filename"] = filename
                    result["line"] = issue_line
                    result["line_end"] = line_number
                    result["kind"] = kind
                    result["issue"] = issue
                    result["head"] = prep_filename(filename) + issue.split("\n")[0]
                    result["context"] = context
                    result["repo"] = repo.full_name
                    result["branch-hash"] = commit_hash
                    yield result
            line_number += 1


def issues_from_glob(pattern):
    for filename in glob.iglob(pattern, recursive=True):
        for issue in issues_from_file(filename):
            yield issue


def render(issue):
    issue = issue.copy()
    issue.pop("open-issue-index", None)
    issue["json"] = json.dumps(issue)

    if issue["author-mail"] in users:
        issue["user-mention"] = "@" + users[issue["author-mail"]].login + " wrote in"
    else:
        issue["user-mention"] = "in"

    issue["author-time-pretty"] = date.fromtimestamp(issue["author-time"]).isoformat()
    issue["commit-hash-short"] = issue["commit-hash"][0:7]
    issue["line-one"] = issue["line"] + 1

    issue_obj = dict(
        title="{head}".format(**issue),
        body="""
*On {author-time-pretty} {user-mention} [`{commit-hash-short}`](https://github.com/{repo}/commit/{commit-hash}) “{summary}”:*

{issue}

```rust
{context}
```
*From [`{filename}:{line-one}`](https://github.com/{repo}/blob/{branch-hash}/{filename}#L{line-one})*

<!--{json}-->
""".strip().format(**issue),
        labels=labels[issue["kind"]] + (["blocked"] if "blocked" in issue else []),
    )

    if issue["author-mail"] in users:
        issue_obj["assignee"] = users[issue["author-mail"]]

    return issue_obj


def create_issue(source_issue):
    print("Creating issue...")
    r = render(source_issue)
    r["labels"] = [repo_labels[l] for l in r["labels"]]
    if not DRY_RUN:
        gh_issue = repo.create_issue(**r)
        print("Created issue", gh_issue.number)


def update_issue(github_issue, source_issue):
    # We update headline, body and labels
    gh = github_issue["github"]
    r = render(source_issue)
    if (
        gh.title == r["title"]
        and github_issue["issue"] == source_issue["issue"]
        and set([l.name for l in gh.labels]) == set(r["labels"])
    ):
        # Issue up to date
        return
    # NOTE: We don't update if the body changed because this changes with each
    # commit hash. It is a good check to add if the json structure or the
    # render function is modified.
    print("Updating issue", github_issue["github"].number)
    if not DRY_RUN:
        gh.edit(**r)


def close_issue(github_issue):
    print("Closing issue", github_issue["github"].number)
    gh = github_issue["github"]
    if not DRY_RUN:
        gh.edit(state="closed")


# Collect source issues
source_issues = list(issues_from_glob("**/*.rs"))
print("Found", len(source_issues), "issues in source.")

# Match source issues with open issues
print("Issue closeness matrix")
closeness = np.zeros((len(open_issues), len(source_issues)))
for i in range(len(open_issues)):
    open_issue = open_issues[i]
    for j in range(len(source_issues)):
        source_issue = source_issues[j]
        score = 0
        if open_issue["head"] == source_issue["head"]:
            # Three point if the head matches
            score += 3
        if (
            open_issue["filename"] == source_issue["filename"]
            and open_issue["line"] == source_issue["line"]
        ):
            # Three points if location matches
            score += 3
        if open_issue["commit-hash"] == source_issue["commit-hash"]:
            # Two points if commit-hash matches
            score += 2
        closeness[i, j] = score
        sys.stdout.write(str(score) + " ")
        sys.stdout.flush()
    sys.stdout.write("\n")

# Greedy match up pairs by highest scores first
min_score = 2.5
if len(source_issues) > 0 and len(open_issues) > 0:
    while True:
        # Pick a highest score
        (i, j) = np.unravel_index(np.argmax(closeness), closeness.shape)

        # If the score is less than the minimum required we are done
        if closeness[i, j] <= min_score:
            break

        # Remove pair from matrix
        closeness[i, :] = 0.0
        closeness[:, j] = 0.0

        # Pair up issues
        open_issues[i]["source-issue-index"] = j
        source_issues[j]["open-issue-index"] = i

# Process issues
for issue in open_issues:
    if "source-issue-index" in issue:
        # TODO: Update github issue
        update_issue(issue, source_issues[issue["source-issue-index"]])
    else:
        # Close issue
        close_issue(issue)
for issue in source_issues:
    if "open-issue-index" in issue:
        # already handles above
        pass
    else:
        create_issue(issue)
