# 🤖 JarvisDesktop

**JarvisDesktop** is an AI-powered personal assistant for Windows PCs, inspired by the fictional JARVIS from Iron Man. It understands natural language commands, controls your PC locally, and can be operated remotely via a mobile app over your local network or the cloud.

---

## ✨ Features

### 🧠 AI & Natural Language
- Powered by **Groq LLaMA 3.3 70B** for intent recognition and natural, context-aware responses
- Understands commands in natural language (including French)
- Persistent **conversational memory** across sessions — remembers facts, preferences and history

### 🖥️ System Control
- **Power management**: shutdown, restart, sleep, hibernate, lock screen
- **Process management**: list, kill processes, open Task Manager
- **System monitoring**: CPU, RAM, disk usage, temperature

### 📁 File Management
- Search, open, copy, move, rename and delete files
- Create folders, find duplicates, retrieve file metadata
- Named folder shortcuts (Desktop, Documents, Downloads, Music, Videos)

### 🌐 Browser Automation
- Open/close tabs, navigate URLs, perform Google searches
- Read and summarize page content (via Groq)
- Fill forms, click elements, download files
- Multi-step autonomous browsing tasks

### 🔊 Audio Control
- Volume up/down, set exact percentage, mute/unmute
- Play local music files, search your music library

### 📶 Network Management
- List/connect/disconnect WiFi networks
- Enable/disable Bluetooth, list paired devices
- Network diagnostics, Wake-on-LAN

### 🪟 Window & Display
- Minimize, maximize, close, move, resize windows
- Screen brightness, resolution, screenshot

### 🔁 Macros & Automation
- Pre-built modes: *Work Mode*, *Night Mode*, *Cinema Mode*, *Startup*
- Create and delete custom macro sequences

### 🔐 Security
- HMAC-SHA256 token authentication for remote commands
- Device registry and timestamp-based replay protection

### 📡 Remote Control
- **Local network bridge** (`jarvis_bridge.py`) — HTTP REST API for mobile app communication over WiFi
- **Azure Functions** backend for cloud-based command routing
- **WebSocket** support for real-time bidirectional communication

---

## 🛠️ Technology Stack

| Category | Technologies |
|---|---|
| **Core AI** | Groq LLaMA 3.3 70B, OpenAI GPT |
| **Speech** | Azure Cognitive Services (STT/TTS), OpenAI Whisper |
| **Cloud** | Azure Functions, Azure Storage Blob, Azure Notification Hub |
| **Windows APIs** | pywin32, pycaw, psutil, ctypes |
| **Browser** | Chrome DevTools Protocol (CDP) |
| **Networking** | asyncio WebSocket, Python ThreadingHTTPServer |
| **Language** | Python 3.11+ |
| **Storage** | Local JSON files, Azure Blob Storage |

---

## 📋 Prerequisites

