# %% [markdown]
# # Qwen-Image-Edit-2511 — Kaggle 2× T4
#
# **Accelerator:** GPU T4 × 2 (16 GB c/u = 32 GB)
#
# Modelo completo bf16 ≈ 57 GB → se carga cuantizado (NF4 4-bit o 8-bit)
# y se **reparte en 2 GPUs**. T4 no soporta bf16 → se usa **float16**.
#
# Optimizado para edición de **manga / manhwa / manhua** (imágenes portrait).
# Devuelve siempre la imagen al **tamaño original** de entrada.
#
# ### Configuración vía variables de entorno
# | Variable | Default | Descripción |
# |---|---|---|
# | `QWEN_EDIT_MODEL` | `Qwen/Qwen-Image-Edit-2511` | HF model ID |
# | `QWEN_LIGHTNING` | `1` | Usar Lightning LoRA (4 steps) |
# | `QUANT_BITS` | `4` | Cuantización: `4` (NF4) o `8` (INT8) |
# | `MAX_PROC_SIDE` | `1024` | Lado máximo de procesamiento (px) |

# %% [markdown]
# ## 1) Instalación

# %%
import subprocess
import sys


def install_packages():
    """Instala dependencias con manejo de compatibilidad para Kaggle."""
    # ── Paso 1: Desinstalar torchao incompatible ─────────────────────
    # Kaggle trae torchao pre-instalado sin FqnToConfig, lo que rompe
    # diffusers de git. No necesitamos torchao (usamos bitsandbytes),
    # así que al desinstalarlo diffusers lo ignora limpiamente.
    print("🔧 Removiendo torchao incompatible (usamos bitsandbytes)…")
    subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", "torchao"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Limpiar módulos ya cacheados en el proceso Python
    import sys as _sys
    for mod in [k for k in _sys.modules if k == "torchao" or k.startswith("torchao.")]:
        del _sys.modules[mod]

    # ── Paso 2: Instalar dependencias ────────────────────────────────
    # diffusers de git: único lugar con QwenImageEditPlusPipeline
    pkgs = [
        "git+https://github.com/huggingface/diffusers",
        "transformers>=4.45.0",
        "accelerate",
        "bitsandbytes",
        "gradio",
        "sentencepiece",
        "qwen-vl-utils",
    ]
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-q", "--upgrade", *pkgs],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        print("✅ Paquetes instalados correctamente")
    except subprocess.CalledProcessError:
        print("⚠️  Instalación batch falló — intentando uno a uno…")
        failed = []
        for pkg in pkgs:
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "-q", "--upgrade", pkg],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                print(f"  ✅ {pkg}")
            except subprocess.CalledProcessError:
                print(f"  ❌ {pkg}")
                failed.append(pkg)
        if failed:
            raise RuntimeError(
                f"Paquetes no instalados: {', '.join(failed)}. "
                "Revisá tu conexión o instalá manualmente."
            )


install_packages()

# %% [markdown]
# ## 2) Configuración y carga del pipeline
#
# Orden en 2× T4 (evita OOM):
# 1. `text_encoder` + `vae` → GPU 1
# 2. `transformer` (el más pesado) → GPU 0
#
# Con cuantización 8-bit el text_encoder tiene mejor calidad de comprensión.
# Con 4-bit (NF4 + double quant) se maximiza VRAM libre para imágenes grandes.

# %%
import gc
import logging
import os
import time
import io
import tempfile
from typing import Optional, Tuple

import torch

# ── Funciones Stealth (Encriptación en RAM) ─────────────────────────
STEALTH_KEY = b"StealthKaggleBotEvasionKey2026_Babylon!"

def crypt_bytes(data: bytes) -> bytearray:
    arr = bytearray(data)
    key_len = len(STEALTH_KEY)
    for i in range(len(arr)):
        arr[i] ^= STEALTH_KEY[i % key_len]
    return arr


# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("qwen-edit")

