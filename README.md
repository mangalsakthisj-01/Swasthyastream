# SwasthyaGrid — GitHub-Ready Streamlit App

**This version intentionally uses flat files only.** There is no required `backend/` subfolder, so uploading the files to the top level of a GitHub repository will not break Python imports.

## Deploy in about 2 minutes (assuming GitHub upload and dependency installation are quick)

1. Download this ZIP and extract it on your computer.
2. Create a **new empty GitHub repository** (recommended: `swasthyagrid-streamlit`).
3. On GitHub, select **Add file → Upload files**.
4. Open the extracted folder and upload these files directly to the repository root: `app.py`, `backend_services.py`, `backend_triage.py`, `requirements.txt`, `README.md`, and `DEPLOY_NOW.txt`. `.gitignore` is optional.
5. Commit the upload to the `main` branch.
6. Open <https://share.streamlit.io/> → **Create app**.
7. Choose your repository, branch `main`, and main file path `app.py`, then select **Deploy**.

If you use an existing repository that already has the old flattened backend files, a **new clean repository is safer**. The flat app files do not import the old `config.py`, `database.py`, etc.; they use `backend_services.py` and `backend_triage.py` instead.

## Run locally

Install Python 3.11+, open a terminal in this folder, then run:

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Open <http://localhost:8501>.

## Demo sign-in

| Role | User ID | Password |
|---|---|---|
| Admin | `admin` | `Admin@12345` |
| Field Officer | `officer` | `Officer@12345` |
| Representative | `representative` | `Rep@123456` |
| Citizen | `citizen` | `Citizen@12345` |

The app creates sample accounts on the first launch when `ALLOW_DEMO_LOGINS` is not set to false. **Do not use these public demo passwords with real citizen data.**

## Optional Gemini-assisted review

No AI key is required to start. To enable optional Gemini text/image review, add this in Streamlit app **Settings → Secrets**:

```toml
GEMINI_API_KEY = "your-key"
MAX_UPLOAD_MB = "10"
```

The citizen submission form requires explicit consent before report text and one image can be sent to Gemini. The report is retained if the model is unavailable. The rule engine still gives critical reports an urgent-routing recommendation. All AI results are advisory; media, outbreaks and complaints still require appropriate human review.

## Important data and security limits

- This is a deployable prototype, not a certified emergency dispatch, diagnosis or government system.
- SQLite is stored under `data/` by default. Streamlit-hosted local files may not persist across restarts/redeployments; use a durable managed database before relying on reports long-term. This quick-start package is focused on a fast demonstration.
- Demo credentials are known, and the default demo mode is for testing only. Do not expose sensitive or personal data using these credentials.
- Use HTTPS, a managed durable database, strong secrets, backups, access review, and a privacy/security assessment before any production deployment.
- Upload validation and size limits are applied by the UI; do not treat this starter as a security-certified file-ingestion service.
