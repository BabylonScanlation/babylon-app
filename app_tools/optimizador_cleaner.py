import os
import glob
import random
import time
import cv2
import numpy as np
import xml.etree.ElementTree as ET
import optuna

# Importamos la clase que queremos optimizar
from cleaner_service import BabylonCleaner

# Configuración de rutas
MANGA109_DIR = r"c:\Users\Administrator\Documents\babylon-app\kaggle_dataset"
ANNOTATIONS_DIR = os.path.join(MANGA109_DIR, "annotations")
IMAGES_DIR = os.path.join(MANGA109_DIR, "images")

# Variables globales para cachear imágenes y optimizar el proceso
CACHE_DATA = []

def parse_manga109_xml(xml_path):
    """Extrae las bounding boxes de texto por página del XML."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    pages_data = {}
    
    for page in root.find('pages').findall('page'):
        index = int(page.get('index'))
        bboxes = []
        for text in page.findall('text'):
            xmin = int(text.get('xmin'))
            ymin = int(text.get('ymin'))
            xmax = int(text.get('xmax'))
            ymax = int(text.get('ymax'))
            bboxes.append((xmin, ymin, xmax, ymax))
        pages_data[index] = bboxes
    return pages_data

def prepare_dataset(num_books=2, pages_per_book=5):
    """Carga una muestra de imágenes y sus anotaciones en memoria RAM para acelerar Optuna."""
    global CACHE_DATA
    CACHE_DATA = []
    
    xml_files = glob.glob(os.path.join(ANNOTATIONS_DIR, "*.xml"))
    if not xml_files:
        raise ValueError(f"No se encontraron XMLs en {ANNOTATIONS_DIR}")
    
    selected_xmls = random.sample(xml_files, min(num_books, len(xml_files)))
    
    print(f"[*] Cargando dataset en memoria (Muestra de {len(selected_xmls)} libros)...")
    
    for xml_path in selected_xmls:
        book_name = os.path.basename(xml_path).replace('.xml', '')
        pages_data = parse_manga109_xml(xml_path)
        
        # Filtramos páginas que tengan texto
        valid_pages = [idx for idx, bboxes in pages_data.items() if len(bboxes) > 0]
        selected_pages = random.sample(valid_pages, min(pages_per_book, len(valid_pages)))
        
        for idx in selected_pages:
            img_filename = f"{idx:03d}.jpg"
            img_path = os.path.join(IMAGES_DIR, book_name, img_filename)
            
            if os.path.exists(img_path):
                img = cv2.imread(img_path)
                if img is not None:
                    h, w = img.shape[:2]
                    
                    # Crear máscara Ground Truth de los textos
                    gt_mask = np.zeros((h, w), dtype=np.uint8)
                    for (xmin, ymin, xmax, ymax) in pages_data[idx]:
                        cv2.rectangle(gt_mask, (xmin, ymin), (xmax, ymax), 255, -1)
                        
                    # Crear máscara dilatada (margen del globo tolerado)
                    # Un globo suele ser más grande que el texto, así que dilatamos el texto
                    # para saber cuál es el "límite máximo aceptable" antes de penalizar como FP.
                    kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (120, 120))
                    gt_dilated = cv2.dilate(gt_mask, kernel_dilate)
                    
                    CACHE_DATA.append({
                        'img': img,
                        'gt_mask': gt_mask,
                        'gt_dilated': gt_dilated,
                        'name': f"{book_name}_{img_filename}"
                    })
    
    print(f"[+] Dataset listo: {len(CACHE_DATA)} imágenes cacheadas.")

def objective(trial):
    """Función objetivo para Optuna."""
    # 1. Definir hiperparámetros a optimizar (espacio de búsqueda de 10 dimensiones)
    kwargs = {
        "umbral_blanco": trial.suggest_int("umbral_blanco", 150, 240),
        "cierre_tinta_px": trial.suggest_int("cierre_tinta_px", 3, 21, step=2),
        "min_area": trial.suggest_int("min_area", 500, 3000),
        "max_area_factor": trial.suggest_float("max_area_factor", 0.1, 0.8),
        "min_extent": trial.suggest_float("min_extent", 0.2, 0.7),
        "min_solidity": trial.suggest_float("min_solidity", 0.4, 0.9),
        "max_aspect_ratio": trial.suggest_float("max_aspect_ratio", 1.5, 5.0),
        "min_text_density": trial.suggest_float("min_text_density", 0.001, 0.05),
        "umbral_tinta": trial.suggest_int("umbral_tinta", 50, 180),
        "borde_padding": trial.suggest_int("borde_padding", 2, 12)
    }
    
    cleaner = BabylonCleaner(**kwargs)
    
    total_f1 = 0.0
    
    for data in CACHE_DATA:
        img = data['img']
        gt_mask = data['gt_mask']
        gt_dilated = data['gt_dilated']
        
        # 2. Ejecutar el cleaner
        try:
            # Retorna: img_limpia, mascara, cant_contornos, tiempo
            _, pred_mask, _, _ = cleaner.limpiar_imagen_blancos(img)
        except Exception as e:
            return 0.0  # Penalización fuerte si falla
            
        # 3. Calcular Fitness (Recall y Precision)
        # Recall: ¿Cubrimos todo el texto del GT?
        tp_text = cv2.bitwise_and(pred_mask, gt_mask)
        suma_gt = np.sum(gt_mask == 255)
        recall = np.sum(tp_text == 255) / suma_gt if suma_gt > 0 else 0.0
        
        # Precision: ¿Nos salimos de los globos (borramos página entera)?
        # Todo lo que esté en pred_mask PERO NO esté en gt_dilated es un Falso Positivo.
        tp_bubble = cv2.bitwise_and(pred_mask, gt_dilated)
        suma_pred = np.sum(pred_mask == 255)
        precision = np.sum(tp_bubble == 255) / suma_pred if suma_pred > 0 else 1.0
        
        # F1-Score como métrica balanceada
        if recall + precision == 0:
            f1 = 0.0
        else:
            f1 = 2 * (recall * precision) / (recall + precision)
            
        total_f1 += f1
        
    # Promedio del dataset
    avg_f1 = total_f1 / len(CACHE_DATA) if CACHE_DATA else 0.0
    return avg_f1

import threading

class EarlyStoppingCallback(object):
    def __init__(self, early_stopping_rounds: int):
        self.early_stopping_rounds = early_stopping_rounds
        self._best_score = None
        self._stagnation = 0
        self._lock = threading.Lock()

    def __call__(self, study: optuna.Study, trial: optuna.Trial):
        if study.best_value is None or trial.value is None:
            return

        with self._lock:
            current_best = study.best_value
            if self._best_score is None:
                self._best_score = current_best
            else:
                if current_best > self._best_score:
                    self._best_score = current_best
                    self._stagnation = 0
                else:
                    self._stagnation += 1

            if self._stagnation >= self.early_stopping_rounds:
                print(f"[RESTART] Se estancó por {self.early_stopping_rounds} trials. ¡Saltando a nuevo horizonte!")
                study.stop()

if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    
    print("==================================================")
    print("  [AI] OPTIMIZADOR BABYLON CLEANER - MAXIMA POTENCIA")
    print("==================================================")
    
    try:
        prepare_dataset(num_books=3, pages_per_book=4)
    except Exception as e:
        print(f"Error cargando dataset: {e}")
        exit(1)
        
    optuna.logging.set_verbosity(optuna.logging.INFO)
    
    max_trials = 10000
    trials_done = 0
    patience = 50
    
    global_best_value = float('-inf')
    global_best_params = {}
    
    study_idx = 1
    
    print(f"\n[!] Iniciando búsqueda en 10 dimensiones con {max_trials} combinaciones totales y saltos de horizonte...")
    
    while trials_done < max_trials:
        print(f"\n--- INICIANDO HORIZONTE {study_idx} (Trials restantes: {max_trials - trials_done}) ---")
        
        study = optuna.create_study(direction="maximize")
        early_stop = EarlyStoppingCallback(early_stopping_rounds=patience)
        
        trials_left = max_trials - trials_done
        study.optimize(objective, n_trials=trials_left, n_jobs=-1, callbacks=[early_stop])
        
        trials_done += len(study.trials)
        
        if study.best_value > global_best_value:
            global_best_value = study.best_value
            global_best_params = study.best_params
            
        print(f"--- FIN HORIZONTE {study_idx} (Mejor local: {study.best_value:.4f}) ---")
        study_idx += 1
    
    print("\n==================================================")
    print("  [WIN] MEJOR CONFIGURACIÓN GLOBAL ENCONTRADA")
    print("==================================================")
    print(f"F1-Score Máximo: {global_best_value:.4f} (1.0 es perfecto)")
    print("Parámetros Infalibles:")
    for key, value in global_best_params.items():
        print(f"    {key}: {value}")
