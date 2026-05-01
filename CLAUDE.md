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

**Completato questa sessione (pipeline fixes + lyrics + cross-instance webhooks):**

### Bug fix: fal.ai 405 su tutti i polling endpoint (v2.6/pro)
- Root cause: fal.ai v2.6/pro non supporta NESSUN endpoint di polling
- Fix: split headers `_headers_get()` / `_headers_post()` (GET non deve avere Content-Type)
- Fix definitivo: architettura webhook — polling eliminato per tutti i clip
- Commits: `1691f81`, `9e74b64`, `eb12f77`

### Feature: lyrics-aware scene prompts
- `transcribe_audio_fal(audio_url)` → fal-ai/whisper → testo canzone
- Lyrics (max 600 char) iniettati nel prompt Claude come "primary narrative driver"
- Fallback graceful: se strumentale → basta analisi melodica/BPM
- `generate_scene_prompt()` aggiornato: firma `lyrics: Optional[str] = None`
- Commit: `eb12f77`

### Bug fix: multi-clip cross-instance (Render ha 2+ istanze)
- Root cause: `_multi_pending` dict in-memory non condiviso tra istanze Render
  - Istanza A riceveva clip 2 (trovato), istanza B riceveva clip 0 e 1 ("unknown job_id")
- Fix: tracking atomico su Supabase via RPC `add_clip_result`
- Schema migration `multi_clip_tracking` applicata su Supabase:
  - `reel_jobs`: aggiunti `clip_results JSONB`, `n_clips_expected INT`, `target_secs_requested INT`
  - `add_clip_result(p_job_id, p_clip_idx, p_clip_url) RETURNS JSONB` (UPSERT atomico)
- `_run_pipeline`: salva stato in DB, rilascia slot/tmp in finally
- `fal_webhook_multi`: usa RPC add_clip_result, fetcha job data da DB, spawna `_run_assembly`
- `_run_assembly`: istanza-agnostica — scarica audio da Supabase Storage (non dipende da tmp locale)
- Commit: `f1a2f6e` (webhook base), `2c9fa2b` (DB-based cross-instance)

### Admin bypass
- `is_admin` letto da `profiles.is_admin BOOLEAN` (non da env var)
- Skip: credit limits, trial limits, deduct_credits RPC
- Confermato funzionante nei log

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
- Causa: video generati correttamente ma mai recuperati (polling 405 + no webhook)
- Soluzione implementata: webhook elimina questo problema

**Prossimi step:**
1. **TEST end-to-end 30s** su produzione — verificare che tutti 3 clip arrivino e assembly completi
2. Monitorare log Render per `assembly/{jid} COMPLETED` dopo il nuovo deploy (2c9fa2b)
3. Eseguire Storage RLS policies su Supabase (bucket reel-uploads, reel-outputs)
4. Implementare email reminder Day 5 via Resend (`_on_trial_will_end`)
5. Test signup → email confirm → trial → pagamento → generazione completa

---

## SKILLS DA USARE IN QUESTO PROGETTO

| Quando | Skill |
|--------|-------|
| Inizio sessione | `delulureel-saas-model` |
| Questioni pricing/costi API | `delulureel-saas-model` |
| Backup periferiche | `obiriec-backup-periferiche` |
| Nuovi moduli Python | `python-js-escape-guard` |
| Backup codice | `ampa-backup` |