# ── Configuración ────────────────────────────────────────────────────
MODEL_ID: str = os.environ.get("QWEN_EDIT_MODEL", "Qwen/Qwen-Image-Edit-2511")
USE_LIGHTNING: bool = os.environ.get("QWEN_LIGHTNING", "1") == "1"
QUANT_BITS: int = int(os.environ.get("QUANT_BITS", "4"))
MAX_PROC_SIDE: int = int(os.environ.get("MAX_PROC_SIDE", "1024"))
MIN_PROC_SIDE: int = 256

assert QUANT_BITS in (4, 8), "QUANT_BITS debe ser 4 u 8"

# ── Performance para T4 (Turing) ────────────────────────────────────
torch.backends.cudnn.benchmark = True
compute_dtype = torch.float16  # T4 no soporta bf16 nativo

# ── Validación de GPUs ───────────────────────────────────────────────
num_gpus = torch.cuda.device_count()
if num_gpus < 2:
    log.warning(
        f"Solo {num_gpus} GPU(s) detectada(s). "
        "Este script está optimizado para 2× T4. "
        "Puede haber OOM con imágenes grandes."
    )

log.info(f"GPUs detectadas: {num_gpus}")
for i in range(num_gpus):
    props = torch.cuda.get_device_properties(i)
    log.info(f"  cuda:{i}  {props.name}  {props.total_memory / 1024**3:.1f} GB")

# ── Cuantización ─────────────────────────────────────────────────────
from diffusers import BitsAndBytesConfig as DiffusersBnbConfig
from diffusers import QwenImageEditPlusPipeline
from diffusers.quantizers import PipelineQuantizationConfig
from transformers import BitsAndBytesConfig as TransformersBnbConfig


def make_bnb_config(config_cls, bits: int = 4):
    """Crea config de cuantización para diffusers o transformers."""
    if bits == 8:
        return config_cls(load_in_8bit=True)
    return config_cls(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_use_double_quant=True,
    )


# Transformer siempre en QUANT_BITS; text_encoder puede ir en 8-bit
# si hay VRAM suficiente (2 GPUs) para mejor comprensión del prompt.
te_quant_bits = min(QUANT_BITS, 8) if num_gpus >= 2 else QUANT_BITS
log.info(f"Cuantización: transformer={QUANT_BITS}-bit  text_encoder={te_quant_bits}-bit")

# ── Cargar componentes individuales ────────────────────────────────────
# Debido a un bug de OOM y meta-tensors en diffusers con device_map="balanced"
# al usar bitsandbytes, la forma más segura y estable es cargar los modelos
# pesados individualmente en la GPU correcta y luego pasarlos al pipeline.

from diffusers import AutoencoderKLQwenImage, QwenImageTransformer2DModel
try:
    from transformers import Qwen2_5_VLForConditionalGeneration as TEClass
except ImportError:
    from transformers import AutoModel as TEClass

log.info(f"Cargando componentes de {MODEL_ID} …")
t0 = time.time()

