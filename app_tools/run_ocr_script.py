import json
import os
import sys

def run_paddleocr_v5(images):
    try:
        from paddleocr import PaddleOCR
        import logging
        logging.getLogger('ppocr').setLevel(logging.ERROR) # Suppress debug logs
        
        # Initialize PaddleOCR with the specific v5 Korean model
        ocr = PaddleOCR(
            text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
            show_log=False
        )
        
        results = {}
        for path in images:
            try:
                result = ocr.ocr(path, cls=True)
                texts = []
                if result:
                    for line in result:
                        if line:
                            for word_info in line:
                                texts.append(word_info[1][0])
                results[path] = "\n".join(texts)
            except Exception as e:
                results[path] = f"Error procesando imagen: {e}"
        return results
    except Exception as e:
        return {images[0]: f"Error cargando paddleocr: {e}"} if images else {}



def run_mit48x(images):
    # Por ahora devolvemos un mock, ya que la implementación real del 
    # checkpoint de PyTorch dependerá de la arquitectura del modelo de BallloonTranslator.
    results = {}
    for path in images:
        results[path] = f"MIT48x OCR: Listo para procesar {os.path.basename(path)}. (Implementación pendiente de la red neuronal)."
    return results

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
    
    if engine == "mit48x":
        results = run_mit48x(images)
        print(json.dumps(results))
    elif engine == "paddleocr-v5":
        results = run_paddleocr_v5(images)
        print(json.dumps(results))
    else:
        print(json.dumps({"error": f"Motor no soportado: {engine}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()