- Windows 10 / 11
- Python 3.11+
- [Groq API key](https://console.groq.com/) (**required**)
- Google Chrome (for browser automation features)
- Optional: Azure account (for cloud/remote features and speech)

---

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/christoban/JarvisDesktop.git
cd JarvisDesktop
```

### 2. Install Python dependencies

```bash
pip install groq openai azure-cognitiveservices-speech azure-storage-blob \
            psutil requests python-dotenv pycaw pywin32
```

### 3. Configure environment variables

Create a file at `config/.env` with the following content:

```env
# ── Required ──────────────────────────────────────────────
GROQ_API_KEY=your_groq_api_key
SECRET_TOKEN=your_secret_token          # Used for HMAC authentication

# ── Optional: OpenAI ──────────────────────────────────────
OPENAI_API_KEY=your_openai_key
OPENAI_TTS_MODEL=tts-1
OPENAI_TTS_VOICE=alloy
OPENAI_WHISPER_MODEL=whisper-1

# ── Optional: Azure Speech ────────────────────────────────
AZURE_SPEECH_KEY=your_azure_speech_key
AZURE_SPEECH_REGION=eastus

# ── Optional: Azure Functions (cloud remote control) ──────
AZURE_FUNCTION_URL=https://your-function.azurewebsites.net
AZURE_FUNCTION_KEY=your_azure_function_key

# ── Optional: Azure Notification Hub ─────────────────────
AZURE_NOTIFICATION_HUB_CONNECTION=your_connection_string
AZURE_NOTIFICATION_HUB_NAME=your_hub_name

# ── Agent settings ────────────────────────────────────────
DEVICE_ID=my-desktop
AGENT_HOST=0.0.0.0
AGENT_PORT=8765
LOG_LEVEL=INFO
```

---

## ▶️ Usage

### Mode 1 — Interactive Terminal Agent

Launch Jarvis directly in your terminal and type commands:

```bash
python main.py
```

Example commands:
```
> open Chrome
> what's my CPU usage?
> search for iron man on Google
> set volume to 50%
> shutdown in 10 minutes
```

### Mode 2 — Local Network Bridge (for mobile app)

Start the HTTP bridge so a mobile app can send commands over your local WiFi:

```bash
python jarvis_bridge.py
```

The server listens on `http://0.0.0.0:7071` by default.

---

## 🏗️ Project Architecture

```
JarvisDesktop/
├── main.py                  # Entry point — interactive terminal agent
├── jarvis_bridge.py         # HTTP/WebSocket bridge for mobile app
│
├── core/                    # Brain & orchestration
│   ├── agent.py             # Main conversation loop & orchestrator
│   ├── command_parser.py    # Intent recognition via Groq LLM
│   ├── intent_executor.py   # Routes intents to the right module
│   ├── jarvis_voice.py      # Natural response generation
│   ├── jarvis_memory.py     # Persistent memory system
│   ├── history_manager.py   # Conversation history tracking
│   └── macros.py            # Macro/automation sequences
│
├── modules/                 # Functional capabilities
│   ├── app_manager.py       # Launch & control applications
│   ├── file_manager.py      # File system operations
│   ├── system_control.py    # Shutdown, processes, monitoring
│   ├── network_manager.py   # WiFi & Bluetooth
│   ├── audio_manager.py     # Volume & music playback
│   ├── window_manager.py    # Window manipulation
│   ├── power_manager.py     # Power state management
│   ├── screen_manager.py    # Display control & screenshots
│   ├── keyboard_mouse.py    # Input simulation
│   ├── doc_reader.py        # Document reading & summarization
│   └── browser/             # Browser automation (9-level system)
│
├── communication/           # Cloud & network integrations
│   ├── websocket_client.py  # Azure WebSocket client
│   ├── notification_sender.py
│   └── mock_azure_server.py # Local mock for testing
│
├── config/                  # Configuration
│   ├── settings.py          # Central config loader
│   └── logger.py            # Logging setup
│
├── security/                # Auth & encryption
│   ├── auth.py              # HMAC token auth & device registry
│   ├── permissions.py
│   └── crypto.py
│
├── azure_function/          # Azure serverless backend
│   ├── command/             # Command processing function
│   ├── result/              # Result storage function
│   ├── health/              # Health check function
│   └── shared/              # Shared utilities
│
├── data/                    # Persistent local data
│   ├── macros.json
│   ├── devices.json
│   └── jarvis_memory.json
│
└── tests/                   # Test suite (Weeks 2–12)
```

---

## 🧪 Testing

Tests are organized by development sprint (week), each covering the features added in that sprint:

| File | Sprint | Key features tested |
|---|---|---|
| `test_semaine2.py` | Week 2 | Core agent, command parsing |
| `test_semaine3.py` | Week 3 | System control, app management |
| `test_semaine4.py` | Week 4 | File management |
| `test_semaine5.py` | Week 5 | Browser automation |
| `test_semaine6.py` | Week 6 | WebSocket, cloud integration |
| `test_semaine7.py` | Week 7 | Audio & network management |
| `test_semaine9.py` | Week 9 | Memory system |
| `test_semaine10.py` | Week 10 | Macros & automation |
| `test_semaine11.py` | Week 11 | Security & authentication |
| `test_semaine12.py` | Week 12 | End-to-end scenarios |

```bash
# Run the full test suite
pytest tests/

# Run tests for a specific sprint / feature area
pytest tests/test_semaine5.py -v
```

---

## 📄 License

This project is currently unlicensed. All rights reserved by the author.

---

## 🙋 Author

**christoban** — [GitHub](https://github.com/christoban)
