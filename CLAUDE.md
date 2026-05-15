# DELULUREEL — CLAUDE.md
*Ultimo aggiornamento: 2026-05-06*
*Progetto: OBIRIEC LABS — Armando Brecciaroli*

---

## PRIMA COSA DA FARE AD OGNI SESSIONE

Eseguire la skill `delulureel-saas-model` per ricaricare il contesto completo del modello prodotto.

---

## IDENTITA DEL PROGETTO

**DELULUREEL** — SaaS web che trasforma brano musicale + foto in videoclip sincronizzato.
**Dominio:** delulureel.com (registrato Porkbun 2026-04-29)
**Tagline:** *Be delulu enough to drop your reel.*
**Working dir:** `/Volumes/Crucial X9/CLAUDE_Works/DELULUREEL`
**Stato attuale:** MVP in sviluppo — Fase 1 completata

---

## STACK TECNICO

| Layer       | Tech                        | Note                                 |
|-------------|-----------------------------|--------------------------------------|
| Backend     | Python 3 / Flask            | `app_server.py` — porta 5000-5100    |
| Database    | Supabase (PostgreSQL + Auth) | `schema.sql` pronto                 |
| Auth        | Supabase Auth + JWT          | sessione Flask (cookie)             |
| Pagamenti   | Stripe Subscriptions         | trial 7gg, card obbligatoria        |
| Video AI    | fal.ai — Kling 3.0 Pro       | $0.112/sec · endpoint in `.env`     |
| Scene AI    | Anthropic Claude             | `claude-sonnet-4-6`                 |
| Audio       | librosa                      | BPM, beat_times, energy peaks       |
| Assembly    | FFmpeg / ffmpeg-python       | concat + audio sync + 9:16/16:9/1:1 |
| Storage     | Supabase Storage             | bucket `reel-uploads` + `reel-outputs` |
| Deploy      | Fly.io                       | `flyctl deploy` MANUALE da locale — NON auto da GitHub |
| Landing     | HTML statico in `landing/`   | self-contained, zero Flask          |

---

## ARCHITETTURA FILE CHIAVE

```
DELULUREEL/
├── app_server.py              # Flask entry point — route + blueprint wiring
├── schema.sql                 # Schema Supabase (run in SQL Editor)
├── requirements.txt           # Dipendenze Python
├── .env / .env.example        # Variabili d'ambiente
│
├── landing/
│   └── index.html             # Landing page statica (self-contained)
│
├── core/
│   ├── audio_analyzer.py      # librosa — BPM, beats, energy, peaks
│   ├── scene_director.py      # Claude — genera prompt Kling da analisi audio
│   ├── video_generator.py     # fal.ai — submit + poll Kling 3.0 Pro
│   └── assembler.py           # FFmpeg — concat clips + mux audio
│
├── saas/
│   ├── auth/routes.py         # Supabase auth — login, signup, logout, decorator
│   ├── billing/routes.py      # Stripe — setup_trial, webhook, portal
│   └── video/routes.py        # Job queue — generate, status, history, profile
│
├── templates/
│   ├── base.html              # Layout base (nav, container, script)
│   ├── dashboard.html         # Dashboard utente — plan info + reel grid
│   ├── upload.html            # Form upload — dropzone photo+audio, style, AR
│   ├── result.html            # Risultato — video player + download
│   └── auth/
│       ├── login.html         # Form login
│       └── signup.html        # Form signup + piano
│
└── static/
    ├── css/main.css           # Design system app (dark, grad violet/pink/orange)
    └── js/main.js             # Session refresh ogni 25 min
```

---

## REGOLE OPERATIVE CRITICHE

- **DAILY_BUDGET_CAP_USD = 200** — hard stop in `saas/video/routes.py`. MAI aumentare senza analisi costi.
- **Stripe trial: card_required = True SEMPRE** — `payment_method_collection='always'` in `setup_trial()`.
- **NESSUN free tier con generazione reale** — la landing mostra solo demo placeholder, niente API calls.
- **TRIAL_MAX_GENERATIONS = 3** — verificato in `saas/video/routes.py` prima di ogni generazione.
- **MAX_CONCURRENT_PER_USER = 1** — un job alla volta per utente (in-memory lock `_active_user_jobs`).
- **Rate limiting in-memory** — per MVP ok; in produzione sostituire con Redis.
- **pg_dump Supabase** prima di ogni modifica schema — usare Supabase CLI o dashboard backup.
- **MAI pushare su Render senza conferma esplicita di Armando.**
- **MAI usare API key Admin di fal.ai nel codice — solo chiave API normale.**

