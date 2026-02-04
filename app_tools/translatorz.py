# Bibliotecas
import os
import sys
import time
import langid  # type: ignore
import asyncio
from typing import Any, Dict, cast
import threading
import subprocess
import warnings

from config import Config

# Suprimir avisos de Brotli de la librería translators que son ruidosos
warnings.filterwarnings("ignore", message=".*Received response with content-encoding: br.*")
warnings.filterwarnings("ignore", category=UserWarning, module="translators.server")

# --- Monkey Patch para suprimir ventanas de cscript.exe (execjs) en Windows ---
if sys.platform == "win32":
    _original_popen = subprocess.Popen

    class _PopenNoWindow(_original_popen):
        def __init__(self, *args, **kwargs):
            creationflags = kwargs.get('creationflags', 0)
            if not (creationflags & 0x08000000):
                kwargs['creationflags'] = creationflags | 0x08000000
            
            if 'startupinfo' not in kwargs:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                kwargs['startupinfo'] = startupinfo
                
            super().__init__(*args, **kwargs)

    subprocess.Popen = _PopenNoWindow # type: ignore

# Bloqueo global para asegurar que las llamadas a las APIs sean secuenciales
_global_lock = threading.Lock()

# Configurar región
os.environ["translators_default_region"] = "EN"

try:
    import translators as ts  # type: ignore
except Exception:
    ts = None

try:
    from google import genai
except ImportError:
    genai = None # type: ignore

try:
    import deepl
except ImportError:
    deepl = None # type: ignore

try:
    from mistralai import Mistral
except ImportError:
    Mistral = None # type: ignore

try:
    from pentago import Pentago  # type: ignore
except ImportError:
    Pentago = None # type: ignore

# --- CONFIGURACIÓN ---
GEMINI_CHEAP_MODEL = "gemini-2.5-flash-lite"
GEMMA_TEXT_MODEL = "gemma-3-27b-it"

# --- PROMPT PARA IA ---
PROMPT_IA = (
    "Instrucción: Traduce el siguiente texto al {idioma}. "
    "Manten el formato, saltos de línea y el tono original. "
    "IMPORTANTE: Devuelve ÚNICAMENTE la traducción, sin textos adicionales, sin introducciones y sin explicaciones."
)

# --- MAPEOS PRECISOS (Sincronizado con UlionTse/translators README) ---
MAPEO_UNIVERSAL: Dict[str, Dict[str, str]] = {
    "es": {
        "default": "es", "baidu": "spa", "itranslate": "es-ES", "modernmt": "es-ES", 
        "lingvanex": "es_ES", "deepl": "ES", "systran": "es", "cloudtrans": "es"
    },
    "en": {
        "default": "en", "itranslate": "en-US", "modernmt": "en-GB", 
        "lingvanex": "en_GB", "deepl": "EN-US", "systran": "en", "cloudtrans": "en"
    },
    "ja": {"default": "ja", "baidu": "jp", "deepl": "JA", "lingvanex": "ja_JP", "systran": "ja"},
    "ko": {"default": "ko", "baidu": "kor", "deepl": "KO", "lingvanex": "ko_KR", "systran": "ko"},
    
    # Chino Simplificado
    "zh": {
        "default": "zh", "google": "zh-CN", "bing": "zh-Hans", "yandex": "zh", 
        "baidu": "zh", "itranslate": "zh-CN", "lingvanex": "zh-Hans_CN",
        "sogou": "zh-CHS", "caiyun": "zh", "alibaba": "zh", "systran": "zh"
    },
    "zh-CN": {
        "default": "zh", "google": "zh-CN", "bing": "zh-Hans", "yandex": "zh", 
        "baidu": "zh", "itranslate": "zh-CN", "lingvanex": "zh-Hans_CN",
        "sogou": "zh-CHS", "caiyun": "zh"
    },
    
    # Chino Tradicional
    "zh-TW": {
        "default": "zh", "google": "zh-TW", "bing": "zh-Hant", "yandex": "zh", 
        "baidu": "cht", "itranslate": "zh-TW", "lingvanex": "zh-Hant_TW",
        "sogou": "zh-CHS", "caiyun": "zh", "alibaba": "zh", "systran": "zh",
        "cloudtrans": "zh", "qqtransmart": "zh"
    },
}

