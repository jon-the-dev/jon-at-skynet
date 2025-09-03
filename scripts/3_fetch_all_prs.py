#!/usr/bin/env python3
"""
Fetch ALL open PRs from repositories owned by specific users/orgs.
This includes PRs created by anyone (Dependabot, external contributors, etc.)
"""

import json
import subprocess
import argparse
from datetime import datetime, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

def run_command(command, timeout=30):
    """Execute a command and return the result."""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True, timeout=timeout)
        return result.stdout
    except subprocess.TimeoutExpired:
        print(f"Command timed out after {timeout} seconds: {command}")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {command}")
        print(f"Error: {e.stderr}")
        return None

def check_github_rate_limits():
    """Check GitHub API rate limits and return current status."""
    try:
        # Get rate limit info for different endpoints
        rate_limit_info = {}
        
        # Core API rate limit (most API calls)
        core_result = run_command("gh api rate_limit", timeout=10)
        if core_result:
            try:
                rate_data = json.loads(core_result)
                core = rate_data.get('rate', {})
                search = rate_data.get('resources', {}).get('search', {})  # Search API has separate limits
                
                rate_limit_info = {
                    'core': {
                        'limit': core.get('limit', 0),
                        'remaining': core.get('remaining', 0),
                        'reset': core.get('reset', 0),
                        'used': core.get('used', 0)
                    },
                    'search': {
                        'limit': search.get('limit', 0),
                        'remaining': search.get('remaining', 0),
                        'reset': search.get('reset', 0),
                        'used': search.get('used', 0)
                    }
                }
                
                # Calculate reset time in minutes
                if rate_limit_info['core']['reset']:
                    reset_time = datetime.fromtimestamp(rate_limit_info['core']['reset'])
                    now = datetime.now()
                    rate_limit_info['core']['reset_in_minutes'] = max(0, (reset_time - now).total_seconds() / 60)
                else:
                    rate_limit_info['core']['reset_in_minutes'] = None
                
                if rate_limit_info['search']['reset']:
                    reset_time = datetime.fromtimestamp(rate_limit_info['search']['reset'])
                    now = datetime.now()
                    rate_limit_info['search']['reset_in_minutes'] = max(0, (reset_time - now).total_seconds() / 60)
                else:
                    rate_limit_info['search']['reset_in_minutes'] = None
                    
                return rate_limit_info
                
            except json.JSONDecodeError:
                print("⚠️  Could not parse rate limit response")
                return None
        else:
            print("⚠️  Could not fetch rate limit information")
            return None
            
    except Exception as e:
        print(f"⚠️  Error checking rate limits: {e}")
        return None

def estimate_api_requests(owners):
    """Estimate the number of API requests needed for the operation."""
    estimates = {
        'search_requests': len(owners),  # 1 search API call per owner
        'repo_list_requests': 0,
        'pr_list_requests': 0,
        'total_requests': 0
    }
    
    # Get rough repo count estimates (this uses API calls, but minimal)
    for owner in owners:
        print(f"🔍 Estimating requests for {owner}...")
        
        # Check if it's an org or user (1 API call)
        owner_type = "users"
        check_org = run_command(f"gh api orgs/{owner} 2>/dev/null", timeout=5)
        if check_org:
            owner_type = "orgs"
        
        # Get first page of repos to estimate total (1 API call)
        command = f"gh api '{owner_type}/{owner}/repos?per_page=100&page=1' --jq '. | length'"
        result = run_command(command, timeout=10)
        
        if result and result.strip().isdigit():
            first_page_count = int(result.strip())
            
            # Estimate total repos (GitHub paginates at 100 per page)
            if first_page_count == 100:
                # Likely more pages, estimate 2-3x for large orgs
                estimated_repos = first_page_count * 3  # Conservative estimate
            else:
                estimated_repos = first_page_count
            
            # Estimate API calls needed:
            # - Repo listing: ~1 call per 100 repos  
            repo_pages = (estimated_repos + 99) // 100  # Round up
            # - PR listing: 1 call per repo
            pr_calls = estimated_repos
            
            estimates['repo_list_requests'] += repo_pages
            estimates['pr_list_requests'] += pr_calls
            
            print(f"   📊 {owner}: ~{estimated_repos} repos = {repo_pages + pr_calls} API calls")
        else:
            print(f"   ⚠️  Could not estimate repos for {owner}")
            # Use conservative fallback
            estimates['repo_list_requests'] += 5
            estimates['pr_list_requests'] += 200
    
    estimates['total_requests'] = (estimates['search_requests'] + 
                                 estimates['repo_list_requests'] + 
                                 estimates['pr_list_requests'])
    
    return estimates