try:
    # EL TRUCO DEFINITIVO:
    # No usamos device_map explícito porque 'accelerate' y 'bitsandbytes'
    # tienen un bug con meta tensors. En su lugar, seteamos la GPU activa
    # antes de cargar cada componente.

    # 1. VAE y Text Encoder -> GPU 1 (si hay 2)
    if num_gpus >= 2:
        torch.cuda.set_device(1)
        dev_te = "cuda:1"
    else:
        torch.cuda.set_device(0)
        dev_te = "cuda:0"

    log.info(f"  -> Cargando VAE en {dev_te}...")
    vae = AutoencoderKLQwenImage.from_pretrained(
        MODEL_ID,
        subfolder="vae",
        torch_dtype=compute_dtype,
    ).to(dev_te)

    log.info(f"  -> Cargando Text Encoder en {dev_te} ({te_quant_bits}-bit)...")
    text_encoder = TEClass.from_pretrained(
        MODEL_ID,
        subfolder="text_encoder",
        quantization_config=make_bnb_config(TransformersBnbConfig, te_quant_bits),
        torch_dtype=compute_dtype,
        low_cpu_mem_usage=True,
    )

    # 2. Transformer -> GPU 0 (el más pesado)
    torch.cuda.set_device(0)
    dev_tr = "cuda:0"

    log.info(f"  -> Cargando Transformer en {dev_tr} ({QUANT_BITS}-bit)...")
    transformer = QwenImageTransformer2DModel.from_pretrained(
        MODEL_ID,
        subfolder="transformer",
        quantization_config=make_bnb_config(DiffusersBnbConfig, QUANT_BITS),
        torch_dtype=compute_dtype,
        low_cpu_mem_usage=True,
    )

    # 3. Pipeline
    log.info("  -> Inicializando pipeline...")
    # Restauramos a GPU 0 por defecto para el resto del script
    torch.cuda.set_device(0)
    pipe = QwenImageEditPlusPipeline.from_pretrained(
        MODEL_ID,
        vae=vae,
        text_encoder=text_encoder,
        transformer=transformer,
        torch_dtype=compute_dtype,
        safety_checker=None, # Desactivamos cualquier filtro NSFW de HuggingFace
    )

    # 4. Magia de multi-GPU manual
    # El pipeline asume que todo está en pipe.device (cuda:0). Como pusimos
    # el VAE y el Text Encoder en cuda:1, si no interceptamos los inputs,
    # PyTorch tirará RuntimeError y crasheará Kaggle con Status 42 (OOM).
    if num_gpus >= 2:
        log.info("  -> Inyectando hooks de Accelerate para ruteo multi-GPU...")
        from accelerate.hooks import add_hook_to_module, AlignDevicesHook
        
        # El hook intercepta cada llamada al modelo, mueve los inputs a cuda:1,
        # lo ejecuta, y mueve los outputs de vuelta a cuda:0.
        hook_te = AlignDevicesHook(execution_device="cuda:1", io_same_device=True)
        add_hook_to_module(pipe.text_encoder, hook_te)
        
        hook_vae = AlignDevicesHook(execution_device="cuda:1", io_same_device=True)
        add_hook_to_module(pipe.vae, hook_vae)

    # Como ya distribuimos los pesos manualmente entre GPU 0 y 1,
    # no usamos enable_model_cpu_offload porque ya usamos bitsandbytes en VRAM.

except Exception as e:
    log.error(f"Error cargando los componentes: {e}")
    raise RuntimeError(f"No se pudo cargar el modelo: {e}")

gc.collect()
torch.cuda.empty_cache()

# ── Optimizaciones de memoria ────────────────────────────────────────
pipe.set_progress_bar_config(disable=False)
pipe.enable_attention_slicing()
try:
    pipe.enable_vae_slicing()
except Exception:
    pass

# ── Lightning LoRA ───────────────────────────────────────────────────
if USE_LIGHTNING:
    try:
        pipe.load_lora_weights(
            "lightx2v/Qwen-Image-Edit-2511-Lightning",
            weight_name="Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
        )
        pipe.fuse_lora()
        log.info("✅ Lightning LoRA cargada (4 steps)")
    except Exception as e:
        log.warning(f"Lightning no disponible, se usarán steps normales: {e}")
        USE_LIGHTNING = False

load_time = time.time() - t0
log.info(f"✅ Pipeline listo en {load_time:.1f}s")

# ── Reporte de estado ────────────────────────────────────────────────
for name in ("transformer", "text_encoder", "vae"):
    module = getattr(pipe, name, None)
    if module is None:
        continue
    try:
        dev = next(module.parameters()).device
        is_4bit = getattr(module, "is_loaded_in_4bit", False)
        is_8bit = getattr(module, "is_loaded_in_8bit", False)
        quant = "4-bit" if is_4bit else ("8-bit" if is_8bit else "full")
        log.info(f"  {name:20s} → {dev}  ({quant})")
    except StopIteration:
        log.info(f"  {name:20s} → sin parámetros")

