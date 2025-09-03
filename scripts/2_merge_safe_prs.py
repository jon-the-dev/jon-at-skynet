#!/usr/bin/env python3
"""
Script to check and merge PRs with passing status checks
Usage: python merge_safe_prs.py
"""

import json
import subprocess
import sys
from typing import List, Dict, Any, Optional


# Global counters for summary
stats = {
    "total_prs": 0,
    "merged_prs": 0,
    "auto_merged_prs": 0,
    "dependabot_recreated": 0,
    "failed_checks": 0,
    "pending_checks": 0,
    "not_mergeable": 0,
    "errors": 0
}


def run_gh_command(cmd: List[str]) -> Optional[Dict[Any, Any]]:
    """Run a GitHub CLI command and return JSON result."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(result.stdout) if result.stdout.strip() else None
    except subprocess.CalledProcessError as e:
        print(f"❌ Command failed: {' '.join(cmd)}")
        print(f"Error: {e.stderr}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON from command: {' '.join(cmd)}")
        print(f"Output: {result.stdout}")
        return None


def get_open_prs(owner: str) -> List[Dict[str, Any]]:
    """Get all open PRs for a given organization."""
    cmd = [
        "gh", "search", "prs",
        "--state=open",
        f"--owner={owner}",
        "--json", "number,repository,title,url,author",
        "--limit", "100"
    ]
    
    result = run_gh_command(cmd)
    return result if result else []


def is_dependabot_pr(author: Dict[str, Any]) -> bool:
    """Check if a PR is from Dependabot."""
    if not author:
        return False
    
    login = author.get("login", "").lower()
    return login in ["dependabot[bot]", "dependabot"]


def recreate_dependabot_pr(repo_name: str, pr_number: int, pr_title: str) -> bool:
    """Recreate a Dependabot PR by commenting to trigger recreation."""
    print(f"🤖 Attempting to recreate Dependabot PR #{pr_number}")
    
    # Comment on the PR to trigger Dependabot to recreate it
    comment_cmd = [
        "gh", "pr", "comment", str(pr_number),
        "--repo", repo_name,
        "--body", "@dependabot recreate"
    ]
    
    try:
        subprocess.run(comment_cmd, check=True, capture_output=True, text=True)
        print(f"✅ Tagged Dependabot PR #{pr_number} for recreation")
        stats["dependabot_recreated"] += 1
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to tag PR #{pr_number} for recreation")
        if e.stderr:
            print(f"Error: {e.stderr}")
        stats["errors"] += 1
        return False


def check_and_merge_pr(repo_name: str, pr_number: int, pr_title: str, pr_url: str, author: Dict[str, Any]) -> None:
    """Check if a PR is safe to merge and merge it if so."""
    stats["total_prs"] += 1
    
    author_name = author.get("login", "Unknown") if author else "Unknown"
    is_dependabot = is_dependabot_pr(author)
    
    print(f"\n🔍 Checking PR #{pr_number} in {repo_name}")
    print(f"   📝 Title: {pr_title}")
    print(f"   👤 Author: {author_name}{' 🤖' if is_dependabot else ''}")
    
    # Get PR status
    cmd = [
        "gh", "pr", "view", str(pr_number),
        "--repo", repo_name,
        "--json", "statusCheckRollup,mergeable,mergeStateStatus"
    ]
    
    status_data = run_gh_command(cmd)
    if not status_data:
        print(f"❌ Failed to get status for PR #{pr_number}")
        stats["errors"] += 1
        return
    
    mergeable = status_data.get("mergeable", "UNKNOWN")
    merge_state_status = status_data.get("mergeStateStatus", "UNKNOWN")
    status_checks = status_data.get("statusCheckRollup", [])
    
    # Check if mergeable
    if mergeable != "MERGEABLE":
        print(f"❌ PR #{pr_number} is not mergeable (status: {mergeable})")
        stats["not_mergeable"] += 1
        
        # Special handling for Dependabot PRs with conflicts
        if is_dependabot and merge_state_status == "DIRTY":
            print(f"🔄 Dependabot PR has merge conflicts - attempting to recreate")
            recreate_dependabot_pr(repo_name, pr_number, pr_title)
        elif is_dependabot:
            print(f"🤖 Dependabot PR not mergeable (merge state: {merge_state_status})")
            print(f"   Consider manually checking or recreating this PR")
        
        return
    
    # Check status checks
    failed_checks = []
    pending_checks = []
    
    for check in status_checks:
        conclusion = check.get("conclusion")
        status = check.get("status")
        name = check.get("name", "Unknown")
        
        if conclusion in ["FAILURE", "CANCELLED", "TIMED_OUT"]:
            failed_checks.append(name)
        elif status in ["IN_PROGRESS", "QUEUED", "PENDING"]:
            pending_checks.append(name)
    
    if failed_checks:
        print(f"❌ PR #{pr_number} has failed checks: {', '.join(failed_checks)}")
        stats["failed_checks"] += 1
        return
    
    if pending_checks:
        print(f"⏳ PR #{pr_number} has pending checks: {', '.join(pending_checks)}")
        stats["pending_checks"] += 1
        return
    
    # If we get here, the PR is safe to merge
    print(f"✅ PR #{pr_number} is safe to merge - all checks passed")
    print(f"🔗 {pr_url}")
    
    # Merge the PR
    merge_cmd = [
        "gh", "pr", "merge", str(pr_number),
        "--repo", repo_name,
        "--squash",
        "--delete-branch"
    ]
    
    try:
        subprocess.run(merge_cmd, check=True, capture_output=True, text=True)
        print(f"🎉 Successfully merged PR #{pr_number}")
        stats["merged_prs"] += 1
    except subprocess.CalledProcessError as e:
        # Check if it's a branch protection policy issue
        if "base branch policy prohibits the merge" in e.stderr:
            print(f"🔒 PR #{pr_number} blocked by branch protection - trying auto-merge")
            
            # Try with --auto flag for branch protection
            auto_merge_cmd = merge_cmd + ["--auto"]
            try:
                subprocess.run(auto_merge_cmd, check=True, capture_output=True, text=True)
                print(f"⏰ PR #{pr_number} queued for auto-merge when requirements are met")
                stats["auto_merged_prs"] += 1
            except subprocess.CalledProcessError as auto_e:
                print(f"❌ Failed to queue PR #{pr_number} for auto-merge")
                if auto_e.stderr:
                    print(f"Error: {auto_e.stderr}")
                stats["errors"] += 1
        else:
            print(f"⚠️  Failed to merge PR #{pr_number} - may need manual intervention")
            if e.stderr:
                print(f"Error: {e.stderr}")
            stats["errors"] += 1


def main():
    """Main function to check and merge safe PRs."""
    print("🔍 Finding all open PRs across both organizations...")
    
    # Get all open PRs from both organizations
    organizations = ["zerodaysec", "jon-the-dev"]
    all_prs = []
    
    for org in organizations:
        print(f"📋 Fetching PRs from {org}...")
        prs = get_open_prs(org)
        all_prs.extend(prs)
        print(f"Found {len(prs)} open PRs in {org}")
    
    if not all_prs:
        print("No open PRs found in either organization.")
        return
    
    print(f"\n📊 Total PRs to check: {len(all_prs)}")
    
    # Process each PR
    for pr in all_prs:
        try:
            repo_name = pr["repository"]["nameWithOwner"]
            pr_number = pr["number"]
            pr_title = pr["title"]
            pr_url = pr["url"]
            author = pr.get("author")
            
            check_and_merge_pr(repo_name, pr_number, pr_title, pr_url, author)
            
        except KeyError as e:
            print(f"❌ Missing required field in PR data: {e}")
            print(f"PR data: {pr}")
            continue
        except Exception as e:
            print(f"❌ Unexpected error processing PR: {e}")
            print(f"PR data: {pr}")
            stats["errors"] += 1
            continue
    
    # Print summary
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60)
    print(f"Total PRs processed: {stats['total_prs']}")
    print(f"✅ Successfully merged: {stats['merged_prs']}")
    print(f"⏰ Queued for auto-merge: {stats['auto_merged_prs']}")
    print(f"🤖 Dependabot PRs recreated: {stats['dependabot_recreated']}")
    print(f"❌ PRs with failed checks: {stats['failed_checks']}")
    print(f"⏳ PRs with pending checks: {stats['pending_checks']}")
    print(f"🚫 PRs not mergeable: {stats['not_mergeable']}")
    print(f"⚠️  Errors encountered: {stats['errors']}")
    print("="*60)
    
    print("\n🏁 Finished checking and merging PRs!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Script interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)