def validate_rate_limits_before_execution(owners):
    """Check if we have enough API quota before starting the operation."""
    print("🔍 Checking GitHub API rate limits...")
    
    # Get current rate limit status
    rate_limits = check_github_rate_limits()
    if not rate_limits:
        print("⚠️  Could not check rate limits. Proceeding with caution...")
        return True  # Don't block execution if we can't check
    
    # Estimate API requests needed
    print("\n📊 Estimating API requests needed...")
    estimates = estimate_api_requests(owners)
    
    # Display current rate limit status
    print(f"\n📈 Current GitHub API Status:")
    core = rate_limits['core']
    search = rate_limits['search']
    
    print(f"   🔧 Core API: {core['remaining']:,}/{core['limit']:,} remaining")
    if core.get('reset_in_minutes'):
        print(f"      (Resets in {core['reset_in_minutes']:.0f} minutes)")
        
    print(f"   🔍 Search API: {search['remaining']:,}/{search['limit']:,} remaining")  
    if search.get('reset_in_minutes'):
        print(f"      (Resets in {search['reset_in_minutes']:.0f} minutes)")
    
    print(f"\n🎯 Estimated API Requests Needed:")
    print(f"   🔍 Search API: {estimates['search_requests']} requests")
    print(f"   📋 Core API: {estimates['repo_list_requests'] + estimates['pr_list_requests']:,} requests")
    print(f"   📊 Total: {estimates['total_requests']:,} requests")
    
    # Check if we have enough quota
    core_needed = estimates['repo_list_requests'] + estimates['pr_list_requests']
    search_needed = estimates['search_requests']
    
    issues = []
    
    if core_needed > core['remaining']:
        issues.append(f"Core API: Need {core_needed:,}, have {core['remaining']:,}")
        
    if search_needed > search['remaining']:
        issues.append(f"Search API: Need {search_needed:,}, have {search['remaining']:,}")
    
    if issues:
        print(f"\n❌ INSUFFICIENT API QUOTA:")
        for issue in issues:
            print(f"   • {issue}")
        print(f"\n💡 Options:")
        print(f"   1. Wait for rate limits to reset")
        print(f"   2. Use a GitHub token with higher limits") 
        print(f"   3. Process fewer repositories")
        
        # Ask user if they want to proceed anyway
        try:
            response = input(f"\n⚠️  Proceed anyway? (y/N): ").lower().strip()
            return response == 'y'
        except KeyboardInterrupt:
            print(f"\n\n👋 Operation cancelled by user")
            return False
    else:
        print(f"\n✅ Sufficient API quota available!")
        return True

def fetch_all_prs_for_owner(owner):
    """Fetch ALL open PRs in repositories owned by a specific user/org."""
    print(f"Fetching all open PRs in {owner}'s repositories...")
    all_prs = []
    page = 1
    
    while True:
        print(f"  Page {page}...")
        
        # Search for all open PRs in repos owned by this user/org
        command = f'''gh api -X GET search/issues \
            -f q="is:pr state:open org:{owner} user:{owner}" \
            -f sort="created" \
            -f order="desc" \
            -f per_page=100 \
            -f page={page} \
            --jq '.items[] | {{
                number: .number,
                title: .title,
                url: .html_url,
                created_at: .created_at,
                updated_at: .updated_at,
                draft: .draft,
                user: .user.login,
                repository_url: .repository_url
            }}' '''
        
        result = run_command(command)
        if not result or not result.strip():
            break
        
        pr_count = 0
        for line in result.strip().split('\n'):
            if line:
                try:
                    pr = json.loads(line)
                    # Extract repository info
                    if pr.get('repository_url'):
                        repo_parts = pr['repository_url'].replace('https://api.github.com/repos/', '').split('/')
                        pr['repository'] = {
                            'nameWithOwner': f"{repo_parts[0]}/{repo_parts[1]}",
                            'owner': repo_parts[0],
                            'name': repo_parts[1]
                        }
                    all_prs.append(pr)
                    pr_count += 1
                except json.JSONDecodeError:
                    continue
        
        if pr_count < 100:
            break
        page += 1
    
    print(f"  Found {len(all_prs)} PRs for {owner}")
    return all_prs

