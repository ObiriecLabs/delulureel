---
name: obiriec-backup
description: Backup completo di qualsiasi progetto Obiriec/AMPA con copia obbligatoria su Crucial X9. Attiva SEMPRE quando Armando dice "fai il backup", "backup del progetto", "backup di X", "dump del database", "salva una copia", "fai un dump", "crea un backup", "copia di sicurezza", o qualsiasi variante che implichi salvare/archiviare lo stato corrente di un progetto. Ogni backup senza copia su Crucial X9 è incompleto — questa skill garantisce che non accada mai.
---

# Obiriec Backup — Procedura Completa

Ogni richiesta di backup ha una struttura in 3 fasi. Non saltare la fase Crucial X9, anche se il progetto è piccolo.

---

## FASE 1 — Backup specifico del progetto

Identifica il progetto dalla richiesta e scegli la strategia giusta:

### AMPA (AI Music Prompt Architect)
Usa la skill `ampa-backup` che gestisce: pg_dump Supabase + ZIP codice sorgente + manifest.

### CLAUDE_Works (intera cartella di lavoro)
```bash
rsync -av --delete \
  '/Users/armandobrecciaroli/Desktop/CLAUDE_Works/' \
  'DESTINAZIONE/' \
  > /tmp/rsync_backup.log 2>&1 &
```

### SUNO-Projects
```bash
rsync -av \
  '/Users/armandobrecciaroli/Desktop/CLAUDE_Works/SUNO-Projects/' \
  'DESTINAZIONE/SUNO-Projects/' \
  > /tmp/rsync_suno.log 2>&1 &
```

### Progetto generico / cartella specifica
Usa `rsync -av` dalla sorgente alla destinazione appropriata. Se contiene un database, esegui prima il dump del DB, poi zipper il codice.

### Repository Git
```bash
cd /percorso/progetto && git push origin main
```

---

## FASE 2 — Conferma Crucial X9

**Prima di procedere con la copia su HD esterno, chiedi SEMPRE:**

> "Vuoi che includa anche la copia su Crucial X9? Verifica che l'HD esterno sia connesso prima di confermare."

Poi verifica il montaggio:

```bash
ls '/Volumes/Crucial X9/' 2>/dev/null && echo "MONTATO" || echo "NON MONTATO"
```

- **Se montato e confermato** → procedi con FASE 3
- **Se non montato** → informa Armando: "Crucial X9 non risulta montato. Collega l'HD e confermami quando è pronto, oppure posso saltare questo step."
- **Se Armando dice di saltare** → completa il backup senza copia fisica, annota che la copia su X9 è ancora da fare

---

## FASE 3 — Copia su Crucial X9

**Percorso di destinazione:**
```
/Volumes/Crucial X9/BACKUP MAC Book Pro - Febbraio 2026/Backup CLAUDE WORKS/
```

Per progetti specifici, crea una sottocartella dedicata:
```
/Volumes/Crucial X9/BACKUP MAC Book Pro - Febbraio 2026/Backup CLAUDE WORKS/[NOME_PROGETTO]/
```

**Comando standard:**
```bash
rsync -av --delete \
  'SORGENTE/' \
  '/Volumes/Crucial X9/BACKUP MAC Book Pro - Febbraio 2026/Backup CLAUDE WORKS/DESTINAZIONE/' \
  > /tmp/rsync_x9.log 2>&1 &
```

Avvia in background e monitora il completamento controllando periodicamente il log e il processo:
```bash
pgrep -x rsync && echo "IN CORSO" || echo "TERMINATO"
tail -5 /tmp/rsync_x9.log
```

Al completamento, verifica con:
```bash
du -sh '/Volumes/Crucial X9/BACKUP MAC Book Pro - Febbraio 2026/Backup CLAUDE WORKS/DESTINAZIONE/'
```

---

## Report finale

Al termine di tutte le fasi, comunica:

```
✅ Backup completato
- Progetto: [nome]
- Backup locale/cloud: [cosa è stato fatto]
- Copia Crucial X9: [percorso] — [dimensione totale] — [numero file]
- Metodo: rsync incrementale
```

Se qualche fase è saltata (es. X9 non montato), segnalalo chiaramente come step pendente.

---

## Note operative

- `rsync --delete` rimuove dalla destinazione i file non più presenti in sorgente — comportamento corretto per backup sincronizzati
- Per backup molto grandi (>5GB), avvia in background e aggiorna Armando sul progresso ogni ~30 secondi
- Se rsync fallisce per permessi, prova via `do shell script` con AppleScript tramite `mcp__Control_your_Mac__osascript`
- Il prossimo backup rsync sullo stesso target sarà molto più veloce — trasferisce solo le differenze
