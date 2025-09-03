
get-work: get-prs
	scripts/1_generate_repo_report.py zerodaysec jon-the-dev

cleanup-prs:
	scripts/2_merge_safe_prs.py

get-prs:
	scripts/3_fetch_all_prs.py

monthly-cost-review:
	claude '/aws:monthly-cost-review'