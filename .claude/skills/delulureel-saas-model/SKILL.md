---
name: delulureel-saas-model
description: >
  Contesto completo del Modello Prodotto "DELULUREEL" e della sua applicabilita al portfolio
  OBIRIEC LABS. INVOCARE OBBLIGATORIAMENTE quando Armando lavora su DELULUREEL, AVARCAST,
  o su qualsiasi prodotto OBIRIEC LABS che genera contenuto tramite API costose (video, audio,
  immagini AI). Trigger esatti: "DELULUREEL", "modello prodotto", "trial 7 giorni",
  "video generation saas", "fal.ai", "Kling", "rate limiting API", "protezione costi",
  "applicabilita portfolio", "schema business", "AVARCAST stack", "bundle AMPA DELULUREEL",
  "modello delulureel su", "applica il modello", "stesso stack", "stesso schema",
  "come DELULUREEL", "come avarcast", "nuovo prodotto video". Usare anche quando si parla
  di pricing SaaS con API a costo variabile, trial con carta obbligatoria, DAILY_BUDGET_CAP,
  o si chiede come strutturare un nuovo prodotto OBIRIEC LABS.
---

# DELULUREEL — Modello Prodotto Video Generation SaaS

Questo documento e' la fonte di verita per il Modello Prodotto DELULUREEL e la sua
applicabilita a tutto il portfolio OBIRIEC LABS. Leggilo integralmente prima di
lavorare su qualsiasi prodotto che usa API costose per generare contenuto.

---

## IDENTITA DEL PRODOTTO

**DELULUREEL** — SaaS web che trasforma brano musicale + foto in videoclip sincronizzato.
**Dominio:** delulureel.com (registrato Porkbun 2026-04-29)
**Tagline:** *Be delulu enough to drop your reel.*
**Stato:** Setup completato — in sviluppo Fase 1 MVP

---

## PRINCIPIO FONDAMENTALE

**NESSUN free tier con generazione reale.**

Costo per Reel = $3.36 (Kling 3.0 Pro, audio off).
Con 1.000 utenti free attivi = $3.360 bruciati senza incassare.
Il visitatore vede SOLO demo pre-generati sulla landing page.

---

## FUNNEL DI CONVERSIONE

```
Visitatore
  → demo video pre-generati sulla landing (zero costo API)
  → click "Prova gratis 7 giorni"
  → inserisce carta (Stripe SetupIntent — CARTA OBBLIGATORIA)
  → genera max 3 Reel in 7 giorni
  → Day 5: email reminder automatica (webhook trial_will_end)
  → Day 7: addebito automatico primo mese
  → cancella prima di Day 7 → zero costo per OBIRIEC LABS
```

---

## PIANI E PRICING

| Piano   | Mensile    | Annuale    | Reel/mese |
|---------|------------|------------|-----------|
| Creator | €14.99     | €149.90    | 5 Reel    |
| Pro     | €34.99     | €349.90    | 15 Reel   |
| Studio  | €79.99     | €799.90    | 40 Reel   |

Annuale = 2 mesi gratis. Trial 7 giorni su tutti i piani.

---

## COSTI API FAL.AI (KLING 3.0 PRO)

Endpoint: `fal-ai/kling-video/v3/pro/image-to-video`
- $0.112/sec audio OFF
- $0.168/sec audio ON
- $0.196/sec voice control + audio

| Formato  | Costo API  | Piano minimo |
|----------|------------|--------------|
| Reel 30s | $3.36      | Creator      |
| Reel 60s | $6.72      | Pro          |
| MV 3min  | $20.16     | Studio       |

fal.ai NON addebita generazioni fallite.

Feature chiave: **Elements** — character consistency da foto frontale, incluso nel prezzo.
Referenzia nel prompt come `@Element1`.

---

## RATE LIMITING — OBBLIGATORIO SU OGNI PRODOTTO

```python
MAX_CONCURRENT_PER_USER = 1       # una generazione per utente
MAX_CONCURRENT_GLOBAL   = 10      # scala con crediti fal.ai
TRIAL_MAX_GENERATIONS   = 3       # max in 7 giorni di trial
DAILY_BUDGET_CAP_USD    = 200     # hard stop giornaliero
```

Mai aumentare DAILY_BUDGET_CAP senza analisi costi preventiva.

---

## STACK TECNICO

