---
name: obiriec-backup-periferiche
description: >
  Esegue il backup completo e verificato di CLAUDE_Works su tutte le periferiche (Crucial X9 e DockCase 1TB)
  e il git push di tutti i repository verso GitHub. Include: analisi struttura Desktop, verifica divergenza
  commit git prima del backup, rilevamento e gestione di cartelle orfane sui dischi, rsync incrementale
  Desktop → periferiche, pulizia orfani DENTRO CLAUDE_Works su periferiche, verifica post-backup
  allineamento commit, report riepilogativo finale.

  Trigger: "backup periferiche", "backup completo", "sincronizza periferiche", "aggiorna X9",
  "aggiorna DockCase", "copia su tutti i dischi", "backup su tutte le periferiche",
  "sincronizza i dischi", "aggiorna i backup", "push e backup", "fai il backup",
  "backup CLAUDE_Works", "allinea le periferiche".

  Da usare SEMPRE quando Armando chiede di sincronizzare, aggiornare o fare backup dei dischi
  esterni, anche se la richiesta non usa queste parole esatte.
---

# Obiriec Backup Periferiche

Backup completo e verificato di CLAUDE_Works con git push, pulizia orfani e report finale.

---

## REGOLA FONDAMENTALE

**Desktop comanda. Sempre.**

```
FONTE:    /Users/armandobrecciaroli/Desktop/CLAUDE_Works/
BACKUP 1: /Volumes/Crucial X9/CLAUDE_Works/
BACKUP 2: /Volumes/DockCase 1TB/CLAUDE_Works/
```

Le periferiche ricevono dal Mac. Se una periferica ha file che il Mac non ha,
integrali prima su Mac, poi redistribuisci. Non lavorare mai direttamente sulle periferiche.

---

## STRUTTURA CLAUDE_Works (aggiornata 2026-04-28)

```
CLAUDE_Works/
├── CLAUDE.md / TASKS.md              ← sistema Claude
├── AI MUSIC PROMPT ARCHITECT/        ← AMPA SaaS [GIT GitHub]
├── AURIS_Backup (NON TOCCARE)/       ← snapshot produzione AURIS v1.2.6 [GIT GitHub] — NON MODIFICARE
├── AURIS_APEX/                       ← fork AURIS v2.0.0 [GIT locale] — sviluppo tier APEX
├── AVARCAST/                         ← strategia
├── GAMES/                            ← concept game (no git) — CIPHERUN, RHYTHMIKA, UNWIRE
├── OBIRIEC LABS/                     ← sito + API [GIT GitHub]
│   └── _Docs/                        ← documenti infrastruttura, backlog, PDF
├── OL-API-DASHBOARD/                 ← dashboard monitoraggio API OL [GIT GitHub]
├── SP€$IO/                           ← app locale [GIT GitHub]
├── SUNO SCHEDULER/                   ← pipeline brani [GIT locale, no remote] — usa launchd
├── TEST x OL API DASHBOARD/          ← cartella test/prototipi dashboard
├── TOKNORA/                          ← prodotto SaaS [GIT GitHub]
├── MCPs/                             ← config MCP
├── Personal Identity/
├── TRANSCRIBE/                       ← output Whisper
├── _Clienti/                         ← ABDF, FN Immobiliare
├── _Diagnostica/                     ← log, report, diagnostica
│   └── scrapped/                     ← progetti scartati (es. FLICK)
├── _Shared/                          ← skills, memory, documenti
└── _Tools/                           ← strumenti e script di supporto
```

Repo git attivi con remote GitHub: **AI MUSIC PROMPT ARCHITECT**, **OBIRIEC LABS**, **AURIS_Backup**, **SP€$IO**, **OL-API-DASHBOARD**, **TOKNORA**
Repo git locali (no remote): **AURIS_APEX**, **SUNO SCHEDULER**

**NOTA:** La cartella `AURIS` sulle periferiche (backup precedente) è un orfano storico —
non è sul Desktop. Le periferiche la mantengono come archivio (nessun `--delete`). Non cestinarla.

---

## STEP 0 — Verifica montaggio periferiche

```bash
ls /Volumes/ 2>/dev/null || echo "Volumes not accessible"
```

Se `/Volumes/` non è accessibile, usa osascript:
```applescript
tell application "Finder"
    set vols to name of every disk
    return vols as string
end tell
```

Se una periferica non è montata, comunicarlo ad Armando e attendere prima di procedere.

---

## STEP 1 — Analisi struttura Desktop CLAUDE_Works

