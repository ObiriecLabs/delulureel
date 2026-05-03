# /graphify-context

Inizializzazione sessione con memoria graphify. Carica il contesto del grafo di conoscenza e stabilisce il protocollo graph-first per massimizzare il risparmio di token.

## Quando invocare

- **All'avvio di ogni sessione** — automaticamente, come prima cosa
- **Quando riprendi una chat esistente** — per riorientarti sul grafo aggiornato
- Quando l'utente chiede "dove eravamo rimasti" o "cosa c'è nel workspace"

## Cosa fa questa skill

1. Verifica che il grafo esista e sia valido
2. Legge le sezioni chiave di GRAPH_REPORT.md (God Nodes + Surprising Connections + Community Hubs)
3. Stampa un briefing di sessione compatto
4. Dichiara il protocollo graph-first attivo per questa sessione

---

## Step 1 — Verifica grafo

```bash
if [ ! -f graphify-out/graph.json ]; then
  echo "GRAPH_MISSING"
else
  GRAPHIFY_PY=$(cat graphify-out/.graphify_python 2>/dev/null || echo "python3")
  $GRAPHIFY_PY -c "
import json
from pathlib import Path
g = json.loads(Path('graphify-out/graph.json').read_text())
nodes = g.get('nodes', [])
print(f'GRAPH_OK nodes={len(nodes)}')
"
fi
```

Se `GRAPH_MISSING`: informa l'utente che il grafo non esiste e proponi `/graphify .` per costruirlo. Non procedere.

## Step 2 — Carica contesto da GRAPH_REPORT.md

Leggi `graphify-out/GRAPH_REPORT.md` ed estrai SOLO queste sezioni:
- `## Summary` (prima 5 righe)
- `## God Nodes` (tutti i nodi)
- `## Surprising Connections` (tutti)
- `## Community Hubs` (lista nomi)

NON leggere le sezioni `## Communities` (troppo verbose) né `## Hyperedges`.

Presenta il briefing in questo formato compatto:

```
=== GRAPHIFY MEMORY LOADED ===
Graph: [N] nodi · [E] archi · [C] community · [data ultima build]

GOD NODES (concetti centrali):
  1. [label] — [N] archi
  2. ...

COMMUNITY MAP:
  [nome] · [nome] · [nome] · ...

CONNESSIONI SORPRENDENTI:
  • [A] --[rel]--> [B]  [EXTRACTED/INFERRED]
  ...

PROTOCOLLO GRAPH-FIRST ATTIVO ✓
```

## Step 3 — Dichiara protocollo attivo

Dopo il briefing, dichiara esplicitamente che il protocollo graph-first è attivo per questa sessione. Il protocollo definisce:

### REGOLA 1 — Grafo prima dei file

Prima di fare Glob, Grep o Read su qualsiasi file, interroga sempre il grafo:

```bash
# Domanda generale → BFS
$(cat graphify-out/.graphify_python) -m graphify query "domanda" --budget 1500

# Trovare un concetto → explain
$(cat graphify-out/.graphify_python) -m graphify explain "NomeConcetto"

# Relazione tra due cose → path
$(cat graphify-out/.graphify_python) -m graphify path "A" "B"
```

**Leggi un file direttamente solo se:**
- Il grafo indica `source_location: L42` e hai bisogno del codice esatto a quella riga
- Stai per modificare un file (Read obbligatorio prima di Edit)
- Il grafo dice AMBIGUOUS e hai bisogno di verificare

### REGOLA 2 — Zero letture esplorative

Non fare mai Glob o Grep "per vedere cosa c'è". Il grafo sa già cosa c'è. Usa:
- `explain "Concetto"` per capire un modulo
- `query "cosa gestisce X"` per trovare dove vive una funzionalità
- `path "A" "B"` per capire come due componenti sono collegati

Risparmio tipico: 200-2000 token per domanda evitata su file raw.

### REGOLA 3 — Aggiorna dopo ogni modifica al codice

Dopo ogni `Edit` o `Write` su file `.py`, `.js`, `.ts`, `.go`, esegui immediatamente:

```bash
$(cat graphify-out/.graphify_python) -m graphify update .
```

Questo ri-estrae solo i file modificati via AST (tree-sitter). **Zero token LLM. ~5 secondi.**
Mantiene `graph.json` e `GRAPH_REPORT.md` aggiornati in tempo reale.

### REGOLA 4 — Salva Q&A nel grafo

Dopo ogni risposta basata sul grafo, chiudi il loop:

```bash
$(cat graphify-out/.graphify_python) -m graphify save-result \
  --question "domanda originale" \
  --answer "risposta sintetizzata" \
  --type query \
  --nodes "NodoA" "NodoB"
```

Questo arricchisce il grafo con le sessioni precedenti, rendendolo più utile nel tempo.

### REGOLA 5 — Doc/immagini modificate → segnala needs_update

Dopo aver creato o modificato `.md`, `.txt`, `.pdf` o immagini, informa l'utente:

> "File non-codice modificato. Esegui `/graphify . --update` per aggiornare l'estrazione semantica (richiede LLM)."

Non eseguire l'update semantico automaticamente — costa token e richiede conferma.

---

## Tabella risparmio token attesa

| Operazione sostituita | Token evitati | Alternativa graph-first |
|---|---|---|
| Read file 500 righe | ~2.000 | `explain "NomeClasse"` → ~150 token |
| Grep ricorsivo su 192 file .py | ~8.000 | `query "cosa fa X"` → ~300 token |
| Glob + Read 10 file per capire architettura | ~15.000 | `GRAPH_REPORT.md` sezioni chiave → ~500 token |
| Rileggere context precedente | ~10.000+ | Graph persistente → 0 (già in memoria) |

---

## Note operative

- Il grafo è in `graphify-out/graph.json` — persiste tra sessioni, settimane, mesi
- Ogni nodo ha `source_file` e `source_location`: se ti serve il codice esatto, vai diretto alla riga
- Archi EXTRACTED = certezza 1.0 | INFERRED = inferenza 0.6-0.9 | AMBIGUOUS = da verificare
- Il watcher (`PID in graphify-out/watch.pid`) aggiorna AST automaticamente su modifiche codice
