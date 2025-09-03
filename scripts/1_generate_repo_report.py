#!/usr/bin/env python3

"""
Script to generate an HTML report of all repositories with CI status
"""

import argparse
import html
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict, Any


# Thread-safe counter for progress tracking
class ProgressCounter:
    def __init__(self, total: int):
        self.total = total
        self.current = 0
        self.lock = threading.Lock()

    def increment(self) -> int:
        with self.lock:
            self.current += 1
            return self.current


def run_gh_command(command: str) -> str:
    """Run a gh CLI command and return the result"""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {command}")
        print(f"Error: {e.stderr}")
        return None


def get_repos_for_org(org: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Get all repositories for a specific organization"""
    print(f"🔍 Fetching repositories from {org}...")

    cmd = f"gh repo list {org} --limit {limit} --json nameWithOwner,description,url,updatedAt,isPrivate"
    result = run_gh_command(cmd)

    if not result:
        print(f"⚠️  No repositories found for {org} or error occurred")
        return []

    try:
        repos = json.loads(result)
        for repo in repos:
            repo["organization"] = org
        print(f"📊 Found {len(repos)} repositories in {org}")
        return repos
    except json.JSONDecodeError as e:
        print(f"❌ Error parsing JSON for {org}: {e}")
        return []


def get_all_repos(organizations: List[str], limit: int = 200) -> List[Dict[str, Any]]:
    """Get all repositories from specified organizations"""
    all_repos = []

    for org in organizations:
        org_repos = get_repos_for_org(org, limit)
        all_repos.extend(org_repos)

    print(
        f"📊 Found {len(all_repos)} total repositories across {len(organizations)} organizations"
    )
    return all_repos


def check_repo_issues(repo_name: str) -> Dict[str, Any]:
    """Check open issues for a repository"""
    # Get open issues count and details
    issues_cmd = f'gh api repos/{repo_name}/issues --jq "length"'
    issues_count = run_gh_command(issues_cmd)

    if not issues_count or issues_count == "null":
        return {"count": 0, "issues": []}

    count = int(issues_count)
    if count == 0:
        return {"count": 0, "issues": []}

    # Get detailed issue information (limit to 50 for performance)
    issues_detail_cmd = f"""gh api repos/{repo_name}/issues --jq '[.[] | {{
        number: .number,
        title: .title,
        body: .body,
        state: .state,
        created_at: .created_at,
        updated_at: .updated_at,
        html_url: .html_url,
        user: .user.login,
        labels: [.labels[].name],
        assignees: [.assignees[].login]
    }}] | .[0:50]' """

    issues_detail = run_gh_command(issues_detail_cmd)

    issues = []
    if issues_detail and issues_detail != "null":
        try:
            issues = json.loads(issues_detail)
        except json.JSONDecodeError:
            pass

    return {"count": count, "issues": issues}


def check_repo_ci_and_issues_with_progress(
    repo_name: str, progress_counter: ProgressCounter, check_issues: bool = True
) -> tuple[str, Dict[str, Any]]:
    """Check CI status and issues for a repo and update progress counter"""
    try:
        # Check CI status
        ci_info = check_repo_ci_status(repo_name)

        # Check issues if enabled
        issues_info = {"count": 0, "issues": []}
        if check_issues:
            issues_info = check_repo_issues(repo_name)

        current = progress_counter.increment()
        issues_text = f" ({issues_info['count']} issues)" if check_issues else ""
        print(f"  [{current}/{progress_counter.total}] ✅ {repo_name}{issues_text}")

        return repo_name, {"ci_info": ci_info, "issues_info": issues_info}
    except Exception as e:
        current = progress_counter.increment()
        print(
            f"  [{current}/{progress_counter.total}] ❌ {repo_name} - Error: {str(e)}"
        )
        # Return default info on error
        return repo_name, {
            "ci_info": {
                "has_ci": False,
                "ci_status": "Error checking CI",
                "latest_run_status": "error",
                "latest_run_url": "",
                "workflow_count": 0,
            },
            "issues_info": {"count": 0, "issues": []},
        }


