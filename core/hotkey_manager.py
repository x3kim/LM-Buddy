# core/hotkey_manager.py
"""
Manages the global hotkey listener for the application.

This module uses the 'keyboard' library to listen for a specific hotkey
combination defined in the application's configuration. When the hotkey
is pressed, a registered callback function is executed.

The listener runs in a separate daemon thread to avoid blocking the main
application. It also includes a debounce mechanism to prevent multiple
triggers from a single long key press.
"""
import threading
import time
import keyboard # type: ignore # Assuming 'keyboard' might not have perfect stubs
import logging
import typing # For type hinting

# Relative import for configuration access
from . import config_manager

class HotkeyManager:
    """
    Manages the lifecycle of a global hotkey listener.
    """
    def __init__(self, hotkey_callback: typing.Callable[[], None], app_stop_event: threading.Event):
        """
        Initializes the HotkeyManager.

        Args:
            hotkey_callback (typing.Callable[[], None]): 
                The function to be called when the hotkey is detected.
                This callback should be non-blocking or run its own tasks in a thread.
            app_stop_event (threading.Event): 
                An event that signals the application is shutting down. The listener
                thread will monitor this event to terminate itself gracefully.
        """
        if not callable(hotkey_callback):
            raise ValueError("hotkey_callback must be a callable function.")
        
        self.hotkey_callback = hotkey_callback
        self.app_stop_event = app_stop_event
        
        self._listener_thread: typing.Optional[threading.Thread] = None
        self._hotkey_str: typing.Optional[str] = None # Loaded from config
        self._is_valid_hotkey: bool = False # Flag to indicate if current _hotkey_str is valid

        # Debounce parameters to prevent multiple triggers for a single press
        self._debounce_time: float = 1.2  # Seconds
        self._last_pressed_time: float = 0.0

        self.load_hotkey_from_config() # Initial load and validation

    def load_hotkey_from_config(self) -> typing.Optional[str]:
        """
        Loads the hotkey string from the application configuration and validates it.
        Updates internal state `_hotkey_str` and `_is_valid_hotkey`.

        Returns:
            str or None: The loaded and validated hotkey string, or None if invalid.
        """
        self._hotkey_str = config_manager.get_config_value('hotkey', 'ctrl+shift+f')
        self._is_valid_hotkey = False # Assume invalid until successfully parsed
        
        if not self._hotkey_str or not isinstance(self._hotkey_str, str):
            logging.error("HOTKEY_MANAGER: Hotkey string is missing or not a string in configuration.")
            self._hotkey_str = None # Ensure it's None if invalid type
            return None

        try:
            keyboard.parse_hotkey(self._hotkey_str) # Validate the hotkey string format
            self._is_valid_hotkey = True
            logging.info(f"HOTKEY_MANAGER: Hotkey loaded and validated: '{self._hotkey_str}'")
        except ValueError as e:
            logging.error(f"HOTKEY_MANAGER: Invalid hotkey string '{self._hotkey_str}' in configuration: {e}. Listener will not function correctly.")
            self._hotkey_str = None # Mark as None to prevent listener from using invalid key
        return self._hotkey_str

    def _listener_worker(self):
        """
        The worker function for the hotkey listener thread.
        Continuously checks if the configured hotkey is pressed until
        the `app_stop_event` is set.
        """
        if not self._is_valid_hotkey or not self._hotkey_str:
            logging.error("HOTKEY_MANAGER: Listener thread cannot start, no valid hotkey is configured.")
            return

        logging.info(f"HOTKEY_MANAGER: Listener thread started for hotkey '{self._hotkey_str}'. Monitoring stop event.")
        while not self.app_stop_event.is_set():
            try:
                if keyboard.is_pressed(self._hotkey_str):
                    current_time = time.time()
                    if current_time - self._last_pressed_time > self._debounce_time:
                        self._last_pressed_time = current_time
                        logging.info(f"HOTKEY_MANAGER: Hotkey '{self._hotkey_str}' detected.")
                        
                        # Execute the callback
                        if self.hotkey_callback:
                            try:
                                # It's generally safer to run the callback in its own thread
                                # if it might perform blocking operations (like GUI updates or file I/O).
                                # The callback itself should be designed to handle this (e.g., put tasks on a queue).
                                callback_thread = threading.Thread(target=self.hotkey_callback, daemon=True)
                                callback_thread.start()
                            except Exception as e_cb:
                                logging.error(f"HOTKEY_MANAGER: Error executing hotkey callback: {e_cb}", exc_info=True)
                
                # Adjust sleep time for responsiveness vs CPU usage.
                # 0.05 seconds = 20 checks per second.
                time.sleep(0.05)
            except Exception as e: 
                logging.error(f"HOTKEY_MANAGER: Error in listener worker loop for '{self._hotkey_str}': {e}", exc_info=True)
                # Handle critical errors that might require stopping the listener
                if isinstance(e, ImportError) or "hook" in str(e).lower() or "permissions" in str(e).lower():
                    logging.critical("HOTKEY_MANAGER: Critical error in keyboard library (permissions/backend issue?). Stopping listener thread.")
                    # Optionally, could try to inform the main app via a status update if a mechanism exists.
                    break # Exit the loop, effectively stopping this listener thread.
                time.sleep(1) # Wait a bit before continuing after a non-critical error.
        
        logging.info(f"HOTKEY_MANAGER: Listener thread for '{self._hotkey_str}' has been stopped.")

    def start_listener(self) -> bool:
        """
        Starts the hotkey listener thread if it's not already running and a valid hotkey is configured.

        Returns:
            bool: True if the listener was started or is already running, False otherwise.
        """
        if not self._is_valid_hotkey or not self._hotkey_str:
            logging.warning("HOTKEY_MANAGER: Cannot start listener, no valid hotkey configured.")
            return False
            
        if self._listener_thread and self._listener_thread.is_alive():
            logging.info("HOTKEY_MANAGER: Listener thread is already running.")
            return True

        # Ensure the latest hotkey from config is used, in case it changed
        # and start_listener is called again (e.g., by the engine).
        current_hotkey_in_config = self.load_hotkey_from_config()
        if not self._is_valid_hotkey or not current_hotkey_in_config:
            logging.error("HOTKEY_MANAGER: Cannot start listener, hotkey became invalid or missing after config reload.")
            return False

        self._listener_thread = threading.Thread(target=self._listener_worker, daemon=True)
        self._listener_thread.name = f"HotkeyListenerThread-{self._hotkey_str}" # Assign a name for easier debugging
        try:
            self._listener_thread.start()
            logging.info(f"HOTKEY_MANAGER: Listener thread successfully started for '{self._hotkey_str}'.")
            return True
        except Exception as e_start:
            logging.error(f"HOTKEY_MANAGER: Failed to start listener thread: {e_start}", exc_info=True)
            self._listener_thread = None
            return False

    def stop_listener(self):
        """
        Signals the listener thread to stop (by relying on the external `app_stop_event`)
        and waits for it to join. This method is typically called during application shutdown.
        """
        logging.info("HOTKEY_MANAGER: stop_listener called. Listener termination relies on app_stop_event.")
        if self._listener_thread and self._listener_thread.is_alive():
            # The app_stop_event should be set externally to signal shutdown.
            # This join is to ensure this manager waits for its thread.
            self._listener_thread.join(timeout=1.0) 
            if self._listener_thread.is_alive():
                logging.warning("HOTKEY_MANAGER: Listener thread did not terminate within timeout after app_stop_event was expected to be set.")
            else:
                logging.info("HOTKEY_MANAGER: Listener thread joined successfully.")
        else:
            logging.debug("HOTKEY_MANAGER: No active listener thread to stop/join.")
        self._listener_thread = None # Clear the reference

    def update_hotkey_from_config(self) -> typing.Optional[str]:
        """
        Reloads the hotkey from configuration.
        
        Note: This method currently DOES NOT restart the listener thread with the new hotkey
        if it's already running. The running listener will continue with the old hotkey.
        A full application restart or a more sophisticated stop/start mechanism for the
        listener thread itself would be required for dynamic hotkey changes without restart.
        This method primarily updates the internal `_hotkey_str` for future `start_listener` calls.

        Returns:
            str or None: The newly loaded hotkey string, or None if invalid.
        """
        logging.info("HOTKEY_MANAGER: Updating hotkey from configuration.")
        is_currently_running = self._listener_thread and self._listener_thread.is_alive()
        
        old_hotkey = self._hotkey_str
        new_hotkey = self.load_hotkey_from_config() # This updates self._hotkey_str and self._is_valid_hotkey

        if is_currently_running and old_hotkey != new_hotkey:
            logging.warning(
                f"HOTKEY_MANAGER: Hotkey configuration changed from '{old_hotkey}' to '{new_hotkey}'. "
                "The currently active listener is still using the old hotkey. "
                "A full application restart is recommended for the new hotkey to take effect."
            )
            # To truly update dynamically:
            # 1. Need a dedicated stop event for just the listener thread.
            # 2. Signal that event here.
            # 3. Join the old thread.
            # 4. Call self.start_listener() to start a new thread with the new hotkey.
            # This adds complexity, so deferred for now.
        elif not is_currently_running and self._is_valid_hotkey:
             logging.info(f"HOTKEY_MANAGER: Hotkey updated to '{new_hotkey}'. Listener is not running; will use new key on next start.")
        
        return new_hotkey


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG, 
        format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s'
    )

    def example_callback():
        print(f"CALLBACK: Hotkey was pressed at {time.strftime('%X')}!")

    print("--- HotkeyManager Test Script ---")
    # Create a shared stop event for the test application context
    application_should_stop = threading.Event()
    
    print("Initializing HotkeyManager...")
    manager = HotkeyManager(hotkey_callback=example_callback, app_stop_event=application_should_stop)
    
    if manager.start_listener():
        print(f"Listener started for hotkey: '{manager._hotkey_str}'. Press the hotkey to test.")
        print("The listener will run for about 15 seconds or until Ctrl+C is pressed in this console.")
        
        try:
            # Keep the main thread alive to observe the listener
            for i in range(150):  # Approx 15 seconds (150 * 0.1s)
                if application_should_stop.is_set(): # Check if an external signal stops the app
                    break
                time.sleep(0.1)
                if i % 50 == 0 and i > 0: # Every 5 seconds
                    print(f"Test script still running... ({i//10}s elapsed)")
        except KeyboardInterrupt:
            print("\nTest Script: KeyboardInterrupt received.")
        finally:
            print("\nTest Script: Signaling application to stop...")
            application_should_stop.set() # This will signal the listener thread to stop
            manager.stop_listener() # Wait for the listener thread to join
            print("--- HotkeyManager Test Script Finished ---")
    else:
        print("Test Script: Failed to start the hotkey listener (check configuration or logs for errors).")