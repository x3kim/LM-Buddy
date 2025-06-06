# core/config_manager.py
"""
Manages the application's configuration settings.

This module handles loading configuration from a JSON file, providing access
to configuration values, and saving changes back to the file.
It uses a default configuration dictionary as a fallback and to ensure
all expected keys are present.
"""
import json
import logging
import os
import typing # For type hinting

# --- Constants ---
CONFIG_FILE_NAME: str = "config.json"  # Name of the configuration file

# Default configuration values. These serve as a template and fallback.
# Comments here explain the purpose of each key.
DEFAULT_CONFIG: typing.Dict[str, typing.Any] = {
    # --- LLM Provider & Model Configuration ---
    "llm_provider": "custom",  # Options: "custom", "openai", "google_vertexai", "anthropic", etc.
    "llm_endpoint": "http://127.0.0.1:1234/v1/chat/completions",  # Endpoint for "custom" provider or specific model.
    "llm_api_key": "",         # API key for cloud providers (e.g., OpenAI, Google, Anthropic).
    "llm_model": "local-model/example-model-name",  # Model identifier (e.g., for Ollama, Hugging Face, or provider-specific).
    "tokenizer_model_name": "local-model/example-model-name",  # Usually same as llm_model, or a specific tokenizer from Hugging Face.

    # --- Feature Configuration ---
    "ocr_language": "deu",               # Default OCR language (e.g., "eng", "deu+eng").
    "enable_vision_if_available": True,  # Whether to attempt using vision capabilities of the LLM if supported.
    "hotkey": "ctrl+shift+f",            # Global hotkey to trigger the application's main action.

    # --- LLM Interaction Parameters ---
    "max_tokens": 4096,                  # Max tokens the LLM should generate in a response.
    "temperature": 0.3,                  # LLM temperature (0.0 to 2.0). Lower is more deterministic.
    "llm_request_timeout": 180,          # Timeout in seconds for LLM API requests.
    "screenshot_delay": 0.5,             # Delay in seconds after hiding GUI before taking a screenshot.
    "system_prompt_global": "You are LM Buddy, a helpful and friendly AI assistant. Format your answers clearly using Markdown. Be concise but helpful. Explain things simply.",
    "max_context_messages": 30,          # Max number of user/assistant message pairs to keep in history for context.
    "max_context_tokens_warning": 6000,  # Token threshold for context length warning in UI.

    # --- UI Skin & Appearance (Classic Window) ---
    "active_ui_skin": "classic",         # Current active UI skin ("classic", "avatar").
    "classic_ui_title": "LM Buddy",      # Base title for the classic UI window. App version will be appended.
    "classic_ui_alpha": 0.9,             # Transparency of the classic UI window (0.0 to 1.0).
    "classic_ui_initial_geometry": "800x700+100+100", # Initial size and position (WxH+X+Y).
    "classic_ui_save_window_geometry": True, # Save window geometry on exit.
    
    # --- UI Skin & Appearance (Avatar - Sherlox) ---
    "avatar_name": "Sherlox",                # Name of the default/current avatar.
    "avatar_skin": "fox",                # Identifier for the avatar's visual appearance (e.g., "fox", "cat").
    "avatar_accessories": [],            # List of equipped accessory identifiers.
    "avatar_system_prompt_override": "", # Specific system prompt for this avatar, overrides global if set.
    "avatar_show_on_startup": True,      # Whether the avatar UI should appear on application startup.
    "avatar_position_x": -1,             # Last known X position of avatar window (-1 for default/center).
    "avatar_position_y": -1,             # Last known Y position of avatar window.
    "avatar_scale": 1.0,                 # Scaling factor for the avatar graphics.

    # --- General Application Settings ---
    "app_version": "v0.9.6",              # Application version, used in window title.
    "user_language": "auto",             # Preferred UI language ("auto", "en", "de", etc.). For i18n.
    "config_version": "1.0"              # Version of the config file structure (for future migrations).
}

# Module-level global variables to hold the current configuration and its path.
# These are considered "private" to this module (by convention with underscore).
_current_config: typing.Optional[typing.Dict[str, typing.Any]] = None
_config_path: typing.Optional[str] = None
def _get_project_root() -> str:
    """
    Determines the project's root directory.
    Assumes this script is in 'core/' and config.json is in the parent directory.
    Adjust if your project structure is different.
    """
    # Navigates one level up from the directory of the current file (core/)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def init_config_path(custom_path: typing.Optional[str] = None) -> str:
    """
    Initializes and returns the absolute path to the configuration file.

    If `custom_path` is provided, it's used. Otherwise, a default path
    relative to the project root is constructed.

    This function is called automatically when the module is loaded.

    Args:
        custom_path (str, optional): A custom path to the configuration file.

    Returns:
        str: The absolute path to the configuration file.
    """
    global _config_path
    if custom_path:
        _config_path = os.path.abspath(custom_path)
    else:
        # Default path: config.json in the project root directory.
        _config_path = os.path.join(_get_project_root(), CONFIG_FILE_NAME)
    logging.debug(f"CONFIG_MANAGER: Configuration file path set to: {_config_path}")
    return _config_path

