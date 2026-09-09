import json
import os
import sys

# Modelos de reconocimiento de PaddleOCR por idioma (nombres reales de PaddleX 3.7)
# Para japonés se usa el rec universal PP-OCRv6 (el japan_PP-OCRv3_mobile_rec lee muy mal en manga)
PADDLE_REC_MODELS = {
    "ch": "PP-OCRv5_mobile_rec",
    "ch_sim": "PP-OCRv5_mobile_rec",
    "zh": "PP-OCRv5_mobile_rec",
    "en": "en_PP-OCRv5_mobile_rec",
    "latin": "latin_PP-OCRv5_mobile_rec",
    "ja": "PP-OCRv6_small_rec",
    "japan": "PP-OCRv6_small_rec",
    "jp": "PP-OCRv6_small_rec",
    "ko": "korean_PP-OCRv5_mobile_rec",
    "korean": "korean_PP-OCRv5_mobile_rec",
    "es": "latin_PP-OCRv5_mobile_rec",
}
DEFAULT_REC_MODEL = "PP-OCRv5_mobile_rec"
# La detección móvil v5 usa resize_long=960 por defecto, lo que aplasta páginas/viñetas y hace
# que el texto diminuto del manga ni se detecte. Subimos el límite del lado largo.
DET_LIMIT_SIDE_LEN = 1600


def run_paddleocr_v5(images, langs=None):
    try:
        from paddleocr import PaddleOCR
        import logging
        logging.getLogger('ppocr').setLevel(logging.ERROR)
        logging.getLogger('paddle').setLevel(logging.ERROR)
        logging.getLogger('paddlex').setLevel(logging.ERROR)

        # Elegir el modelo de reconocimiento según el primer idioma conocido
        rec_model = DEFAULT_REC_MODEL
        for lang in (langs or []):
            key = str(lang).lower()
            if key in PADDLE_REC_MODELS:
                rec_model = PADDLE_REC_MODELS[key]
                break

        ocr = PaddleOCR(
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name=rec_model,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            text_det_limit_side_len=DET_LIMIT_SIDE_LEN,
        )

        results = {}
        for path in images:
            try:
                texts = []
                if hasattr(ocr, "predict"):
                    items = ocr.predict(path)
                    if items is None:
                        items = []
                    elif not isinstance(items, (list, tuple)):
                        try:
                            items = list(items)
                        except TypeError:
                            items = [items]
                    for item in items:
                        if isinstance(item, dict):
                            texts.extend(item.get("rec_texts") or [])
                        else:
                            res = getattr(item, "get", None)
                            if res:
                                texts.extend(res("rec_texts") or [])
                else:
                    # API legada (PaddleOCR 2.x)
                    legacy = ocr.ocr(path, cls=True) or []
                    for page in legacy:
                        for word_info in page or []:
                            if word_info and len(word_info) > 1 and word_info[1]:
                                texts.append(word_info[1][0])
                results[path] = "\n".join(texts)
            except Exception as e:
                results[path] = f"Error procesando imagen: {e}"
        return results
    except Exception as e:
        return {images[0]: f"Error cargando paddleocr: {e}"} if images else {}



def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No se proporcionó el archivo JSON temporal."}))
        sys.exit(1)
        
    temp_json_path = sys.argv[1]
    if not os.path.exists(temp_json_path):
        print(json.dumps({"error": f"Archivo temporal no encontrado: {temp_json_path}"}))
        sys.exit(1)
        
    with open(temp_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    engine = data.get("engine", "").lower()
    images = data.get("images", [])
    langs = data.get("langs", [])
    
    if engine == "paddleocr-v5":
        results = run_paddleocr_v5(images, langs)
        print(json.dumps(results))
    else:
        print(json.dumps({"error": f"Motor no soportado: {engine}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()
