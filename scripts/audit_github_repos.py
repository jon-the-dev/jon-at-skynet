#!/usr/bin/env python3
"""
GitHub Repository Audit Script

This script audits all repositories across your GitHub organizations to check for:
1. Standard files: CODEOWNERS, README.md, LICENSE
2. Standard labels: frontend, backend, bug, feature, documentation, etc.
3. Repository metadata and compliance status

Usage:
    python scripts/audit_github_repos.py
    python scripts/audit_github_repos.py --org jon-the-dev
    python scripts/audit_github_repos.py --output audit_report.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class GitHubAuditor:
    """GitHub repository auditor for compliance checking."""

    # Standard files that should exist in repositories
    STANDARD_FILES = {
        "CODEOWNERS": {
            "paths": [".github/CODEOWNERS", "CODEOWNERS", ".github/CODEOWNERS.md"],
            "required": True,
            "description": "Code ownership and review assignments",
        },
        "README": {
            "paths": ["README.md", "README.rst", "README.txt", "README"],
            "required": True,
            "description": "Project documentation and getting started guide",
        },
        "LICENSE": {
            "paths": ["LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE"],
            "required": True,
            "description": "Software license information",
        },
        "CONTRIBUTING": {
            "paths": [".github/CONTRIBUTING.md", "CONTRIBUTING.md", "CONTRIBUTING.rst"],
            "required": False,
            "description": "Contribution guidelines",
        },
        "CODE_OF_CONDUCT": {
            "paths": [".github/CODE_OF_CONDUCT.md", "CODE_OF_CONDUCT.md"],
            "required": False,
            "description": "Community code of conduct",
        },
        "SECURITY": {
            "paths": [".github/SECURITY.md", "SECURITY.md"],
            "required": False,
            "description": "Security policy and vulnerability reporting",
        },
        "CHANGELOG": {
            "paths": ["CHANGELOG.md", "CHANGELOG.rst", "CHANGELOG.txt", "HISTORY.md"],
            "required": False,
            "description": "Version history and release notes",
        },
    }

    # Standard labels that should exist on all repositories
    STANDARD_LABELS = {
        "frontend": {"color": "B60205", "description": "Frontend development work"},
        "backend": {"color": "0052CC", "description": "Backend development work"},
        "bug": {"color": "D73A4A", "description": "Something isn't working"},
        "feature": {"color": "7F4A00", "description": "New feature or enhancement"},
        "documentation": {
            "color": "0075CA",
            "description": "Improvements or additions to documentation",
        },
        "enhancement": {
            "color": "A2EEEF",
            "description": "Enhancement to existing functionality",
        },
        "good first issue": {"color": "7057FF", "description": "Good for newcomers"},
        "help wanted": {"color": "008672", "description": "Extra attention is needed"},
        "wontfix": {"color": "FFFFFF", "description": "This will not be worked on"},
        "duplicate": {
            "color": "CFD3D7",
            "description": "This issue or pull request already exists",
        },
        "invalid": {"color": "E4E669", "description": "This doesn't seem right"},
        "question": {
            "color": "D876E3",
            "description": "Further information is requested",
        },
        "security": {
            "color": "FF0000",
            "description": "Security-related issues or fixes",
        },
        "performance": {"color": "FF6600", "description": "Performance improvements"},
        "testing": {"color": "00FF00", "description": "Testing-related work"},
        "triage": {"color": "00FF00", "description": "Triage-related work"},
        "planned": {"color": "00FF00", "description": "Planned-related work"},
        "in-review": {"color": "00FF00", "description": "In-Review-related work"},
    }

    def __init__(self, token: Optional[str] = None):
        """Initialize the auditor with GitHub token."""
        self.token = token or os.getenv("GITHUB_TOKEN")
        if not self.token:
            raise ValueError(
                "GitHub token required. Set GITHUB_TOKEN environment variable "
                "or pass token directly."
            )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"token {self.token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "GitHub-Repo-Auditor/1.0",
            }
        )

        self.rate_limit_remaining = 5000
        self.rate_limit_reset = time.time()

    def _make_request(self, url: str, params: Optional[Dict] = None) -> Dict:
        """Make authenticated request to GitHub API with rate limiting."""
        if self.rate_limit_remaining < 10:
            sleep_time = max(0, self.rate_limit_reset - time.time() + 1)
            if sleep_time > 0:
                print(f"⏳ Rate limit low, sleeping for {sleep_time:.1f}s...")
                time.sleep(sleep_time)

        response = self.session.get(url, params=params)

        # Update rate limit info
        self.rate_limit_remaining = int(
            response.headers.get("X-RateLimit-Remaining", 0)
        )
        self.rate_limit_reset = int(
            response.headers.get("X-RateLimit-Reset", time.time())
        )

        if response.status_code == 404:
            return None

        response.raise_for_status()
        return response.json()

    def get_user_orgs(self) -> List[str]:
        """Get list of organizations the user belongs to."""
        print("🔍 Fetching user organizations...")
        orgs_data = self._make_request("https://api.github.com/user/orgs")

        orgs = [org["login"] for org in orgs_data]
        print(f"📋 Found {len(orgs)} organizations: {', '.join(orgs)}")
        return orgs

    def get_org_repos(self, org: str) -> List[Dict]:
        """Get all repositories for an organization."""
        print(f"📂 Fetching repositories for organization: {org}")
        repos = []
        page = 1

        while True:
            params = {"per_page": 100, "page": page, "type": "all"}
            repos_data = self._make_request(
                f"https://api.github.com/orgs/{org}/repos", params
            )

            if not repos_data:
                break

            repos.extend(repos_data)

            if len(repos_data) < 100:
                break

            page += 1

        print(f"  ✅ Found {len(repos)} repositories in {org}")
        return repos

    def check_file_exists(
        self, owner: str, repo: str, file_paths: List[str]
    ) -> Tuple[bool, Optional[str]]:
        """Check if any of the specified file paths exist in the repository."""
        for path in file_paths:
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
            result = self._make_request(url)
            if result:
                return True, path
        return False, None

    def get_repo_labels(self, owner: str, repo: str) -> List[Dict]:
        """Get all labels for a repository."""
        labels = []
        page = 1

        while True:
            params = {"per_page": 100, "page": page}
            labels_data = self._make_request(
                f"https://api.github.com/repos/{owner}/{repo}/labels", params
            )

            if not labels_data:
                break

            labels.extend(labels_data)

            if len(labels_data) < 100:
                break

            page += 1

        return labels

    def create_label(self, owner: str, repo: str, label_name: str, label_config: Dict) -> bool:
        """Create a label in a repository."""
        url = f"https://api.github.com/repos/{owner}/{repo}/labels"
        data = {
            "name": label_name,
            "color": label_config["color"],
            "description": label_config["description"]
        }
        
        response = self.session.post(url, json=data)
        
        if response.status_code == 201:
            print(f"    ✅ Created label: {label_name}")
            return True
        elif response.status_code == 422:
            # Label already exists
            print(f"    ℹ️ Label already exists: {label_name}")
            return True
        else:
            print(f"    ❌ Failed to create label {label_name}: {response.status_code}")
            return False

    def create_missing_labels(self, owner: str, repo: str, missing_labels: List[Dict]) -> int:
        """Create all missing labels for a repository."""
        created_count = 0
        print(f"  📝 Creating {len(missing_labels)} missing labels for {owner}/{repo}")
        
        for label_info in missing_labels:
            label_name = label_info["name"]
            label_config = {
                "color": label_info["color"],
                "description": label_info["description"]
            }
            
            if self.create_label(owner, repo, label_name, label_config):
                created_count += 1
                
        return created_count

    def search_issues(self, owner: str, repo: str, title: str) -> Optional[Dict]:
        """Search for existing issues by title."""
        # GitHub's search API requires URL encoding of the query
        import urllib.parse
        query = f"repo:{owner}/{repo} in:title \"{title}\""
        encoded_query = urllib.parse.quote(query)
        
        url = f"https://api.github.com/search/issues?q={encoded_query}"
        result = self._make_request(url)
        
        if result and result.get("items"):
            # Return the most recent issue with matching title
            for item in result["items"]:
                if item["title"] == title:
                    return item
        
        return None

    def create_issue(self, owner: str, repo: str, title: str, body: str, labels: List[str] = None) -> bool:
        """Create an issue in a repository."""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        data = {
            "title": title,
            "body": body,
            "labels": labels or []
        }
        
        response = self.session.post(url, json=data)
        
        if response.status_code == 201:
            issue_data = response.json()
            print(f"    ✅ Created issue #{issue_data['number']}: {title}")
            return True
        else:
            print(f"    ❌ Failed to create issue '{title}': {response.status_code}")
            return False

    def reopen_issue(self, owner: str, repo: str, issue_number: int, title: str) -> bool:
        """Reopen a closed issue."""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
        data = {"state": "open"}
        
        response = self.session.patch(url, json=data)
        
        if response.status_code == 200:
            print(f"    🔄 Reopened issue #{issue_number}: {title}")
            return True
        else:
            print(f"    ❌ Failed to reopen issue #{issue_number}: {response.status_code}")
            return False

    def create_or_update_issue(self, owner: str, repo: str, title: str, body: str, labels: List[str] = None) -> bool:
        """Create a new issue or reopen a closed one with the same title."""
        # Search for existing issue with same title
        existing_issue = self.search_issues(owner, repo, title)
        
        if existing_issue:
            if existing_issue["state"] == "open":
                print(f"    ℹ️ Issue already exists and is open: #{existing_issue['number']} - {title}")
                return True
            elif existing_issue["state"] == "closed":
                # Reopen the closed issue
                return self.reopen_issue(owner, repo, existing_issue["number"], title)
        
        # No existing issue found, create new one
        return self.create_issue(owner, repo, title, body, labels)

    def create_compliance_issues(self, owner: str, repo: str, audit_result: Dict) -> int:
        """Create GitHub issues for compliance problems."""
        created_count = 0
        issues = audit_result['compliance']['issues']
        
        if not issues:
            return 0
            
        print(f"  🎫 Creating {len(issues)} compliance issues for {owner}/{repo}")
        
        # Group issues by type for better organization
        file_issues = [issue for issue in issues if "Missing required file" in issue]
        label_issues = [issue for issue in issues if "Missing standard label" in issue]
        
        # Create issue for missing files
        if file_issues:
            file_list = "\n".join([f"- {issue.replace('Missing required file: ', '')}" for issue in file_issues])
            title = "Repository Compliance: Missing Required Files"
            body = f"""# Missing Required Files

