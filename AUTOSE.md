# AutoSE on this fork

This fork of [msiemens/tinydb](https://github.com/msiemens/tinydb) is pinned at upstream commit
`edff96aeb2616402e9489d46e90a592c05565621` and adds an agentic issue-to-PR workflow
(`.github/workflows/autose.yml`, agent code in `autose/`).

* **Trigger:** a maintainer adds the label `autose` to an issue, or comments `/autose` on it.
  `/autose review` on a PR (whose body says `Fixes #n`) runs only the independent reviewer.
* **Output:** a PR from `autose/issue-<n>` with stage artifacts attached to the Actions run, and an
  independent review posted as a PR comment. **The workflow never merges.**
* Design, ADRs and results: see the submission repository linked in its README.