def get_repo_list(owner):
    """Get list of all repositories for an owner."""
    print(f"Getting repository list for {owner}...")
    repos = []
    page = 1
    
    # Determine if it's a user or an org
    owner_type = "users"
    check_org = run_command(f"gh api orgs/{owner} 2>/dev/null")
    if check_org:
        owner_type = "orgs"
    
    while True:
        command = f"gh api '{owner_type}/{owner}/repos?per_page=100&page={page}' | jq -r '.[].full_name'"
        result = run_command(command)
        
        if not result or not result.strip():
            break
            
        for line in result.strip().split('\n'):
            if line and not line.startswith('{'):  # Skip JSON objects, only process repo names
                repos.append(line)
        
        # Check if we got less than 100 repos (last page)
        if len(result.strip().split('\n')) < 100:
            break
        page += 1
    
    print(f"  Found {len(repos)} repositories")
    return repos

def fetch_prs_for_single_repo(repo):
    """Fetch PRs for a single repository - used for parallel processing."""
    try:
        command = f'''gh pr list --repo {repo} --state open --json number,title,url,createdAt,updatedAt,isDraft,author --limit 100'''
        
        result = run_command(command, timeout=60)
        if result and result.strip():
            try:
                prs = json.loads(result)
                repo_prs = []
                for pr in prs:
                    # Transform to match our expected format
                    pr_data = {
                        'number': pr['number'],
                        'title': pr['title'],
                        'url': pr['url'],
                        'created_at': pr['createdAt'],
                        'updated_at': pr['updatedAt'],
                        'draft': pr.get('isDraft', False),
                        'user': pr.get('author', {}).get('login', 'unknown'),
                        'repository': {
                            'nameWithOwner': repo,
                            'owner': repo.split('/')[0],
                            'name': repo.split('/')[1]
                        }
                    }
                    repo_prs.append(pr_data)
                return repo, repo_prs, None  # repo, prs, error
            except json.JSONDecodeError as e:
                return repo, [], f"JSON decode error: {e}"
        return repo, [], None
    except Exception as e:
        return repo, [], f"Error: {e}"

def fetch_prs_by_repo_list(owner, max_workers=8):
    """Alternative method: Get all repos first, then fetch PRs for each using parallel processing."""
    repos = get_repo_list(owner)
    all_prs = []
    
    if not repos:
        return all_prs
    
    print(f"  Processing {len(repos)} repositories with {max_workers} parallel workers...")
    start_time = time.time()
    completed_count = 0
    error_count = 0
    
    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_repo = {executor.submit(fetch_prs_for_single_repo, repo): repo for repo in repos}
        
        # Process results as they complete
        for future in as_completed(future_to_repo):
            repo = future_to_repo[future]
            try:
                repo_name, prs, error = future.result()
                completed_count += 1
                
                if error:
                    print(f"    [{completed_count}/{len(repos)}] ❌ {repo_name}: {error}")
                    error_count += 1
                else:
                    pr_count = len(prs)
                    all_prs.extend(prs)
                    if pr_count > 0:
                        print(f"    [{completed_count}/{len(repos)}] ✅ {repo_name}: {pr_count} PRs")
                    else:
                        print(f"    [{completed_count}/{len(repos)}] ⚪ {repo_name}: 0 PRs")
                        
            except Exception as exc:
                completed_count += 1
                error_count += 1
                print(f"    [{completed_count}/{len(repos)}] ❌ {repo}: Exception - {exc}")
    
    elapsed_time = time.time() - start_time
    print(f"  ✅ Completed in {elapsed_time:.1f}s - Total PRs found: {len(all_prs)} (Errors: {error_count})")
    return all_prs