This repository is missing the following required standard files:

{file_list}

## Action Required
Please add these files to ensure repository compliance and improve project documentation.

## Standard Files Guide
- **CODEOWNERS**: Define code ownership and review assignments
- **README.md**: Project documentation and getting started guide  
- **LICENSE**: Software license information
- **CONTRIBUTING.md**: Contribution guidelines
- **CODE_OF_CONDUCT.md**: Community code of conduct
- **SECURITY.md**: Security policy and vulnerability reporting

---
*This issue was created automatically by the repository audit system.*"""
            
            if self.create_or_update_issue(owner, repo, title, body, ["triage", "documentation"]):
                created_count += 1
        
        # Create issue for missing labels (if any remain after label creation)
        if label_issues:
            label_list = "\n".join([f"- {issue.replace('Missing standard label: ', '')}" for issue in label_issues])
            title = "Repository Compliance: Missing Standard Labels"
            body = f"""# Missing Standard Labels

This repository is missing the following standard labels:

{label_list}

## Action Required
These labels help with issue and PR organization. They should be added to maintain consistency across repositories.

## Standard Labels
Standard labels include: frontend, backend, bug, feature, documentation, enhancement, security, performance, testing, and others for consistent project management.

---
*This issue was created automatically by the repository audit system.*"""
            
            if self.create_or_update_issue(owner, repo, title, body, ["triage", "maintenance"]):
                created_count += 1
                
        return created_count

    def audit_repository(self, repo_data: Dict, fix_labels: bool = False, create_issues: bool = False) -> Dict:
        """Audit a single repository for compliance."""
        owner = repo_data["owner"]["login"]
        repo_name = repo_data["name"]

        print(f"  🔍 Auditing {owner}/{repo_name}")

        audit_result = {
            "name": repo_name,
            "full_name": repo_data["full_name"],
            "private": repo_data["private"],
            "archived": repo_data["archived"],
            "fork": repo_data["fork"],
            "default_branch": repo_data["default_branch"],
            "language": repo_data["language"],
            "description": repo_data["description"],
            "url": repo_data["html_url"],
            "created_at": repo_data["created_at"],
            "updated_at": repo_data["updated_at"],
            "size": repo_data["size"],
            "stars": repo_data["stargazers_count"],
            "forks": repo_data["forks_count"],
            "open_issues": repo_data["open_issues_count"],
            "files": {},
            "labels": {"existing": [], "missing": [], "total_count": 0},
            "compliance": {
                "files_score": 0,
                "labels_score": 0,
                "overall_score": 0,
                "issues": [],
            },
        }

        # Check for standard files
        files_found = 0
        required_files_found = 0
        required_files_total = sum(
            1 for f in self.STANDARD_FILES.values() if f["required"]
        )

        for file_type, file_config in self.STANDARD_FILES.items():
            exists, found_path = self.check_file_exists(
                owner, repo_name, file_config["paths"]
            )
            audit_result["files"][file_type] = {
                "exists": exists,
                "path": found_path,
                "required": file_config["required"],
                "description": file_config["description"],
            }

            if exists:
                files_found += 1
                if file_config["required"]:
                    required_files_found += 1
            elif file_config["required"]:
                audit_result["compliance"]["issues"].append(
                    f"Missing required file: {file_type}"
                )

        # Calculate files compliance score
        audit_result["compliance"]["files_score"] = (
            (
                (required_files_found / required_files_total * 0.8)
                + (files_found / len(self.STANDARD_FILES) * 0.2)
            )
            * 100
            if required_files_total > 0
            else 100
        )

        # Check labels
        existing_labels = self.get_repo_labels(owner, repo_name)
        existing_label_names = {label["name"].lower() for label in existing_labels}

        audit_result["labels"]["total_count"] = len(existing_labels)

        for label_name, label_config in self.STANDARD_LABELS.items():
            if label_name.lower() in existing_label_names:
                audit_result["labels"]["existing"].append(
                    {"name": label_name, "found": True}
                )
            else:
                audit_result["labels"]["missing"].append(
                    {
                        "name": label_name,
                        "color": label_config["color"],
                        "description": label_config["description"],
                    }
                )
                audit_result["compliance"]["issues"].append(
                    f"Missing standard label: {label_name}"
                )

        # Calculate labels compliance score
        labels_found = len(audit_result["labels"]["existing"])
        labels_total = len(self.STANDARD_LABELS)
        audit_result["compliance"]["labels_score"] = (
            (labels_found / labels_total) * 100 if labels_total > 0 else 100
        )

        # Calculate overall compliance score
        audit_result["compliance"]["overall_score"] = (
            audit_result["compliance"]["files_score"] * 0.6
            + audit_result["compliance"]["labels_score"] * 0.4
        )

        # Fix labels if requested
        if fix_labels and audit_result["labels"]["missing"]:
            created_labels = self.create_missing_labels(owner, repo_name, audit_result["labels"]["missing"])
            if created_labels > 0:
                # Re-scan labels after creation to update the audit result
                updated_labels = self.get_repo_labels(owner, repo_name)
                updated_label_names = {label["name"].lower() for label in updated_labels}
                
                # Update the audit result with newly created labels
                audit_result["labels"]["existing"] = []
                audit_result["labels"]["missing"] = []
                audit_result["labels"]["total_count"] = len(updated_labels)
                
                for label_name, label_config in self.STANDARD_LABELS.items():
                    if label_name.lower() in updated_label_names:
                        audit_result["labels"]["existing"].append({"name": label_name, "found": True})
                    else:
                        audit_result["labels"]["missing"].append({
                            "name": label_name,
                            "color": label_config["color"],
                            "description": label_config["description"],
                        })
                
                # Recalculate labels score
                labels_found = len(audit_result["labels"]["existing"])
                audit_result["compliance"]["labels_score"] = (
                    (labels_found / len(self.STANDARD_LABELS)) * 100 if len(self.STANDARD_LABELS) > 0 else 100
                )
                
                # Recalculate overall score
                audit_result["compliance"]["overall_score"] = (
                    audit_result["compliance"]["files_score"] * 0.6
                    + audit_result["compliance"]["labels_score"] * 0.4
                )
                
                # Update issues list to remove resolved label issues
                audit_result["compliance"]["issues"] = [
                    issue for issue in audit_result["compliance"]["issues"] 
                    if not issue.startswith("Missing standard label:")
                ]
                
                # Add any remaining missing label issues
                for missing_label in audit_result["labels"]["missing"]:
                    audit_result["compliance"]["issues"].append(
                        f"Missing standard label: {missing_label['name']}"
                    )

        # Create compliance issues if requested
        if create_issues:
            self.create_compliance_issues(owner, repo_name, audit_result)

        return audit_result

    def audit_organization(self, org: str, fix_labels: bool = False, create_issues: bool = False) -> Dict:
        """Audit all repositories in an organization."""
        print(f"\n🏢 Auditing organization: {org}")

        repos = self.get_org_repos(org)

        org_audit = {
            "organization": org,
            "total_repos": len(repos),
            "audited_at": datetime.now().isoformat(),
            "repositories": [],
            "summary": {
                "files_compliance": [],
                "labels_compliance": [],
                "overall_compliance": [],
                "common_issues": {},
                "repo_types": {"public": 0, "private": 0, "archived": 0, "forks": 0},
            },
        }

        for repo_data in repos:
            # Skip archived repos unless explicitly requested
            if repo_data["archived"]:
                org_audit["summary"]["repo_types"]["archived"] += 1
                continue

            # Update repo type counts
            if repo_data["private"]:
                org_audit["summary"]["repo_types"]["private"] += 1
            else:
                org_audit["summary"]["repo_types"]["public"] += 1

            if repo_data["fork"]:
                org_audit["summary"]["repo_types"]["forks"] += 1

            # Audit the repository
            try:
                repo_audit = self.audit_repository(repo_data, fix_labels, create_issues)
                org_audit["repositories"].append(repo_audit)

                # Collect compliance scores
                org_audit["summary"]["files_compliance"].append(
                    repo_audit["compliance"]["files_score"]
                )
                org_audit["summary"]["labels_compliance"].append(
                    repo_audit["compliance"]["labels_score"]
                )
                org_audit["summary"]["overall_compliance"].append(
                    repo_audit["compliance"]["overall_score"]
                )

                # Collect common issues
                for issue in repo_audit["compliance"]["issues"]:
                    if issue not in org_audit["summary"]["common_issues"]:
                        org_audit["summary"]["common_issues"][issue] = 0
                    org_audit["summary"]["common_issues"][issue] += 1

            except Exception as e:
                print(f"  ❌ Error auditing {repo_data['full_name']}: {e}")
                continue

        # Calculate summary statistics
        if org_audit["summary"]["files_compliance"]:
            org_audit["summary"]["avg_files_compliance"] = sum(
                org_audit["summary"]["files_compliance"]
            ) / len(org_audit["summary"]["files_compliance"])

        if org_audit["summary"]["labels_compliance"]:
            org_audit["summary"]["avg_labels_compliance"] = sum(
                org_audit["summary"]["labels_compliance"]
            ) / len(org_audit["summary"]["labels_compliance"])

        if org_audit["summary"]["overall_compliance"]:
            org_audit["summary"]["avg_overall_compliance"] = sum(
                org_audit["summary"]["overall_compliance"]
            ) / len(org_audit["summary"]["overall_compliance"])

        print(
            f"  ✅ Completed audit of {org} - {len(org_audit['repositories'])} repositories processed"
        )
        return org_audit

    def generate_report(
        self, audit_results: List[Dict], output_file: Optional[str] = None
    ) -> str:
        """Generate a comprehensive audit report."""
        report = {
            "audit_metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_organizations": len(audit_results),
                "total_repositories": sum(org["total_repos"] for org in audit_results),
                "standard_files_checked": list(self.STANDARD_FILES.keys()),
                "standard_labels_checked": list(self.STANDARD_LABELS.keys()),
            },
            "organizations": audit_results,
            "global_summary": {
                "repo_distribution": {
                    "public": 0,
                    "private": 0,
                    "archived": 0,
                    "forks": 0,
                },
                "compliance_averages": {"files": 0, "labels": 0, "overall": 0},
                "most_common_issues": {},
                "recommendations": [],
            },
        }

        # Calculate global statistics
        all_files_scores = []
        all_labels_scores = []
        all_overall_scores = []
        all_issues = {}

        for org_audit in audit_results:
            # Aggregate repo types
            for repo_type, count in org_audit["summary"]["repo_types"].items():
                report["global_summary"]["repo_distribution"][repo_type] += count

            # Aggregate scores
            all_files_scores.extend(org_audit["summary"]["files_compliance"])
            all_labels_scores.extend(org_audit["summary"]["labels_compliance"])
            all_overall_scores.extend(org_audit["summary"]["overall_compliance"])

            # Aggregate issues
            for issue, count in org_audit["summary"]["common_issues"].items():
                if issue not in all_issues:
                    all_issues[issue] = 0
                all_issues[issue] += count

        # Calculate global averages
        if all_files_scores:
            report["global_summary"]["compliance_averages"]["files"] = sum(
                all_files_scores
            ) / len(all_files_scores)

        if all_labels_scores:
            report["global_summary"]["compliance_averages"]["labels"] = sum(
                all_labels_scores
            ) / len(all_labels_scores)

        if all_overall_scores:
            report["global_summary"]["compliance_averages"]["overall"] = sum(
                all_overall_scores
            ) / len(all_overall_scores)

        # Sort issues by frequency
        report["global_summary"]["most_common_issues"] = dict(
            sorted(all_issues.items(), key=lambda x: x[1], reverse=True)[:10]
        )

        # Generate recommendations
        recommendations = []

        if report["global_summary"]["compliance_averages"]["files"] < 80:
            recommendations.append(
                "📁 Priority: Add missing standard files (CODEOWNERS, README, LICENSE)"
            )

        if report["global_summary"]["compliance_averages"]["labels"] < 70:
            recommendations.append(
                "🏷️ Priority: Standardize repository labels across all repos"
            )

        top_issues = list(report["global_summary"]["most_common_issues"].keys())[:3]
        if top_issues:
            recommendations.append(
                f"🔧 Focus on resolving: {', '.join(top_issues[:2])}"
            )

        if not recommendations:
            recommendations.append(
                "✅ Great job! Your repositories are well-maintained."
            )

        report["global_summary"]["recommendations"] = recommendations

        # Save to file if specified
        if output_file:
            with open(output_file, "w") as f:
                json.dump(report, f, indent=2, default=str)
            print(f"📄 Report saved to: {output_file}")

        return json.dumps(report, indent=2, default=str)


def main():
    """Main function to run the audit."""
    parser = argparse.ArgumentParser(
        description="Audit GitHub repositories for compliance"
    )
    parser.add_argument("--org", type=str, help="Specific organization to audit")
    parser.add_argument(
        "--output",
        type=str,
        default="github_audit_report.json",
        help="Output file for the audit report",
    )
    parser.add_argument(
        "--token", type=str, help="GitHub token (or use GITHUB_TOKEN env var)"
    )
    parser.add_argument(
        "--include-archived",
        action="store_true",
        help="Include archived repositories in the audit",
    )
    parser.add_argument(
        "--fix-labels",
        action="store_true",
        help="Automatically create missing standard labels",
    )
    parser.add_argument(
        "--create-issues",
        action="store_true",
        help="Create GitHub issues for compliance problems with triage label",
    )

    args = parser.parse_args()

    try:
        auditor = GitHubAuditor(token=args.token)

        print("🚀 Starting GitHub Repository Audit")
        print(f"📊 Checking for {len(auditor.STANDARD_FILES)} standard files")
        print(f"🏷️ Checking for {len(auditor.STANDARD_LABELS)} standard labels")
        if args.fix_labels:
            print("🔧 Auto-fixing: Missing labels will be created")
        if args.create_issues:
            print("🎫 Auto-creating: Issues will be created for compliance problems")
        print()

        # Get organizations to audit
        if args.org:
            orgs_to_audit = [args.org]
        else:
            orgs_to_audit = auditor.get_user_orgs()

        # Perform audit
        audit_results = []
        for org in orgs_to_audit:
            try:
                org_audit = auditor.audit_organization(org, args.fix_labels, args.create_issues)
                audit_results.append(org_audit)
            except Exception as e:
                print(f"❌ Error auditing organization {org}: {e}")
                continue

        # Generate report
        print("\n📋 Generating comprehensive audit report...")
        report_json = auditor.generate_report(audit_results, args.output)

        # Print summary
        report_data = json.loads(report_json)
        global_summary = report_data["global_summary"]

        print("\n" + "=" * 80)
        print("📊 AUDIT SUMMARY")
        print("=" * 80)
        print(
            f"Total Organizations: {report_data['audit_metadata']['total_organizations']}"
        )
        print(
            f"Total Repositories: {report_data['audit_metadata']['total_repositories']}"
        )
        print()
        print("Repository Distribution:")
        for repo_type, count in global_summary["repo_distribution"].items():
            print(f"  {repo_type.title()}: {count}")
        print()
        print("Compliance Averages:")
        for metric, score in global_summary["compliance_averages"].items():
            print(f"  {metric.title()}: {score:.1f}%")
        print()
        print("Top Issues:")
        for issue, count in list(global_summary["most_common_issues"].items())[:5]:
            print(f"  {issue}: {count} repositories")
        print()
        print("Recommendations:")
        for rec in global_summary["recommendations"]:
            print(f"  {rec}")
        print("=" * 80)

        print(f"\n✅ Audit completed successfully!")
        print(f"📄 Full report saved to: {args.output}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
