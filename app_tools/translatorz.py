# Bibliotecas
import os
import sys
import time
import langid  # type: ignore
import asyncio
from typing import Any, Dict, cast
import threading
import subprocess

# --- Monkey Patch para suprimir ventanas de cscript.exe (execjs) en Windows ---
if sys.platform == "win32":
    _original_popen = subprocess.Popen

    class _PopenNoWindow(_original_popen):
        def __init__(self, *args, **kwargs):
            # Forzar CREATE_NO_WINDOW (0x08000000)
            creationflags = kwargs.get('creationflags', 0)
            if not (creationflags & 0x08000000):
                kwargs['creationflags'] = creationflags | 0x08000000
            
            # Asegurar startupinfo con SW_HIDE
            if 'startupinfo' not in kwargs:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                kwargs['startupinfo'] = startupinfo
                
            super().__init__(*args, **kwargs)

    subprocess.Popen = _PopenNoWindow

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
    genai = None

try:
    import deepl
except ImportError:
    deepl = None

try:
    from mistralai import Mistral
except ImportError:
    Mistral = None

try:
    from pentago import Pentago  # type: ignore
except ImportError:
    Pentago = None

# Configurar rutas
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from config import Config

# --- CONFIGURACIÓN ---
GEMINI_CHEAP_MODEL = "gemini-2.5-flash-lite"
GEMMA_TEXT_MODEL = "gemma-3-27b-it"

# --- PROMPT PARA IA ---
PROMPT_IA = (
    "Instrucción: Traduce el siguiente texto al {idioma}. "
    "Manten el formato, saltos de línea y el tono original. "
    "IMPORTANTE: Devuelve ÚNICAMENTE la traducción, sin textos adicionales, sin introducciones y sin explicaciones."
)

# --- MAPEOS PRECISOS ---
MAPEO_UNIVERSAL: Dict[str, Dict[str, str]] = {
    "es": {"default": "es", "baidu": "spa", "itranslate": "es-ES", "modernmt": "es-ES", "lingvanex": "es_ES", "deepl": "ES", "systran": "es", "cloudtrans": "es"},
    "en": {"default": "en", "itranslate": "en-US", "modernmt": "en-GB", "lingvanex": "en_GB", "deepl": "EN-US", "systran": "en", "cloudtrans": "en"},
    "ja": {"default": "ja", "baidu": "jp", "deepl": "JA", "lingvanex": "ja_JP", "systran": "ja"},
    "ko": {"default": "ko", "baidu": "kor", "deepl": "KO", "lingvanex": "ko_KR", "systran": "ko"},
    "zh-TW": {"default": "zh-TW", "baidu": "cht", "lingvanex": "zh-Hant_TW", "systran": "zh", "cloudtrans": "zh-tw"},
    "zh-CN": {"default": "zh-CN", "baidu": "zh", "lingvanex": "zh-Hans_CN", "systran": "zh", "cloudtrans": "zh-cn"},
}

def obtener_codigo(traductor: str, lang_code: str) -> str:
    traductor = traductor.lower()
    base_code = lang_code.split("-")[0].split("_")[0]
    lang_map = MAPEO_UNIVERSAL.get(lang_code, MAPEO_UNIVERSAL.get(base_code, {}))
    return str(lang_map.get(traductor, lang_map.get("default", base_code)))

def detectar_idioma(texto: str) -> str:
    try:
        if len(texto.split()) <= 2:
            return "es" if any(c in texto.lower() for c in "áéíóúñ") else "en"
        # Usamos type: ignore porque classify devuelve un tipo parcialmente desconocido para Pylance
        res = langid.classify(texto) # type: ignore
        return str(res[0]).split("-")[0]
    except Exception:
        return "en"

# --- SERVICIOS ---

def _translate_baidu_with_retries(text: str, from_l: str, to_l: str) -> str:
    if ts is None: return "Error: translators no disponible."
    
    # Aumentar intentos porque Baidu es inestable
    max_retries = 15 
    
    for i in range(max_retries):
        try:
            res = cast(Any, ts).translate_text(text, translator='baidu', from_language=from_l, to_language=to_l, timeout=10)
            res_str = _ensure_string_result(res)
            
            # Si devuelve mensaje de no certificado o está vacío, reintentar
            if "not certified" in res_str or not res_str.strip():
                if i < max_retries - 1:
                    time.sleep(1.0 + (i * 0.2)) # Backoff ligero
                    continue
                else:
                    return "Error Baidu: Servicio inestable o no certificado tras múltiples intentos."
            
            return res_str
            
        except Exception as e:
            if i == max_retries - 1: return f"Error Baidu: {str(e)}"
            time.sleep(1.5)
            
    return "Error Baidu: Fallo tras múltiples reintentos."

