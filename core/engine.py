# core/engine.py
"""
The Core Engine for LM Buddy application.

This class orchestrates the main functionalities of the application, including:
- Managing application configuration.
- Handling conversation context history.
- Processing user inputs (direct questions, OCR/image-based actions).
- Interacting with the LLM via llm_handler.
- Managing OCR operations via ocr_utils.
- Controlling Text-to-Speech output via tts_utils.
- Managing the global hotkey listener via hotkey_manager.
- Communicating updates and results to the GUI via a queue.
"""
import logging
import threading
import queue
from PIL import Image # For type hinting PIL.Image.Image
import typing       # For extensive type hinting

# Import core utility modules
from . import config_manager
from . import ocr_utils
from . import tts_utils
from . import llm_handler
from . import hotkey_manager
from . import message_types as mt # For structured communication with the GUI

class LMBuddyCoreEngine:
    """
    The central processing engine for LM Buddy. It manages state,
    coordinates module interactions, and communicates with the GUI.
    """
    def __init__(self, gui_queue: queue.Queue, app_stop_event: threading.Event):
        """
        Initializes the LMBuddyCoreEngine.

        Args:
            gui_queue (queue.Queue): A queue for sending messages and updates
                                     asynchronously to the GUI.
            app_stop_event (threading.Event): An event that signals when the
                                              application is shutting down, allowing
                                              threads to terminate gracefully.
        """
        self.gui_queue = gui_queue
        self.app_stop_event = app_stop_event # Used by components like HotkeyManager

        # Initialize core utility modules. Errors during init are logged by the modules.
        if not tts_utils.initialize_tts():
            logging.warning("ENGINE: TTS Engine could not be initialized during engine setup.")
        if not llm_handler.initialize_tokenizer(): # Tokenizer is crucial for token counts
            logging.warning("ENGINE: LLM Tokenizer could not be initialized during engine setup.")
        
        # --- Application Core State ---
        self.context_history: typing.List[typing.Tuple[str, typing.Union[str, list], typing.Optional[Image.Image]]] = []
        """
        Conversation history. List of tuples: (role, content, pil_image_object).
        'content' can be a string or a list of parts for multimodal messages.
        'pil_image_object' is the PIL.Image if the user turn included an image.
        """
        self.ocr_text_buffer: typing.Optional[str] = None
        """Stores the most recently extracted OCR text."""
        self.last_image_buffer: typing.Optional[Image.Image] = None
        """Stores the most recently captured PIL Image object."""
        self.last_action_was_ocr_initiated: bool = False
        """Flag to indicate if the last LLM interaction was triggered by an OCR/image action."""

        # Initialize and start the HotkeyManager
        self.hotkey_mgr = hotkey_manager.HotkeyManager(
            hotkey_callback=self._handle_hotkey_press, # Engine method as callback
            app_stop_event=self.app_stop_event       # Pass the global app stop event
        )
        if not self.hotkey_mgr.start_listener():
            logging.error("ENGINE: Failed to start the hotkey listener.")
            # Inform GUI about the failure if possible (queue might be used by GUI constructor later)
            self.gui_queue.put({"type": mt.MSG_TYPE_ERROR, "content": "Critical: Hotkey listener failed to start. Check logs/config."})

        logging.info("LMBuddyCoreEngine initialized successfully.")

    # --- Configuration Access ---
    def get_config_value(self, key: str, default: typing.Any = None) -> typing.Any:
        """Convenience method to retrieve a configuration value."""
        return config_manager.get_config_value(key, default)

    def set_config_value(self, key: str, value: typing.Any, save_now: bool = True):
        """
        Convenience method to set a configuration value.
        By default, it saves the entire configuration immediately.
        """
        config_manager.set_config_value(key, value)
        if save_now:
            config_manager.save_configuration()

    # --- State Management ---
    def clear_all_context_and_buffers(self):
        """Clears the conversation history and resets temporary buffers."""
        self.context_history.clear()
        self.ocr_text_buffer = None
        self.last_image_buffer = None
        self.last_action_was_ocr_initiated = False
        logging.info("ENGINE: Conversation context history and internal buffers have been cleared.")
        
        # Inform GUI about the clearance for UI updates
        self.gui_queue.put({"type": mt.MSG_TYPE_INFO, "content": "Context and buffers cleared."})
        # Reset token counts in GUI
        self.gui_queue.put({"type": mt.MSG_TYPE_LLM_PROMPT_TOKENS_UPDATE, "count": 0})
        self.gui_queue.put({
            "type": mt.MSG_TYPE_LLM_FINAL_TOKEN_COUNTS,
            "prompt_tokens":0, "completion_tokens":0, "total_tokens":0
        })

    # --- Hotkey Handling ---
    def _handle_hotkey_press(self):
        """
        Callback executed by HotkeyManager when the registered hotkey is pressed.
        Initiates the screenshot and OCR process.
        """
        logging.info("ENGINE: Hotkey press detected by manager, engine is now handling it.")
        
        # Determine the main GUI window title for the ocr_utils to attempt hiding it.
        # This relies on the configuration being up-to-date with the GUI's actual title.
        app_ver = self.get_config_value("app_version", "") # Default to empty if not set
        base_title = self.get_config_value("classic_ui_title", "LM Buddy")
        main_window_title = f"{base_title} {app_ver}".strip() # Construct and strip potential trailing space

        # The perform_screenshot_and_ocr method is potentially blocking (file I/O, OCR).
        # Run it in a new thread to keep the hotkey callback (and thus listener) responsive.
        # Results (image, OCR text, or errors) will be put onto self.gui_queue.
        threading.Thread(
            target=self.perform_screenshot_and_ocr,
            args=(main_window_title,),
            daemon=True,
            name="ScreenshotOCRThread"
        ).start()

    # --- Core Action Processing ---
    def process_direct_question(self, question_text: str):
        """
        Processes a direct text question from the user by sending it to the LLM.
        """
        if not question_text or not question_text.strip():
            logging.warning("ENGINE: process_direct_question called with empty text.")
            self.gui_queue.put({"type": mt.MSG_TYPE_ERROR, "content": "Cannot process an empty question."})
            return

        logging.debug(f"ENGINE: Processing direct question: '{question_text[:60]}...'")
        self.last_action_was_ocr_initiated = False # Update engine state

        current_message_parts = [{"type": "text", "text": question_text}]
        
        # Call llm_handler in a new thread. llm_handler will use self.gui_queue.
        threading.Thread(
            target=llm_handler.stream_llm_response,
            args=(
                self.context_history,           # Shared, mutable history list
                current_message_parts,
                self.gui_queue,
                self.app_stop_event,            # For graceful shutdown during stream
                True,                           # is_direct_question
                None,                           # specific_action
                None,                           # pil_image_obj (no image for direct q)
                None                            # target_language
            ), 
            daemon=True,
            name="LLMDirectQuestionThread"
        ).start()

    def process_ocr_action(self, action_key: str, ocr_text: typing.Optional[str], 
                           image_pil: typing.Optional[Image.Image], 
                           target_language: typing.Optional[str] = None):
        """
        Processes an action (e.g., summarize, translate) based on provided
        OCR text and/or a PIL Image.
        """
        logging.debug(f"ENGINE: Processing OCR action '{action_key}'. OCR text length: {len(ocr_text or '')}. Image provided: {image_pil is not None}")
        
        # Update engine's internal buffers with the current context for this action
        self.ocr_text_buffer = ocr_text
        self.last_image_buffer = image_pil
        self.last_action_was_ocr_initiated = True # This action is OCR/Image based

        # Construct the `current_user_message_parts` for the LLM
        current_message_parts: typing.List[typing.Dict[str, typing.Any]] = []
        action_description_prompt = "" # This will be the main text part of the user's message

        if image_pil and self.get_config_value("enable_vision_if_available", False):
            base64_img_str = llm_handler.convert_image_to_base64_str(image_pil)
            current_message_parts.append({"type": "image_url", "image_url": {"url": base64_img_str}})
        
        # Build the textual part of the prompt based on the action
        # (This logic was previously in gui.py's _process_ocr_action_thread)
        if action_key == "summarize": action_description_prompt = "Fasse den folgenden Inhalt (Text/Bild) prägnant zusammen."
        elif action_key == "help": action_description_prompt = "Ich benötige Hilfe zum folgenden Inhalt (Text/Bild). Gib eine verständliche Hilfestellung."
        elif action_key == "improve_text" and ocr_text: action_description_prompt = "Bitte verbessere den folgenden Text:"
        elif action_key == "analyze_image": action_description_prompt = "Analysiere das folgende Bild detailliert."
        elif action_key == "bullet_points": action_description_prompt = "Extrahiere die wichtigsten Informationen aus dem folgenden Inhalt (Text/Bild) als Stichpunkte."
        elif action_key == "translate" and target_language: action_description_prompt = f"Übersetze den folgenden Text (oder beschreibe das Bild und übersetze die Beschreibung) in die Sprache: {target_language}."
        elif action_key == "set_context_for_question":
            action_description_prompt = "Der folgende Inhalt (Text/Bild) dient als Kontext für meine nächste Frage:"
            if ocr_text: action_description_prompt += f"\n\nOCR-Text:\n---\n{ocr_text}\n---"
        else: # Default action if key is unrecognized (should not happen with button UI)
            action_description_prompt = "Analysiere den folgenden Inhalt (Text/Bild) und gib eine kurze Hilfestellung/Zusammenfassung."
        
        # Append OCR text to the prompt if available and not already part of a specific instruction (like set_context)
        if ocr_text and action_key != "set_context_for_question":
            action_description_prompt += f"\n\nExtrahierter Text:\n\"\"\"{ocr_text}\"\"\""
        
        # Adjust prompt wording if no image or no text was provided for a generic action
        if not image_pil and "(Text/Bild)" in action_description_prompt:
            action_description_prompt = action_description_prompt.replace("(Text/Bild)", "(Text)")
        elif not image_pil and "Bild" in action_description_prompt and "Text" not in action_description_prompt: # e.g. "Analyze Image" but no image
             action_description_prompt = "Aktion nicht möglich, da kein Bild vorhanden."
             if ocr_text: action_description_prompt += f" Dennoch hier der extrahierte Text:\n\"\"\"{ocr_text}\"\"\""
        
        current_message_parts.append({"type": "text", "text": action_description_prompt})
        
        # Call llm_handler in a new thread
        threading.Thread(
            target=llm_handler.stream_llm_response,
            args=(
                self.context_history, current_message_parts, self.gui_queue,
                self.app_stop_event,
                False, # is_direct_question = False for OCR actions
                action_key, # Pass the specific action for context (e.g. "set_context")
                image_pil,  # Pass the PIL image for this turn to be stored in history
                target_language
            ), 
            daemon=True,
            name=f"LLMOCRActionThread-{action_key}"
        ).start()

    def perform_screenshot_and_ocr(self, main_gui_window_title: str):
        """
        Orchestrates capturing a screenshot, performing OCR, and then
        sending the results (or errors) to the GUI queue for further action.
        """
        logging.debug(f"ENGINE: Performing screenshot and OCR. Attempting to hide window: '{main_gui_window_title}'.")
        
        image_pil, err_msg_screenshot = ocr_utils.capture_active_window_pil(main_gui_window_title=main_gui_window_title)

        if err_msg_screenshot or not image_pil:
            error_to_send_to_gui = err_msg_screenshot or "Error: Screenshot capture failed (no image returned)."
            self.gui_queue.put({"type": mt.MSG_TYPE_ERROR, "content": error_to_send_to_gui})
            self.gui_queue.put({"type": mt.MSG_TYPE_OCR_ACTIONS_HIDE}) # Tell GUI to hide action buttons
            return

        ocr_text, err_msg_ocr = ocr_utils.extract_text_from_image(image_pil)
        vision_is_enabled = self.get_config_value("enable_vision_if_available", False)

        if err_msg_ocr and not vision_is_enabled: # OCR failed, and no vision fallback
            self.gui_queue.put({"type": mt.MSG_TYPE_ERROR, "content": err_msg_ocr})
            self.gui_queue.put({"type": mt.MSG_TYPE_OCR_ACTIONS_HIDE})
            return
        
        if err_msg_ocr and vision_is_enabled: # OCR failed, but vision is on, so proceed with image
            logging.warning(f"ENGINE: OCR process failed (error: {err_msg_ocr}), but vision is enabled. Proceeding with image only.")
            ocr_text = "" # Ensure ocr_text is empty so only image is primary for vision prompt

        # Send successful OCR/image data to GUI to display action choices
        self.gui_queue.put({
            "type": mt.MSG_TYPE_OCR_RESULT_FOR_ACTIONS,
            "ocr_text": ocr_text if ocr_text is not None else "", # Ensure string
            "image_pil": image_pil # GUI will need this for context if user picks an image action
        })

    # --- TTS Control ---
    def speak(self, text_to_speak: str):
        """Delegates to tts_utils to speak the given text."""
        if not text_to_speak or not text_to_speak.strip():
            logging.debug("ENGINE: Speak called with empty text, ignoring.")
            return
        tts_utils.speak_text(text_to_speak)

    def stop_speech(self):
        """Delegates to tts_utils to stop any ongoing speech."""
        tts_utils.stop_speaking()

    # --- Hotkey Listener Control (delegated from GUI) ---
    def update_hotkey_listener_config(self) -> typing.Optional[str]:
        """
        Instructs the HotkeyManager to reload its hotkey configuration.
        Returns the new hotkey string or None if invalid.
        """
        if hasattr(self, 'hotkey_mgr') and self.hotkey_mgr:
            return self.hotkey_mgr.update_hotkey_from_config()
        logging.warning("ENGINE: update_hotkey_listener_config called but hotkey_mgr not found.")
        return None

    # --- Application Shutdown ---
    def shutdown(self):
        """
        Performs cleanup tasks when the application is shutting down.
        This includes stopping the hotkey listener and TTS.
        The `app_stop_event` (passed to __init__) should be set by the main
        application (GUI) before calling this method to signal all threads.
        """
        logging.info("ENGINE: Shutdown sequence initiated...")
        
        # Stop the hotkey listener thread (it monitors app_stop_event, but join ensures it finishes)
        if hasattr(self, 'hotkey_mgr') and self.hotkey_mgr:
             self.hotkey_mgr.stop_listener() # This will attempt to join the thread
        
        # Stop any ongoing TTS
        self.stop_speech()
        
        # Any other cleanup tasks for the engine can be added here.
        logging.info("ENGINE: Shutdown sequence complete.")

