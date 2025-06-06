# core/ocr_utils.py
"""
Utility functions for Optical Character Recognition (OCR) tasks, including
capturing screenshots of the active window and extracting text from images
using Tesseract OCR.
"""
import logging
import typing
import time
from PIL import Image, ImageGrab # Pillow for image manipulation and screenshots
import pytesseract               # For OCR via Tesseract
import pygetwindow as gw         # To get active window details and manage windows

# Relative import for configuration access
from . import config_manager

def capture_active_window_pil(main_gui_window_title: str = "LM Buddy", 
                              hide_delay_override: typing.Optional[float] = None) -> typing.Tuple[typing.Optional[Image.Image], typing.Optional[str]]:
    """
    Captures the currently active window as a PIL Image object.

    It attempts to find and minimize the main application's GUI window first
    to prevent it from being captured. If the main GUI window cannot be identified
    or if, after minimizing, the main GUI is still the active window, it will try
    to find another suitable window to capture.

    Args:
        main_gui_window_title (str): The title of the main application window.
                                     This is used to identify and minimize the app's own GUI.
                                     It should match the title set in the GUI.
        hide_delay_override (float, optional): If provided, overrides the default delay
                                               (from config: "screenshot_delay")
                                               to wait after minimizing the main GUI.

    Returns:
        tuple: A tuple containing:
            - PIL.Image.Image or None: The captured image if successful, else None.
            - str or None: An error message string if an error occurred, else None.
    """
    overlay_window: typing.Optional[gw.Window] = None
    original_overlay_was_visible = True
    original_overlay_was_minimized = False

    try:
        # Attempt to find the main application window by its title.
        # Note: Title matching can be fragile if the window title is very dynamic.
        app_windows = gw.getWindowsWithTitle(main_gui_window_title)
        if app_windows:
            overlay_window = app_windows[0] # Assume the first match is our window
            original_overlay_was_visible = overlay_window.visible
            original_overlay_was_minimized = overlay_window.isMinimized

            if original_overlay_was_visible and not original_overlay_was_minimized:
                overlay_window.minimize()
                logging.debug(f"OCR_UTILS: Minimized main GUI window: '{main_gui_window_title}'.")
            elif original_overlay_was_visible and original_overlay_was_minimized:
                logging.debug(f"OCR_UTILS: Main GUI window '{main_gui_window_title}' was already minimized.")
            else: # Was not visible
                logging.debug(f"OCR_UTILS: Main GUI window '{main_gui_window_title}' was not visible initially.")
            
            # Wait for the window to minimize and for focus to potentially shift.
            delay = hide_delay_override if hide_delay_override is not None \
                    else float(config_manager.get_config_value("screenshot_delay", 0.5))
            time.sleep(delay)
        else:
            logging.warning(f"OCR_UTILS: Main GUI window with title '{main_gui_window_title}' not found. Proceeding to capture active window.")

        # Get the (hopefully new) active window.
        active_win = gw.getActiveWindow()

        if not active_win:
            logging.warning("OCR_UTILS: No active window found by pygetwindow.")
            return None, "No active window found to capture."

        # Check if the active window is still our application's main GUI.
        if overlay_window and active_win.title == overlay_window.title:
            logging.warning("OCR_UTILS: Main GUI is still the active window. Attempting to find another window.")
            all_windows = gw.getAllWindows()
            # Filter for other visible, non-minimized windows with valid dimensions.
            candidate_windows = [
                w for w in all_windows if w.title != overlay_window.title and \
                w.visible and not w.isMinimized and w.width > 0 and w.height > 0
            ]
            if candidate_windows:
                # Simple heuristic: pick the largest of the candidates.
                active_win = sorted(candidate_windows, key=lambda w_sort: w_sort.width * w_sort.height, reverse=True)[0]
                logging.info(f"OCR_UTILS: Fallback capture to window: '{active_win.title}' (Size: {active_win.size}).")
            else:
                logging.warning("OCR_UTILS: No other suitable window found to capture.")
                return None, "No other suitable window found for capture."
        
        # Final validation of the selected window for capture.
        if not active_win.visible or active_win.isMinimized or active_win.width <= 0 or active_win.height <= 0:
            logging.warning(f"OCR_UTILS: Target window '{active_win.title}' is not suitable for capture (hidden, minimized, or zero-size).")
            return None, f"Cannot capture '{active_win.title}'; window state is unsuitable."

        logging.debug(f"OCR_UTILS: Capturing window '{active_win.title}' with box coordinates: {active_win.box}")
        # Pillow's ImageGrab.grab expects bbox=(left, top, right, bottom).
        # pygetwindow's box is (left, top, width, height).
        left, top, width, height = active_win.left, active_win.top, active_win.width, active_win.height
        bbox_to_capture = (left, top, left + width, top + height)
        
        img = ImageGrab.grab(bbox=bbox_to_capture, all_screens=True) # all_screens=True for multi-monitor
        return img, None

    except Exception as e:
        logging.error(f"OCR_UTILS: Unexpected error during screenshot capture: {e}", exc_info=True)
        return None, f"Screenshot error: {str(e)}"
    finally:
        # Attempt to restore the main GUI window to its original state.
        if overlay_window:
            try:
                # If we minimized it and it wasn't originally minimized.
                if original_overlay_was_visible and not original_overlay_was_minimized and overlay_window.isMinimized:
                    overlay_window.restore()
                    logging.debug(f"OCR_UTILS: Restored main GUI window '{main_gui_window_title}'.")
                # Ensure it's active if it was originally visible and not minimized.
                # This helps bring it back to focus.
                if original_overlay_was_visible and not original_overlay_was_minimized and not overlay_window.isActive:
                    overlay_window.activate() # Try to bring to front
            except Exception as e_restore:
                logging.warning(f"OCR_UTILS: Error restoring main GUI window '{main_gui_window_title}': {e_restore}")
                
