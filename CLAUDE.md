# DELULUREEL — CLAUDE.md
*Ultimo aggiornamento: 2026-04-29*
*Progetto: OBIRIEC LABS — Armando Brecciaroli*

---

## PRIMA COSA DA FARE AD OGNI SESSIONE

Eseguire la skill `delulureel-saas-model` per ricaricare il contesto completo del modello prodotto.

---

## IDENTITA DEL PROGETTO

**DELULUREEL** — SaaS web che trasforma brano musicale + foto in videoclip sincronizzato.
**Dominio:** delulureel.com (registrato Porkbun 2026-04-29)
**Tagline:** *Be delulu enough to drop your reel.*
**Working dir:** `/Users/armandobrecciaroli/Desktop/CLAUDE_Works/DELULUREEL`
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
| Deploy      | Render                       | auto-deploy da GitHub main          |
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

## STATO CORRENTE (2026-05-02)

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

**Prossimi step:**
1. **`git push origin main`** — deployare gli 8 commit dell'audit su Render (conferma Armando)
2. **Eseguire `ALTER TABLE daily_budget ENABLE ROW LEVEL SECURITY;` su Supabase** — la migrazione schema.sql va applicata manualmente al DB esistente (Dashboard → SQL Editor)
3. **TEST end-to-end 30s** su produzione — verificare che tutti 3 clip arrivino e assembly completi
4. Monitorare log Render per `assembly/{jid} COMPLETED`
5. Eseguire Storage RLS policies su Supabase (bucket reel-uploads, reel-outputs)
6. Implementare email reminder Day 5 via Resend (`_on_trial_will_end`)
7. Test signup → email confirm → trial → pagamento → generazione completa

---

## SKILLS DA USARE IN QUESTO PROGETTO

| Quando | Skill |
|--------|-------|
| Inizio sessione | `delulureel-saas-model` |
| Questioni pricing/costi API | `delulureel-saas-model` |
| Backup periferiche | `obiriec-backup-periferiche` |
| Nuovi moduli Python | `python-js-escape-guard` |
| Backup codice | `ampa-backup` |
