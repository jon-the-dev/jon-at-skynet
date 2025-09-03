# Adding CODEOWNERS

Please launch parallel infrastructure-maintainer subagent to gather the repos under zerodaysec and jon-the-dev (personal repos) and then launch a batch ofor missing CODEOWNERS files. Use the Github MCP Server and tools and fallback to the gh cli. Identify a batch of the next 3 repos to validate.

launch parallel infrastructure-maintainer subagent for each of the in scope repos. so this should launch 10 subagents.

Look for CODEOWNERS or a PR that addresses a missing CODEOWNERS. If it is missing please add and create a PR.

Use a file like GH_CODEOWNERS_CLEANUP.json to track state of repos, PRs etc. I want to be able to run this prompt a few times to get all of the repos cleaned up. Read this file if it exists before starting so we have our state.