---

## FLUSSO GENERAZIONE (pipeline completa)

```
POST /video/generate
  → access check (status, trial limit, monthly limit, budget)
  → salva job in Supabase (status: queued)
  → thread background:
      1. audio_analyzer.analyze_audio()      → BPM, beats, peaks
      2. scene_director.generate_scene_prompt()  → prompt Claude
      3. upload photo → Supabase Storage (reel-uploads)
      4. video_generator.submit_reel()       → fal.ai request_id
      5. video_generator.poll_until_done()   → URL video raw
      6. download raw video → tempfile
      7. assembler.assemble_reel()           → FFmpeg mux audio
      8. upload reel → Supabase Storage (reel-outputs)
      9. update job (status: completed, output_url)
     10. increment_reel_count() RPC
```

---

## WEBHOOK STRIPE (obbligatori)

| Evento | Handler | Azione |
|--------|---------|--------|
| `customer.subscription.created` | `_on_subscription_created` | Crea profilo in Supabase |
| `customer.subscription.updated` | `_on_subscription_updated` | Aggiorna status |
| `customer.subscription.trial_will_end` | `_on_trial_will_end` | TODO: email reminder Day 5 (Resend) |
| `invoice.payment_failed` | `_on_payment_failed` | Sospende account |
| `customer.subscription.deleted` | `_on_subscription_deleted` | Cancella accesso |

---

## PIANI E PRICING

**Unità: 1 credito = 5 secondi di video generato (lipsync incluso)**
**Margine minimo garantito: 43% anche su Studio annuale**

| Piano   | Mensile  | Annuale   | Crediti/mese | Equivale a…              |
|---------|----------|-----------|--------------|--------------------------|
| Creator | €16.99   | €169.90   | 10           | 5 reel 10s · 1 reel 30s  |
| Pro     | €39.99   | €399.90   | 30           | 15 reel 10s · 5 reel 30s |
| Studio  | €89.99   | €899.90   | 80           | 40 reel 10s · 13 reel 30s|

Trial 7gg su tutti — 6 crediti trial (≈ 1 reel 30s o 3 reel 10s).
Annuale = 2 mesi gratis.

**TRIAL_MAX_CREDITS = 6** — env var su Render
**CREDIT_LIMITS** in `saas/billing/routes.py`
**_credits_for_duration(secs)** in `saas/video/routes.py` — `ceil(secs/5)`
**deduct_credits(user_id, credits)** — funzione Supabase (sostituisce increment_reel_count)

---

## STATO CORRENTE (2026-05-02 — aggiornato sessione 2)

**Completato sessioni precedenti:**
- MVP completo: landing, auth, billing, video pipeline, templates, CSS, JS
- Deploy Render + custom domain delulureel.com + Cloudflare + Resend + Stripe webhook produzione
- Pipeline fixes: fal.ai 405, lyrics-aware prompts, multi-clip cross-instance (webhooks + DB)
- Admin bypass via `profiles.is_admin BOOLEAN`

**Completato questa sessione — Audit approfondito 8 pass (pass 5→8):**

### Audit pass 5 — nuovi bug trovati e fixati
- **BUG critico**: `fal_result()` in `video_generator.py` usava un solo URL pattern
  - Causa: _startup_recovery() falliva silenziosamente nel recuperare job orfani con v2.6/pro
  - Fix: aggiunto cascade 2-URL identico a fal_status() (endpoint-scoped → global queue)
  - Commit: `a83642e`
- Dead code rimosso: `submit_multi_reel()`, `poll_until_done()` da video_generator.py
- Stale comment fixato: `fly.toml` header aggiornato (era "auto-triggered", ora "MANUALE")

### Audit pass 6 — dead code residuo post-refactoring
- `fal_status()` rimossa (unico caller era poll_until_done, già rimossa)
- `POLL_INTERVAL`, `MAX_WAIT_SINGLE` rimossi da video_generator.py (solo per polling)
- `MAX_WAIT_MULTI` rimosso da import in routes.py (importato ma mai usato)
- `lipsync.py` docstring aggiornata (riferimento a fal_status → fal_result)
- Commit: `e527d2c`

