#!/usr/bin/env python3
"""Vérifier les dépendances OCR/Vision."""

print("Vérification des dépendances OCR/Vision...\n")

packages = {
    'pytesseract': 'OCR TextRecognition', 
    'PIL': 'Image handling (Pillow)',
    'pyautogui': 'Mouse/Keyboard control',
    'cv2': 'Computer Vision (OpenCV)',
    'numpy': 'Numerical computing',
    'requests': 'HTTP requests',
}

installed = []
missing = []

for pkg, desc in packages.items():
    try:
        __import__(pkg)
        installed.append(f"✅ {pkg:15} — {desc}")
    except ImportError:
        missing.append(f"❌ {pkg:15} — {desc}")

print("PRÉSENTS:")
for line in installed:
    print(f"  {line}")

if missing:
    print("\nMANQUANTS:")
    for line in missing:
        print(f"  {line}")
    print("\nCommande d'installation:")
    print("  pip install pytesseract pillow pyautogui opencv-python numpy")
else:
    print("\n✅ Toutes les dépendances Python sont présentes.")

# Vérifier Tesseract exécutable
print("\n" + "="*60)
print("Vérification de Tesseract OCR (dépendance système)...\n")

import shutil
import platform

system = platform.system()
tesseract_path = None

if system == "Windows":
    # Chercher Tesseract sur Windows
    common_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for path in common_paths:
        if __import__('os').path.exists(path):
            tesseract_path = path
            break
    
    if not tesseract_path:
        tesseract_path = shutil.which('tesseract')
elif system in ["Linux", "Darwin"]:
    tesseract_path = shutil.which('tesseract')

if tesseract_path:
    print(f"✅ Tesseract trouvé: {tesseract_path}")
else:
    print("❌ Tesseract OCR non installé")
    if system == "Windows":
        print("   → Installer depuis: https://github.com/UB-Mannheim/tesseract/wiki")
        print("   → Windows: télécharger tesseract-ocr-w64-setup-*.exe")
    elif system == "Linux":
        print("   → Debian/Ubuntu: sudo apt-get install tesseract-ocr")
    elif system == "Darwin":
        print("   → macOS: brew install tesseract")

print("\n" + "="*60)
print("Résumé:\n")

if not missing and tesseract_path:
    print("✅ Toutes les dépendances sont installées - Vision/OCR prêt à l'emploi")
else:
    print("⚠️  Dépendances manquantes - Voir ci-dessus pour installer")