def load_configuration() -> typing.Dict[str, typing.Any]:
    """
    Loads the application configuration from the JSON file.

    If the file doesn't exist, it's created with default values.
    If the file is invalid JSON, defaults are used for the current session,
    and an error is logged. User's invalid file is not overwritten automatically.
    User-defined settings in the config file override the `DEFAULT_CONFIG`.
    Missing keys from `DEFAULT_CONFIG` are added to the loaded config.

    Returns:
        dict: The loaded (or default) configuration dictionary.
    """
    global _current_config
    if not _config_path: # Should have been initialized on module import
        init_config_path()
        assert _config_path is not None, "Config path could not be initialized"

    # Start with a deep copy of defaults to ensure all keys are present
    # and to avoid modifying the original DEFAULT_CONFIG.
    loaded_config = {k: v for k, v in DEFAULT_CONFIG.items()} # Simple deep copy

    try:
        with open(_config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
            # Merge user settings: user_config values override defaults.
            # This also preserves new default keys if the user's config file is older.
            for key in loaded_config: # Iterate over default keys to ensure structure
                if key in user_config:
                    # Basic type check or more sophisticated merge could be added here
                    # For now, direct override.
                    loaded_config[key] = user_config[key]
            # Optionally, handle unknown keys from user_config (e.g., log them, remove them)
            for key_user in user_config:
                if key_user not in loaded_config:
                    logging.warning(f"CONFIG_MANAGER: Unknown key '{key_user}' found in config file. It will be ignored unless added to DEFAULT_CONFIG.")
                    # Or, if you want to load them anyway: loaded_config[key_user] = user_config[key_user]


        logging.info(f"CONFIG_MANAGER: Configuration successfully loaded from '{_config_path}'.")
    except FileNotFoundError:
        logging.warning(f"CONFIG_MANAGER: '{_config_path}' not found. Using default settings and creating the file.")
        _current_config = loaded_config # Use defaults before saving
        save_configuration() # This will save the current _current_config (which are defaults)
        # No need to return _current_config here, it's set globally and returned at the end
    except json.JSONDecodeError:
        logging.error(f"CONFIG_MANAGER: Error reading '{_config_path}'. File is not valid JSON. Using default settings for this session.")
        # In this case, loaded_config (which is DEFAULT_CONFIG) will be used.
        # The invalid user file is not overwritten automatically.
    except Exception as e:
        logging.error(f"CONFIG_MANAGER: Unexpected error loading configuration from '{_config_path}': {e}. Using default settings for this session.", exc_info=True)
        # Defaults will be used.

    _current_config = loaded_config
    
    # Placeholder for potential future config migration logic
    # file_config_version = _current_config.get("config_version", "0.0") # Version from loaded file
    # default_master_version = DEFAULT_CONFIG.get("config_version", "1.0") # Current version in code
    # if file_config_version != default_master_version:
    #     logging.info(f"Config version mismatch (file: {file_config_version}, current: {default_master_version}). Migration might be needed.")
    #     # _current_config = _migrate_config(_current_config, file_config_version, default_master_version)
    #     # _current_config["config_version"] = default_master_version # Update version after migration
    #     # save_configuration() # Save migrated config
    
    return _current_config

def get_config_value(key: str, default_override: typing.Any = None) -> typing.Any:
    """
    Retrieves a specific value from the loaded configuration.

    Args:
        key (str): The configuration key to retrieve.
        default_override (Any, optional): A value to return if the key is not found.
                                         If None, the master default from DEFAULT_CONFIG for that key is used.

    Returns:
        Any: The configuration value, or the default if not found.
    """
    if _current_config is None:
        load_configuration() # Ensure config is loaded
    
    # Ensure _current_config is not None after load_configuration attempt
    # This should ideally not happen if load_configuration always sets _current_config
    if _current_config is None:
        logging.error("CONFIG_MANAGER: _current_config is None even after load_configuration. Returning emergency default.")
        # Fallback to default_override or the master default for the key
        if default_override is not None:
            return default_override
        return DEFAULT_CONFIG.get(key) # Can be None if key not in DEFAULT_CONFIG

    if default_override is not None:
        return _current_config.get(key, default_override)
    # Fallback to the master default defined in DEFAULT_CONFIG if key is missing in _current_config
    return _current_config.get(key, DEFAULT_CONFIG.get(key))

def set_config_value(key: str, value: typing.Any):
    """
    Sets a specific value in the current in-memory configuration.
    Call `save_configuration()` to persist changes to the file.

    Args:
        key (str): The configuration key to set.
        value (Any): The new value for the key.
    """
    if _current_config is None:
        load_configuration() # Ensure config is loaded
    
    if _current_config is not None: # Check again after load
        _current_config[key] = value
        logging.debug(f"CONFIG_MANAGER: Config value set (in memory): '{key}' = '{value}'")
    else:
        # This case should be rare if load_configuration works as expected
        logging.error(f"CONFIG_MANAGER: Failed to set config value for '{key}' because _current_config is still None.")

def save_configuration(geometry_to_save: typing.Optional[str] = None):
    """
    Saves the current in-memory configuration to the JSON file.

    Args:
        geometry_to_save (str, optional): Specific UI geometry string for the classic window.
                                          If provided, it updates "classic_ui_initial_geometry".
    """
    global _current_config # We are modifying the module-level _current_config if it was None
    if not _config_path:
        init_config_path()
        assert _config_path is not None, "Config path could not be initialized for saving"

    if _current_config is None:
        logging.warning("CONFIG_MANAGER: No configuration was loaded to save. Attempting to load/create defaults before saving.")
        load_configuration() # This will set _current_config to defaults if it was None
        if _current_config is None: # If load_configuration still fails to set _current_config
            logging.error("CONFIG_MANAGER: CRITICAL - Cannot save configuration because _current_config is None even after attempting load.")
            return # Prevent further errors

    # Create a copy to avoid modifying the in-memory _current_config during the save process
    # if other parts of the save logic (like geometry) read from it.
    config_to_save = _current_config.copy()

    # Handle specific window geometry saving for the classic UI if provided
    if get_config_value("classic_ui_save_window_geometry", True) and geometry_to_save:
        config_to_save["classic_ui_initial_geometry"] = geometry_to_save # Key used for loading initial geometry
    
    try:
        # Ensure the directory for the config file exists (important for first run or custom paths)
        config_dir = os.path.dirname(_config_path)
        if not os.path.exists(config_dir) and config_dir: # Check if config_dir is not empty (root path case)
            os.makedirs(config_dir, exist_ok=True)
            logging.info(f"CONFIG_MANAGER: Created directory for config file: '{config_dir}'")

        with open(_config_path, "w", encoding="utf-8") as f:
            json.dump(config_to_save, f, indent=4, ensure_ascii=False) # Use indent=4 for better readability
        logging.info(f"CONFIG_MANAGER: Configuration successfully saved to '{_config_path}'.")
    except Exception as e:
        logging.error(f"CONFIG_MANAGER: Error saving configuration to '{_config_path}': {e}", exc_info=True)

# --- Module Initialization ---
# Initialize path and load configuration when the module is first imported.
# This makes the configuration available immediately to any part of the app
# that imports this manager.
if _config_path is None:
    init_config_path()
if _current_config is None:
    load_configuration()

if __name__ == "__main__":
    # Configure logging for direct script execution test
    logging.basicConfig(level=logging.DEBUG, 
                        format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s')
    
    print(f"Config path: {init_config_path()}") # Test path init
    
    cfg = load_configuration() # Test loading
    print(f"\nLoaded LLM Endpoint: {get_config_value('llm_endpoint')}")
    print(f"Loaded Hotkey: {get_config_value('hotkey')}")
    print(f"A non-existent key with default_override: {get_config_value('my_new_shiny_key', 'this is the default')}")
    print(f"A non-existent key falling back to DEFAULT_CONFIG: {get_config_value('another_new_key')}") # Will be None if not in DEFAULT_CONFIG

    # Test setting a value and saving
    original_hotkey = get_config_value('hotkey')
    print(f"\nOriginal hotkey: {original_hotkey}")
    set_config_value('hotkey', 'ctrl+alt+z')
    print(f"Hotkey in memory after set: {get_config_value('hotkey')}")
    save_configuration()
    print("Configuration saved.")

    # Reload to confirm it was saved
    print("\nReloading configuration...")
    load_configuration() # This will update _current_config
    print(f"Hotkey after reload: {get_config_value('hotkey')}")

    # Restore original hotkey for subsequent tests
    if original_hotkey: # Ensure original_hotkey is not None
        set_config_value('hotkey', original_hotkey)
        save_configuration()
        print(f"\nHotkey restored to: {get_config_value('hotkey')}")

    print("\nFull current configuration in memory:")
    if _current_config:
        for k, v_ in _current_config.items(): # Renamed v to v_
            print(f"  {k}: {v_}")
    else:
        print("  Configuration could not be loaded.")