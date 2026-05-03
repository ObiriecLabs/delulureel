---
name: python-js-escape-guard
description: >
  Previene SyntaxError JavaScript silenti e bug CSS silenti causati da escape
  sequences Python o double-brace CSS in stringhe HTML/JS embedded in Python.
  USARE OBBLIGATORIAMENTE prima di ogni commit/push che tocca file Python con
  template HTML o JS inline (Flask routes, Jinja, string templates).
  NON usare solo come diagnosi post-bug - eseguire SEMPRE in fase pre-commit.
  Trigger: commit, push, git add, aggiungi stringa, template HTML,
  flask route, embedded JS, routes.py, ho modificato routes,
  SyntaxError, spinner bloccato, pagina non carica, caricamento infinito,
  bug JS, errore JS, script non esegue, CSS non si aggiorna,
  stili non cambiano, CSS ignorato, pulsanti uguali, nessun cambiamento visivo.
  Valido per qualsiasi progetto Python con HTML/JS/CSS inline: AMPA, OL, AVARCAST, e futuri.
---

# Python to JS/CSS Escape Guard

## REGOLA ASSOLUTA - PRE-COMMIT OBBLIGATORIO

Questa skill va eseguita PRIMA di ogni git add / git commit / git push
che tocca qualsiasi file Python con template HTML, JS o CSS inline.

Non e una skill diagnostica da usare quando qualcosa e gia rotto.
E un controllo preventivo da eseguire SEMPRE prima del deploy.

Storia dei bug AMPA (tutti evitabili con questo check):
- 3e159aa (2026-04-01): 5 apostrofi FR - /referral bloccata in prod
- 38fd5b6 (2026-04-09): newline in confirm dialog - dashboard admin bloccata
- 3a84f49 (2026-04-09): apostrofo in fb.textContent - dashboard bloccata di nuovo
- 725952a (2026-04-09): CSS double-brace in stringa non-format - stili silenziosamente ignorati

---

## CLASSE 1 - Escape sequences Python to JS (bug JS silente)

Quando HTML+JavaScript viene scritto come stringa Python triple-quoted, gli escape
sequences Python vengono processati da Python prima di essere serviti al browser.
Questo crea caratteri invalidi dentro le JS string literals: SyntaxError silente
> l intero blocco script non viene eseguito > spinner infiniti, pagine bloccate.

Tabella degli escape pericolosi (JS):
- backslash-n nudo: newline letterale in JS = CRASH SyntaxError
- backslash-apostrofo nudo: chiude stringa JS prematuramente = CRASH
- backslash-backslash-n: sequenza letterale safe - JS interpreta come newline visuale
- backslash-backslash-apostrofo: apostrofo escaped safe in JS

Fix patterns (JS):
- confirm con newline: usa backslash-backslash-n invece di backslash-n
- apostrofo italiano/francese in JS string: usa backslash-backslash-apostrofo

---

## CLASSE 2 - CSS double-brace in stringhe non-format (bug CSS silente)

Quando CSS viene scritto con {{ e }} in una stringa Python che NON chiama
.format(), il browser riceve letteralmente {{ e }} nel CSS.