```
Backend:     Flask (Python 3) — porta auto-detect 5000-5100
Database:    Supabase (PostgreSQL + Auth + Storage)
Payments:    Stripe Subscriptions (trial_period_days=7)
Video API:   fal.ai — Kling 3.0 Pro
Audio:       librosa (BPM, sezioni, energia)
Assembly:    FFmpeg (concat + audio sync + export 9:16/16:9/1:1)
Storage:     Supabase Storage (TTL 24h output)
Deploy:      Render
```

---

## WEBHOOK STRIPE OBBLIGATORI

```
customer.subscription.trial_will_end  → email reminder Day 5
invoice.payment_failed                → sospensione accesso immediata
customer.subscription.deleted         → revoca accesso + cleanup
customer.subscription.created         → attivazione + email benvenuto
```

Stripe trial: `card_required = True` SEMPRE. Mai trial senza carta.

---

## VARIABILI D'AMBIENTE STANDARD

```env
FAL_KEY=                    # API key fal.ai (solo API, non Admin)
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_KEY=
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_CREATOR_MONTHLY=
STRIPE_PRICE_CREATOR_ANNUAL=
STRIPE_PRICE_PRO_MONTHLY=
STRIPE_PRICE_PRO_ANNUAL=
STRIPE_PRICE_STUDIO_MONTHLY=
STRIPE_PRICE_STUDIO_ANNUAL=
ANTHROPIC_API_KEY=
DAILY_BUDGET_CAP_USD=200
MAX_CONCURRENT_GLOBAL=10
TRIAL_MAX_GENERATIONS=3
FFMPEG_PATH=/usr/bin/ffmpeg
```

---

## APPLICABILITA AL PORTFOLIO OBIRIEC LABS

### AVARCAST — Priorita ALTA (applicazione diretta)
Stack identico a DELULUREEL. Solo endpoint fal.ai e prompt diversi.
Sviluppare DELULUREEL prima → clonare infrastruttura per AVARCAST (2-3 settimane).
Input: testo script + foto → avatar parlante via fal.ai.

### AMPA — Priorita ALTA (funnel + bundle)
Non duplicare video generation in AMPA. Collegamento tramite funnel:
- CTA post-generazione prompt: "Hai il brano? Crea il video →"
- Bundle AMPA Pro + DELULUREEL Creator: €39.99/mese
- Trial 7gg DELULUREEL come benefit per utenti AMPA Pro
- SSO: stesso Supabase Auth, stesso JWT

### AURIS — Media (feature futura)
Trascrizione → subtitle timing → lyric video via fal.ai.
Implementare dopo DELULUREEL live.

### RHYTHMIKA — Bassa (concept futuro)
Performance generativa → share reel clip via DELULUREEL API.

### TOKNORA / SP€$IO / OL-API-DASHBOARD — Non applicabile
App locali/desktop o dashboard interne. Nessuna generazione contenuto per utenti.

---

## ORDINE DI SVILUPPO CONSIGLIATO

```
1. DELULUREEL MVP     (baseline — 10-12 settimane)
2. AVARCAST           (clone + modifica — 2-3 settimane)
3. Bundle AMPA        (cross-sell — 1 settimana)
4. AURIS lyric video  (feature add-on — 2 settimane)
5. RHYTHMIKA clip     (futuro 2026+)
```

---

## REGOLE OPERATIVE INVARIABILI

- Mai API key Admin fal.ai nel codice — solo chiave API
- Mai deploy Render senza conferma esplicita
- Mai modifiche Stripe live senza conferma
- DAILY_BUDGET_CAP attivo prima del go-live
- Stripe trial: card_required = True SEMPRE
- pg_dump obbligatorio prima di ogni modifica schema DB
- Aggiornare CLAUDE.md a fine ogni sessione produttiva

---

## MODELLI VIDEO FAL.AI DISPONIBILI

| Modello          | Prezzo/sec | Feature chiave            | Uso                    |
|------------------|------------|---------------------------|------------------------|
| Kling 3.0 Pro    | $0.112     | Elements char consistency | Produzione principale  |
| Kling 2.5 Turbo  | $0.070     | Veloce, economico         | Fallback / entry tier  |
| Seedance 2.0     | TBD        | Audio nativo + lipsync    | AVARCAST futuro        |
| PixVerse V6      | TBD        | Fisica realistica         | Alternativa economica  |
| Veo 3.1          | $0.400     | Qualita massima           | Studio premium futuro  |

---

## WORKING DIRECTORY

`/Users/armandobrecciaroli/Desktop/CLAUDE_Works/DELULUREEL`

PDF di riferimento completo:
`/Users/armandobrecciaroli/Desktop/CLAUDE_Works/DELULUREEL/OBIRIEC_LABS_SaaS_Video_Model.pdf`
