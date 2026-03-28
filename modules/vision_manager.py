"""
modules/vision_manager.py — Vision et OCR (Tony Stark Niveau 4)
===============================================================

Semaine 13 — OCR et vision de l'écran

Fonctionnalités :
  - OCR Tesseract pour lecture de texte à l'écran
  - Détection d'éléments cliquables par reconnaissance de texte
  - Localisation de boutons, zones de texte, liens
  - Commande "clique sur le bouton OK" → Jarvis trouve et clique
  - Lecture vocale du contenu écran

Dépendances :
    pip install pytesseract pillow pyautogui opencv-python
    (Tesseract OCR doit être installé sur le système)

Usage :
    vm = VisionManager()
    vm.read_screen_text()           # Lit tout le texte
    vm.find_element("OK")           # Trouve les coordonnées de "OK"
    vm.click_element("Submit")      # Clique sur le texte trouvé
"""

from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, List, Optional

from config.logger import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  IMPORTS AVEC FALLBACK
# ═══════════════════════════════════════════════════════════════════════════════

try:
    import pytesseract
    from PIL import Image, ImageGrab
    PYTESSERACT_AVAILABLE = True
except ImportError as e:
    PYTESSERACT_AVAILABLE = False
    logger.warning(f"pytesseract/pillow non installé: {e}")


try:
    import numpy as np
    import cv2
    CV2_AVAILABLE = True
except ImportError as e:
    CV2_AVAILABLE = False
    logger.debug(f"opencv-python non installé: {e}")

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError as e:
    PYAUTOGUI_AVAILABLE = False
    logger.warning(f"pyautogui non installé: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ScreenElement:
    """Élément détecté à l'écran."""
    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float
    element_type: str  # "button", "text", "link", "input"
    
    @property
    def center_x(self) -> int:
        return self.x + self.width // 2
    
    @property
    def center_y(self) -> int:
        return self.y + self.height // 2
    
    @property
    def bounding_box(self) -> tuple:
        return (self.x, self.y, self.x + self.width, self.y + self.height)
    
    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "confidence": self.confidence,
            "element_type": self.element_type,
        }


@dataclass
class OCRResult:
    """Résultat OCR complet."""
    full_text: str
    elements: List[ScreenElement]
    width: int
    height: int
    processing_time_ms: int