def obtener_codigo(traductor: str, lang_code: str) -> str:
    """Retorna el código de idioma específico para cada motor."""
    if lang_code == "auto":
        return "auto"
        
    traductor = traductor.lower()
    
    # Normalizar códigos de detección comunes
    if lang_code == "zh": 
        lang_code = "zh-CN"
        
    # Buscar en el mapa
    lang_map = MAPEO_UNIVERSAL.get(lang_code)
    if not lang_map:
        # Intentar con el código base (ej: 'en-US' -> 'en')
        base_code = lang_code.split("-")[0].split("_")[0]
        lang_map = MAPEO_UNIVERSAL.get(base_code, {})
    
    # Si es Chino Tradicional y el traductor no tiene entrada específica, 
    # intentamos caer a 'zh' genérico antes que al default del mapa
    res = lang_map.get(traductor)
    if not res and (lang_code == "zh-TW" or lang_code == "zh-Hant"):
        return "zh"
        
    return str(res if res else lang_map.get("default", lang_code))

def detectar_idioma(texto: str) -> str:
    try:
        # Prioridad a Chino/Japonés/Coreano por caracteres
        if any('\u4e00' <= c <= '\u9fff' for c in texto):
            tradicionales = "體國會斷廣惡顯現車貝門門"
            return "zh-TW" if any(c in texto for c in tradicionales) else "zh-CN"
        if any('\u3040' <= c <= '\u309f' or '\u30a0' <= c <= '\u30ff' for c in texto):
            return "ja"
        if any('\uac00' <= c <= '\ud7af' for c in texto):
            return "ko"
        
        res = langid.classify(texto) # type: ignore
        return str(res[0]).split("-")[0]
    except Exception:
        return "en"

# --- SERVICIOS ---

def _translate_baidu_with_retries(text: str, from_l: str, to_l: str) -> str:
    if ts is None:
        return "Error: translators no disponible."
    max_retries = 20 
    for i in range(max_retries):
        try:
            res = cast(Any, ts).translate_text(text, translator='baidu', from_language=from_l, to_language=to_l, timeout=10)
            res_str = _ensure_string_result(res)
            if "not certified" in res_str or not res_str.strip():
                if i < max_retries - 1:
                    # Espera exponencial ligera
                    time.sleep(0.5 + (i * 0.1))
                    continue
                else:
                    return "Error Baidu: Servicio no certificado (reintentos agotados)."
            return res_str
        except Exception as e:
            if i == max_retries - 1:
                return f"Error Baidu: {str(e)}"
            time.sleep(1.0)
    return "Error Baidu: Fallo reintentos."

def _translate_papago_pentago(text: str, source_lang: str, target_lang: str) -> str:
    if Pentago is None:
        return "Error: pentago no instalado."
    try:
        from pentago import lang # type: ignore
        p_map = {"es": lang.SPANISH, "en": lang.ENGLISH, "ja": lang.JAPANESE, "ko": lang.KOREAN, "auto": lang.AUTO}
        src = p_map.get(source_lang.split("-")[0], lang.AUTO)
        tgt = p_map.get(target_lang.split("-")[0], lang.SPANISH)
        async def _do():
            return await asyncio.wait_for(cast(Any, Pentago)(src, tgt).translate(text), timeout=15.0)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = cast(Dict[str, Any], loop.run_until_complete(_do()))
            return str(res.get('translatedText', text))
        finally:
            loop.close()
    except Exception as e:
        return f"Error Papago: {str(e)}"

