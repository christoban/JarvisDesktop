# 🤖 JarvisDesktop

**JarvisDesktop** is an intelligent Windows desktop assistant powered by AI (Groq) and Azure. It lets you control your PC with natural language commands — opening apps, managing files, controlling audio/video, browsing the web, and much more.

---

## ✨ Features

- 🗣️ **Natural Language Commands** — Talk to Jarvis in plain language; it understands your intent and acts.
- 🧠 **Conversational Memory** — Jarvis remembers the last few exchanges so it can resolve ambiguous follow-up commands.
- 🖥️ **System Control** — Manage windows, power state, keyboard & mouse, and screen settings.
- 📁 **File Management** — Navigate, open, move, copy, and delete files and folders.
- 🌐 **Browser Control** — Open URLs, search the web, and interact with browser tabs.
- 🔊 **Audio/TTS** — Text-to-speech responses and audio device management.
- 🔒 **Security** — Built-in authentication, encryption, and permission management.
- ☁️ **Azure Integration** — WebSocket communication with Azure Functions for cloud-based command routing.
- 📋 **Macros** — Record and replay sequences of commands.

---

## 📂 Project Structure

```
JarvisDesktop/
├── main.py                  # Entry point — launches the interactive agent
├── jarvis_bridge.py         # Bridge layer between components
├── core/                    # Agent brain: parsing, intent execution, memory, macros
├── modules/                 # PC control modules (files, apps, audio, browser, etc.)
├── voice/                   # Text-to-speech engine
├── communication/           # WebSocket client & Azure notification sender
├── azure_function/          # Azure Functions (command routing, health, polling)
├── config/                  # Settings, logger, and .env configuration
├── security/                # Auth, crypto, and permissions
├── data/                    # Runtime data (history, screenshots)
└── tests/                   # Test suite
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- Windows OS (some modules use Windows-specific APIs)
- A [Groq](https://console.groq.com/) API key
- (Optional) Azure subscription for cloud features

### Installation

```bash
git clone https://github.com/christoban/JarvisDesktop.git
cd JarvisDesktop
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
```

### Configuration

Copy the example environment file and fill in your credentials:

```bash
copy config\.env.example config\.env   # Windows
```

Edit `config/.env`:

```env
GROQ_API_KEY=your_groq_api_key_here
AZURE_FUNCTION_URL=https://your-function-app.azurewebsites.net
# Add other keys as needed
```

> ⚠️ The terminal mode works without Azure keys, but cloud features require them.

### Run

```bash
python main.py
```

You will see the Jarvis banner and can start typing commands immediately.

---

## ☁️ Azure Functions

The `azure_function/` folder contains the serverless backend. To deploy:

```bash
cd azure_function
pip install -r requirements.txt
func start          # local testing
func azure functionapp publish <your-app-name>   # deploy to Azure
```

---

## 🧪 Testing

```bash
python -m pytest tests/
```

---

## 🛡️ Security

- API keys and secrets are stored in `config/.env` (never committed — see `.gitignore`).
- The `security/` module handles authentication, AES encryption, and action permissions.

---

## 🗺️ Roadmap

- [x] Interactive terminal mode
- [x] Conversational memory
- [x] PC control modules (files, apps, audio, browser, system)
- [x] Azure Function backend
- [ ] WebSocket real-time communication (Week 6)
- [ ] Voice input (speech-to-text)
- [ ] Mobile companion app

---

## 📄 License

This project is for personal and educational use. See [LICENSE](LICENSE) for details.
