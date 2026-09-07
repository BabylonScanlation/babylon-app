import json
import os
import sys

def run_paddle_vl(images):
    # Intentamos cargar el modelo VLM
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch
        
        # Cargar con trust_remote_code por si es arquitectura personalizada
        tokenizer = AutoTokenizer.from_pretrained("jzhang533/PaddleOCR-VL-For-Manga", trust_remote_code=True)
        # Cargamos el modelo a CPU por defecto, o CUDA si está disponible
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = AutoModelForCausalLM.from_pretrained("jzhang533/PaddleOCR-VL-For-Manga", trust_remote_code=True).to(device).eval()
        
        results = {}
        for path in images:
            # Creamos el input usando el formato tipico de Qwen-VL / VLM
            try:
                # El prompt exacto depende del entrenamiento, usamos uno genérico de OCR
                query = tokenizer.from_list_format([
                    {'image': path},
                    {'text': 'Extract all text from the image.'},
                ])
                inputs = tokenizer(query, return_tensors='pt').to(device)
                pred = model.generate(**inputs, max_new_tokens=512)
                response = tokenizer.decode(pred.cpu()[0], skip_special_tokens=True)
                # Limpiar la respuesta si devuelve el prompt incluido
                if 'Extract all text from the image.' in response:
                    response = response.split('Extract all text from the image.')[-1].strip()
                results[path] = response
            except Exception as e:
                results[path] = f"Error en inferencia de PaddleOCR-VL: {e}"
        return results
    except Exception as e:
        return {images[0]: f"Error cargando el entorno de transformers/torch: {e}"} if images else {}



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
    elif engine == "paddle-vl":
        results = run_paddle_vl(images)
        print(json.dumps(results))
    else:
        print(json.dumps({"error": f"Motor no soportado: {engine}"}))
        sys.exit(1)

if __name__ == "__main__":
    main()