def _ensure_string_result(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, (list, tuple)):
        return " ".join(str(p) for p in result if p).strip() # type: ignore
    if isinstance(result, dict):
        res_dict = cast(Dict[str, Any], result)
        data = res_dict.get('data')
        if isinstance(data, dict):
            if 'content' in data:
                return str(data['content']).strip()
            if 'translation' in data:
                return str(data['translation']).strip()
        if 'translateText' in res_dict:
            return str(res_dict['translateText']).strip()
        if 'translation' in res_dict:
            return str(res_dict['translation']).strip()
        for val in res_dict.values():
            if isinstance(val, str) and val.strip():
                return val.strip()
        return str(result).strip()
    return str(result).strip() if result is not None else ""

def translatorz(translator_name: str, text: str, source_lang: str, target_lang: str) -> str:
    if not text.strip():
        return ""
    with _global_lock:
        try:
            if translator_name == "Papago":
                return _translate_papago_pentago(text, source_lang, target_lang)
            if translator_name == "DeepL":
                if deepl is None:
                    return "Error: deepl no instalado."
                t = cast(Any, deepl).Translator(Config.DEEPL_API_KEY)
                res = t.translate_text(text, target_lang=obtener_codigo("deepl", target_lang))
                return str(getattr(res, 'text', res))
            if translator_name == "Gemini":
                if genai is None:
                    return "Error: google-genai no instalado."
                client = cast(Any, genai).Client(api_key=Config.GEMINI_API_KEY)
                prompt = PROMPT_IA.format(idioma=obtener_codigo("default", target_lang))
                try:
                    res = client.models.generate_content(model=GEMMA_TEXT_MODEL, contents=[f"{prompt}\n\nTexto: {text}"])
                    return str(res.text).strip()
                except Exception:
                    res = client.models.generate_content(model=GEMINI_CHEAP_MODEL, contents=[f"{prompt}\n\nTexto: {text}"])
                    return str(res.text).strip()
            if translator_name == "Mistral":
                if Mistral is None:
                    return "Error: mistralai no instalado."
                client = cast(Any, Mistral)(api_key=Config.MISTRAL_API_KEY)
                prompt = PROMPT_IA.format(idioma=obtener_codigo("default", target_lang))
                res = client.chat.complete(model=Config.MISTRAL_MODEL, messages=[{"role": "user", "content": f"{prompt}\n\nTexto: {text}"}])
                return str(res.choices[0].message.content).strip()

            if ts is None:
                return "Error: translators no disponible."
            
            lib_name = translator_name.lower() if translator_name != "iTranslate" else "itranslate"
            if lib_name == "transmart":
                lib_name = "qqTranSmart"
            elif lib_name == "systran":
                lib_name = "sysTran"
            elif lib_name == "cloudtrans":
                lib_name = "cloudTranslation"
            
            # --- MANEJO DE AUTO Y MAPEO ---
            if source_lang == "auto":
                # README UlionTse: from_language defaults to 'auto'.
                # Detectamos internamente para poder mapear zh-CN/zh-TW si el motor es estricto.
                from_l = detectar_idioma(text)
                from_l_mapped = obtener_codigo(lib_name, from_l)
            else:
                from_l_mapped = obtener_codigo(lib_name, source_lang)
            
            to_l_mapped = obtener_codigo(lib_name, target_lang)
            
            if lib_name == "baidu":
                return _translate_baidu_with_retries(text, from_l_mapped, to_l_mapped)
            
            res = cast(Any, ts).translate_text(
                text, 
                translator=lib_name, 
                from_language=from_l_mapped, 
                to_language=to_l_mapped,
                timeout=12,
                if_print_warning=False
            )
            return _ensure_string_result(res)
        except Exception as e:
            error_str = str(e)
            if translator_name == "Yandex" and "Unsupported from_language[ko]" in error_str:
                return "Error: Yandex no soporta Coreano origen."
            return f"Error en {translator_name}: {error_str}"