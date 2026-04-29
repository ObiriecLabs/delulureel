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

| Piano   | Mensile  | Annuale   | Reel/mese |
|---------|----------|-----------|-----------|
| Creator | €14.99   | €149.90   | 5         |
| Pro     | €34.99   | €349.90   | 15        |
| Studio  | €79.99   | €799.90   | 40        |

Trial 7gg su tutti. Annuale = 2 mesi gratis.

---

## STATO CORRENTE (2026-04-29)

**Completato questa sessione:**
- `landing/index.html` — landing page completa (dark, animated phone mockup, pricing toggle, trust section)
- `app_server.py` — Flask + blueprint wiring + port auto-detect
- `schema.sql` — tabelle profiles, reel_jobs, daily_budget + RLS + funzioni Supabase
- `core/audio_analyzer.py` — librosa BPM, beats, energy peaks, spectral centroid
- `core/scene_director.py` — Claude prompt generation con style hints
- `core/video_generator.py` — fal.ai Kling 3.0 Pro submit + poll
- `core/assembler.py` — FFmpeg concat + audio mux + aspect ratio scaling
- `saas/auth/routes.py` — Supabase auth, login, signup, logout, decoratori
- `saas/billing/routes.py` — Stripe trial, webhook handlers, billing portal
- `saas/video/routes.py` — generate (rate limiting completo), status SSE, history, profile
- `templates/` — base, dashboard, upload (dropzone), result, login, signup
- `static/css/main.css` — design system completo (dark + grad violet/pink/orange)
- `static/js/main.js` — session refresh automatico

**Prossimi step:**
1. Popolare `.env` con chiavi reali (Supabase, Stripe, fal.ai, Anthropic)
2. Eseguire `schema.sql` in Supabase SQL Editor
3. Creare bucket Supabase Storage: `reel-uploads` e `reel-outputs`
4. Creare prezzi Stripe (6 price_id: 3 piani × 2 billing period)
5. Configurare Stripe webhook con ngrok in sviluppo
6. Implementare email reminder Day 5 via Resend (`_on_trial_will_end`)
7. Test end-to-end della pipeline di generazione
8. Deploy su Render (dopo test locali)

---

## SKILLS DA USARE IN QUESTO PROGETTO

| Quando | Skill |
|--------|-------|
| Inizio sessione | `delulureel-saas-model` |
| Questioni pricing/costi API | `delulureel-saas-model` |
| Backup periferiche | `obiriec-backup-periferiche` |
| Nuovi moduli Python | `python-js-escape-guard` |
| Backup codice | `ampa-backup` |
