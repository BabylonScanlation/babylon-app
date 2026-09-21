#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║    BABYLON CLEANER — OPTIMIZADOR MASIVO PARA KAGGLE             ║
║    Motor: OpenCV + Optuna TPE + Nuevos Horizontes               ║
║    Objetivo: Encontrar los parámetros INFALIBLES para limpiar   ║
║              globos de texto de manga automáticamente.           ║
╚══════════════════════════════════════════════════════════════════╝

INSTRUCCIONES KAGGLE:
  1. En Kaggle, ve a la barra lateral derecha y haz clic en "Add Data".
  2. Sube la carpeta Manga109s como un dataset de Kaggle.
     (Sube un ZIP con las carpetas 'annotations/' e 'images/')
  3. Actualiza la variable MANGA109_DIR con la ruta que Kaggle te dé.
  4. Ejecutar todas las celdas.
"""

# ══════════════════════════════════════════════════════════════════
# CELDA 1 — INSTALACIÓN (ejecutar sola primero en Kaggle)
# ══════════════════════════════════════════════════════════════════
# !pip install optuna opencv-python-headless -q

# ══════════════════════════════════════════════════════════════════
# CELDA 2 — IMPORTS Y CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════

import os
import glob
import random
import time
import threading
import warnings
import cv2
import numpy as np
import xml.etree.ElementTree as ET
import optuna

warnings.filterwarnings("ignore")

# ⚠️ MUY IMPORTANTE: Cambia esta ruta por la que te dé Kaggle al subir tu dataset
MANGA109_DIR = "/kaggle/input/manga109s/Manga109s_released_2026_05_21"
ANNOTATIONS_DIR = os.path.join(MANGA109_DIR, "annotations")
IMAGES_DIR = os.path.join(MANGA109_DIR, "images")

# ── CONFIGURACIÓN GLOBAL ────────────────────────────────────────
CONFIG = {
    "num_books": 10,        # Cuántos libros usar como muestra (de 87 disponibles)
    "pages_per_book": 8,    # Páginas por libro (más = más robusto pero más lento)
    "max_trials": 10000,    # Total de combinaciones a explorar
    "patience": 50,         # Trials sin mejorar antes de saltar de horizonte
    "n_jobs": -1,           # Usar todos los núcleos CPU disponibles
}

# ══════════════════════════════════════════════════════════════════
# CELDA 3 — BABYLON CLEANER (AUTOCONTENIDO)
# ══════════════════════════════════════════════════════════════════

class BabylonCleaner:
    """
    Detecta el interior de los globos de texto como regiones blancas
    TOPOLÓGICAMENTE ENCERRADAS (no conectadas al fondo de la página),
    y las rellena completas (incluyendo el texto) usando el contorno
    externo de cada región.
    """

    def __init__(self,
                 umbral_blanco=200,
                 cierre_tinta_px=9,
                 min_area=1500,
                 max_area_factor=0.5,
                 min_extent=0.45,
                 min_solidity=0.75,
                 max_aspect_ratio=3.0,
                 min_text_density=0.015,
                 umbral_tinta=120,
                 borde_padding=6):
        self.umbral_blanco = umbral_blanco
        self.cierre_tinta_px = cierre_tinta_px
        self.min_area = min_area
        self.max_area_factor = max_area_factor
        self.min_extent = min_extent
        self.min_solidity = min_solidity
        self.max_aspect_ratio = max_aspect_ratio
        self.min_text_density = min_text_density
        self.umbral_tinta = umbral_tinta
        self.borde_padding = borde_padding

    def _binarizar(self, gray):
        _, binaria = cv2.threshold(gray, self.umbral_blanco, 255, cv2.THRESH_BINARY)
        return binaria

    def _sellar_contornos(self, binaria):
        tinta = cv2.bitwise_not(binaria)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                       (self.cierre_tinta_px, self.cierre_tinta_px))
        tinta_cerrada = cv2.morphologyEx(tinta, cv2.MORPH_CLOSE, k)
        return cv2.bitwise_not(tinta_cerrada)

    def _blancos_encerrados(self, binaria):
        h, w = binaria.shape
        pad = self.borde_padding
        con_marco = cv2.copyMakeBorder(binaria, pad, pad, pad, pad,
                                        cv2.BORDER_CONSTANT, value=255)

        flood = con_marco.copy()
        mask_ff = np.zeros((con_marco.shape[0] + 2, con_marco.shape[1] + 2), np.uint8)
        cv2.floodFill(flood, mask_ff, (0, 0), 128)

        encerrado = np.where(flood == 255, 255, 0).astype(np.uint8)
        encerrado = encerrado[pad:pad + h, pad:pad + w]
        return encerrado

    def _contornos_validos(self, encerrado, gray, h_doc, w_doc):
        contornos, _ = cv2.findContours(encerrado, cv2.RETR_EXTERNAL,
                                         cv2.CHAIN_APPROX_SIMPLE)
        area_doc = h_doc * w_doc
        validos = []
        mask_oscuros = cv2.inRange(gray, 0, self.umbral_tinta)

        for cnt in contornos:
            area = cv2.contourArea(cnt)
            if area < self.min_area or area > area_doc * self.max_area_factor:
                continue

            x, y, w, h = cv2.boundingRect(cnt)
            area_bbox = w * h
            extent = area / area_bbox if area_bbox > 0 else 0
            if extent < self.min_extent:
                continue

            aspect = max(w, h) / max(1, min(w, h))
            if aspect > self.max_aspect_ratio:
                continue

            hull = cv2.convexHull(cnt)
            area_hull = cv2.contourArea(hull)
            solidity = area / area_hull if area_hull > 0 else 0
            if solidity < self.min_solidity:
                continue

            mask_cnt = np.zeros_like(encerrado)
            cv2.drawContours(mask_cnt, [cnt], -1, 255, -1)
            oscuros_in_c = cv2.bitwise_and(mask_oscuros, mask_cnt)
            text_density = cv2.countNonZero(oscuros_in_c) / area
            if text_density < self.min_text_density:
                continue

            validos.append(cnt)

        return validos

    def limpiar_imagen_blancos(self, img):
        t0 = time.time()
        h_doc, w_doc = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        binaria = self._binarizar(gray)
        binaria_sellada = self._sellar_contornos(binaria)
        encerrado = self._blancos_encerrados(binaria_sellada)
        contornos = self._contornos_validos(encerrado, gray, h_doc, w_doc)

        mask_final = np.zeros((h_doc, w_doc), np.uint8)
        for cnt in contornos:
            cv2.drawContours(mask_final, [cnt], -1, 255, thickness=-1)

        result = img.copy()
        result[mask_final == 255] = (255, 255, 255)

        return result, mask_final, len(contornos), time.time() - t0


# ══════════════════════════════════════════════════════════════════
# CELDA 4 — CARGA DE DATASET MANGA109s
# ══════════════════════════════════════════════════════════════════

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

def prepare_dataset():
    """Carga una muestra de imágenes y sus anotaciones en memoria RAM."""
    global CACHE_DATA
    CACHE_DATA = []

    num_books = CONFIG["num_books"]
    pages_per_book = CONFIG["pages_per_book"]

    xml_files = glob.glob(os.path.join(ANNOTATIONS_DIR, "*.xml"))
    if not xml_files:
        raise ValueError(f"🚨 No se encontraron XMLs en {ANNOTATIONS_DIR}. Verifica la ruta del dataset.")

    selected_xmls = random.sample(xml_files, min(num_books, len(xml_files)))

    print(f"⚙️  Cargando dataset en memoria ({len(selected_xmls)} libros, {pages_per_book} páginas c/u)...")

    for xml_path in selected_xmls:
        book_name = os.path.basename(xml_path).replace('.xml', '')
        pages_data = parse_manga109_xml(xml_path)

        valid_pages = [idx for idx, bboxes in pages_data.items() if len(bboxes) > 0]
        selected_pages = random.sample(valid_pages, min(pages_per_book, len(valid_pages)))

        for idx in selected_pages:
            img_filename = f"{idx:03d}.jpg"
            img_path = os.path.join(IMAGES_DIR, book_name, img_filename)

            if os.path.exists(img_path):
                img = cv2.imread(img_path)
                if img is not None:
                    h, w = img.shape[:2]

                    # Máscara Ground Truth de los textos
                    gt_mask = np.zeros((h, w), dtype=np.uint8)
                    for (xmin, ymin, xmax, ymax) in pages_data[idx]:
                        cv2.rectangle(gt_mask, (xmin, ymin), (xmax, ymax), 255, -1)

                    # Máscara dilatada (margen del globo tolerado)
                    kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (120, 120))
                    gt_dilated = cv2.dilate(gt_mask, kernel_dilate)

                    CACHE_DATA.append({
                        'img': img,
                        'gt_mask': gt_mask,
                        'gt_dilated': gt_dilated,
                        'name': f"{book_name}_{img_filename}"
                    })

    print(f"✅ Dataset listo: {len(CACHE_DATA)} imágenes cacheadas en RAM.")


# ══════════════════════════════════════════════════════════════════
# CELDA 5 — FUNCIÓN OBJETIVO OPTUNA (10 DIMENSIONES)
# ══════════════════════════════════════════════════════════════════

def objective(trial):
    """Función objetivo para Optuna — 10 dimensiones de búsqueda."""
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

        try:
            _, pred_mask, _, _ = cleaner.limpiar_imagen_blancos(img)
        except Exception:
            return 0.0

        # Recall: ¿Cubrimos todo el texto del GT?
        tp_text = cv2.bitwise_and(pred_mask, gt_mask)
        suma_gt = np.sum(gt_mask == 255)
        recall = np.sum(tp_text == 255) / suma_gt if suma_gt > 0 else 0.0

        # Precision: ¿No borramos partes del dibujo?
        tp_bubble = cv2.bitwise_and(pred_mask, gt_dilated)
        suma_pred = np.sum(pred_mask == 255)
        precision = np.sum(tp_bubble == 255) / suma_pred if suma_pred > 0 else 1.0

        # F1-Score
        if recall + precision == 0:
            f1 = 0.0
        else:
            f1 = 2 * (recall * precision) / (recall + precision)

        total_f1 += f1

    avg_f1 = total_f1 / len(CACHE_DATA) if CACHE_DATA else 0.0

    # Guardar métricas extra para análisis
    trial.set_user_attr("avg_f1", round(avg_f1, 4))

    return avg_f1


# ══════════════════════════════════════════════════════════════════
# CELDA 6 — EARLY STOPPING + NUEVOS HORIZONTES
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# CELDA 7 — MAIN (PUNTO DE ENTRADA)
# ══════════════════════════════════════════════════════════════════

def main():
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  BABYLON CLEANER — OPTIMIZADOR MASIVO KAGGLE               ║")
    print("║  10 Dimensiones • 10,000 Combinaciones • Nuevos Horizontes ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    # ── 1. Cargar dataset ────────────────────────────────────────
    try:
        prepare_dataset()
    except Exception as e:
        print(f"🚨 Error cargando dataset: {e}")
        # Guardar error para debug en Kaggle
        with open("/kaggle/working/error_init.txt", "w") as f:
            import traceback
            f.write(traceback.format_exc())
        return

    optuna.logging.set_verbosity(optuna.logging.INFO)

    max_trials = CONFIG["max_trials"]
    patience = CONFIG["patience"]
    n_jobs = CONFIG["n_jobs"]

    trials_done = 0
    global_best_value = float('-inf')
    global_best_params = {}
    study_idx = 1

    print(f"\n🚀 Iniciando búsqueda en 10 dimensiones con {max_trials:,} combinaciones y saltos de horizonte...\n")

    # ── 2. Bucle de Nuevos Horizontes ─────────────────────────────
    while trials_done < max_trials:
        print(f"\n--- INICIANDO HORIZONTE {study_idx} (Trials restantes: {max_trials - trials_done:,}) ---")

        study = optuna.create_study(direction="maximize")
        early_stop = EarlyStoppingCallback(early_stopping_rounds=patience)

        trials_left = max_trials - trials_done
        study.optimize(objective, n_trials=trials_left, n_jobs=n_jobs, callbacks=[early_stop])

        trials_done += len(study.trials)

        if study.best_value > global_best_value:
            global_best_value = study.best_value
            global_best_params = study.best_params

        print(f"--- FIN HORIZONTE {study_idx} (Mejor local: {study.best_value:.4f} | Global: {global_best_value:.4f}) ---")
        study_idx += 1

        # Guardar el mejor global después de cada horizonte (para no perder info si Kaggle corta)
        with open("/kaggle/working/best_params.txt", "w") as f:
            f.write(f"F1-Score GLOBAL: {global_best_value:.6f}\n")
            f.write(f"Horizontes explorados: {study_idx - 1}\n")
            f.write(f"Trials totales: {trials_done}\n\n")
            f.write("Parámetros Infalibles:\n")
            for key, value in global_best_params.items():
                f.write(f"    {key}: {value}\n")

    # ── 3. Reporte final ──────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  🏆 MEJOR CONFIGURACIÓN GLOBAL ENCONTRADA")
    print("═" * 60)
    print(f"  F1-Score Máximo: {global_best_value:.4f} (1.0 es perfecto)")
    print(f"  Horizontes explorados: {study_idx - 1}")
    print(f"  Trials totales: {trials_done}")
    print("")
    print("  Parámetros Infalibles:")
    for key, value in global_best_params.items():
        print(f"    {key}: {value}")
    print("═" * 60)


if __name__ == "__main__":
    main()
