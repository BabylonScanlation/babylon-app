import cv2
import numpy as np
import time

# ---------------------------------------------------------------
# Parámetros configurables
# ---------------------------------------------------------------
UMBRAL_BLANCO      = 200   # gris >= esto se considera "papel/blanco"
CIERRE_TINTA_PX    = 5     # tamaño del kernel para sellar microfugas en el contorno del globo
BORDE_PADDING      = 6     # marco blanco agregado para garantizar que el fondo sea UNA sola región
MIN_AREA           = 1500  # área mínima de un globo (px^2) - ajustar según resolución
MAX_AREA_FACTOR    = 0.5   # un globo no puede ocupar más de esto de la página
MIN_EXTENT         = 0.45  # area_contorno / area_bbox (globos redondos ~0.6-0.85)
MIN_SOLIDITY       = 0.75  # area_contorno / area_convex_hull
MAX_ASPECT_RATIO   = 3.0   # descarta tiras muy alargadas (brillos de pelo, líneas de cara)
MIN_TEXT_DENSITY   = 0.012 # (NUEVO) proporción mínima de "tinta" dentro de la región blanca
RELLENO_COLOR      = (255, 255, 255)  # color final de relleno (BGR)

class BabylonCleaner:
    """
    Detecta el interior de los globos de texto como regiones blancas
    TOPOLÓGICAMENTE ENCERRADAS (no conectadas al fondo de la página),
    y las rellena completas (incluyendo el texto) usando el contorno
    externo de cada región.
    """

    def __init__(self,
                 umbral_blanco=UMBRAL_BLANCO,
                 cierre_tinta_px=CIERRE_TINTA_PX,
                 min_area=MIN_AREA,
                 max_area_factor=MAX_AREA_FACTOR,
                 min_extent=MIN_EXTENT,
                 min_solidity=MIN_SOLIDITY,
                 max_aspect_ratio=MAX_ASPECT_RATIO,
                 min_text_density=MIN_TEXT_DENSITY):
        self.umbral_blanco = umbral_blanco
        self.cierre_tinta_px = cierre_tinta_px
        self.min_area = min_area
        self.max_area_factor = max_area_factor
        self.min_extent = min_extent
        self.min_solidity = min_solidity
        self.max_aspect_ratio = max_aspect_ratio
        self.min_text_density = min_text_density

    # -----------------------------------------------------------
    # Paso 1: binarizar (blanco=255 / tinta-o-lo-que-sea=0)
    # -----------------------------------------------------------
    def _binarizar(self, gray):
        _, binaria = cv2.threshold(gray, self.umbral_blanco, 255, cv2.THRESH_BINARY)
        return binaria

    # -----------------------------------------------------------
    # Paso 2: sellar microfugas en los contornos de tinta.
    # -----------------------------------------------------------
    def _sellar_contornos(self, binaria):
        # Trabajamos sobre la tinta (invertido), la cerramos, y volvemos.
        tinta = cv2.bitwise_not(binaria)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                       (self.cierre_tinta_px, self.cierre_tinta_px))
        tinta_cerrada = cv2.morphologyEx(tinta, cv2.MORPH_CLOSE, k)
        return cv2.bitwise_not(tinta_cerrada)

    # -----------------------------------------------------------
    # Paso 3: flood-fill desde el borde -> separar "blanco de fondo"
    # de "blanco encerrado" (candidatos a interior de globo)
    # -----------------------------------------------------------
    def _blancos_encerrados(self, binaria):
        h, w = binaria.shape

        # Agregamos un marco blanco para garantizar que TODO el
        # fondo de la página quede como una sola región conectada,
        # y así nos alcanza con un solo seed en (0,0).
        pad = BORDE_PADDING
        con_marco = cv2.copyMakeBorder(binaria, pad, pad, pad, pad,
                                        cv2.BORDER_CONSTANT, value=255)

        flood = con_marco.copy()
        mask_ff = np.zeros((con_marco.shape[0] + 2, con_marco.shape[1] + 2), np.uint8)
        cv2.floodFill(flood, mask_ff, (0, 0), 128)

        # "Encerrado" = sigue siendo 255 (nunca lo tocó el flood fill)
        encerrado = np.where(flood == 255, 255, 0).astype(np.uint8)

        # Quitamos el marco que agregamos
        encerrado = encerrado[pad:pad + h, pad:pad + w]
        return encerrado

    # -----------------------------------------------------------
    # Paso 4: filtrar por forma y DENSIDAD DE TEXTO
    # -----------------------------------------------------------
    def _contornos_validos(self, encerrado, gray, h_doc, w_doc):
        contornos, _ = cv2.findContours(encerrado, cv2.RETR_EXTERNAL,
                                         cv2.CHAIN_APPROX_SIMPLE)
        area_doc = h_doc * w_doc
        validos = []
        
        # Generar máscara de píxeles oscuros (tinta/texto) para comprobar densidad
        mask_oscuros = cv2.inRange(gray, 0, 180)

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
            
            # (Opcional) Las nubes pueden tener solidity más baja.
            if solidity < self.min_solidity:
                continue
                
            # -- FILTRO EXTRA DE DENSIDAD DE TEXTO (Añadido) --
            # "Un brillo de piel es blanco puro sin nada adentro, un globo real tiene texto"
            # Extraemos la silueta rellena del contorno
            mask_cnt = np.zeros_like(encerrado)
            cv2.drawContours(mask_cnt, [cnt], -1, 255, -1)
            
            # Hacemos la intersección de la tinta con esta silueta
            oscuros_in_c = cv2.bitwise_and(mask_oscuros, mask_cnt)
            text_density = cv2.countNonZero(oscuros_in_c) / area
            
            # Si el área blanca tiene menos de 1.2% de tinta adentro, lo descartamos
            # (salva destellos, fondos y brillos que pasaron el filtro topológico)
            if text_density < self.min_text_density:
                continue

            validos.append(cnt)

        return validos

    # -----------------------------------------------------------
    # Orquestación completa
    # -----------------------------------------------------------
    def limpiar_imagen_blancos(self, img):
        t0 = time.time()
        h_doc, w_doc = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        binaria = self._binarizar(gray)
        binaria_sellada = self._sellar_contornos(binaria)
        encerrado = self._blancos_encerrados(binaria_sellada)
        contornos = self._contornos_validos(encerrado, gray, h_doc, w_doc)

        # Relleno: dibujamos el contorno EXTERNO macizo (thickness=-1)
        # esto ignora los "agujeros" de texto y tapa todo de una vez.
        mask_final = np.zeros((h_doc, w_doc), np.uint8)
        for cnt in contornos:
            cv2.drawContours(mask_final, [cnt], -1, 255, thickness=-1)

        result = img.copy()
        result[mask_final == 255] = RELLENO_COLOR

        return result, len(contornos), time.time() - t0