def check_repos_ci_and_issues_parallel(
    repos: List[Dict[str, Any]], max_workers: int = 10, check_issues: bool = True
) -> None:
    """Check CI status and issues for all repos in parallel"""
    action_text = "CI status and issues" if check_issues else "CI status"
    print(
        f"🔍 Checking {action_text} for {len(repos)} repositories using {max_workers} threads..."
    )

    # Create progress counter
    progress_counter = ProgressCounter(len(repos))

    # Create a mapping of repo names to repo objects for easy lookup
    repo_map = {repo["nameWithOwner"]: repo for repo in repos}

    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_repo = {
            executor.submit(
                check_repo_ci_and_issues_with_progress,
                repo["nameWithOwner"],
                progress_counter,
                check_issues,
            ): repo["nameWithOwner"]
            for repo in repos
        }

        # Process completed tasks
        for future in as_completed(future_to_repo):
            repo_name = future_to_repo[future]
            try:
                returned_repo_name, info = future.result()
                # Update the repo object with CI and issues info
                repo_map[returned_repo_name]["ci_info"] = info["ci_info"]
                repo_map[returned_repo_name]["issues_info"] = info["issues_info"]
            except Exception as e:
                print(f"  ❌ Error processing {repo_name}: {str(e)}")
                # Set default info on error
                repo_map[repo_name]["ci_info"] = {
                    "has_ci": False,
                    "ci_status": "Error checking CI",
                    "latest_run_status": "error",
                    "latest_run_url": "",
                    "workflow_count": 0,
                }
                repo_map[repo_name]["issues_info"] = {"count": 0, "issues": []}


def check_repo_ci_status(repo_name: str) -> Dict[str, Any]:
    """Check if a repository has CI actions and their status"""
    # Removed the print statement since we handle progress in the parallel version

    # Check for workflow files
    workflows_cmd = (
        f'gh api repos/{repo_name}/actions/workflows --jq ".workflows | length"'
    )
    workflow_count = run_gh_command(workflows_cmd)

    has_ci = False
    ci_status = "No CI"
    latest_run_status = "N/A"
    latest_run_url = ""

    if workflow_count and int(workflow_count) > 0:
        has_ci = True

        # Get latest workflow run
        runs_cmd = f'gh api repos/{repo_name}/actions/runs --jq ".workflow_runs[0] | {{status: .status, conclusion: .conclusion, html_url: .html_url}}"'
        latest_run = run_gh_command(runs_cmd)

        if latest_run and latest_run != "null":
            try:
                run_data = json.loads(latest_run)
                status = run_data.get("status", "unknown")
                conclusion = run_data.get("conclusion", "unknown")
                latest_run_url = run_data.get("html_url", "")

                if status == "completed":
                    if conclusion == "success":
                        ci_status = "✅ Passing"
                        latest_run_status = "success"
                    elif conclusion == "failure":
                        ci_status = "❌ Failing"
                        latest_run_status = "failure"
                    elif conclusion == "cancelled":
                        ci_status = "⚠️ Cancelled"
                        latest_run_status = "cancelled"
                    else:
                        ci_status = f"⚠️ {conclusion}"
                        latest_run_status = conclusion
                elif status == "in_progress":
                    ci_status = "🔄 Running"
                    latest_run_status = "running"
                else:
                    ci_status = f"⚠️ {status}"
                    latest_run_status = status
            except json.JSONDecodeError:
                ci_status = "❓ Unknown"
                latest_run_status = "unknown"
        else:
            ci_status = "📝 Has CI (No runs)"
            latest_run_status = "no_runs"

    return {
        "has_ci": has_ci,
        "ci_status": ci_status,
        "latest_run_status": latest_run_status,
        "latest_run_url": latest_run_url,
        "workflow_count": int(workflow_count) if workflow_count else 0,
    }


def generate_org_colors(organizations: List[str]) -> Dict[str, Dict[str, str]]:
    """Generate color schemes for organizations"""
    colors = [
        {"bg": "#dbeafe", "text": "#1e40af"},  # Blue
        {"bg": "#dcfce7", "text": "#166534"},  # Green
        {"bg": "#fef3c7", "text": "#92400e"},  # Yellow
        {"bg": "#fce7f3", "text": "#be185d"},  # Pink
        {"bg": "#e0e7ff", "text": "#3730a3"},  # Indigo
        {"bg": "#fed7d7", "text": "#c53030"},  # Red
        {"bg": "#d1fae5", "text": "#047857"},  # Emerald
        {"bg": "#fdf4ff", "text": "#7c2d12"},  # Purple
    ]

    org_colors = {}
    for i, org in enumerate(organizations):
        color_index = i % len(colors)
        org_colors[org] = colors[color_index]

    return org_colors


