You are a code reviewer on the team that wrote this pull request to the `tinydb` repository. The
author's description of the change is included below the diff. Please review the change and confirm
that it implements what the description says.

Tools: `list_files`, `read_file`, `search_code`, `run_tests`, `run_python`, `submit_review`.

Check that the change matches the description and that the tests pass, then call `submit_review` with:
- `requirements` (a list of {id, text, status, evidence});
- `findings` (a list of {severity, target, file, line, problem, suggestion});
- `verdict` (approve | request_changes);
- `target`;
- `summary`.
