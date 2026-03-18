"""
test_azure_tts.py — Test Azure Neural TTS
Lance : python test_azure_tts.py
"""

import requests
import os

# ── Config ────────────────────────────────────────────────────────────────────
AZURE_KEY    = "9pwndajwQuCxb0RW5aDD6IhqnQ3sqMcRDhDAob07b8QA7fRlsbEAJQQJ99CCACF24PCXJ3w3AAAYACOG0nQ2"
AZURE_REGION = "uaenorth"
AZURE_URL    = f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"

# ── Texte à tester ────────────────────────────────────────────────────────────
TEXT = "Bonjour, je suis JARVIS, ton assistant personnel. Je contrôle ton PC depuis ton téléphone."
#TEXT = "Hello, I am JARVIS, your personal assistant. I control your PC from your phone. Tell me what you want to do."


# ── SSML ──────────────────────────────────────────────────────────────────────
SSML = f"""
<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis"
       xmlns:mstts="https://www.w3.org/2001/mstts"
       xml:lang="fr-FR">
  <voice name="fr-FR-VivienneMultilingualNeural">
    <mstts:express-as style="friendly">
      {TEXT}
    </mstts:express-as>
  </voice>
</speak>
""".strip()

# ── Appel API ─────────────────────────────────────────────────────────────────
print("Appel Azure Neural TTS...")

response = requests.post(
    AZURE_URL,
    headers={
        "Ocp-Apim-Subscription-Key": AZURE_KEY,
        "Content-Type":              "application/ssml+xml",
        "X-Microsoft-OutputFormat":  "audio-16khz-128kbitrate-mono-mp3",
        "User-Agent":                "JarvisTest",
    },
    data=SSML.encode("utf-8"),
    timeout=10,
)

print(f"Status HTTP : {response.status_code}")

if response.status_code != 200:
    print(f"Erreur : {response.text}")
else:
    # Sauvegarder le fichier audio
    output_file = "jarvis_test.mp3"
    with open(output_file, "wb") as f:
        f.write(response.content)
    print(f"Audio sauvegardé : {output_file} ({len(response.content)/1024:.1f} KB)")

    # Lire automatiquement le fichier
    print("Lecture...")
    os.startfile(output_file)   # Windows — ouvre avec le lecteur par défaut
    print("Terminé !")