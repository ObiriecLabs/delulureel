import json
import os
from typing import Dict

SUPPORTED_LANGS = ['en', 'it', 'fr', 'de', 'es']
LANG_NAMES = {'en': 'EN', 'it': 'IT', 'fr': 'FR', 'de': 'DE', 'es': 'ES'}

_TRANSLATIONS: Dict[str, dict] = {}


def _load() -> None:
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'translations')
    for lang in SUPPORTED_LANGS:
        path = os.path.join(base, f'{lang}.json')
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                _TRANSLATIONS[lang] = json.load(fh)
        except Exception as exc:
            print(f'[i18n] failed to load {lang}.json: {exc}')
            _TRANSLATIONS[lang] = {}


_load()


def get_lang(session_obj, request_obj) -> str:
    """Resolve active language: session → Accept-Language header → 'en'."""
    lang = session_obj.get('lang')
    if lang in SUPPORTED_LANGS:
        return lang
    accept = (request_obj.headers.get('Accept-Language') or '').lower()
    for part in accept.split(','):
        code = part.split(';')[0].strip()[:2]
        if code in SUPPORTED_LANGS:
            return code
    return 'en'


def t(key: str, lang: str = 'en') -> str:
    """Dot-notation key lookup. Falls back to English, then returns key itself."""
    parts = key.split('.')

    def _dig(d: dict, keys: list):
        if not keys or not isinstance(d, dict):
            return None
        v = d.get(keys[0])
        return v if len(keys) == 1 else _dig(v, keys[1:])

    val = _dig(_TRANSLATIONS.get(lang, {}), parts)
    if val is None and lang != 'en':
        val = _dig(_TRANSLATIONS.get('en', {}), parts)
    return val if isinstance(val, str) else key
