# core/tts_utils.py
"""
Text-to-Speech (TTS) utilities for the application.

This module initializes and manages a TTS engine (currently pyttsx3)
to speak text aloud. It handles speech in a separate thread to prevent
blocking the main application GUI. It also includes text cleaning
functionality to improve speech output.
"""
import pyttsx3
import logging
import threading
import re
import html
import typing # For type hinting

# Module-level global variables for the TTS engine instance and its control.
# These are considered "private" to this module.
_tts_engine: typing.Optional[pyttsx3.Engine] = None
_tts_thread: typing.Optional[threading.Thread] = None
_tts_stop_event: threading.Event = threading.Event() # Event to signal the TTS worker to stop

def initialize_tts() -> bool:
    """
    Initializes the Text-to-Speech (TTS) engine.

    This function should be called once at application startup.
    It sets up the pyttsx3 engine and configures default properties like rate.
    If already initialized, it does nothing and returns True.

    Returns:
        bool: True if the engine was initialized successfully or is already initialized,
              False otherwise.
    """
    global _tts_engine
    if _tts_engine is not None:
        logging.debug("TTS_UTILS: TTS engine is already initialized.")
        return True
    try:
        _tts_engine = pyttsx3.init()
        if _tts_engine:
            _tts_engine.setProperty('rate', 180)  # Default speaking rate
            # Example: Set a specific voice if needed and available
            # voices = _tts_engine.getProperty('voices')
            # if voices:
            #     # Attempt to find a preferred voice (e.g., by name or language)
            #     # For now, just logs available voices if in DEBUG mode.
            #     if logging.getLogger().isEnabledFor(logging.DEBUG):
            #         for voice in voices:
            #             logging.debug(f"TTS_UTILS: Available voice: ID='{voice.id}', Name='{voice.name}', Langs='{voice.languages}'")
            #     # _tts_engine.setProperty('voice', voices[0].id) # Example: set first available voice
            logging.info("TTS_UTILS: TTS engine initialized successfully.")
            return True
        else: # pyttsx3.init() can return None on some systems if it fails
            logging.error("TTS_UTILS: pyttsx3.init() returned None, TTS engine not available.")
            _tts_engine = None # Ensure it's None
            return False
    except Exception as e:
        logging.error(f"TTS_UTILS: TTS Engine Initialization Failed: {e}", exc_info=True)
        _tts_engine = None
        return False

def _clean_text_for_speech(text_to_clean: str) -> str:
    """
    Cleans a given text string to improve its suitability for speech synthesis.
    Removes common Markdown formatting, HTML entities, and full URLs.

    Args:
        text_to_clean (str): The input text string.

    Returns:
        str: The cleaned text string.
    """
    if not isinstance(text_to_clean, str):
        logging.warning("TTS_UTILS: _clean_text_for_speech received non-string input.")
        return ""
        
    text = text_to_clean
    # Remove Markdown bold/italic markers (*, _, also multiple like **, __, ***, ___)
    text = re.sub(r'(?<!\\)(\*|_){1,3}(.+?)(?<!\\)\1{1,3}', r'\2', text) # More robust removal
    # Keep only link text from Markdown links [text](url)
    text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
    # Keep only code content from Markdown code blocks/inline code (`code` or ```code```)
    text = re.sub(r'`{1,3}(.*?)`{1,3}', r'\1', text, flags=re.DOTALL)
    # Decode HTML entities (e.g., & -> &, < -> <)
    text = html.unescape(text)
    # Replace full URLs with the word "link" to avoid reading long URLs.
    text = re.sub(r'http[s]?://\S+', 'link', text)
    # Remove or replace other characters/patterns that are bad for TTS as needed
    # e.g., text = text.replace("#", " hashtag ")
    return text.strip()

def _tts_worker(text_to_speak: str, stop_event: threading.Event):
    """
    The actual worker function that runs in a separate thread for TTS.
    It uses the initialized _tts_engine to speak the cleaned text.
    This function blocks until speech is finished or `_tts_engine.stop()` is called
    (triggered by `stop_event` or direct `stop_speaking` call).

    Args:
        text_to_speak (str): The text to be spoken.
        stop_event (threading.Event): An event to signal this worker to stop.
                                      While pyttsx3's stop() is the primary mechanism,
                                      this event can be used for cooperative cancellation.
    """
    if not _tts_engine:
        logging.warning("TTS_UTILS: TTS worker cannot speak, engine not initialized.")
        return

    try:
        cleaned_text = _clean_text_for_speech(text_to_speak)
        if not cleaned_text:
            logging.info("TTS_UTILS: TTS worker: No text to speak after cleaning.")
            return

        logging.debug(f"TTS_UTILS: TTS worker starting to speak: '{cleaned_text[:70]}...'")
        _tts_engine.say(cleaned_text)
        _tts_engine.runAndWait()  # This blocks until speech is done or stop() is called.
                                  # The stop_event is more for external check before starting long operations.
        
        if stop_event.is_set():
            logging.debug("TTS_UTILS: TTS worker finished speaking but stop_event was set during speech.")
        else:
            logging.debug("TTS_UTILS: TTS worker finished speaking normally.")
            
    except RuntimeError as e:
        # This can happen if stop() is called while the engine is in certain states,
        # or if the engine loop is interrupted.
        logging.warning(f"TTS_UTILS: TTS worker runtime error during speech: {e}")
    except Exception as e:
        logging.error(f"TTS_UTILS: TTS worker unexpected error: {e}", exc_info=True)
    finally:
        # Clear the stop event if this worker was responsible for it,
        # or let the caller manage it if it's a shared event.
        # For now, assuming this worker clears its own completion signal.
        if stop_event.is_set(): # If it was told to stop
            pass # The event is already set, indicating it was stopped.
        # stop_event.clear() # Let the caller (speak_text/stop_speaking) manage the event state primarily.
        logging.debug("TTS_UTILS: TTS worker thread finished.")


