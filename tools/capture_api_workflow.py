#!/usr/bin/env python3
"""
capture_api_workflow.py — converte i tuoi workflow ComfyUI da formato UI (LiteGraph)
a formato API, SENZA che tu debba mai toccare il formato API.

PERCHÉ: il worker RunPod (worker-comfyui) accetta SOLO il formato API. Tu però
lavori in UI sul desktop ComfyUI. Questo script usa la conversione NATIVA di
ComfyUI (la stessa che gira quando premi "Queue Prompt"), quindi è affidabile
al 100% — niente parsing fragile del JSON UI.

DUE MODALITÀ:

  1) capture  (consigliata, bulletproof)
     Tu apri il workflow in ComfyUI e premi "Queue Prompt" una volta (lo fai
     comunque per testarlo). ComfyUI mette il prompt in formato API nella /history.
     Lo script lo pesca e lo salva come template.
        python tools/capture_api_workflow.py capture LTX_720P

  2) convert  (batch, da file UI salvati)
     Converte file .json in formato UI già salvati, interrogando /object_info di
     una istanza ComfyUI in esecuzione (con tutti i custom nodes caricati).
        python tools/capture_api_workflow.py convert /path/al/workflow_ui.json LTX_720P

Output: core/studio_templates/<NOME>.json  (formato API, pronto per il worker)

Richiede ComfyUI in esecuzione su http://127.0.0.1:8188
"""
import sys
import os
import json
import urllib.request

COMFY = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
TEMPLATES = os.path.join(os.path.dirname(__file__), "..", "core", "studio_templates")


def _get(path):
    with urllib.request.urlopen(f"{COMFY}{path}", timeout=15) as r:
        return json.loads(r.read())


def _save(name, api_workflow):
    os.makedirs(TEMPLATES, exist_ok=True)
    out = os.path.join(TEMPLATES, f"{name}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(api_workflow, f, indent=2, ensure_ascii=False)
    nodes = len(api_workflow)
    print(f"✅ Salvato {out}  ({nodes} nodi, formato API)")


# ── Modalità 1: capture da /history ──────────────────────────────────────────

def capture(name):
    """Pesca l'ultimo prompt eseguito (formato API) dalla history di ComfyUI."""
    hist = _get("/history")
    if not hist:
        sys.exit("❌ History vuota. Apri il workflow in ComfyUI e premi 'Queue Prompt' una volta, poi rilancia.")
    # la history è un dict prompt_id -> entry; prendi l'ultimo
    last_id = list(hist.keys())[-1]
    entry = hist[last_id]
    # entry['prompt'] = [number, prompt_id, PROMPT_API_DICT, extra_data, outputs_to_execute]
    prompt = entry.get("prompt")
    if not prompt or len(prompt) < 3:
        sys.exit("❌ Formato history inatteso.")
    api_workflow = prompt[2]
    if not isinstance(api_workflow, dict):
        sys.exit("❌ Prompt API non trovato nella history.")
    _save(name, api_workflow)
    print("   Suggerimento: questo è ESATTAMENTE ciò che ComfyUI ha eseguito → 100% affidabile.")


# ── Modalità 2: convert da file UI via /object_info ──────────────────────────

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN"}
CONTROL_VALUES = {"fixed", "increment", "decrement", "randomize"}


def convert(ui_path, name):
    """Converte un workflow UI salvato in formato API usando /object_info."""
    with open(ui_path, "r", encoding="utf-8") as f:
        ui = json.load(f)
    if "nodes" not in ui:
        sys.exit("❌ Non sembra un workflow UI (manca 'nodes'). Se è già API, non serve convertire.")

    obj_info = _get("/object_info")

    # mappa link_id -> (from_node_id, from_slot)
    link_map = {}
    for link in ui.get("links", []):
        # [link_id, from_node, from_slot, to_node, to_slot, type]
        link_map[link[0]] = (link[1], link[2])

    api = {}
    for node in ui["nodes"]:
        cls = node.get("type")
        nid = str(node.get("id"))
        if cls in ("Note", "Reroute", "PrimitiveNode"):
            continue
        info = obj_info.get(cls)
        if not info:
            print(f"  ⚠ class_type sconosciuto '{cls}' (custom node non caricato?) — salto nodo {nid}")
            continue

        spec = info.get("input", {})
        ordered = list(spec.get("required", {}).items()) + list(spec.get("optional", {}).items())

        connected = {
            i["name"]: i["link"]
            for i in node.get("inputs", []) if i.get("link") is not None
        }

        api_inputs = {}
        wvals = list(node.get("widgets_values") or [])
        wi = 0
        for iname, idef in ordered:
            itype = idef[0]
            iopts = idef[1] if len(idef) > 1 else {}
            is_widget = isinstance(itype, list) or (isinstance(itype, str) and itype in WIDGET_TYPES)

            if iname in connected:
                lid = connected[iname]
                if lid in link_map:
                    fn, fs = link_map[lid]
                    api_inputs[iname] = [str(fn), fs]
            elif is_widget and wi < len(wvals):
                api_inputs[iname] = wvals[wi]
                wi += 1
                # gestisci control_after_generate (seed/noise_seed → valore extra di controllo)
                if (isinstance(iopts, dict) and iopts.get("control_after_generate")) or iname in ("seed", "noise_seed"):
                    if wi < len(wvals) and isinstance(wvals[wi], str) and wvals[wi] in CONTROL_VALUES:
                        wi += 1

        api[nid] = {"class_type": cls, "inputs": api_inputs}

    _save(name, api)
    print("   ⚠ Verifica: la modalità 'convert' gestisce i casi comuni; per workflow complessi")
    print("     (nodi con input dinamici) usa 'capture' che è sempre fedele.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    mode = sys.argv[1]
    if mode == "capture":
        capture(sys.argv[2])
    elif mode == "convert":
        if len(sys.argv) < 4:
            sys.exit("Uso: convert <file_ui.json> <NOME>")
        convert(sys.argv[2], sys.argv[3])
    else:
        sys.exit(f"Modalità sconosciuta: {mode} (usa 'capture' o 'convert')")
