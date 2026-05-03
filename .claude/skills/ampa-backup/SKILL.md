---
name: ampa-backup
description: >
  Esegue il backup completo del progetto AMPA (AI Music Prompt Architect):
  dump del database Supabase in formato pg_dump custom (.dump), ZIP del
  codice sorgente (landing/ + saas/ + file root), e manifest testuale
  con statistiche. Copia tutto sul Crucial X9 (HD esterno).
  Usa questa skill ogni volta che Armando dice
  'fai il backup', 'backup del progetto', 'backup AMPA', 'salva il
  backup', 'crea un backup', o qualsiasi variante che implichi
  salvare lo stato corrente del progetto AMPA.
---

# AMPA Backup — Workflow Operativo

## Percorsi fissi

- **Progetto root:** `/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/`
- **Cartella backup locale:** `.../07_Backups/Backups/AMPA/`
- **Cartella backup Crucial X9:** `/Volumes/Crucial X9/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/`
- **pg_dump binary:** `/opt/homebrew/opt/libpq/bin/pg_dump`
- **DB connection string:** `postgresql://postgres.ekknmsbrniyajprbphvo:The_Clown_69!@aws-1-eu-west-1.pooler.supabase.com:5432/postgres`
- **Timestamp:** `YYYYMMDD` (es. `20260327`) — se piu' backup nello stesso giorno, aggiungi `_HHMM`
- Tutti i comandi vanno eseguiti via `mcp__Control_your_Mac__osascript` (non Bash)

**IMPORTANTE — Crucial X9:** Prima di eseguire il backup, verificare che il Crucial X9 sia montato:
```applescript
do shell script "ls /Volumes/ | grep -i crucial || echo 'CRUCIAL_NOT_MOUNTED'"
```
Se non montato, chiedere ad Armando di collegare il disco prima di procedere.

---

## Step 1 — DB Dump

Formato OBBLIGATORIO: custom `-F c` con estensione `.dump`.
Le virgolette singole attorno alla connection string proteggono il `!` nella password.

```applescript
with timeout of 120 seconds
  do shell script "/opt/homebrew/opt/libpq/bin/pg_dump 'postgresql://postgres.ekknmsbrniyajprbphvo:The_Clown_69!@aws-1-eu-west-1.pooler.supabase.com:5432/postgres' --no-acl --no-owner -F c -f '/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/backup_ampa_YYYYMMDD.dump' 2>&1 && echo DUMP_OK"
end timeout
```

Se l'output non contiene `DUMP_OK`, interrompi — non proseguire con un backup parziale.

---

## Step 2 — ZIP del codice

Include: `landing/`, `saas/`, `app_saas.py`, `requirements_saas.txt`, `Procfile`, `requirements.txt`
Escludi: `*.pyc`, `*/__pycache__/*`, `landing/files.zip`

```applescript
with timeout of 300 seconds
  do shell script "cd '/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT' && zip -r '07_Backups/Backups/AMPA/backup_code_YYYYMMDD.zip' landing/ saas/ app_saas.py requirements_saas.txt Procfile requirements.txt -x '*.pyc' -x '*/__pycache__/*' -x 'landing/files.zip' 2>&1 | tail -3 && echo ZIP_OK"
end timeout
```

---

## Step 3 — Statistiche e Manifest

Raccogli dimensioni e conteggi:

```applescript
do shell script "ls -la '/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/backup_ampa_YYYYMMDD.dump'"
do shell script "ls -la '/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/backup_code_YYYYMMDD.zip'"
do shell script "unzip -l '/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/backup_code_YYYYMMDD.zip' | tail -1"
do shell script "cd '/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT' && git status --short && git log --oneline -5"
```

Scrivi il manifest via Python in `/tmp/ampa_manifest.py`, poi eseguilo con `do shell script "python3 /tmp/ampa_manifest.py"`:

```python
# /tmp/ampa_manifest.py — popola i placeholder con i valori reali, poi esegui
content = (
    "AMPA FULL BACKUP\n"
    "==================\n"
    "Timestamp : YYYYMMDD\n"
    "Data      : YYYY-MM-DD HH:MM CET\n\n"
    "FILE GENERATI\n"
    "========\n"
    "DB  : backup_ampa_YYYYMMDD.dump ([SIZE] bytes)\n"
    "ZIP : backup_code_YYYYMMDD.zip ([SIZE] bytes)\n"
    "     File inclusi: [COUNT]\n\n"
    "GIT STATUS\n[output git status]\n\n"
    "ULTIMI 5 COMMIT\n[output git log --oneline -5]\n\n"
    "CONTENUTO BACKUP DIR\n[ls -la della cartella AMPA]\n"
)
open('/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/BACKUP_MANIFEST_YYYYMMDD.txt', 'w').write(content)
print('MANIFEST_OK')
```

Sostituisci i placeholder `[SIZE]`, `[COUNT]`, `[output ...]` con i valori reali raccolti.

---

## Step 4 — Copia su Crucial X9 (OBBLIGATORIO)

Verificare che il Crucial X9 sia montato, poi copiare i 3 file con timestamp `_HHMM` per evitare conflitti.

```applescript
with timeout of 300 seconds
  do shell script "
    DEST='/Volumes/Crucial X9/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA'
    mkdir -p \"$DEST\"
    TS=$(date +%Y%m%d_%H%M)
    SRC='/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA'
    cp \"$SRC/backup_ampa_YYYYMMDD.dump\" \"$DEST/backup_ampa_${TS}.dump\" &&
    cp \"$SRC/backup_code_YYYYMMDD.zip\" \"$DEST/backup_code_${TS}.zip\" &&
    cp \"$SRC/BACKUP_MANIFEST_YYYYMMDD.txt\" \"$DEST/BACKUP_MANIFEST_${TS}.txt\" &&
    echo CRUCIAL_OK
  "
end timeout
```

Se non contiene `CRUCIAL_OK`, segnalare ad Armando il problema prima di procedere.

---

## Step 5 — Finder reveal (OBBLIGATORIO)

Il Finder NON aggiorna la vista automaticamente quando i file vengono creati da shell.
Senza questo step i file esistono sul disco ma non sono visibili nel Finder.

```applescript
tell application "Finder"
  reveal {POSIX file "/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/backup_ampa_YYYYMMDD.dump", POSIX file "/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/backup_code_YYYYMMDD.zip", POSIX file "/Users/armandobrecciaroli/Desktop/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/BACKUP_MANIFEST_YYYYMMDD.txt"}
  activate
end tell
```

---

## Riepilogo finale

Informa l'utente con:

- Nome e dimensione di ogni file creato (locale + Crucial X9)
- Timestamp di creazione
- Conferma `CRUCIAL_OK` o segnalazione se il disco non era montato
- Eventuali anomalie

---

## Note operative

- Formato DB obbligatorio: `-F c` con estensione `.dump` (NON `.sql`)
- Il file `.sql` (plain text) non e' il formato standard — non e' ripristinabile con `pg_restore`
- Manifest: scrivere Python in `/tmp/ampa_manifest.py` poi eseguire via `do shell script`
- Per il Finder: usare `reveal` API Finder, non `open` ne' `killall -HUP Finder`
- Percorso corretto: `CLAUDE_Works` (con underscore), `07_Backups/Backups/AMPA/` (NON `Backups/AMPA/` alla root del progetto)
- Crucial X9 percorso: `/Volumes/Crucial X9/CLAUDE_Works/AI MUSIC PROMPT ARCHITECT/07_Backups/Backups/AMPA/`
- Se Crucial X9 non e' montato: chiedere ad Armando di collegarlo, poi eseguire il backup esterno separatamente
