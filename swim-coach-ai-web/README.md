# Swim Coach AI

Analyze a swimmer's stroke from phone video with an Olympic‑level AI coach — stroke
technique, root‑cause faults, drills, a race model, and progress tracking.

The app is a single static page (`index.html`) plus a few **Cloudflare Pages Functions**
(`/functions/api/*`). Each visitor uses **their own Anthropic API key** (stored only in
their browser), and an optional **account** (Cloudflare **D1**) syncs swimmers and
progress across devices.

> Robust YOLO tracking runs only in the optional local helper (see below) — it can't run
> on Cloudflare's serverless runtime. Online, the app uses the in‑browser pose model.

---

## ⚠️ Before you push to GitHub

**Never commit an API key.** The `.gitignore` already excludes
`swimlens_config.json`, `swimlens_history.json`, `swimlens_profiles.json`, `.dev.vars`
and `.env`. If a key was ever committed, **rotate it** at console.anthropic.com.

---

## Deploy in ~10 minutes

### 1. Put it on GitHub
```bash
cd swim-coach-ai-web
git init
git add .
git commit -m "Swim Coach AI"
git branch -M main
git remote add origin https://github.com/<you>/swim-coach-ai.git
git push -u origin main
```

### 2. Connect Cloudflare Pages
- Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to Git**.
- Pick the repo. **Build command:** *(none)*. **Build output directory:** `/` (root).
- Deploy. Your app is live at `https://<project>.pages.dev`.

That alone gives you a working app: users add their own Anthropic key in **⚙ Settings**
and analyze video. Accounts/sync stay off until you do step 3.

### 3. (Optional) Turn on accounts + sync with D1
```bash
npm install                       # installs wrangler
npx wrangler login
npx wrangler d1 create swim-coach-ai
```
Copy the printed `database_id` into `wrangler.toml`, then create the tables:
```bash
npx wrangler d1 execute swim-coach-ai --remote --file=./schema.sql
```
Bind the database to Pages so the Functions can see it:
- Pages project → **Settings → Functions → D1 database bindings**
- Variable name **`DB`** → database **swim-coach-ai** → Save → **Redeploy**.

Now the account panel appears in Settings; sign‑ups are stored in D1 and each user's
swimmers/history sync across devices.

### Local development
```bash
npx wrangler pages dev .          # serves index.html + Functions locally
```

---

## How it works

| Concern            | Hosted (Cloudflare)                                   | Local helper (advanced)                 |
|--------------------|-------------------------------------------------------|-----------------------------------------|
| API key            | Your own key, in the **browser** (localStorage)       | Saved in `swimlens_config.json`         |
| Anthropic calls    | `/api/analyze` proxy (adds the key server‑side, no CORS) | Python proxy                          |
| History / profiles | D1 when signed in, else browser localStorage          | Local JSON files                        |
| Robust YOLO tracking | Not available (in‑browser pose model instead)       | YOLO11‑pose + BoT‑SORT                   |

### Endpoints (`functions/api/`)
- `analyze.js` — POST, BYO‑key proxy to Anthropic.
- `config.js` — GET, tells the client it's hosted + whether accounts are on.
- `signup.js` / `login.js` / `logout.js` / `me.js` — email + password auth (PBKDF2, session cookie).
- `history.js` / `profiles.js` — GET/POST, per‑user data (require D1).
- `_auth.js` — shared crypto/session helpers (not routed).

---

## Optional: run the local helper (for YOLO tracking)

The `local-helper/` folder holds the Python launcher. It serves the same `index.html`
from the repo root and adds robust single‑swimmer YOLO tracking.

```bash
cd local-helper
# Windows: double‑click "Swim Coach AI.bat"
# or:
python swimlens_server.py
```
Optionally run **Install AI Tracking (one‑time).bat** to add YOLO11‑pose.
Requires Python 3 only (standard library) for the base app.

---

## Security notes
- Passwords are hashed with PBKDF2‑SHA256; sessions are random tokens in an HttpOnly,
  Secure, SameSite=Lax cookie.
- The BYO key is sent per request and **never** stored on the server.
- This is a hobby‑grade auth setup — add rate limiting / email verification before
  treating it as production infrastructure.

## License
MIT — see `LICENSE` (add one before publishing if you want it open source).

---

## Troubleshooting

**"This uploader does not yet support projects that require a build process…
Please use `wrangler deploy` instead."**
You used the dashboard's **Upload assets** (drag‑and‑drop) flow — it doesn't support
Pages Functions or a `wrangler.toml`. Use one of these instead:
- **Connect to Git** (Workers & Pages → Create → **Pages** → Connect to Git), build
  command blank, output dir `/`; or
- **`npx wrangler pages deploy .`** (after `npx wrangler login`).

Note: use **`wrangler pages deploy`**, *not* `wrangler deploy`. The latter is the Workers
command and will not deploy this Pages + Functions app correctly.