def _translate_papago_pentago(text: str, source_lang: str, target_lang: str) -> str:
    if Pentago is None: return "Error: pentago no instalado."
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
    if isinstance(result, str): return result.strip()
    if isinstance(result, (list, tuple)):
        # type: ignore silencia advertencias sobre p y str() en generadores con tipos desconocidos
        return " ".join(str(p) for p in result if p).strip() # type: ignore
    if isinstance(result, dict):
        res_dict = cast(Dict[str, Any], result)
        # Intentar extraer de forma segura sin asumir estructura fija
        data = res_dict.get('data')
        if isinstance(data, dict):
            if 'content' in data: return str(data['content']).strip()
            if 'translation' in data: return str(data['translation']).strip()
        
        if 'translateText' in res_dict: return str(res_dict['translateText']).strip()
        if 'translation' in res_dict: return str(res_dict['translation']).strip()
        
        # Si es un dict pero no reconocemos la clave, devolver el primer valor que parezca texto
        for val in res_dict.values():
            if isinstance(val, str) and val.strip(): return val.strip()
            
        return str(result).strip()
    return str(result).strip() if result is not None else ""

def translatorz(translator_name: str, text: str, source_lang: str, target_lang: str) -> str:
    if not text.strip(): return ""
    
    with _global_lock:
        try:
            # 1. Especiales
            if translator_name == "Papago": return _translate_papago_pentago(text, source_lang, target_lang)
            
            if translator_name == "DeepL":
                if deepl is None: return "Error: deepl no instalado."
                t = cast(Any, deepl).Translator(Config.DEEPL_API_KEY)
                res = t.translate_text(text, target_lang=obtener_codigo("deepl", target_lang))
                return str(getattr(res, 'text', res))
                
            if translator_name == "Gemini":
                if genai is None: return "Error: google-genai no instalado."
                client = cast(Any, genai).Client(api_key=Config.GEMINI_API_KEY)
                prompt = PROMPT_IA.format(idioma=MAPEO_UNIVERSAL.get(target_lang, {}).get("default", "Español"))
                
                # Intentar primero con Gemma (Modelo de texto puro de alta calidad)
                try:
                    res = client.models.generate_content(model=GEMMA_TEXT_MODEL, contents=[f"{prompt}\n\nTexto: {text}"])
                    return str(res.text).strip()
                except Exception:
                    # Fallback a Gemini Flash Lite si Gemma falla (por cuota o disponibilidad)
                    res = client.models.generate_content(model=GEMINI_CHEAP_MODEL, contents=[f"{prompt}\n\nTexto: {text}"])
                    return str(res.text).strip()
                
            if translator_name == "Mistral":
                if Mistral is None: return "Error: mistralai no instalado."
                client = cast(Any, Mistral)(api_key=Config.MISTRAL_API_KEY)
                prompt = PROMPT_IA.format(idioma=MAPEO_UNIVERSAL.get(target_lang, {}).get("default", "Español"))
                res = client.chat.complete(model=Config.MISTRAL_MODEL, messages=[{"role": "user", "content": f"{prompt}\n\nTexto: {text}"}])
                return str(res.choices[0].message.content).strip()

            # 2. Librería 'translators'
            if ts is None: return "Error: translators no disponible."
            
            lib_name = translator_name.lower() if translator_name != "iTranslate" else "itranslate"
            if lib_name == "transmart": lib_name = "qqTranSmart"
            if lib_name == "systran": lib_name = "sysTran"
            if lib_name == "cloudtrans": lib_name = "cloudTranslation"
            
            from_l = source_lang
            if from_l == "auto": from_l = detectar_idioma(text)
            
            from_l_mapped = obtener_codigo(lib_name, from_l)
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
                return "Error: Yandex Translate no soporta Coreano como idioma de origen."
            return f"Error en {translator_name}: {error_str}"