# ═══════════════════════════════════════════════════════════════════════════════
#  VISION MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class VisionManager:
    """
    Gestionnaire de vision pour Jarvis.
    Combine OCR, détection d'éléments et actions souris.
    """
    
    # Boutons courants pour classification
    COMMON_BUTTONS = {
        "ok", "cancel", "submit", "save", "delete", "close", "yes", "no",
        "apply", "confirm", "accept", "refuse", "next", "previous", "back",
        "send", "reply", "forward", "login", "sign in", "register", "sign up",
        "download", "upload", "play", "pause", "stop", "start", "connect",
        "disconnect", "refresh", "update", "install", "uninstall", "exit",
    }
    
    # Mots-clés pour classification d'éléments
    LINK_KEYWORDS = {"http", "www", ".com", ".fr", ".org", "lien", "link", "url"}
    INPUT_KEYWORDS = {"search", "email", "password", "username", "input", "field"}
    
    def __init__(self, tesseract_path: str = None):
        """
        Args:
            tesseract_path: Chemin vers l'exécutable tesseract (optionnel)
        """
        self._tesseract_path = tesseract_path
        self._screen_size = self._get_screen_size()
        
        # Configurer tesseract si chemin fourni
        if tesseract_path and PYTESSERACT_AVAILABLE:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        
        self._check_dependencies()
        
    def _check_dependencies(self):
        """Vérifie les dépendances disponibles."""
        self._available = (
            PYTESSERACT_AVAILABLE and 
            PYAUTOGUI_AVAILABLE and 
            (CV2_AVAILABLE or PYTESSERACT_AVAILABLE)
        )
        
        if not self._available:
            missing = []
            if not PYTESSERACT_AVAILABLE:
                missing.append("pytesseract")
            if not PYAUTOGUI_AVAILABLE:
                missing.append("pyautogui")
            if not PYTESSERACT_AVAILABLE:
                missing.append("pillow")
            logger.warning(f"VisionManager: dépendances manquantes: {missing}")
    
    def _get_screen_size(self) -> tuple:
        """Retourne la taille de l'écran principal."""
        if PYAUTOGUI_AVAILABLE:
            try:
                return pyautogui.size()
            except Exception:
                pass
        return (1920, 1080)  # Fallback
    
    def _capture_screen_image(self) -> Image.Image | None:
        """Capture l'écran et retourne une image PIL."""
        if not PYTESSERACT_AVAILABLE:
            return None
            
        try:
            if CV2_AVAILABLE:
                # Utiliser opencv pour capture plus rapide
                import mss
                with mss.mss() as sct:
                    monitor = sct.monitors[1]
                    screenshot = sct.grab(monitor)
                    return Image.frombytes(
                        "RGB", 
                        screenshot.size, 
                        screenshot.bgra, 
                        "raw", 
                        "BGRX"
                    )
            else:
                # Fallback PIL
                return ImageGrab.grab()
        except Exception as e:
            logger.error(f"Erreur capture écran: {e}")
            return None
    
    # ═══════════════════════════════════════════════════════════════════════════
    #  OCR - LECTURE TEXTE
    # ═══════════════════════════════════════════════════════════════════════════
    
    def read_screen_text(self, language: str = "fra+eng") -> OCRResult:
        """
        Lit tout le texte visible à l'écran via OCR.
        
        Args:
            language: Code langue pour Tesseract (défaut: français + anglais)
            
        Returns:
            OCRResult avec full_text et liste d'éléments détectés
        """
        if not PYTESSERACT_AVAILABLE:
            return OCRResult(
                full_text="OCR non disponible. Installe pytesseract.",
                elements=[],
                width=0,
                height=0,
                processing_time_ms=0
            )
        
        start_time = time.time()
        
        # Capture écran
        img = self._capture_screen_image()
        if img is None:
            return OCRResult(
                full_text="Échec de capture écran.",
                elements=[],
                width=0,
                height=0,
                processing_time_ms=0
            )
        
        width, height = img.size
        
        try:
            # OCR avec extraction détaillée
            data = pytesseract.image_to_data(
                img, 
                output_type=pytesseract.Output.DICT,
                lang=language,
                config='--psm 6'  # PSM 6 = Block de texte uniforme
            )
            
            # Extraire le texte complet
            full_text = pytesseract.image_to_string(img, lang=language)
            
            # Parser les éléments détectés
            elements = self._parse_ocr_data(data)
            
            processing_ms = int((time.time() - start_time) * 1000)
            
            logger.info(f"OCR: {len(elements)} éléments, {processing_ms}ms")
            
            return OCRResult(
                full_text=full_text.strip(),
                elements=elements,
                width=width,
                height=height,
                processing_time_ms=processing_ms
            )
            
        except Exception as e:
            logger.error(f"Erreur OCR: {e}")
            return OCRResult(
                full_text=f"Erreur OCR: {e}",
                elements=[],
                width=width,
                height=height,
                processing_time_ms=0
            )
    
    def _parse_ocr_data(self, data: dict) -> List[ScreenElement]:
        """Parse les données OCR en éléments structurés."""
        elements = []
        
        n_boxes = len(data["text"])
        
        for i in range(n_boxes):
            text = data["text"][i].strip()
            if not text:
                continue
            
            # Filtrer les éléments trop petits ou peu confiants
            conf = float(data["conf"][i])
            if conf < 30:
                continue
            
            x = data["left"][i]
            y = data["top"][i]
            w = data["width"][i]
            h = data["height"][i]
            
            # Classifier le type d'élément
            elem_type = self._classify_element(text, w, h)
            
            elements.append(ScreenElement(
                text=text,
                x=x,
                y=y,
                width=w,
                height=h,
                confidence=conf,
                element_type=elem_type
            ))
        
        return elements
    
    def _classify_element(self, text: str, width: int, height: int) -> str:
        """Classifie le type d'élément basé sur le texte et les dimensions."""
        text_lower = text.lower()
        
        # Boutons: texte court, souvent en majuscules
        if text_lower in self.COMMON_BUTTONS:
            return "button"
        
        # Liens: contient URL ou mots-clés
        if any(kw in text_lower for kw in self.LINK_KEYWORDS):
            return "link"
        
        # Inputs: mots-clés de formulaire
        if any(kw in text_lower for kw in self.INPUT_KEYWORDS):
            return "input"
        
        # Boutons larges et courts (probablement bouton)
        if width > 100 and height < 50:
            return "button"
        
        return "text"
    
    # ═══════════════════════════════════════════════════════════════════════════
    #  RECHERCHE D'ÉLÉMENTS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def find_element(
        self, 
        target_text: str,
        element_type: str = None,
        fuzzy: bool = True,
        min_confidence: int = 40
    ) -> List[ScreenElement]:
        """
        Trouve les éléments correspondant au texte cible.
        
        Args:
            target_text: Texte à rechercher
            element_type: Filtrer par type (button, text, link, input)
            fuzzy: Recherche approximative (tolérant aux petites erreurs)
            min_confidence: Seuil de confiance minimum
            
        Returns:
            Liste d'éléments trouvés (peut être vide)
        """
        result = self.read_screen_text()
        target_lower = target_text.lower().strip()
        
        matches = []
        
        for elem in result.elements:
            if elem.confidence < min_confidence:
                continue
            
            # Filtrer par type si demandé
            if element_type and elem.element_type != element_type:
                continue
            
            elem_text_lower = elem.text.lower()
            
            # Recherche exacte ou fuzzy
            if fuzzy:
                # Vérifier si le texte cible est contenu dans l'élément
                if target_lower in elem_text_lower:
                    matches.append(elem)
                # Ou l'inverse (élément contenu dans cible)
                elif elem_text_lower in target_lower:
                    matches.append(elem)
            else:
                if target_lower == elem_text_lower:
                    matches.append(elem)
        
        # Trier par confiance décroissante
        matches.sort(key=lambda e: e.confidence, reverse=True)
        
        logger.info(f"Recherche '{target_text}': {len(matches)} élément(s) trouvé(s)")
        
        return matches
    
    def find_button(self, button_text: str) -> List[ScreenElement]:
        """Trouve les boutons correspondant au texte."""
        return self.find_element(button_text, element_type="button")
    
    def find_text(self, text: str) -> List[ScreenElement]:
        """Trouve le texte任意 (peu importe le type d'élément)."""
        return self.find_element(text)
    
    # ═══════════════════════════════════════════════════════════════════════════
    #  ACTIONS - CLIQUER SUR ÉLÉMENTS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def click_element(
        self, 
        target_text: str,
        offset_x: int = 0,
        offset_y: int = 0,
        click_type: str = "left",
        double: bool = False,
        fuzzy: bool = True
    ) -> dict:
        """
        Clique sur un élément trouvé par son texte.
        
        Args:
            target_text: Texte de l'élément sur lequel cliquer
            offset_x: Décalage X depuis le centre (optionnel)
            offset_y: Décalage Y depuis le centre (optionnel)
            click_type: "left", "right", "middle"
            double: Double clic si True
            fuzzy: Recherche approximative
            
        Returns:
            dict avec success, message, position
        """
        if not PYAUTOGUI_AVAILABLE:
            return {"success": False, "message": "pyautogui non disponible."}
        
        # Trouver l'élément
        elements = self.find_element(target_text, fuzzy=fuzzy)
        
        if not elements:
            return {
                "success": False, 
                "message": f"Élément '{target_text}' non trouvé à l'écran.",
                "found": False
            }
        
        # Prendre le premier résultat (plus confiant)
        elem = elements[0]
        target_x = elem.center_x + offset_x
        target_y = elem.center_y + offset_y
        
        try:
            # Cliquer
            pyautogui.moveTo(target_x, target_y, duration=0.1)
            
            if double:
                pyautogui.doubleClick()
            elif click_type == "right":
                pyautogui.rightClick()
            elif click_type == "middle":
                pyautogui.middleClick()
            else:
                pyautogui.click()
            
            logger.info(f"Cliqué sur '{target_text}' à ({target_x}, {target_y})")
            
            return {
                "success": True,
                "message": f"Cliqué sur '{elem.text}' à ({target_x}, {target_y})",
                "position": {"x": target_x, "y": target_y},
                "element": elem.to_dict()
            }
            
        except Exception as e:
            logger.error(f"Erreur clic: {e}")
            return {"success": False, "message": f"Erreur lors du clic: {e}"}
    
    def click_button(self, button_text: str) -> dict:
        """Clique sur un bouton spécifique (raccourci pour click_element avec type=bouton)."""
        elements = self.find_button(button_text)
        
        if not elements:
            # Essayer recherche globale
            return self.click_element(button_text)
        
        elem = elements[0]
        return self.click_element(elem.text)
    
    def hover_element(self, target_text: str) -> dict:
        """Déplace la souris sur un élément sans cliquer."""
        if not PYAUTOGUI_AVAILABLE:
            return {"success": False, "message": "pyautogui non disponible."}
        
        elements = self.find_element(target_text)
        
        if not elements:
            return {"success": False, "message": f"Élément '{target_text}' non trouvé."}
        
        elem = elements[0]
        pyautogui.moveTo(elem.center_x, elem.center_y, duration=0.2)
        
        return {
            "success": True,
            "message": f"Souris survole '{elem.text}'",
            "position": {"x": elem.center_x, "y": elem.center_y}
        }
    
    # ═══════════════════════════════════════════════════════════════════════════
    #  SCROLL ET NAVIGATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def scroll_to_element(self, target_text: str, direction: str = "down") -> dict:
        """
        Fait défiler jusqu'à un élément spécifique.
        
        Args:
            target_text: Texte à chercher
            direction: "up" ou "down"
        """
        if not PYAUTOGUI_AVAILABLE:
            return {"success": False, "message": "pyautogui non disponible."}
        
        elements = self.find_element(target_text)
        
        if not elements:
            return {"success": False, "message": f"Élément '{target_text}' non trouvé."}
        
        # Scroller vers l'élément
        scroll_amount = -500 if direction == "up" else 500
        pyautogui.scroll(scroll_amount)
        
        return {
            "success": True,
            "message": f"Défilement {direction} pour atteindre '{target_text}'"
        }
    
    # ═══════════════════════════════════════════════════════════════════════════
    #  EXTRACTION DE CONTENU
    # ═══════════════════════════════════════════════════════════════════════════
    
    def extract_links(self) -> List[dict]:
        """Extrait tous les liens/URL de la page actuelle."""
        result = self.read_screen_text()
        
        links = []
        for elem in result.elements:
            if elem.element_type == "link":
                links.append({
                    "text": elem.text,
                    "x": elem.x,
                    "y": elem.y,
                })
        
        return links
    
    def extract_buttons(self) -> List[dict]:
        """Extrait tous les boutons visibles."""
        result = self.read_screen_text()
        
        buttons = []
        for elem in result.elements:
            if elem.element_type == "button":
                buttons.append({
                    "text": elem.text,
                    "x": elem.x,
                    "y": elem.y,
                    "width": elem.width,
                    "height": elem.height,
                })
        
        return buttons
    
    def summarize_screen(self) -> dict:
        """
        Résume le contenu actuel de l'écran.
        Retourne un dict avec statistiques et exemples.
        """
        result = self.read_screen_text()
        
        # Compter par type
        type_counts = {}
        for elem in result.elements:
            t = elem.element_type
            type_counts[t] = type_counts.get(t, 0) + 1
        
        # Premiers éléments de chaque type
        buttons = [e.text for e in result.elements if e.element_type == "button"][:5]
        links = [e.text for e in result.elements if e.element_type == "link"][:5]
        
        return {
            "success": True,
            "message": f"Écran analysé: {len(result.elements)} éléments",
            "data": {
                "total_elements": len(result.elements),
                "element_counts": type_counts,
                "buttons": buttons,
                "links": links,
                "full_text_preview": result.full_text[:500],
                "screen_size": {"width": result.width, "height": result.height},
                "processing_time_ms": result.processing_time_ms,
            }
        }
    
    # ═══════════════════════════════════════════════════════════════════════════
    #  HEALTH CHECK
    # ═══════════════════════════════════════════════════════════════════════════
    
    def health_check(self) -> dict:
        """État du module de vision."""
        return {
            "success": True,
            "message": "VisionManager OK",
            "data": {
                "available": self._available,
                "pytesseract": PYTESSERACT_AVAILABLE,
                "pyautogui": PYAUTOGUI_AVAILABLE,
                "cv2": CV2_AVAILABLE,
                "screen_size": self._screen_size,
            }
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  FACADE API
# ═══════════════════════════════════════════════════════════════════════════════

def create_vision_manager(tesseract_path: str = None) -> VisionManager:
    """Crée une instance du VisionManager."""
    return VisionManager(tesseract_path=tesseract_path)