I browser moderni con CSS Nesting (Chrome 112+, Firefox 117+, Safari 17+)
interpretano {{ come apertura di una regola annidata senza selettore > la regola
viene ignorata silenziosamente > ZERO proprieta CSS applicate.

Il bug non produce errori. La pagina funziona, gli elementi ci sono, ma hanno solo
lo stile default del browser. Nessuna differenza osservabile tra deploy vecchio e nuovo.

Regola pratica: prima di scrivere CSS in una stringa Python, chiedi:
Questa stringa chiama .format() o e usata in una f-string?
- SI  > usa {{}} per i selettori CSS
- NO  > usa {} singoli normali

---

## WORKFLOW PRE-COMMIT - 4 STEP OBBLIGATORI

### STEP 1 - Check escape pericolosi JS

Esegui questo script Python sul file da committare:

import re, sys
src = open('saas/admin/routes.py', encoding='utf-8').read()
issues = []
for m in re.finditer(r'"""(.*?)"""', src, re.DOTALL):
    content = m.group(1)
    line_start = src[:m.start()].count('\n') + 1
    raw_n    = [(i+1, l.strip()[:90]) for i, l in enumerate(content.split('\n'))
                if re.search(r'(?<!\\)\\n', l) and any(k in l for k in
                   ['confirm(','alert(','prompt(','= \'','fb.text','toast(','innerHTML','textContent'])]
    raw_apos = [(i+1, l.strip()[:90]) for i, l in enumerate(content.split('\n'))
                if re.search(r"(?<!\\)\\'", l)]
    for rel, snip in raw_n:
        issues.append(f'L{line_start+rel} backslash-n nudo: {snip}')
    for rel, snip in raw_apos:
        issues.append(f'L{line_start+rel} apostrofo nudo: {snip}')
if issues:
    print('STOP - escape JS pericolosi trovati:')
    for i in issues: print(i)
    sys.exit(1)
else:
    print('CLEAN JS - nessun escape pericoloso.')

### STEP 2 - Check CSS double-brace in stringhe non-format

Esegui questo script Python:

import re, sys
src = open('saas/admin/routes.py', encoding='utf-8').read()
issues = []
for m in re.finditer(r'(\w+)\s*=\s*"""(.*?)"""', src, re.DOTALL):
    varname = m.group(1)
    content = m.group(2)
    line_start = src[:m.start()].count('\n') + 1
    rest = src[m.end():]
    is_formatted = (varname + '.format(') in rest
    if not is_formatted:
        css_doubles = re.findall(r'(\.\w[\w-]*)\{\{', content)
        if css_doubles:
            for cls in css_doubles:
                issues.append(f'{varname} (L{line_start}): {cls}{{{{}}}} in stringa non-format')
if issues:
    print('STOP - CSS double-brace in stringhe non-format:')
    for i in issues: print(i)
    print('Fix: usa {} singoli. Double-brace solo se la stringa chiama .format()')
    sys.exit(1)
else:
    print('CLEAN CSS - nessun double-brace problematico.')

### STEP 3 - Validazione sintattica Python

python3 -m py_compile saas/admin/routes.py && echo SYNTAX_OK

### STEP 4 - CHECK FUNZIONALE (OBBLIGATORIO - non saltare mai)

Prima del commit, verifica che quello che hai modificato funzioni davvero.

Il check funzionale non e opzionale. Un commit che supera STEP 1-3 ma non e stato
verificato funzionalmente non e un commit sicuro. Il bug 725952a lo dimostra:
escape guard OK, py_compile OK, CSS completamente non applicato in produzione.

Per modifiche CSS/UI:
- Verifica che le classi CSS abbiano {} singoli nel sorgente Python (grep)
- Apri browser, naviga alla pagina, verifica visivamente il cambiamento
- Se pagina con auth: usa curl per verificare che la classe sia nel sorgente HTML

Per modifiche JS:
- Verifica che la funzione sia nel sorgente HTML (grep)
- DevTools Console: nessun errore rosso, funzione disponibile

Per modifiche backend (route/API):
- Testa localmente o verifica nei log Render post-deploy
- Controlla struttura risposta JSON

Se non puoi verificare localmente: dichiaralo prima del commit e prepara un
piano di verifica post-deploy con rollback rapido se necessario.

---

SOLO SE TUTTI E 4 GLI STEP SONO OK: commit e push

git add saas/admin/routes.py
git commit -m '...'
git push origin main

Se uno qualsiasi dei check fallisce: NON procedere. Fixare prima.

---

## Diagnosi rapida - pagina bloccata (CLASSE 1)
1. DevTools Console: SyntaxError Unexpected identifier
2. Log Render: zero richieste API dopo page load
3. Esegui STEP 1, fixa, STEP 3, commit

## Diagnosi rapida - CSS non si aggiorna (CLASSE 2)
1. Deploy confermato, hard refresh confermato, nessun cambiamento visivo
2. Grep le classi CSS nel file Python: cercano {{ }} in stringa senza .format()?
3. Esegui STEP 2, fixa (usa {} singoli), STEP 3, commit

## Regola strutturale alternativa (i18n con apostrofi in JS)

Usa json.dumps() per gestire tutti gli escape automaticamente:
import json
translations = {'fr': {'msg': "Chaque ami qui s inscrit..."}}
html = f'<script>var T = {json.dumps(translations, ensure_ascii=False)};</script>'