def speak_text(text: str, force_new: bool = True) -> bool:
    """
    Speaks the given text using the TTS engine in a separate thread.

    By default (`force_new=True`), it stops any currently speaking text
    before starting the new one.

    Args:
        text (str): The text to speak.
        force_new (bool): If True (default), stops current speech and starts new.
                          If False, this implementation currently still stops previous speech.
                          A more complex queueing system would be needed to truly allow
                          non-forced additions if already speaking.

    Returns:
        bool: True if speech was initiated, False if the TTS engine is not available
              or text is empty.
    """
    global _tts_thread, _tts_stop_event, _tts_engine

    if not _tts_engine:
        if not initialize_tts(): # Attempt to initialize if not already
            logging.error("TTS_UTILS: Cannot speak text, TTS engine failed to initialize.")
            return False
        # Check again after attempt
        if not _tts_engine:
             logging.error("TTS_UTILS: TTS engine still not available after init attempt.")
             return False
        
    if not text or not text.strip():
        logging.info("TTS_UTILS: No text provided to speak_text function.")
        return True # No action needed, considered successful in not failing.

    if force_new:
        stop_speaking() # Stop any previously ongoing speech.

    # _tts_stop_event.clear() # Ensure stop event is clear before starting new speech.
    # This is now handled more carefully in stop_speaking and the worker.
    # If stop_speaking was effective, the event should be clear or the old thread gone.

    _tts_thread = threading.Thread(target=_tts_worker, args=(text, _tts_stop_event), daemon=True)
    _tts_thread.start()
    logging.debug(f"TTS_UTILS: Initiated speech for: '{text[:70]}...'")
    return True

def stop_speaking():
    """
    Stops any currently playing speech.

    It signals the TTS worker thread to stop and commands the TTS engine to stop.
    It then waits for the TTS thread to terminate.
    """
    global _tts_thread, _tts_stop_event, _tts_engine
    
    if not _tts_engine:
        logging.debug("TTS_UTILS: stop_speaking called but TTS engine not initialized.")
        return

    if _tts_thread and _tts_thread.is_alive():
        logging.debug("TTS_UTILS: Attempting to stop ongoing speech...")
        _tts_stop_event.set()  # Signal the worker thread that a stop is requested.
        
        try:
            _tts_engine.stop()  # Command the engine to stop its current utterance.
                                # This might be blocking or raise an error on some platforms/states.
        except RuntimeError as e:
            logging.warning(f"TTS_UTILS: RuntimeError while calling _tts_engine.stop(): {e}")
        except Exception as e_stop: # Catch other potential errors from engine.stop()
            logging.error(f"TTS_UTILS: Error calling _tts_engine.stop(): {e_stop}", exc_info=True)

        _tts_thread.join(timeout=1.5)  # Wait a bit longer for the thread to finish.
        if _tts_thread.is_alive():
            logging.warning("TTS_UTILS: TTS thread did not terminate in the expected time after stop signal.")
        else:
            logging.debug("TTS_UTILS: TTS thread terminated.")
        _tts_thread = None # Clear the thread reference
    else:
        logging.debug("TTS_UTILS: No active TTS thread to stop.")
    
    # Always clear the event after attempting a stop, so it's ready for the next speak_text call.
    _tts_stop_event.clear()


def is_speaking() -> bool:
    """
    Checks if the TTS is currently active (i.e., a speech thread is alive).

    Returns:
        bool: True if speaking, False otherwise.
    """
    return _tts_thread is not None and _tts_thread.is_alive()

# --- Example Usage for Direct Testing of this Module ---
if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG, 
        format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s'
    )
    
    print("--- TTS Utils Test Script ---")
    if initialize_tts():
        print("\n[Test 1: Basic Speech]")
        speak_text("Hello, this is a test of the Text to Speech utility.")
        # Wait for speech to likely finish for this simple test case
        while is_speaking():
            time.sleep(0.2)
        print("  Test 1 speech finished.")

        print("\n[Test 2: Interrupting Speech]")
        speak_text("This is a longer message that we are going to try to interrupt very soon.")
        time.sleep(2.5) # Let it speak for a bit
        if is_speaking():
            print("  Attempting to interrupt with a new message...")
            speak_text("Interruption successful! This is the new message.")
            while is_speaking():
                time.sleep(0.2)
            print("  Test 2 speech finished.")
        else:
            print("  Test 2: First message finished too quickly to test interruption with new speech.")

        print("\n[Test 3: Explicit Stop]")
        speak_text("This message will be stopped explicitly using the stop speaking function.")
        time.sleep(2)
        if is_speaking():
            print("  Attempting to stop speech explicitly...")
            stop_speaking()
            if not is_speaking():
                print("  Speech successfully stopped explicitly.")
            else:
                print("  Speech did not stop as expected after explicit stop call.")
        else:
            print("  Test 3: Message finished before explicit stop could be tested.")
        
        print("\n[Test 4: Speaking Markdown/HTML like text]")
        speak_text("This is **bold** and _italic_ and `code`. Check [this link](http://example.com) or <html> tags.")
        while is_speaking():
            time.sleep(0.2)
        print("  Test 4 speech finished (check console for cleaned text in DEBUG logs).")

        print("\n--- TTS Utils Test Script Finished ---")
    else:
        print("TTS Test Script Failed: Could not initialize TTS engine.")