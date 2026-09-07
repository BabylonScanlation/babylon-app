import base64
import logging
import os
import random
import time
import threading
from typing import List, Optional, Any, Callable, Dict
from mistralai import Mistral
from app_tools.ai_service import BaseAIProcessor, AIAPIError
from config import Config

class MistralAPIError(AIAPIError):
    """Excepción específica para errores de la API de Mistral."""
    pass

class MistralProcessor(BaseAIProcessor):
    def __init__(self):
        super().__init__(model_name="Mistral")

    def encode_image(self, image_path: str) -> Optional[str]:
        try:
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode("utf-8")
        except Exception as e:
            logging.error(f"Error codificando imagen: {e}")
            return None

    def call_api(self, prompt: str, image_path: Optional[str] = None, content: Optional[str] = None) -> Optional[str]:
        client = Mistral(api_key=Config.MISTRAL_API_KEY)
        
        content_list: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        
        if image_path:
            base64_image = self.encode_image(image_path)
            if base64_image:
                content_list.append({
                    "type": "image_url",
                    "image_url": f"data:image/jpeg;base64,{base64_image}",
                })
        
        if content:
            content_list.append({"type": "text", "text": content})

        # Usamos casting para que Pylance no se queje del tipo de mensajes
        messages: Any = [{"role": "user", "content": content_list}]

        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Usamos pixtral-12b-2409 si hay imagen, de lo contrario mistral-large-latest
                model = "pixtral-12b-2409" if image_path else "mistral-large-latest"
                
                response = client.chat.complete(
                    model=model,
                    messages=messages,
                    temperature=0.7,
                )
                
                if response and response.choices:
                    res_content = response.choices[0].message.content
                    return str(res_content) if res_content is not None else None
                else:
                    raise MistralAPIError("Respuesta de Mistral vacía.")
                    
            except Exception as e:
                err_msg = str(e).lower()
                is_rate_limit = "429" in err_msg or "rate limit" in err_msg
                
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    if is_rate_limit:
                        wait_time += 5 # Penalización extra por rate limit
                        logging.warning(f"[Mistral] Rate limit (429). Esperando {wait_time:.1f}s...")
                    
                    time.sleep(wait_time)
                else:
                    raise MistralAPIError(f"Error persistente con Mistral: {e}")
        return None

    def _process_selected_files_mistral(self, file_paths: List[str], output_dir: str, cancel_event: Optional[threading.Event], callback: Callable[[str], None]):
        """Compatibilidad con firma antigua."""
        self.reset_counters()
        input_base = os.path.commonpath(file_paths)
        if os.path.isfile(input_base):
            input_base = os.path.dirname(input_base)
        
        try:
            for f in file_paths:
                if cancel_event and cancel_event.is_set():
                    callback("cancelled")
                    return
                self.process_file(f, output_dir, input_base)
            callback("success")
        except Exception as e:
            logging.error(f"Error en Mistral seleccionado: {e}")
            callback(f"Error: {str(e)}")
