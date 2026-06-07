"""
Studio workflow templates — gestione workflow ComfyUI in formato API.

REGOLA FONDAMENTALE (verificata su runpod-workers/worker-comfyui):
  Il worker accetta SOLO il formato API ComfyUI, NON il formato UI LiteGraph.
  - Formato API:  { "6": {"inputs": {...}, "class_type": "CLIPTextEncode"}, ... }
                  chiavi = node_id numerici, ogni nodo ha class_type + inputs.
  - Formato UI:   { "nodes": [...], "links": [...], "widgets_values": [...] }
                  → NON accettato dal worker (causa #1 di errori).

CONVERSIONE LiteGraph → API (da fare UNA VOLTA per ogni workflow):
  1. Apri il workflow .json in ComfyUI (Settings → abilita "Dev mode Options").
  2. Workflow → Export (API) → salva il .json risultante.
  3. Metti il file in:  core/studio_templates/<NOME>.json
  Questi template versionati sono ciò che inviamo al worker, sostituendo
  a runtime solo i valori variabili (prompt, seed, immagine) per node_id.
"""
import json
import os
import copy

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "studio_templates")


def load_template(name: str) -> dict:
    """Carica un workflow API-format dai template versionati."""
    path = os.path.join(TEMPLATES_DIR, f"{name}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_api_format(workflow: dict) -> bool:
    """
    True se il dict è in formato API (node_id → {class_type, inputs}).
    False se è formato UI LiteGraph (ha chiave 'nodes' come array).
    Usare come guard prima di inviare al worker.
    """
    if "nodes" in workflow and isinstance(workflow.get("nodes"), list):
        return False
    # formato API: ogni valore ha 'class_type'
    return all(
        isinstance(v, dict) and "class_type" in v
        for v in workflow.values()
    )


def substitute(template: dict, overrides: dict) -> dict:
    """
    Sostituisce valori in un workflow API per node_id + nome input.

    overrides = {
        "6":  {"text": "un prompt positivo"},      # CLIPTextEncode positivo
        "7":  {"text": "blurry, low quality"},     # negativo
        "3":  {"seed": 123456, "steps": 30},       # KSampler
        "10": {"image": "input.png"},              # LoadImage
        "5":  {"width": 832, "height": 1216},      # EmptyLatentImage
    }

    Modifica solo gli input indicati, lascia intatto il resto.
    """
    wf = copy.deepcopy(template)
    for node_id, inputs in overrides.items():
        if node_id not in wf:
            raise KeyError(f"Node {node_id} non presente nel template")
        wf[node_id].setdefault("inputs", {})
        for key, value in inputs.items():
            wf[node_id]["inputs"][key] = value
    return wf


def find_nodes_by_class(workflow: dict, class_type: str) -> list[str]:
    """
    Restituisce i node_id di tutti i nodi di un dato class_type.
    Utile per individuare dinamicamente seed/prompt/save senza hardcodare gli ID.
    """
    return [
        nid for nid, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


_SAMPLER_CLASSES = (
    "KSampler", "KSamplerAdvanced",
    "LTXVSampler", "WanVideoSampler", "RandomNoise",
)
_SEED_KEYS = ("seed", "noise_seed")
_NEGATIVE_HINTS = frozenset({
    "blurry", "ugly", "low quality", "bad", "worst", "nsfw",
    "watermark", "deformed", "disfigured",
})


def randomize_seeds(workflow: dict, seed: int) -> dict:
    """Imposta un seed esplicito su tutti i nodi sampler del workflow."""
    wf = copy.deepcopy(workflow)
    for cls in _SAMPLER_CLASSES:
        for nid in find_nodes_by_class(wf, cls):
            wf[nid].setdefault("inputs", {})
            placed = False
            for k in _SEED_KEYS:
                if k in wf[nid]["inputs"]:
                    wf[nid]["inputs"][k] = seed
                    placed = True
                    break
            if not placed and cls in ("KSampler", "KSamplerAdvanced", "LTXVSampler"):
                wf[nid]["inputs"]["seed"] = seed
    return wf


def apply_prompt(workflow: dict, prompt: str) -> dict:
    """Inietta il prompt nei nodi testo positivi del workflow.

    Gestisce CLIPTextEncodeFlux (clip_l + t5xxl), CLIPTextEncode,
    WanTextEncode. Salta i nodi negativi rilevati euristicamente
    dal testo già presente (parole-chiave negative comuni).
    """
    PROMPT_CLASSES = ("CLIPTextEncodeFlux", "CLIPTextEncode", "WanTextEncode")
    wf = copy.deepcopy(workflow)

    for nid, node in wf.items():
        cls = node.get("class_type", "")
        if cls not in PROMPT_CLASSES:
            continue
        inputs = dict(node.get("inputs", {}))

        # Detect negative conditioning node by existing text content
        existing = ""
        for key in ("text", "t5xxl", "clip_l"):
            val = inputs.get(key)
            if isinstance(val, str):
                existing = val.lower()
                break
        if any(h in existing for h in _NEGATIVE_HINTS):
            continue

        if cls == "CLIPTextEncodeFlux":
            if "clip_l" in inputs and not isinstance(inputs["clip_l"], list):
                inputs["clip_l"] = prompt[:200]
            if "t5xxl" in inputs and not isinstance(inputs["t5xxl"], list):
                inputs["t5xxl"] = prompt
        else:
            if "text" in inputs and not isinstance(inputs["text"], list):
                inputs["text"] = prompt

        wf[nid]["inputs"] = inputs
    return wf
