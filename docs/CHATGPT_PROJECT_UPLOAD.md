# ChatGPT Project Upload Bundle

Use this when creating a fresh ChatGPT Project from the repo.

The full repository has more than 35 source files, plus generated databases and
artifacts that are too large/noisy for project context. Build the curated bundle
instead:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build_chatgpt_project_bundle.ps1
```

Outputs:

- `chatgpt_project_upload/`
- `chatgpt_project_upload.zip`

The bundle is capped at 35 files. It includes the Streamlit frontend, backend
service layer, model tournament, pipeline workflow, key validation scripts, high
signal docs, selected tests, and a generated `PROJECT_BRIEF.md` with latest run
metadata from `dashboard_data.db`.

Do not upload the full repo when the target has a 35-source limit. Upload the
generated zip or the generated folder contents.