def generate_markdown_report(prs, output_file="open-prs-report-all.md"):
    """Generate a comprehensive markdown report from PR data."""
    
    # Group PRs by organization/owner
    orgs = defaultdict(list)
    for pr in prs:
        if pr.get('repository'):
            org = pr['repository']['owner']
            orgs[org].append(pr)
    
    # Sort organizations by PR count
    sorted_orgs = sorted(orgs.items(), key=lambda x: len(x[1]), reverse=True)
    
    # Generate report
    with open(output_file, 'w') as f:
        f.write("# Complete Open Pull Requests Report - All Contributors\n\n")
        f.write(f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write("**Repository Owners**: jon-the-dev, zerodaysec\n")
        f.write("**Includes**: All PRs from all contributors (Dependabot, external contributors, etc.)\n\n")
        
        f.write("## Summary\n\n")
        f.write(f"- **Total Open PRs**: {len(prs)}\n")
        f.write("- **Organizations**:\n")
        for org, org_prs in sorted_orgs:
            f.write(f"  - {org}: {len(org_prs)} PR{'s' if len(org_prs) != 1 else ''}\n")
        
        # Count by author
        authors = defaultdict(int)
        for pr in prs:
            author = pr.get('user', 'unknown')
            authors[author] += 1
        
        f.write("\n### Top Contributors\n\n")
        for author, count in sorted(authors.items(), key=lambda x: x[1], reverse=True)[:10]:
            f.write(f"- {author}: {count} PR{'s' if count != 1 else ''}\n")
        
        f.write("\n## Pull Requests by Organization\n")
        
        for org, org_prs in sorted_orgs:
            f.write(f"\n### {org} ({len(org_prs)} PR{'s' if len(org_prs) != 1 else ''})\n\n")
            f.write("| Repository | PR # | Title | Author | Created | Updated | Status |\n")
            f.write("|------------|------|-------|--------|---------|---------|--------|\n")
            
            # Sort PRs by repo name and then by PR number
            sorted_prs = sorted(org_prs, key=lambda x: (
                x['repository']['name'] if x.get('repository') else '',
                x.get('number', 0)
            ))
            
            for pr in sorted_prs:
                repo_name = pr['repository']['nameWithOwner'] if pr.get('repository') else 'Unknown'
                pr_num = pr.get('number', '?')
                title = pr.get('title', 'No title')[:80]  # Truncate long titles
                author = pr.get('user', 'unknown')
                created = pr.get('created_at', '')[:10] if pr.get('created_at') else ''
                updated = pr.get('updated_at', '')[:10] if pr.get('updated_at') else ''
                url = pr.get('url', '#')
                status = "Draft" if pr.get('draft') else "Open"
                
                # Escape pipe characters in title
                title = title.replace('|', '\\|')
                if len(pr.get('title', '')) > 80:
                    title += '...'
                
                f.write(f"| [{repo_name}]({url}) | #{pr_num} | {title} | {author} | {created} | {updated} | {status} |\n")
        
        # Add analysis section
        f.write("\n## Analysis\n\n")
        
        # PR age distribution
        f.write("### PR Age Distribution\n\n")
        now = datetime.now(timezone.utc)
        age_buckets = {"< 1 week": 0, "1-4 weeks": 0, "1-3 months": 0, "3-6 months": 0, "6-12 months": 0, "> 1 year": 0}
        
        for pr in prs:
            if pr.get('created_at'):
                try:
                    created = datetime.fromisoformat(pr['created_at'].replace('Z', '+00:00'))
                    age_days = (now - created).days
                    
                    if age_days < 7:
                        age_buckets["< 1 week"] += 1
                    elif age_days < 30:
                        age_buckets["1-4 weeks"] += 1
                    elif age_days < 90:
                        age_buckets["1-3 months"] += 1
                    elif age_days < 180:
                        age_buckets["3-6 months"] += 1
                    elif age_days < 365:
                        age_buckets["6-12 months"] += 1
                    else:
                        age_buckets["> 1 year"] += 1
                except:
                    pass
        
        for bucket, count in age_buckets.items():
            if count > 0:
                f.write(f"- {bucket}: {count} PR{'s' if count != 1 else ''}\n")
        
        # Author analysis
        f.write("\n### PR Authors\n\n")
        author_types = {
            "Dependabot": 0,
            "jon-the-dev": 0,
            "External Contributors": 0,
            "Other Bots": 0
        }
        
        for pr in prs:
            author = pr.get('user', '').lower()
            if 'dependabot' in author:
                author_types["Dependabot"] += 1
            elif author == 'jon-the-dev':
                author_types["jon-the-dev"] += 1
            elif 'bot' in author or '[bot]' in author:
                author_types["Other Bots"] += 1
            else:
                author_types["External Contributors"] += 1
        
        for author_type, count in sorted(author_types.items(), key=lambda x: x[1], reverse=True):
            if count > 0:
                f.write(f"- {author_type}: {count} PR{'s' if count != 1 else ''}\n")
        
        # Pattern analysis
        f.write("\n### PR Patterns\n\n")
        patterns = defaultdict(int)
        for pr in prs:
            title = pr.get('title', '').lower()
            if 'bump' in title or 'update' in title:
                patterns['Dependency Updates'] += 1
            elif 'claude github actions' in title:
                patterns['Claude GitHub Actions'] += 1
            elif 'wip' in title:
                patterns['Work in Progress'] += 1
            elif 'fix' in title:
                patterns['Bug Fixes'] += 1
            elif 'feat' in title or 'feature' in title:
                patterns['Features'] += 1
            else:
                patterns['Other'] += 1
        
        for pattern, count in sorted(patterns.items(), key=lambda x: x[1], reverse=True):
            f.write(f"- {pattern}: {count} PR{'s' if count != 1 else ''}\n")
    
    print(f"\nReport generated: {output_file}")
    return output_file

def main(max_workers=8):
    """Main function to fetch all PRs from specified owners."""
    owners = ["jon-the-dev", "zerodaysec"]
    all_prs = []
    
    print(f"🚀 Fetching ALL open PRs from repositories owned by {', '.join(owners)}...")
    print(f"   Using {max_workers} parallel workers for faster processing\n")
    
    # Check API rate limits before execution
    print("🔍 Checking GitHub API rate limits...")
    if not validate_rate_limits_before_execution(owners):
        print("❌ Aborting execution due to insufficient API quota")
        return
    print("✅ Rate limit validation passed\n")
    
    overall_start_time = time.time()
    
    for owner in owners:
        print(f"\n{'='*60}")
        print(f"📋 Processing: {owner}")
        print('='*60)
        
        owner_start_time = time.time()
        
        # Method 1: Search API
        print("🔍 Method 1: GitHub Search API")
        prs1 = fetch_all_prs_for_owner(owner)
        
        # Method 2: Repo list (more accurate for private repos)
        print(f"\n📂 Method 2: Repository List (Parallel Processing)")
        prs2 = fetch_prs_by_repo_list(owner, max_workers=max_workers)
        
        # Combine and deduplicate
        pr_map = {}
        for pr in prs1 + prs2:
            key = f"{pr.get('repository', {}).get('nameWithOwner', '')}#{pr.get('number', '')}"
            if key not in pr_map:
                pr_map[key] = pr
        
        owner_prs = list(pr_map.values())
        owner_elapsed = time.time() - owner_start_time
        print(f"\n✅ {owner} completed in {owner_elapsed:.1f}s - Total unique PRs: {len(owner_prs)}")
        all_prs.extend(owner_prs)
    
    overall_elapsed = time.time() - overall_start_time
    
    print(f"\n{'='*60}")
    print(f"🎯 FINAL RESULTS")
    print('='*60)
    print(f"📊 Total PRs across all repositories: {len(all_prs)}")
    print(f"⏱️  Total processing time: {overall_elapsed:.1f}s")
    print(f"🚀 Speed improvement: ~{len(all_prs)/overall_elapsed:.1f} PRs/second")
    
    # Generate report
    print(f"\n📝 Generating markdown report...")
    output_file = "/Volumes/Backup4TB/code-projects/jon-at-skynet/open-prs-report-all.md"
    generate_markdown_report(all_prs, output_file)
    
    # Summary statistics
    print("\n📈 Summary Statistics:")
    
    org_counts = defaultdict(int)
    author_counts = defaultdict(int)
    
    for pr in all_prs:
        if pr.get('repository'):
            org_counts[pr['repository']['owner']] += 1
        author_counts[pr.get('user', 'unknown')] += 1
    
    print("\n🏢 By Organization:")
    for org, count in sorted(org_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {org}: {count} PRs")
    
    print("\n👥 Top Authors:")
    for author, count in sorted(author_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  - {author}: {count} PRs")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Fetch ALL open PRs from GitHub repositories')
    parser.add_argument('--workers', '-w', type=int, default=8, 
                       help='Number of parallel workers (default: 8, recommended: 4-12)')
    parser.add_argument('--dry-run', action='store_true', help='Only check rate limits, do not fetch PRs')
    args = parser.parse_args()
    
    if args.workers < 1 or args.workers > 20:
        print("⚠️  Warning: Workers should be between 1-20 for optimal performance")
    
    if args.dry_run:
        print("🔍 Dry run mode: checking rate limits only...")
        owners = ["jon-the-dev", "zerodaysec"]
        validate_rate_limits_before_execution(owners)
    else:
        main(max_workers=args.workers)