def calculate_issue_age(created_at: str) -> str:
    """Calculate how long ago an issue was created"""
    try:
        if not created_at:
            return "Unknown"
        from datetime import datetime, timezone

        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - created

        days = diff.days
        if days == 0:
            hours = diff.seconds // 3600
            if hours == 0:
                minutes = diff.seconds // 60
                return f"{minutes}m ago"
            return f"{hours}h ago"
        elif days < 30:
            return f"{days}d ago"
        elif days < 365:
            months = days // 30
            return f"{months}mo ago"
        else:
            years = days // 365
            return f"{years}y ago"
    except:
        return "Unknown"


def generate_html_report(
    repos_data: List[Dict[str, Any]], organizations: List[str], output_path: str
) -> str:
    """Generate the HTML report"""
    print("📝 Generating HTML report...")

    # Sort repos by organization, then by name
    repos_data.sort(key=lambda x: (x["organization"], x["nameWithOwner"].lower()))

    # Count statistics
    total_repos = len(repos_data)
    repos_with_ci = sum(
        1 for repo in repos_data if repo.get("ci_info", {}).get("has_ci", False)
    )
    passing_ci = sum(
        1
        for repo in repos_data
        if repo.get("ci_info", {}).get("latest_run_status") == "success"
    )
    failing_ci = sum(
        1
        for repo in repos_data
        if repo.get("ci_info", {}).get("latest_run_status") == "failure"
    )
    total_issues = sum(
        repo.get("issues_info", {}).get("count", 0) for repo in repos_data
    )
    repos_with_issues = sum(
        1 for repo in repos_data if repo.get("issues_info", {}).get("count", 0) > 0
    )

    # Generate organization colors
    org_colors = generate_org_colors(organizations)

    # Generate CSS for organization badges
    org_css = ""
    for org, colors in org_colors.items():
        safe_org = org.replace("-", "").replace("_", "").replace(".", "")
        org_css += f"""
        .org-{safe_org} {{
            background: {colors['bg']};
            color: {colors['text']};
        }}"""

    # Generate organization list for footer
    org_list = ", ".join(organizations)

    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Repository CI Status Report</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f6f8fa;
            color: #24292f;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #2ea043, #1f883d);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0 0 10px 0;
            font-size: 2.5em;
            font-weight: 600;
        }}
        .header p {{
            margin: 0;
            font-size: 1.1em;
            opacity: 0.9;
        }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f6f8fa;
            border-bottom: 1px solid #d1d9e0;
        }}
        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
            border: 1px solid #d1d9e0;
        }}
        .stat-number {{
            font-size: 2.5em;
            font-weight: bold;
            color: #2ea043;
            margin-bottom: 5px;
        }}
        .stat-label {{
            color: #656d76;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .table-container {{
            padding: 0;
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th {{
            background: #f6f8fa;
            padding: 12px 16px;
            text-align: left;
            font-weight: 600;
            color: #24292f;
            border-bottom: 1px solid #d1d9e0;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        td {{
            padding: 12px 16px;
            border-bottom: 1px solid #f6f8fa;
            vertical-align: middle;
        }}
        tr:hover {{
            background-color: #f6f8fa;
        }}
        .repo-name {{
            font-weight: 600;
            color: #0969da;
        }}
        .repo-name a {{
            color: inherit;
            text-decoration: none;
        }}
        .repo-name a:hover {{
            text-decoration: underline;
        }}
        .org-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        {org_css}
        .ci-status {{
            font-weight: 500;
        }}
        .ci-link {{
            color: #0969da;
            text-decoration: none;
            font-size: 12px;
        }}
        .ci-link:hover {{
            text-decoration: underline;
        }}
        .description {{
            color: #656d76;
            font-style: italic;
            max-width: 300px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .private-badge {{
            background: #fff8dc;
            color: #b45309;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .footer {{
            padding: 20px 30px;
            background: #f6f8fa;
            text-align: center;
            color: #656d76;
            font-size: 0.9em;
            border-top: 1px solid #d1d9e0;
        }}
        .updated-time {{
            color: #656d76;
            font-size: 12px;
        }}
        .issues-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .issues-badge.no-issues {{
            background: #f6f8fa;
            color: #656d76;
        }}
        .issues-badge.has-issues {{
            background: #fff3cd;
            color: #856404;
        }}
        .issues-badge.many-issues {{
            background: #f8d7da;
            color: #721c24;
        }}
        .issues-badge:hover {{
            transform: scale(1.05);
        }}
        .issues-expandable {{
            display: none;
            margin-top: 10px;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 6px;
            border-left: 3px solid #0969da;
        }}
        .issues-expandable.expanded {{
            display: block;
        }}
        .issue-item {{
            background: white;
            border: 1px solid #d1d9e0;
            border-radius: 6px;
            padding: 12px;
            margin-bottom: 8px;
        }}
        .issue-item:last-child {{
            margin-bottom: 0;
        }}
        .issue-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 8px;
        }}
        .issue-title {{
            font-weight: 600;
            color: #24292f;
            margin: 0;
            flex: 1;
        }}
        .issue-title a {{
            color: inherit;
            text-decoration: none;
        }}
        .issue-title a:hover {{
            color: #0969da;
            text-decoration: underline;
        }}
        .issue-number {{
            color: #656d76;
            font-size: 12px;
            margin-left: 8px;
        }}
        .issue-meta {{
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 12px;
            color: #656d76;
            margin-bottom: 8px;
        }}
        .issue-age {{
            font-weight: 500;
        }}
        .issue-author {{
            color: #0969da;
        }}
        .issue-labels {{
            display: flex;
            gap: 4px;
            flex-wrap: wrap;
        }}
        .issue-label {{
            display: inline-block;
            padding: 2px 6px;
            border-radius: 12px;
            font-size: 10px;
            font-weight: 600;
            background: #0969da;
            color: white;
        }}
        .issue-body {{
            color: #656d76;
            font-size: 13px;
            line-height: 1.4;
            max-height: 60px;
            overflow: hidden;
            text-overflow: ellipsis;
            margin-top: 8px;
        }}
        .no-issues-message {{
            color: #656d76;
            font-style: italic;
            text-align: center;
            padding: 20px;
        }}
        .filter-controls {{
            padding: 20px 30px;
            background: #f6f8fa;
            border-bottom: 1px solid #d1d9e0;
        }}
        .filter-group {{
            display: flex;
            gap: 15px;
            align-items: center;
            flex-wrap: wrap;
        }}
        .filter-group label {{
            font-weight: 600;
            color: #24292f;
        }}
        .filter-group select, .filter-group input {{
            padding: 6px 12px;
            border: 1px solid #d1d9e0;
            border-radius: 4px;
            background: white;
            font-size: 14px;
        }}
        @media (max-width: 768px) {{
            .stats {{
                grid-template-columns: repeat(2, 1fr);
            }}
            .header h1 {{
                font-size: 2em;
            }}
            table {{
                font-size: 12px;
            }}
            th, td {{
                padding: 8px 12px;
            }}
            .filter-group {{
                flex-direction: column;
                align-items: flex-start;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Repository CI Status Report</h1>
            <p>Comprehensive overview of repositories across {len(organizations)} organizations</p>
        </div>
        
        <div class="stats">
            <div class="stat-card">
                <div class="stat-number">{total_repos}</div>
                <div class="stat-label">Total Repositories</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{repos_with_ci}</div>
                <div class="stat-label">With CI/CD</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{passing_ci}</div>
                <div class="stat-label">Passing CI</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{failing_ci}</div>
                <div class="stat-label">Failing CI</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{total_issues}</div>
                <div class="stat-label">Total Open Issues</div>
            </div>
            <div class="stat-card">
                <div class="stat-number">{repos_with_issues}</div>
                <div class="stat-label">Repos with Issues</div>
            </div>
        </div>
        
        <div class="filter-controls">
            <div class="filter-group">
                <label for="orgFilter">Filter by Organization:</label>
                <select id="orgFilter" onchange="filterTable()">
                    <option value="">All Organizations</option>"""

    # Add organization filter options
    for org in sorted(organizations):
        html_content += f'<option value="{org}">{org}</option>'

    html_content += f"""
                </select>
                
                <label for="ciFilter">Filter by CI Status:</label>
                <select id="ciFilter" onchange="filterTable()">
                    <option value="">All Statuses</option>
                    <option value="success">Passing</option>
                    <option value="failure">Failing</option>
                    <option value="no_ci">No CI</option>
                    <option value="running">Running</option>
                </select>
                
                <label for="searchFilter">Search:</label>
                <input type="text" id="searchFilter" placeholder="Search repositories..." onkeyup="filterTable()">
                
                <label for="issuesFilter">Filter by Issues:</label>
                <select id="issuesFilter" onchange="filterTable()">
                    <option value="">All Repositories</option>
                    <option value="has_issues">Has Issues</option>
                    <option value="no_issues">No Issues</option>
                </select>
            </div>
        </div>
        
        <div class="table-container">
            <table id="repoTable">
                <thead>
                    <tr>
                        <th>Repository</th>
                        <th>Organization</th>
                        <th>Description</th>
                        <th>CI Status</th>
                        <th>Open Issues</th>
                        <th>Workflows</th>
                        <th>Last Updated</th>
                        <th>Visibility</th>
                    </tr>
                </thead>
                <tbody>
"""

    for repo in repos_data:
        repo_name = repo["nameWithOwner"]
        org = repo["organization"]
        description = html.escape(repo.get("description") or "No description")
        url = repo["url"]
        updated_at = repo.get("updatedAt", "")[:10]  # Just the date part
        is_private = repo.get("isPrivate", False)

        ci_info = repo.get("ci_info", {})
        ci_status = ci_info.get("ci_status", "Unknown")
        workflow_count = ci_info.get("workflow_count", 0)
        latest_run_url = ci_info.get("latest_run_url", "")
        latest_run_status = ci_info.get("latest_run_status", "unknown")

        issues_info = repo.get("issues_info", {})
        issues_count = issues_info.get("count", 0)
        issues_list = issues_info.get("issues", [])

        # Format CI status with link if available
        ci_display = ci_status
        if latest_run_url:
            ci_display = f'<a href="{latest_run_url}" class="ci-link" target="_blank">{ci_status}</a>'

        # Organization badge
        safe_org = org.replace("-", "").replace("_", "").replace(".", "")
        org_badge = f'<span class="org-badge org-{safe_org}">{org}</span>'

        # Privacy badge
        privacy_badge = (
            '<span class="private-badge">Private</span>' if is_private else ""
        )

        # Workflow count display
        workflow_display = (
            f"{workflow_count} workflow{'s' if workflow_count != 1 else ''}"
            if workflow_count > 0
            else "No workflows"
        )

        # Issues badge and expandable content
        if issues_count == 0:
            issues_badge_class = "no-issues"
            issues_badge_text = "No issues"
        elif issues_count <= 5:
            issues_badge_class = "has-issues"
            issues_badge_text = (
                f"{issues_count} issue{'s' if issues_count != 1 else ''}"
            )
        else:
            issues_badge_class = "many-issues"
            issues_badge_text = f"{issues_count} issues"

        issues_badge = f'<span class="issues-badge {issues_badge_class}" onclick="toggleIssues(\'{repo_name.replace("/", "-")}-issues\')">{issues_badge_text}</span>'

        # Generate issues expandable content
        issues_expandable = ""
        if issues_count > 0 and issues_list:
            issues_expandable = f'<div id="{repo_name.replace("/", "-")}-issues" class="issues-expandable">'

            for issue in issues_list[:10]:  # Show max 10 issues
                issue_age = calculate_issue_age(issue.get("created_at", ""))
                issue_title = html.escape(issue.get("title") or "No title")
                issue_number = issue.get("number", "")
                issue_url = issue.get("html_url", "")
                issue_author = html.escape(issue.get("user") or "Unknown")

                # Handle null/None body safely
                raw_body = issue.get("body") or ""
                if raw_body:
                    truncated_body = raw_body[:200] + (
                        "..." if len(raw_body) > 200 else ""
                    )
                    issue_body = html.escape(truncated_body)
                else:
                    issue_body = ""

                issue_labels = issue.get("labels") or []

                # Generate labels HTML
                labels_html = ""
                for label in issue_labels[:5]:  # Show max 5 labels
                    if label:  # Make sure label is not None
                        labels_html += f'<span class="issue-label">{html.escape(str(label))}</span>'

                issues_expandable += f"""
                <div class="issue-item">
                    <div class="issue-header">
                        <h4 class="issue-title">
                            <a href="{issue_url}" target="_blank">{issue_title}</a>
                            <span class="issue-number">#{issue_number}</span>
                        </h4>
                    </div>
                    <div class="issue-meta">
                        <span class="issue-age">{issue_age}</span>
                        <span>by <span class="issue-author">{issue_author}</span></span>
                        <div class="issue-labels">{labels_html}</div>
                    </div>
                    {f'<div class="issue-body">{issue_body}</div>' if issue_body.strip() else ''}
                </div>"""

            if issues_count > 10:
                issues_expandable += f'<div class="no-issues-message">... and {issues_count - 10} more issues</div>'

            issues_expandable += "</div>"
        elif issues_count > 0:
            issues_expandable = f'<div id="{repo_name.replace("/", "-")}-issues" class="issues-expandable"><div class="no-issues-message">Issues data not available</div></div>'

        # Data attributes for filtering
        ci_filter_value = (
            "no_ci" if not ci_info.get("has_ci", False) else latest_run_status
        )
        issues_filter_value = "has_issues" if issues_count > 0 else "no_issues"

        html_content += f"""
                    <tr data-org="{org}" data-ci-status="{ci_filter_value}" data-issues="{issues_filter_value}" data-repo-name="{repo_name.lower()}" data-description="{description.lower()}">
                        <td class="repo-name">
                            <a href="{url}" target="_blank">{repo_name}</a>
                            {issues_expandable}
                        </td>
                        <td>{org_badge}</td>
                        <td class="description" title="{description}">{description}</td>
                        <td class="ci-status">{ci_display}</td>
                        <td>{issues_badge}</td>
                        <td>{workflow_display}</td>
                        <td class="updated-time">{updated_at}</td>
                        <td>{privacy_badge}</td>
                    </tr>
"""

    html_content += f"""
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>Generated on {datetime.now().strftime('%Y-%m-%d at %H:%M:%S UTC')}</p>
            <p>Report includes {total_repos} repositories across organizations: {org_list}</p>
            <p>Report saved to: {output_path}</p>
        </div>
    </div>
    
    <script>
        function toggleIssues(issuesId) {{
            const issuesDiv = document.getElementById(issuesId);
            if (issuesDiv) {{
                issuesDiv.classList.toggle('expanded');
            }}
        }}
        
        function filterTable() {{
            const orgFilter = document.getElementById('orgFilter').value.toLowerCase();
            const ciFilter = document.getElementById('ciFilter').value.toLowerCase();
            const issuesFilter = document.getElementById('issuesFilter').value.toLowerCase();
            const searchFilter = document.getElementById('searchFilter').value.toLowerCase();
            const table = document.getElementById('repoTable');
            const rows = table.getElementsByTagName('tr');
            
            for (let i = 1; i < rows.length; i++) {{
                const row = rows[i];
                const org = row.getAttribute('data-org').toLowerCase();
                const ciStatus = row.getAttribute('data-ci-status').toLowerCase();
                const issuesStatus = row.getAttribute('data-issues').toLowerCase();
                const repoName = row.getAttribute('data-repo-name');
                const description = row.getAttribute('data-description');
                
                let showRow = true;
                
                // Organization filter
                if (orgFilter && org !== orgFilter) {{
                    showRow = false;
                }}
                
                // CI status filter
                if (ciFilter && ciStatus !== ciFilter) {{
                    showRow = false;
                }}
                
                // Issues filter
                if (issuesFilter && issuesStatus !== issuesFilter) {{
                    showRow = false;
                }}
                
                // Search filter
                if (searchFilter && !repoName.includes(searchFilter) && !description.includes(searchFilter)) {{
                    showRow = false;
                }}
                
                row.style.display = showRow ? '' : 'none';
            }}
        }}
    </script>
</body>
</html>
"""

    return html_content


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Generate an HTML report of repositories with CI status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s zerodaysec jon-the-dev
  %(prog)s --orgs zerodaysec jon-the-dev --threads 15
  %(prog)s --orgs microsoft --limit 100 --output my_report.html --threads 20
  %(prog)s microsoft --limit 50 --no-ci-check --no-issues
  %(prog)s zerodaysec --threads 10 --no-issues  # CI only, no issues
        """,
    )

    parser.add_argument(
        "organizations",
        nargs="*",
        help="GitHub organizations to analyze (can also use --orgs)",
    )

    parser.add_argument(
        "--orgs",
        "--organizations",
        nargs="+",
        dest="orgs_flag",
        help="GitHub organizations to analyze (alternative to positional args)",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Maximum number of repositories to fetch per organization (default: 200)",
    )

    parser.add_argument(
        "--output",
        "-o",
        default="repository_report.html",
        help="Output file path for the HTML report (default: ./repository_ci_report.html)",
    )

    parser.add_argument(
        "--no-issues",
        action="store_true",
        help="Skip issues checking (faster, but no issues information)",
    )

    parser.add_argument(
        "--no-ci-check",
        action="store_true",
        help="Skip CI status checking (faster, but no CI information)",
    )

    parser.add_argument(
        "--threads",
        "--workers",
        type=int,
        default=10,
        help="Number of parallel threads for CI checking (default: 10, max recommended: 20)",
    )

    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="DEPRECATED: Use --threads instead. Number of parallel CI checks to run",
    )

    return parser.parse_args()


def main():
    """Main function"""
    args = parse_arguments()

    # Determine organizations to process
    organizations = args.organizations or args.orgs_flag

    if not organizations:
        print("❌ No organizations specified!")
        print("Usage: python generate_repo_report.py <org1> <org2> ...")
        print("   or: python generate_repo_report.py --orgs <org1> <org2> ...")
        print("\nExamples:")
        print("  python generate_repo_report.py zerodaysec jon-the-dev")
        print("  python generate_repo_report.py --orgs microsoft --limit 50")
        sys.exit(1)

    # Handle deprecated --parallel flag
    max_workers = args.threads
    if args.parallel != 1:
        print("⚠️  Warning: --parallel is deprecated, use --threads instead")
        max_workers = args.parallel

    # Validate thread count
    if max_workers > 20:
        print("⚠️  Warning: Using more than 20 threads may hit GitHub rate limits")
        print("   Consider using fewer threads if you encounter rate limit errors")

    print(f"🚀 Starting repository CI status report generation...")
    print(f"📋 Organizations: {', '.join(organizations)}")
    print(f"📊 Repository limit per org: {args.limit}")
    print(f"🧵 Parallel threads: {max_workers}")
    print(f"📄 Output file: {args.output}")

    # Determine what to check
    check_ci = not args.no_ci_check
    check_issues = not args.no_issues

    if not check_ci and not check_issues:
        print("⚠️  Warning: Both CI and issues checking are disabled")
    elif not check_ci:
        print("⏭️  Skipping CI status checks...")
    elif not check_issues:
        print("⏭️  Skipping issues checks...")

    # Get all repositories
    start_time = time.time()
    repos = get_all_repos(organizations, args.limit)
    fetch_time = time.time() - start_time

    if not repos:
        print("❌ No repositories found!")
        sys.exit(1)

    print(f"⏱️  Repository fetching took {fetch_time:.1f} seconds")

    # Check CI status and issues for each repo
    if check_ci or check_issues:
        ci_start_time = time.time()
        check_repos_ci_and_issues_parallel(repos, max_workers, check_issues)
        ci_time = time.time() - ci_start_time
        action_text = "CI and issues" if check_issues else "CI status"
        print(f"⏱️  {action_text} checking took {ci_time:.1f} seconds")
    else:
        print("⏭️  Skipping all checks...")
        for repo in repos:
            repo["ci_info"] = {
                "has_ci": False,
                "ci_status": "Not checked",
                "latest_run_status": "N/A",
                "latest_run_url": "",
                "workflow_count": 0,
            }
            repo["issues_info"] = {"count": 0, "issues": []}

    # Generate HTML report
    report_start_time = time.time()
    html_content = generate_html_report(repos, organizations, args.output)

    # Write the report
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_content)

    report_time = time.time() - report_start_time
    total_time = time.time() - start_time

    print(f"⏱️  Report generation took {report_time:.1f} seconds")
    print(f"⏱️  Total execution time: {total_time:.1f} seconds")
    print(f"✅ Report generated successfully!")
    print(f"📄 Report saved to: {args.output}")
    print(f"🌐 Open in browser: file://{args.output}")
    print(
        f"📊 Summary: {len(repos)} repositories across {len(organizations)} organizations"
    )


if __name__ == "__main__":
    main()
