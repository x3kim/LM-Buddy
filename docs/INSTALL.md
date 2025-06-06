# 🚀 Setup Guide 🛠️

## 📋 Prerequisites

*   **🐍 Python:** Version 3.10 or higher recommended.
*   **👁️ Tesseract OCR:**
    *   Must be installed on your system.
    *   The Tesseract installation directory (containing `tesseract.exe` on Windows) must be added to your system's PATH environment variable.
    *   You will also need to install the language data for the languages you intend to use with OCR (e.g., English, German). These can typically be selected during Tesseract installation or added later.
*   **🐙 Git:** (Optional, for cloning the repository).
*   **🧠 (Optional) An LLM Endpoint:**
    *   For local LLMs: An Ollama, LM Studio, or other OpenAI-API compatible server running.
    *   For cloud LLMs: An API key for your chosen provider (e.g., OpenAI).

## ⚙️ Setup and Installation

1.  **📂 Clone the Repository (Optional):**
    If you have Git, clone the repository:
    ```bash
    git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY_NAME.git
    cd YOUR_REPOSITORY_NAME
    ```
    Alternatively, download the source code ZIP and extract it.

2.  **🌱 Create and Activate a Virtual Environment:**
    It's highly recommended to use a virtual environment to manage project dependencies.
    ```bash
    # Create a virtual environment (e.g., named .venv)
    python -m venv .venv

    # Activate the virtual environment
    # On Windows:
    .venv\Scripts\activate
    # On macOS/Linux:
    source .venv/bin/activate
    ```

🚨 3.  **📦 Install Dependencies:** ⛔ NOT included yet
    Install the required Python packages using pip:
    ```bash
    python -m pip install -r requirements.txt
    ```
    *(If a `requirements.txt` file is not yet present, you'll need to install packages manually. See "Manual Dependency Installation" below.)*

    **🛠️ Manual Dependency Installation (if no `requirements.txt`):**
    ```bash
    python -m pip install Pillow pytesseract keyboard requests pyttsx3 pygetwindow tkhtmlview markdown2 PySide6 transformers torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
    ```
    *💡 Note on `torch` for `transformers`: The `transformers` library (especially for local models) often benefits from PyTorch. The `--index-url ...` part is for installing a CUDA-enabled version if you have an NVIDIA GPU and CUDA 11.8 installed. If you don't have an NVIDIA GPU or want a CPU-only version, you can install PyTorch differently (check [PyTorch Get Started](https://pytorch.org/get-started/locally/)). For basic `transformers` tokenizer usage without running models locally, a simpler PyTorch install might suffice, or it might even work without if the chosen tokenizer has no PyTorch dependency.*
    *Minimal set if you only use the tokenizer part of transformers for now:*
    `python -m pip install Pillow pytesseract keyboard requests pyttsx3 pygetwindow tkhtmlview markdown2 PySide6 transformers`


## ⚙️ Configuration

Upon first run, a `config.json` 📄 file will be created in the project's root directory with default settings. You may need to edit this file, especially for:

*   **🔌 `llm_provider`**: Set to `"custom"` for local LLMs, or `"openai"`, etc., for cloud services. ⚠️ (not yet fully implemented - only tested with LM Studio)
*   **🔗 `llm_endpoint`**: If `llm_provider` is `"custom"`, set this to the URL of your local LLM server (e.g., `http://localhost:1234/v1/chat/completions`). For OpenAI, this can be left blank to use the default, or set to a specific endpoint (e.g., Azure OpenAI). ⚠️ (not yet fully implemented - only tested with LM Studio)
*   **🔑 `llm_api_key`**: Your API key if using a cloud provider like OpenAI.
*   **🤖 `llm_model`**: The model identifier for your chosen LLM.
*   **🗣️ `tokenizer_model_name`**: The Hugging Face model name for the tokenizer (often the same as `llm_model`).
*   **🌍 `ocr_language`**: The language code(s) for Tesseract OCR (e.g., `deu` for German, `eng` for English, `deu+eng` for both).
*   **⌨️ `hotkey`**: The global hotkey to trigger screen analysis (default: `ctrl+shift+f`).

## ▶️ Running the Application

1.  **✅ Ensure your virtual environment is activated.** (See Setup step 2).
2.  **✅ Ensure your LLM server is running** (if using a local LLM) or you have a valid API key configured.
3.  **🚀 Run the main GUI script:**
    ```bash
    python gui.py
    ```
    This will start the classic Tkinter-based window.

4.  **🤖 To test the Avatar UI (Sherlox) standalone (for development):**
    Ensure you have an image for Sherlox at `data/avatar/sherlox/idle.png` and for the display at `data/display/blackboard_green/min.png` (or let the script create dummy images).
    ```bash
    python avatar_ui.py
    ```

    *(📝 Note: Full integration of the Avatar UI with the main application and engine is in progress.)*

## 🖱️ Usage

*   **♨️ Hotkey:** Press the configured hotkey (default: `Ctrl+Shift+F`) to capture the active window.
*   **🎬 Actions:** After capture, choose an action (Summarize, Analyze Image, Get Help, etc.).
*   **❓ Direct Questions:** Type questions directly into the input field in the classic GUI.
*   **👤 Avatar (Standalone Test):**
    *   Drag the avatar window to move it.
    *   It will automatically change its facing direction based on its screen position.
    *   Right-click on the avatar for a context menu (e.g., to close it during testing).

## 🛠️ Troubleshooting

*   **🚫 `TesseractNotFoundError` or OCR issues:**
    *   Ensure Tesseract OCR is installed correctly.
    *   Ensure the Tesseract installation directory is in your system's PATH.
    *   Ensure the required language data files (e.g., `deu.traineddata`, `eng.traineddata`) are present in Tesseract's `tessdata` folder.
*   **⚠️ `AttributeError: '_tkinter.tkapp' object has no attribute ...`:**
    *   This can sometimes occur due to issues with how Tkinter methods are bound or called, especially in complex UIs or with threading. Ensure you have the latest version of the code.
*   **❓ `NameError: name 'QMenu' is not defined` (or similar for Qt components):**
    *   Ensure all necessary PySide6 components are imported at the top of `avatar_ui.py`.
*   **⌨️ Hotkey not working:**
    *   **🛡️ Permissions (macOS/Linux):** On some systems, the application might need special permissions to listen for global hotkeys (e.g., Accessibility permissions on macOS, or running with sudo on Linux - though sudo is generally not recommended for GUI apps).
    *   **🔄 Other applications:** Another application might be using the same hotkey. Try changing it in `config.json`.
    *   **🔧 Keyboard library issues:** The `keyboard` library can sometimes have platform-specific issues.