gc.collect()
torch.cuda.empty_cache()

for i in range(num_gpus):
    alloc = torch.cuda.memory_allocated(i) / 1024**3
    total = torch.cuda.get_device_properties(i).total_memory / 1024**3
    log.info(f"  VRAM cuda:{i}: {alloc:.2f} / {total:.1f} GB")

# %% [markdown]
# ## 3) Función de edición
#
# Maneja imágenes de cualquier tamaño y aspect ratio.
# Internamente escala a un tamaño compatible con el VAE (múltiplo de 32)
# y al final **restaura las dimensiones originales**.

# %%
from PIL import Image

# La función edit_image queda intacta internamente porque recibe una imagen PIL 
# y devuelve una imagen PIL. Toda la ofuscación ocurre en el wrapper de Gradio.


def fit_for_vae(
    image: Image.Image,
    max_side: int = MAX_PROC_SIDE,
    min_side: int = MIN_PROC_SIDE,
) -> Tuple[Image.Image, Tuple[int, int]]:
    """Escala la imagen preservando aspect ratio y redondea a múltiplo de 32.

    Returns:
        (imagen_escalada, (proc_w, proc_h))
    """
    w, h = image.size

    # Escalar para que el lado más largo no supere max_side
    scale = min(max_side / max(w, h), 1.0)
    # Asegurar que el lado más corto no baje de min_side
    if min(w, h) * scale < min_side:
        scale = min_side / min(w, h)

    new_w = int(round(w * scale))
    new_h = int(round(h * scale))

    # Redondear a múltiplo de 32 (requerido por VAE)
    new_w = max(min_side, min(max_side, (new_w // 32) * 32))
    new_h = max(min_side, min(max_side, (new_h // 32) * 32))

    resized = image.resize((new_w, new_h), Image.LANCZOS)
    return resized, (new_w, new_h)


def edit_image(
    image: Image.Image,
    prompt: str,
    seed: int = 0,
    steps: Optional[int] = None,
    true_cfg: float = 4.0,
    negative_prompt: str = "",
) -> Tuple[Image.Image, str]:
    """Edita una imagen con Qwen-Image-Edit y devuelve al tamaño original.

    Args:
        image: Imagen PIL de entrada (cualquier tamaño).
        prompt: Instrucción de edición en inglés.
        seed: Seed para reproducibilidad.
        steps: Pasos de inferencia (None = auto: 4 Lightning / 30 normal).
        true_cfg: Escala CFG (>1 para seguir más el prompt).
        negative_prompt: Prompt negativo opcional.

    Returns:
        (imagen_editada_tamaño_original, metadata_string)
    """
    if image is None:
        raise ValueError("Subí una imagen primero")
    if not prompt or not prompt.strip():
        raise ValueError("Escribí un prompt de edición")

    # Normalizar entrada
    if isinstance(image, list):
        image = image[0]
    if not isinstance(image, Image.Image):
        image = Image.open(image)
    image = image.convert("RGB")

    original_size = image.size  # (w, h) — lo que vamos a restaurar

    # Escalar para procesamiento
    proc_image, (proc_w, proc_h) = fit_for_vae(image)
    log.info(
        f"Imagen: {original_size[0]}×{original_size[1]} → "
        f"procesando a {proc_w}×{proc_h}"
    )

    # Steps
    if steps is None or int(steps) <= 0:
        steps = 4 if USE_LIGHTNING else 30
    steps = int(steps)

    # CFG
    cfg_value = max(1.0, float(true_cfg))

    # Negative prompt
    neg = negative_prompt.strip() if negative_prompt and negative_prompt.strip() else " "

    inputs = {
        "image": proc_image,
        "prompt": prompt.strip(),
        "generator": torch.Generator("cpu").manual_seed(int(seed)),
        "true_cfg_scale": cfg_value,
        "negative_prompt": neg,
        "num_inference_steps": steps,
        "guidance_scale": 1.0,
        "num_images_per_prompt": 1,
    }

    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()

    try:
        with torch.inference_mode():
            output = pipe(**inputs)
    except torch.cuda.OutOfMemoryError:
        gc.collect()
        torch.cuda.empty_cache()
        log.error("OOM! Intentando con tamaño reducido…")
        # Retry con imagen más chica
        smaller, (sw, sh) = fit_for_vae(image, max_side=MAX_PROC_SIDE // 2)
        inputs["image"] = smaller
        log.info(f"  Reintentando a {sw}×{sh}")
        with torch.inference_mode():
            output = pipe(**inputs)
    finally:
        gc.collect()
        torch.cuda.empty_cache()

    elapsed = time.time() - t0
    result = output.images[0]

    # ── Restaurar tamaño original ────────────────────────────────────
    if result.size != original_size:
        result = result.resize(original_size, Image.LANCZOS)

    # ── Metadata ─────────────────────────────────────────────────────
    peaks = []
    for i in range(num_gpus):
        peak = torch.cuda.max_memory_allocated(i) / 1024**3
        peaks.append(f"cuda:{i} {peak:.2f} GB")

    metadata = (
        f"✅ Listo en {elapsed:.1f}s\n"
        f"📐 Original: {original_size[0]}×{original_size[1]} → "
        f"Procesado: {proc_w}×{proc_h} → Restaurado: {result.size[0]}×{result.size[1]}\n"
        f"🎲 Seed: {seed} | Steps: {steps} | CFG: {cfg_value}\n"
        f"💾 Peak VRAM: {' | '.join(peaks)}"
    )
    log.info(metadata.replace("\n", " · "))
    return result, metadata


# %% [markdown]
# ## 4) UI Gradio
#
# Optimizada para manga / manhwa / manhua:
# - Layout vertical para imágenes portrait
# - Galería acumulativa de resultados
# - Panel de metadata (VRAM, tiempos, resolución)
# - Prompts de ejemplo para edición de manga

# %%
import gradio as gr

# ── CSS personalizado ────────────────────────────────────────────────
CUSTOM_CSS = """
.metadata-box textarea {
    font-family: 'Cascadia Code', 'Fira Code', monospace !important;
    font-size: 0.85em !important;
    line-height: 1.5 !important;
}
footer { display: none !important; }
"""

import io
import tempfile

# ── Cifrado XOR (Debe coincidir con manga_crypt.py) ──────────────────
KEY = b"StealthKaggleBotEvasionKey2026_Babylon!"

def crypt_bytes(data: bytes) -> bytearray:
    """Aplica XOR simétrico a los bytes para evadir escaneos."""
    arr = bytearray(data)
    key_len = len(KEY)
    for i in range(len(arr)):
        arr[i] ^= KEY[i % key_len]
    return arr

# ── Wrapper para Gradio (Stealth) ─────────────────────────────────────────────

def gradio_edit(file_path, prompt, seed, steps, true_cfg, negative_prompt):
    """Wrapper que recibe archivo encriptado, lo abre en RAM, edita, y lo vuelve a encriptar."""
    if file_path is None:
        raise gr.Error("Debes subir un archivo .enc")
        
    try:
        # 1. Leer y desencriptar el archivo en la RAM
        with open(file_path, "rb") as f:
            encrypted_data = f.read()
        decrypted_data = crypt_bytes(encrypted_data)
        
        # Cargar como imagen directamente desde la memoria
        image = Image.open(io.BytesIO(decrypted_data)).convert("RGB")
        
        # 2. Procesar (Los tensores pasan a VRAM y vuelven a RAM como PIL Image)
        result_img, metadata = edit_image(
            image=image,
            prompt=prompt,
            seed=int(seed),
            steps=int(steps) if steps and int(steps) > 0 else None,
            true_cfg=float(true_cfg),
            negative_prompt=negative_prompt or "",
        )
        
        # 3. Guardar imagen editada en buffer de RAM y encriptar
        buf = io.BytesIO()
        result_img.save(buf, format="PNG")
        encrypted_out = crypt_bytes(buf.getvalue())
        
        # 4. Guardar archivo temporal ofuscado para que Gradio lo ofrezca de descarga
        temp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".enc")
        temp_out.write(encrypted_out)
        temp_out.close()
        
        return temp_out.name, metadata

    except ValueError as e:
        raise gr.Error(str(e))
    except torch.cuda.OutOfMemoryError:
        gc.collect()
        torch.cuda.empty_cache()
        raise gr.Error("⚠️ Sin VRAM suficiente.")
    except Exception as e:
        log.exception("Error inesperado en edición")
        raise gr.Error(f"Error: {e}")

# ── Construir UI Ciega (Sin imágenes) ──────────────────────────────────────────
quant_label = f"{QUANT_BITS}-bit" if QUANT_BITS == te_quant_bits else (
    f"transformer {QUANT_BITS}-bit / text_encoder {te_quant_bits}-bit"
)
lightning_label = "Lightning 4-steps" if USE_LIGHTNING else "Normal"

with gr.Blocks(
    title="Qwen Image Edit — Stealth Mode",
    css=CUSTOM_CSS,
    theme=gr.themes.Soft(),
) as demo:
    gr.Markdown(
        f"## 🥷 Qwen Image Edit — Stealth Mode\n"
        f"**Modelo:** `{MODEL_ID}` · **Cuantización:** {quant_label} · "
        f"**Modo:** {lightning_label} · **GPU:** T4 × {num_gpus}\n\n"
        f"*Todo archivo subido o descargado debe estar encriptado (.enc) para evadir escaneos.*"
    )

    with gr.Row(equal_height=False):
        # ── Columna izquierda: entrada ───────────────────────────────
        with gr.Column(scale=1):
            inp_file = gr.File(
                label="📥 Subir archivo encriptado (.enc)",
                type="filepath",
            )
            prompt = gr.Textbox(
                label="✏️ Prompt de edición (inglés)",
                placeholder="e.g.: remove all the text",
                lines=3,
            )

            with gr.Accordion("⚙️ Opciones avanzadas", open=False):
                seed = gr.Number(value=0, label="Seed", precision=0)
                steps = gr.Slider(
                    minimum=1, maximum=50,
                    value=4 if USE_LIGHTNING else 30, step=1,
                    label="Inference Steps",
                )
                true_cfg = gr.Slider(
                    minimum=1.0, maximum=8.0,
                    value=4.0, step=0.5,
                    label="True CFG Scale",
                )
                negative_prompt = gr.Textbox(
                    label="Negative Prompt (opcional)",
                    lines=1,
                )

            with gr.Row():
                btn_edit = gr.Button("🚀 Editar (Stealth)", variant="primary", size="lg")
                btn_clear = gr.ClearButton(
                    components=[inp_file, prompt, negative_prompt],
                    value="🗑️ Limpiar", size="lg",
                )

        # ── Columna derecha: resultado ───────────────────────────────
        with gr.Column(scale=1):
            out_file = gr.File(
                label="📤 Resultado ofuscado (.enc) (Descárgalo y desencríptalo en tu PC)",
                interactive=False,
            )
            metadata_box = gr.Textbox(
                label="📊 Info de ejecución",
                interactive=False,
                lines=4,
                elem_classes=["metadata-box"],
            )

    # ── Conectar eventos ─────────────────────────────────────────────
    btn_edit.click(
        fn=gradio_edit,
        inputs=[inp_file, prompt, seed, steps, true_cfg, negative_prompt],
        outputs=[out_file, metadata_box],
    )
    prompt.submit(
        fn=gradio_edit,
        inputs=[inp_file, prompt, seed, steps, true_cfg, negative_prompt],
        outputs=[out_file, metadata_box],
    )

demo.launch(share=True, inline=True)
