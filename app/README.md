# ComplyLens app

React + Vite + TypeScript frontend, FastAPI backend, deployed as a Databricks App.

```
app/
├── app.yaml              runtime config + resource bindings
├── requirements.txt      Python deps (databricks-sdk pinned >= 0.57)
├── package.json          Node deps + build script
├── deploy.ps1            build, sync, deploy
├── smoke_test.py         local verification
├── backend/
│   ├── main.py           FastAPI: serves the SPA and /api
│   ├── genie_client.py   Genie Conversation API wrapper with progress events
│   ├── suggestions.py    the 12 certified questions
│   └── config.py         settings from the Apps runtime
└── frontend/
    ├── index.html
    └── src/
        ├── App.tsx                     tiles, ask box, chips, answer cards
        ├── api.ts                      SSE client
        └── components/
            ├── ResultTable.tsx         typed table with status badges
            ├── ResultChart.tsx         conservative auto-charting
            └── EvidenceDrawer.tsx      the audit artifact
```

---

## Genie at the core

Every number the user sees originates from a Genie conversation:

- **Posture tiles** — one Genie call. The certified Q01 returns the overall percentage
  plus the covered / partial / gap / high-criticality-gap counts in a single row, so all
  four tiles come from one round trip rather than four. Clicking a tile opens the
  conversation that produced it, making the dashboard an on-ramp into the chat rather
  than a static thing sitting beside it.
- **Every answer** — streamed from the Conversation API, with Genie's own SQL shown.
- **Suggested questions** — the same twelve the agent was benchmarked against, so the app
  never invites a question that was not measured.

Two endpoints run SQL directly, and both are point reads rather than analysis:
`/api/evidence/{id}` and `/api/catalog`. They fire when a user clicks a row Genie already
returned. Routing a single-record lookup through a second Genie conversation would add
20 seconds to a click that should be instant, with no analytical benefit. This is called
out in the code where it happens.

**Remove Genie and the app has nothing to display.**

---

## Local development

```powershell
# Terminal 1 — backend
cd app
python -m pip install -r requirements.txt
$env:DATABRICKS_HOST="https://<workspace>.cloud.databricks.com"
$env:GENIE_SPACE_ID="<space id>"
$env:DATABRICKS_WAREHOUSE_ID="<warehouse id>"
$env:CATALOG="workspace"
python -m uvicorn backend.main:app --reload --port 8000

# Terminal 2 — frontend with API proxy
cd app
npm install
npm run dev        # http://localhost:5173
```

Local auth uses your `databricks auth login` profile via the SDK's default credential
chain. No token needs to be pasted.

Verify without credentials:

```powershell
python app/smoke_test.py
```

---

## Deploy

```powershell
cd app
./deploy.ps1 -Profile complylens -AppName complylens
```

Or manually:

```powershell
npm install; npm run build
databricks sync . /Workspace/Users/<you>/complylens-app --profile complylens
databricks apps deploy complylens `
  --source-code-path /Workspace/Users/<you>/complylens-app --profile complylens
```

`npm run build` must run before deploying — FastAPI serves the SPA from
`frontend/dist`, and that directory is gitignored, so it has to exist locally at
deploy time.

### Resource bindings

In the app's **Resources** panel:

| Resource | Key | Permission |
|---|---|---|
| Genie Agent | `genie-space` | **Can run** |
| SQL warehouse | `sql-warehouse` | **Can use** |

Then grant the app's service principal `USE CATALOG`, `USE SCHEMA` and `SELECT` on
`<catalog>.complylens_genie`:

```sql
GRANT USE CATALOG ON CATALOG workspace TO `<app-service-principal>`;
GRANT USE SCHEMA  ON SCHEMA workspace.complylens_genie TO `<app-service-principal>`;
GRANT SELECT      ON SCHEMA workspace.complylens_genie TO `<app-service-principal>`;
```

Set `CATALOG` in `app.yaml` if yours is not `workspace`.

### No secrets

Databricks Apps injects `DATABRICKS_HOST`, `DATABRICKS_CLIENT_ID`,
`DATABRICKS_CLIENT_SECRET` and `DATABRICKS_APP_PORT` automatically. There is no `.env`,
no secret scope and no API key anywhere in this app.

---

## Free Edition constraints

| Constraint | How the app handles it |
|---|---|
| Apps auto-stop 24h after deploy | Restart before demoing or submitting. The demo video is the durable artifact. |
| One 2X-Small warehouse | 180s Genie timeout, and the UI narrates each stage so a 20s wait reads as progress rather than a hang. |
| Max 3 apps per account | — |
| Files capped at 10 MB | Vite splits `recharts` into its own chunk; largest asset is ~513 KB. |
| `databricks-sdk` pre-installed at 0.33.0 | Pinned to `>=0.57.0` in `requirements.txt`; 0.33 predates the Genie API. |

## Troubleshooting

**Tiles show "—" and health says misconfigured** — the resource bindings are missing.
Check `/api/health` for exactly which one.

**"Genie did not respond within 180s"** — the warehouse was cold. Run one query in the
SQL editor to warm it, then retry. Always do this before recording a demo.

**Answers return but rows are empty** — the app service principal can reach the Genie
Agent but not the underlying views. Re-run the `GRANT` statements above.