### Audit pass 7 — dead imports
- `import time` e `List` da typing rimossi da video_generator.py
- Commit: `0e73789`

### Audit pass 8 — CONVERGENZA ✅
- Zero nuovi problemi trovati vs pass 7
- Tutti file .py parsano senza errori di sintassi
- Nessun simbolo rimosso trovato in codebase
- Working tree clean

### Audit pass 9 (check indipendente) — 5 nuovi bug trovati e fixati
- **BUG 7 — TOCTOU race in `generate()`**: check slot e acquire slot erano due operazioni
  lock separate con ~100ms+ di lavoro DB nel mezzo. Due request concorrenti dallo stesso
  utente potevano entrambe passare il check prima che una avesse scritto su `_active_user_jobs`.
  Fix: acquisizione atomica del slot (sentinel `'__pending__'`) nel PRIMO lock block + pattern
  `_thread_started` con `finally` che rilascia se il thread non parte.
  Commit: `fcc0893`
- **BUG 8 — dead `global _global_active` in `_run_assembly`**: dichiarazione residua da
  refactoring precedente, la funzione non modifica mai `_global_active`. Rimossa.
- **BUG 9 — `result.html` line 99**: `'Kling 3.0 Pro is building your reel'` → `'Kling AI'`
- **BUG 10 — `upload.html` line 136** + **`landing/index.html` line 986**: stesso stale brand name
- **BUG 11 — `requirements.txt`**: rimossi `resend>=2.0.0` (billing usa raw requests, mai l'SDK)
  e `Pillow>=10.0.0` (nessun import PIL in tutto il codebase)

### Audit pass 10 (check globale definitivo) — 3 nuovi bug trovati e fixati
- **BUG A — `video_generator.py` costante morta**: `MAX_WAIT_MULTI = 2700` definita ma mai
  usata dopo il cleanup del pass 6 (era già rimossa dall'import in routes.py). Rimossa.
- **BUG B — `lipsync.py` `poll_lipsync()` no fallback URL**: nel branch "fetch esplicito result"
  (quando `_fal_status_lipsync` non embedded il dict), si usava solo l'URL endpoint-scoped che
  può restituire 405 su modelli fal.ai più recenti. Aggiunto 2-URL cascade identico a
  `fal_result()`: prova endpoint-scoped, se 405 fallback a global queue URL.
- **BUG C — `schema.sql` RLS mancante su `daily_budget`**: solo `profiles` e `reel_jobs`
  avevano `ENABLE ROW LEVEL SECURITY`. `daily_budget` era accessibile via anon/authenticated
  key PostgREST → attaccante poteva azzerare o gonfiare `usd_spent` e bypassare
  `DAILY_BUDGET_CAP_USD`. Fix: `ALTER TABLE daily_budget ENABLE ROW LEVEL SECURITY;` senza
  policy aggiuntive → zero accesso da client key; solo funzioni SECURITY DEFINER possono scrivere.
  Commit: `0c6d6b4`

**Stato codebase al termine dell'audit pass 10 — CONVERGENZA ✅:**
- `video_generator.py`: solo `submit_reel`, `fal_result`, `transcribe_audio_fal`, helpers di costo
- `lipsync.py`: 2-URL cascade su tutti i path (status 4-URL + result fetch 2-URL)
- `generate()` in `video/routes.py`: TOCTOU race chiusa, pattern check+acquire atomico
- `schema.sql`: RLS abilitata su tutti e 3 le tabelle
- Nessun dead code, nessun import orfano, nessun commento stale, zero branding stale
- 8 commit in attesa di push (`a83642e`…`0c6d6b4`) — push richiede conferma Armando

### Audit pass 11 — 3 nuovi bug trovati e fixati
- **BUG D — `generate()` tmp_dir leak**: `tmp_dir` creata dentro il `try:` → se thread non
  partiva, il `finally` non poteva pulirla (variabile non definita). Fix: `tmp_dir = None`
  prima del `try:`, cleanup condizionale in `finally`.
- **BUG E — `_budget_ok()` / `_record_spend()` race condition**: `_daily` dict mutato senza
  `_lock` → in ambiente multithreaded (`--threads 8`) due thread potevano superare il budget cap
  simultaneamente. Fix: wrap mutazioni dict in `with _lock:`. Chiamata `rpc()` mantenuta fuori
  dal lock per non bloccare altri thread su I/O lento.
- **BUG F — `billing/portal()` crash HTTP 500**: `single().execute()` su profilo mancante
  solleva eccezione non catturata. Fix: try/except + redirect a dashboard.
  Commit: `a758658`

### Audit pass 12 — 2 nuovi bug trovati e fixati
- **BUG G — `app_server.py` no `MAX_CONTENT_LENGTH`**: Flask accettava upload di dimensioni
  illimitate → possibile DoS / OOM su Render free tier. Fix: 60 MB cap (10 MB photo + 50 MB audio).
- **BUG H — `upload.html` poll timeout mostra errore invece di redirect**: `pollStatus()` aveva
  ceiling 90 × 8s = 12 min, poi mostrava "Generation timed out." — ma job full-track 30s impiegano
  20-40+ min. Fix: su `polls > MAX_POLLS`, redirect a `/result/<jobId>` che pollina indefinitamente.
  Commit: `9b94b82`

**Stato codebase al termine dell'audit pass 12 — CONVERGENZA ✅:**
- Tutti i file .py, templates, config, auth, static verificati — zero bug residui noti
- 10 commit in attesa di push (`a83642e`…`9b94b82`) — push richiede conferma Armando
- `daily_budget` RLS già applicata su DB live Supabase (migration via MCP tool)

### Audit pass 13 — check globale esteso (file auth + esterni: GitHub, Render, Supabase)
- **BUG I — CRITICO: `reset_password.html` password reset completamente rotto**:
  `auth/routes.py` documenta esplicitamente che Supabase invia `access_token` nel fragment URL
  (`#access_token=...`) — mai inviato al server. Il template NON aveva il JS per estrarlo, quindi
  il campo hidden era sempre vuoto e il form restituiva sempre "Invalid or expired reset link".
  Fix: IIFE (stesso pattern di `callback.html`) che legge `window.location.hash`, popola il campo
  hidden e rimuove il token dall'URL bar via `history.replaceState` (sicurezza: token non rimane
  nella history del browser).
- **BUG K — `status_stream` SSE ceiling troppo basso**: 80 × 10s = 13 min. Job full-track 30s
  impiegano 20-40+ min. Raised to 360 (~60 min). Endpoint non usato dai template attuali (usa
  fetch polling), ma parte dell'API pubblica.
  Commit: `57a49bc`

**Check esterno completato:**
- GitHub: 13 commit locali non pushati (commits `a83642e`…`57a49bc`) — push richiede conferma Armando
- Render: servizio live su commit `05d1801a` (obsoleto di 13 commit) — deploy automatico dopo push
- Supabase: DB OK, `daily_budget` RLS applicata, tutte le funzioni SECURITY DEFINER intatte
- `render.yaml`: tutte le 20 env var correttamente dichiarate (compresi i `sync: false` per secrets)

**Stato codebase al termine dell'audit pass 13 — CONVERGENZA ✅:**
- Copertura totale: tutti i file .py (core/, saas/), tutti i template HTML, auth templates,
  static/js/main.js, static/css/main.css, render.yaml, requirements.txt, schema.sql, runtime.txt,
  supabase/email-templates/, GitHub, Render service
- 11 commit in attesa di push (`a83642e`…`57a49bc`) — push richiede conferma Armando

### Audit pass 14 — analisi approfondita core/, sicurezza, edge-case input
- **BUG L — SECURITY: `aspect_ratio` non validato → path traversal**: valore dal form usato
  direttamente in `ar_slug = aspect_ratio.replace(':','x')` e interpolato in:
  - `output_key = f'jobs/{job_id}/reel_{ar_slug}.mp4'` (Supabase Storage key)
  - `final_path = os.path.join(tmp, f'reel_{ar_slug}.mp4')` (local filepath)
  Un attacker poteva iniettare `'../outputs/stealth'` per scrivere fuori dal path atteso.
  Fix: whitelist check in `generate()` — qualsiasi valore non in `('9:16','16:9','1:1')` → `'9:16'`.
- **BUG M — `transcribe_audio_fal` lingua hardcodata italiano**: `language='it'` default forzava
  Whisper a disabilitare l'auto-detection. Brani inglesi/spagnoli/francesi venivano "trascritti"
  in italiano producendo testo spazzatura che inquinava i prompt Claude.
  Fix: campo `language` rimosso dal body → Whisper usa auto-detect.
- **BUG N — `extract_segment()` dead code in `assembler.py`**: mai importata né chiamata.
  Rimossa.
- **BUG O — `target_secs=0` su audio full-track < 1s**: `int(0.9) = 0` → `duration="0"` a
  fal.ai → job failure. Fix: `max(5, min(round(audio_dur_sec), MAX_AUDIO_SEC))` — floor di 5s
  (minimo supportato da Kling).
  Commit: `4ce882f`

**Stato codebase al termine dell'audit pass 14 — CONVERGENZA ✅:**
- core/assembler.py: solo `assemble_reel` + `_trim_clips_to_beats` (nessun dead code)
- core/video_generator.py: Whisper auto-detect, nessun parametro lingua
- saas/video/routes.py: aspect_ratio validato a whitelist, target_secs floor 5s
- 12 commit in attesa di push (`a83642e`…`4ce882f`) — push richiede conferma Armando

**Architettura pipeline multi-clip (definitiva):**
```
_run_pipeline (thread breve ~60s):
  → analyze_audio → upload audio → whisper transcription
  → claude scene prompt → upload photo
  → DB: n_clips_expected=N, clip_results='{}'
  → submit N clips con webhook /video/webhook/fal/multi/{job_id}/{i}/{n_clips}
  → finally: rilascia slot + pulisce tmp_dir

fal_webhook_multi (qualsiasi istanza Render):
  → add_clip_result RPC (atomico JSONB merge)
  → se n_done == n_clips: spawna _run_assembly

_run_assembly (thread breve ~120s, su istanza che ha ricevuto l'ultimo webhook):
  → scarica audio da Supabase Storage (non tmp locale)
  → scarica N clip da fal.ai
  → FFmpeg beat-sync assemble
  → upload reel-outputs → mark completed → deduct_credits
```

**Costo fal.ai accumulato in test (non recuperabili):** ~$17.51

### Sessione 2 — i18n completa + Storage RLS

**i18n (commit `681f34a`):**
- `core/i18n.py`: loader traduzioni con `get_lang()` + fallback `Accept-Language`
- `translations/{en,it,fr,de,es}.json`: 5 file completi (nav, dashboard, upload, result, auth)
- `app_server.py`: context processor `inject_i18n` + route `POST /set-lang`
- `templates/base.html`: lang switcher nel nav + `<html lang="{{ lang }}">`
- Tutti i template (`dashboard`, `upload`, `result`, `auth/*`): stringhe JS via `| tojson`
- CSS: `.lang-switcher`, `.lang-switcher-auth`

**Email reminder Day 5 (`_on_trial_will_end`):** già implementata in sessione precedente — confermata presente e funzionante in `billing/routes.py`

**Storage RLS (migration `storage_rls_bucket_security`):**
- `reel-uploads`: reso PRIVATE — foto e audio non più accessibili via CDN pubblico
- `reel-outputs`: rimasto PUBLIC (link condivisione reel) + policy SELECT API per own jobs
- Policy `users_select_own_outputs` + `users_select_own_uploads` su `storage.objects`
- Logica: `split_part(name, '/', 2)` = job_id → join `reel_jobs.user_id = auth.uid()`
- Service_role (server) bypassa RLS → pipeline non impattata

**Stato Supabase DB (completo):**
- `profiles`: RLS ✅
- `reel_jobs`: RLS ✅
- `daily_budget`: RLS ✅ (applicata sessione 1)
- `storage.objects`: RLS ✅ + 2 policy SELECT (applicata questa sessione)
- `reel-uploads` bucket: PRIVATE ✅
- `reel-outputs` bucket: PUBLIC ✅

### Sessione 3 — Error pages, share page, email templates, password reset end-to-end

**Punti implementati:**

**Error pages 404/500 (`commit` in sessione):**
- `templates/404.html` + `templates/500.html` — pagine branded dark, link di ritorno
- `@app.errorhandler(404)` + `@app.errorhandler(500)` in `app_server.py`

**Public share page:**
- `templates/share.html` — player pubblico, polling `/video/public/<job_id>` senza auth
- `GET /video/public/<job_id>` — endpoint che espone solo campi safe (`status`, `style`, `aspect_ratio`, `bpm`, `output_url` solo se `completed`)
- `GET /share/<job_id>` in `app_server.py` — route pubblica (no `require_auth`)

**Supabase email templates:**
- `supabase/email-templates/confirm-signup.html` — email conferma account branded
- `supabase/email-templates/reset-password.html` — email reset password branded
- Applicati manualmente su Supabase dashboard (Auth → Email Templates)

**DB-based cross-instance lock in `generate()`:**
- Query `reel_jobs` per job `in_progress` prima di acquisire slot in-memory
- Blocca duplicati anche su più istanze Fly.io (non solo per-thread con `_lock`)

**pg_cron monthly credit reset:**
- `cron.schedule('monthly-credit-reset', '0 0 1 * *', ...)` — run via Supabase SQL Editor
- Reset `monthly_reel_count = 0` su tutti i profili il 1° di ogni mese

**Fix critico — password reset broken end-to-end:**
- Root cause: Supabase manda ENTRAMBI `access_token` E `refresh_token` nel fragment URL
- `reset_password.html` catturava solo `access_token` → `set_session(token, '')` falliva in ~30s
- Fix: campo hidden `refresh_token` aggiunto al form; IIFE aggiornata per leggere entrambi
- `auth/routes.py`: `refresh_token = data.get('refresh_token', '')` → `sb.auth.set_session(token, refresh_token)`
- Validazione inline mismatch password: variabile rinominata da `confirm` → `confirmInput` (conflitto con `window.confirm()` built-in); classe CSS `field-input-error` con `!important` per override `:focus`

**Scoperta architetturale critica:**
- Produzione traffico → **Fly.io** (`flyctl deploy --app delulureel`), NON Render
- Render è secondary/staging (auto-deploy da GitHub, ma NON riceve traffico delulureel.com)
- Identificato via response header `via: 1.1 fly.io`
- CLAUDE.md e `fly.toml` già aggiornati

**Password reset testato e confermato funzionante da Armando (2026-05-02).**

### Sessione 4 — Interactive clip flow + automatic recovery

**Commits sessione 4 (tutti pushati):**
- `710ccb5` fix: startup recovery spawns assembly for multi-clip jobs with complete clip_results
- `b32f25e` fix: per-clip prompt variation + lipsync in multi-clip assembly
- `e5d69c9` feat: interactive clip-by-clip flow (prompt review + per-clip preview)
- `0027436` fix: fal.ai status URL uses namespace path, not full versioned endpoint
- `8401501` feat: automatic clip recovery from fal.ai for stuck interactive jobs

**Feature aggiunte in sessione 4:**
- **Interactive clip flow**: ogni clip viene inviata singolarmente con review del prompt utente prima della generazione. L'utente può modificare il prompt Claude prima di approvare ogni clip.
- **Per-clip preview**: dopo la generazione di ogni clip, l'utente può visualizzare l'anteprima prima di procedere con la successiva.
- **Lipsync in multi-clip**: aggiunto lipsync pipeline nei job multi-clip durante l'assembly.
- **Automatic clip recovery**: sistema di recovery per job interactive bloccati — rileva clip già generate su fal.ai e le recupera senza rigenerazione.
- **fal.ai status URL fix**: namespace path corretto (non full versioned endpoint) per la chiamata di status.

**Ultimo commit**: `8401501` — feat: automatic clip recovery from fal.ai for stuck interactive jobs

**Prossimi step:**
1. **TEST end-to-end su produzione** — signup → trial → upload foto+audio → generazione 30s (3 clip via webhook) → assembly → download reel
2. **Landing page** — quella attuale è placeholder statico; serve landing vera con pricing, demo video, CTA per traffico organico
3. Aggiungere nota in `render.yaml` che Render non è produzione (evitare confusione futura)

---

## SKILLS DA USARE IN QUESTO PROGETTO

| Quando | Skill |
|--------|-------|
| Inizio sessione | `delulureel-saas-model` |
| Questioni pricing/costi API | `delulureel-saas-model` |
| Backup periferiche | `obiriec-backup-periferiche` |
| Nuovi moduli Python | `python-js-escape-guard` |
| Backup codice | `ampa-backup` |