def extract_text_from_image(pil_image: typing.Optional[Image.Image]) -> typing.Tuple[typing.Optional[str], typing.Optional[str]]:
    """
    Extracts text from a given PIL Image object using Tesseract OCR.

    Args:
        pil_image (PIL.Image.Image or None): The image to process. If None,
                                             an error is returned.

    Returns:
        tuple: A tuple containing:
            - str or None: The extracted text if successful, else None.
            - str or None: An error message string if an error occurred, else None.
    """
    if not pil_image:
        logging.warning("OCR_UTILS: No image provided to extract_text_from_image.")
        return None, "No image provided for OCR."
    
    try:
        # Get OCR language(s) from configuration.
        ocr_lang = config_manager.get_config_value("ocr_language", "deu")
        if not ocr_lang: # Fallback if config value is empty
            ocr_lang = "deu"
            logging.warning(f"OCR_UTILS: OCR language not configured or empty, defaulting to '{ocr_lang}'.")

        # Perform OCR using pytesseract.
        text = pytesseract.image_to_string(pil_image, lang=ocr_lang).strip()
        
        logging.info(f"OCR_UTILS: Extracted {len(text)} characters using language(s) '{ocr_lang}'.")
        if not text:
            logging.info("OCR_UTILS: OCR process completed, but no text was extracted from the image.")
            # It's not an error, but useful to know. Caller can decide if empty text is an issue.
        return text, None
    except pytesseract.TesseractNotFoundError:
        # This specific error means Tesseract itself is not installed or not in PATH.
        logging.error("OCR_UTILS: Tesseract OCR engine not found. Please ensure Tesseract is installed and its executable is in the system's PATH.")
        return None, "Tesseract OCR not found. Please install it and add to system PATH."
    except Exception as e:
        # Catch any other unexpected errors during OCR processing.
        logging.error(f"OCR_UTILS: An unexpected error occurred during OCR: {e}", exc_info=True)
        return None, f"OCR processing error: {str(e)}"

# --- Example Usage for Direct Testing of this Module ---
if __name__ == '__main__':
    # Configure logging for direct script execution test
    logging.basicConfig(
        level=logging.DEBUG, 
        format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s'
    )
    
    print("--- OCR Utils Test Script ---")
    
    # Test 1: Attempt to capture an active window
    # For this test to be meaningful, ensure another application window (e.g., Notepad)
    # is active and visible when you run this script directly.
    # The script will try to minimize a window with the title "Test Dummy Window"
    # if it exists, to simulate hiding the main app.
    print("\n[Test 1: Window Capture]")
    print("Ensure another application (e.g., Notepad) is the active window.")
    print("The script will attempt to minimize a window titled 'Test Dummy Window' if it exists.")
    
    # Create a dummy window to test the hiding logic (optional, but makes test more realistic)
    dummy_main_app_for_testing: typing.Optional[gw.Window] = None
    DUMMY_TITLE = "Test Dummy Window For OCR Utils"
    try:
        # Try to create a simple Tkinter window to act as the "main app" for hiding test
        import tkinter as tk_test
        root_test = tk_test.Tk()
        root_test.title(DUMMY_TITLE)
        root_test.geometry("150x50+0+0") # Small, out of the way
        root_test.update() # Make it appear
        logging.info(f"Test Script: Created dummy window '{DUMMY_TITLE}'. Please focus another app now.")
        time.sleep(3) # Give user time to focus another app
    except Exception as e_tk_test:
        logging.warning(f"Test Script: Could not create dummy Tkinter window for full test: {e_tk_test}")
        # Test will proceed without trying to hide a specific dummy window.

    captured_image, screenshot_error = capture_active_window_pil(
        main_gui_window_title=DUMMY_TITLE, # Title of the window to try to hide
        hide_delay_override=0.3
    )

    if screenshot_error:
        print(f"  Screenshot Test Error: {screenshot_error}")
    elif captured_image:
        print(f"  Screenshot Test Success! Captured image size: {captured_image.size}")
        # captured_image.show() # Uncomment to display the captured image

        # Test 2: OCR on the captured image
        print("\n[Test 2: OCR on Captured Image]")
        extracted_text, ocr_error_msg = extract_text_from_image(captured_image)
        if ocr_error_msg:
            print(f"  OCR Test Error: {ocr_error_msg}")
        elif extracted_text:
            print(f"  OCR Test Success! Extracted text (first 200 chars):\n---\n{extracted_text[:200]}...\n---")
        else:
            print("  OCR Test: No text extracted from the image, but no explicit error reported.")
    else:
        # This case should ideally not be reached if error handling in capture_active_window_pil is robust.
        print("  Screenshot Test: No image returned and no error message (unexpected).")

    # Test 3: OCR on a non-existent image (to test error handling)
    print("\n[Test 3: OCR on None Image]")
    _, ocr_error_on_none = extract_text_from_image(None)
    if ocr_error_on_none:
        print(f"  OCR on None Test Success (error expected): {ocr_error_on_none}")
    else:
        print("  OCR on None Test Failed (an error was expected).")
        
    if 'root_test' in locals() and root_test:
        root_test.destroy() # Clean up dummy window

    print("\n--- OCR Utils Test Script Finished ---")