Mappa tutti i progetti e identifica quelli con git:

```bash
BASE="/Users/armandobrecciaroli/Desktop/CLAUDE_Works"
for dir in "$BASE"/*/; do
    if [ -d "$dir/.git" ]; then
        echo "GIT: $(basename "$dir")"
        cd "$dir" && git log --format="%H %ai %s" -1
    else
        echo "NO GIT: $(basename "$dir")"
    fi
done
```

Dimensioni:
```bash
du -sh "/Users/armandobrecciaroli/Desktop/CLAUDE_Works"/* 2>/dev/null | sort -rh
```

---

## STEP 2 — Verifica divergenza git pre-backup

Per ogni repo git, confronta il commit su Desktop con quello sui backup:

```bash
BASE="/Users/armandobrecciaroli/Desktop/CLAUDE_Works"
DK="/Volumes/DockCase 1TB/CLAUDE_Works"
CX="/Volumes/Crucial X9/CLAUDE_Works"

for REPO in "AI MUSIC PROMPT ARCHITECT" "OBIRIEC LABS" "AURIS" "SP€\$IO"; do
    REPO_REAL=$(echo "$REPO" | sed 's/\\//g')
    echo "=== $REPO_REAL ==="
    echo "Desktop:    $(cd "$BASE/$REPO_REAL" 2>/dev/null && git log --format='%H %ai %s' -1)"
    echo "DockCase:   $(cd "$DK/$REPO_REAL" 2>/dev/null && git log --format='%H %ai %s' -1 || echo 'NON TROVATO')"
    echo "Crucial X9: $(cd "$CX/$REPO_REAL" 2>/dev/null && git log --format='%H %ai %s' -1 || echo 'NON TROVATO')"
    echo ""
done
```

Se un backup è indietro di N commit, riportarlo nel report finale.

**Nota:** Sul Crucial X9 possono apparire errori `non-monotonic index ._pack-*.idx`.
Sono file shadow macOS — non indicano corruzione reale. Il backup li sovrascrive.

---

## STEP 3A — Rilevamento orfane FUORI da CLAUDE_Works (root volumi)

Cerca cartelle di progetto che esistono sul root delle periferiche **fuori** da CLAUDE_Works:

```applescript
tell application "Finder"
    set crucialRoot to POSIX file "/Volumes/Crucial X9" as alias
    set crucialItems to name of every item of crucialRoot
    set dockRoot to POSIX file "/Volumes/DockCase 1TB" as alias
    set dockItems to name of every item of dockRoot
    return "CRUCIAL: " & (crucialItems as string) & return & "DOCKCASE: " & (dockItems as string)
end tell
```

Se trovi cartelle di progetto Obiriec fuori da CLAUDE_Works, segnalarle e chiedere se cestinarle.

---

## STEP 3B — Rilevamento orfane DENTRO CLAUDE_Works (root periferiche vs Desktop)

**Questo step è necessario perché rsync non usa --delete.**
Dopo ogni riorganizzazione file su Desktop, le periferiche mantengono le vecchie copie.

Confronta la root di Desktop con quella delle periferiche:

```bash
BASE="/Users/armandobrecciaroli/Desktop/CLAUDE_Works"
DK="/Volumes/DockCase 1TB/CLAUDE_Works"
CX="/Volumes/Crucial X9/CLAUDE_Works"

echo "=== DESKTOP ===" && ls "$BASE" | sort
echo "=== DOCKCASE ===" && ls "$DK" | sort
echo "=== CRUCIAL X9 ===" && ls "$CX" | sort
```

Identifica gli item presenti sulle periferiche ma **non sul Desktop** — sono orfani.

Per cestinarli (reversibile) su entrambe le periferiche in un colpo:

```applescript
tell application "Finder"
    set dk to "/Volumes/DockCase 1TB/CLAUDE_Works/"
    set cx to "/Volumes/Crucial X9/CLAUDE_Works/"
    set orphans to {"NOME1", "NOME2"} -- lista degli orfani rilevati

    repeat with orphan in orphans
        try
            move (POSIX file (dk & orphan) as alias) to trash
        end try
        try
            move (POSIX file (cx & orphan) as alias) to trash
        end try
    end repeat
end tell
```

Fare lo stesso per sottocartelle (es. `GAMES/file_orfano.html`).

**Riportare nel report finale** quanti e quali item sono stati cestinati.

---

## STEP 4 — Git push di tutti i repository