# --- Example Usage for Direct Testing of the Engine ---
if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG, 
        format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s'
    )
    
    # Create a dummy queue and stop event for testing
    test_gui_q = queue.Queue()
    test_app_stop_signal = threading.Event()

    print("--- LMBuddyCoreEngine Test Script ---")
    print("Initializing engine...")
    engine_instance = LMBuddyCoreEngine(gui_queue=test_gui_q, app_stop_event=test_app_stop_signal)
    print("Engine initialized.")

    print(f"\nConfigured Hotkey via engine: {engine_instance.get_config_value('hotkey')}")

    # Test context clearing
    print("\nTesting context clearing:")
    engine_instance.context_history.append(("user", "A test message in history", None))
    print(f"  History before clear: {engine_instance.context_history}")
    engine_instance.clear_all_context_and_buffers()
    print(f"  History after clear: {engine_instance.context_history}")
    print("  Messages sent to GUI queue by clear_all_context_and_buffers:")
    try:
        while not test_gui_q.empty(): print(f"    Queue item: {test_gui_q.get_nowait()}")
    except queue.Empty: pass


    # Simulate a direct question (LLM call is threaded, results go to queue)
    print("\nSimulating a direct question to LLM: 'What is Python?'")
    engine_instance.process_direct_question("What is Python programming language?")
    print("  Direct question processing initiated by engine (LLM call is in a separate thread).")
    print("  Monitoring GUI queue for LLM response messages (up to 5s for this test):")
    
    start_time = time.time()
    llm_response_complete = False
    while time.time() - start_time < 5: # Wait up to 5 seconds for messages
        try:
            msg = test_gui_q.get(timeout=0.1)
            print(f"    Queue item (Direct Q): {msg}")
            if msg is None: # Sentinel for end of LLM stream sequence
                print("    End of LLM response sequence received for Direct Q.")
                llm_response_complete = True
                break
            # test_gui_q.task_done() # Not strictly needed for this test loop
        except queue.Empty:
            pass # No message yet
        if llm_response_complete: break
    if not llm_response_complete: print("    Test timeout waiting for full LLM response sequence for Direct Q.")


    # To test OCR action, we would need to simulate an image and OCR text
    # For now, this part is more complex to test in isolation without GUI interaction.
    # print("\nSimulating an OCR action (requires mock image/text or real capture)...")
    # mock_image = Image.new('RGB', (100,100), 'blue')
    # engine_instance.process_ocr_action("summarize", "This is some OCR text from an image.", mock_image)
    # print("  OCR action processing initiated by engine.")
    # (Similar queue monitoring loop as above)

    print("\nSignaling application stop and shutting down engine...")
    test_app_stop_signal.set() # This will make the hotkey listener thread exit
    engine_instance.shutdown() # Perform engine-specific cleanup

    print("\n--- LMBuddyCoreEngine Test Script Finished ---")