```bash
BASE="/Users/armandobrecciaroli/Desktop/CLAUDE_Works"
for REPO in "AI MUSIC PROMPT ARCHITECT" "OBIRIEC LABS" "AURIS" "SP€\$IO"; do
    echo "=== PUSH $REPO ==="
    cd "$BASE/$REPO" && git push origin main 2>&1
done
```

AURIS_APEX e SUNO SCHEDULER non hanno remote — rsync li copia comunque.

**Nota:** File untracked non vengono committati automaticamente.
Se ci sono file non tracciati rilevanti, segnalarli ad Armando per una decisione.

---

## STEP 5 — rsync Desktop → periferiche

Lanciare i due rsync in parallelo:

```bash
# Desktop → DockCase 1TB
rsync -av --exclude='.git/objects/pack/._*' \
  "/Users/armandobrecciaroli/Desktop/CLAUDE_Works/" \
  "/Volumes/DockCase 1TB/CLAUDE_Works/" \
  2>&1 | tail -5
echo "DOCKCASE_EXIT=$?"

# Desktop → Crucial X9 (parallelo)
rsync -av --exclude='.git/objects/pack/._*' \
  "/Users/armandobrecciaroli/Desktop/CLAUDE_Works/" \
  "/Volumes/Crucial X9/CLAUDE_Works/" \
  2>&1 | tail -5
echo "CRUCIAL_EXIT=$?"
```

**Nessun `--delete`**: le periferiche sono archivi accumulativi.

---

## STEP 6 — Verifica post-backup

Conferma che i commit git siano allineati su tutte e tre le copie:

```bash
BASE="/Users/armandobrecciaroli/Desktop/CLAUDE_Works"
DK="/Volumes/DockCase 1TB/CLAUDE_Works"
CX="/Volumes/Crucial X9/CLAUDE_Works"

for REPO in "AI MUSIC PROMPT ARCHITECT" "OBIRIEC LABS" "AURIS" "SP€\$IO"; do
    REPO_REAL=$(echo "$REPO" | sed 's/\\//g')
    D=$(cd "$BASE/$REPO_REAL" 2>/dev/null && git log --format='%H' -1)
    K=$(cd "$DK/$REPO_REAL" 2>/dev/null && git log --format='%H' -1 2>/dev/null || echo 'ERR')
    X=$(cd "$CX/$REPO_REAL" 2>/dev/null && git log --format='%H' -1 2>/dev/null || echo 'ERR')
    if [[ "$D" == "$K" && "$D" == "$X" ]]; then
        echo "✅ $REPO_REAL — $D"
    else
        echo "❌ $REPO_REAL — Desktop:$D | DockCase:$K | CrucialX9:$X"
    fi
done
```

---

## STEP 7 — Report finale

```
BACKUP PERIFERICHE COMPLETATO — [DATA ORA]

SORGENTE DESKTOP
  Progetti totali: N
  Repo git:        N (nomi)
  Progetti senza git: N (nomi)
  Dimensione totale: X GB

GIT PUSH GITHUB
  REPO 1: ✅ già in sync / ✅ N commit pushati / ❌ errore
  REPO 2: ✅ già in sync / ✅ N commit pushati / ❌ errore

RSYNC PERIFERICHE
  DockCase 1TB:  ✅ EXIT 0 — X GB trasferiti
  Crucial X9:    ✅ EXIT 0 — X GB trasferiti

DIVERGENZA PRE-BACKUP
  Nessuna / [REPO (DockCase): era N commit indietro → ora allineato]

ORFANE RILEVATE E CESTINATE
  Nessuna / [N item su DockCase + N item su Crucial X9 → Cestino]

ALLINEAMENTO FINALE
  ✅ PERFETTO — tutti i commit coincidono su Desktop, DockCase e Crucial X9
  ❌ DIVERGENZA — [dettaglio]
```

---

## Note operative

- **Tempo stimato rsync** con ~30 GB parzialmente sincronizzati: 30–120 secondi per periferica.
- **GitHub già aggiornato?** Se `git push` risponde "Everything up-to-date", è normale.
- **Backup DB AMPA**: questa skill NON esegue il dump di Supabase. Usa `ampa-backup` prima.
- **File untracked nei repo git**: segnalarli sempre ad Armando, non committarli automaticamente.
- **Orfani accumulati**: dopo riorganizzazioni di Desktop, eseguire STEP 3B per pulire le copie stale sulle periferiche. Le periferiche non usano --delete, quindi tengono tutto il pregresso.
- **Cestino periferiche**: i file cestinati da Finder su volumi esterni vanno in `.Trashes/` sul disco — recuperabili fino a svuotamento manuale.
