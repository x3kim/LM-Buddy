# core/llm_handler.py
"""
Handles all interactions with Language Learning Models (LLMs).

This module is responsible for:
1.  Initializing and providing access to the tokenizer.
2.  Counting tokens for text and API message structures.
3.  Converting images to base64 format suitable for multimodal LLMs.
4.  Constructing API requests for LLMs (supporting streaming).
5.  Sending requests to the configured LLM endpoint.
6.  Processing streamed responses and relaying data (chunks, errors, token counts)
    to a provided GUI queue.
7.  Managing the conversation context history (appending new turns).
"""
import logging
import json
import requests
import base64
from io import BytesIO
from PIL import Image # For image type hinting and conversion
import threading      # For threading.Event type hint
import queue          # For queue.Queue type hint
import typing         # For extensive type hinting

# Relative imports from the same 'core' package
from . import config_manager
from . import message_types as mt 

# --- Tokenizer Setup ---
_llm_tokenizer_instance: typing.Optional[typing.Any] = None # Holds the loaded tokenizer
TRANSFORMERS_AVAILABLE: bool = False # Flag indicating if 'transformers' library is installed

try:
    from transformers import AutoTokenizer
    TRANSFORMERS_AVAILABLE = True
    logging.debug("LLM_HANDLER: 'transformers' library successfully imported.")
except ImportError:
    AutoTokenizer = None # Define for type hinting even if not available
    logging.warning("LLM_HANDLER: 'transformers' library not found. Client-side token counting will be disabled or approximate.")


def initialize_tokenizer() -> bool:
    """
    Initializes the tokenizer based on settings in the configuration file.

    It attempts to load a tokenizer using the 'transformers' library.
    If successful, the tokenizer instance is stored in a module-level variable.
    This function is typically called once at application startup or when the
    tokenizer configuration changes.

    Returns:
        bool: True if the tokenizer was successfully initialized or is already
              initialized, False otherwise (e.g., 'transformers' not found or
              model loading failed).
    """
    global _llm_tokenizer_instance
    if not TRANSFORMERS_AVAILABLE:
        logging.warning("LLM_HANDLER: Cannot initialize tokenizer, 'transformers' library is unavailable.")
        _llm_tokenizer_instance = None
        return False
    
    if _llm_tokenizer_instance is not None:
        logging.debug("LLM_HANDLER: Tokenizer is already initialized.")
        return True

    # Determine tokenizer name: primary from 'tokenizer_model_name', fallback to 'llm_model', then 'gpt2'
    tokenizer_name_to_load = config_manager.get_config_value(
        "tokenizer_model_name", 
        config_manager.get_config_value("llm_model") # Fallback
    )
    if not tokenizer_name_to_load: # Further fallback
        tokenizer_name_to_load = "gpt2" 
        logging.warning(f"LLM_HANDLER: No tokenizer model name found in config, defaulting to '{tokenizer_name_to_load}'.")

    logging.info(f"LLM_HANDLER: Attempting to load tokenizer for: '{tokenizer_name_to_load}'")
    try:
        # trust_remote_code=True may be needed for some custom models from Hugging Face Hub
        assert AutoTokenizer is not None, "AutoTokenizer is None, should not happen if TRANSFORMERS_AVAILABLE is True"
        _llm_tokenizer_instance = AutoTokenizer.from_pretrained(tokenizer_name_to_load, trust_remote_code=True)
        logging.info(f"LLM_HANDLER: Successfully loaded tokenizer for '{tokenizer_name_to_load}'.")
        return True
    except Exception as e:
        logging.error(f"LLM_HANDLER: Error loading tokenizer for '{tokenizer_name_to_load}': {e}", exc_info=True)
        _llm_tokenizer_instance = None
        return False

def get_tokenizer() -> typing.Optional[typing.Any]:
    """
    Returns the current tokenizer instance. Initializes it if not already done.

    Returns:
        typing.Any or None: The loaded tokenizer instance, or None if initialization failed.
    """
    if _llm_tokenizer_instance is None:
        initialize_tokenizer() # Attempt to initialize if called before explicit init
    return _llm_tokenizer_instance

def count_text_tokens(text_to_tokenize: str) -> int:
    """
    Counts the number of tokens in a given string using the loaded tokenizer.
    If the tokenizer is unavailable or an error occurs, it falls back to a
    rough character-based approximation (length / 4).

    Args:
        text_to_tokenize (str): The string for which to count tokens.

    Returns:
        int: The estimated number of tokens. Returns 0 if input is not a string.
    """
    tokenizer = get_tokenizer()
    if tokenizer and isinstance(text_to_tokenize, str):
        try:
            # Some tokenizers might return input_ids, others just a list of token IDs.
            # Taking the length of the encoded output is generally reliable.
            encoded_output = tokenizer.encode(text_to_tokenize)
            return len(encoded_output)
        except Exception as e:
            logging.error(f"LLM_HANDLER: Error tokenizing text with '{tokenizer.__class__.__name__}': {e}. Falling back to char count.")
            return len(text_to_tokenize) // 4 # Approximation
    elif isinstance(text_to_tokenize, str): # Fallback if no tokenizer
        logging.debug("LLM_HANDLER: Tokenizer not available for count_text_tokens, using char count fallback.")
        return len(text_to_tokenize) // 4
    return 0 # Not a string or empty

def count_tokens_for_api_messages(api_messages: typing.List[typing.Dict[str, typing.Any]]) -> int:
    """
    Calculates the total number of tokens for a list of messages structured
    for an LLM API call. It processes text content within messages, including
    multi-part messages. Image tokens are currently NOT counted by this function
    as their cost is highly model-specific.

    Args:
        api_messages (list): A list of message dictionaries, where each dictionary
                             is expected to have a "content" key. The content can
                             be a string or a list of parts (for multimodal).

    Returns:
        int: The total estimated number of text tokens in the messages.
    """
    if not api_messages:
        return 0
    total_tokens = 0
    for message in api_messages:
        content = message.get("content")
        if isinstance(content, str):
            total_tokens += count_text_tokens(content)
        elif isinstance(content, list): # Handles multi-part content arrays
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    total_tokens += count_text_tokens(part.get("text"))
                # Note: Image parts (type "image_url") are not token-counted here.
    return total_tokens

def convert_image_to_base64_str(pil_image: Image.Image, quality: int = 75, max_size_kb: int = 500) -> str:
    """
    Converts a PIL (Pillow) Image object to a base64 encoded data URL string.

    It first attempts to save the image as JPEG for better compression of
    photographic images. If the image has an alpha channel or JPEG conversion fails,
    it falls back to PNG.
    If the image is JPEG and exceeds `max_size_kb`, its quality is iteratively
    reduced until the size constraint is met or a minimum quality is reached.

    Args:
        pil_image (PIL.Image.Image): The image to convert.
        quality (int): Initial JPEG quality (0-100). Default is 75.
        max_size_kb (int): The target maximum size in kilobytes. Default is 500KB.

    Returns:
        str: A data URL string (e.g., "data:image/jpeg;base64,...").
    """
    if not isinstance(pil_image, Image.Image):
        logging.error("LLM_HANDLER: convert_image_to_base64_str received non-PIL.Image object.")
        # Return a placeholder or raise error, depending on desired handling
        return "data:text/plain;base64,ZXJyb3I=" # "error" in base64

    output_buffer = BytesIO()
    is_jpeg = False
    image_format_used = "PNG" # Default if JPEG fails

    try:
        # Try JPEG first. Convert to RGB as JPEG doesn't support alpha.
        rgb_image = pil_image.convert("RGB")
        rgb_image.save(output_buffer, format="JPEG", quality=quality, optimize=True)
        is_jpeg = True
        image_format_used = "JPEG"
        logging.debug(f"LLM_HANDLER: Image initially saved as JPEG with quality {quality}.")
    except Exception as e_jpeg:
        logging.warning(f"LLM_HANDLER: Could not save image as JPEG (e.g., due to alpha channel or other issue: {e_jpeg}). Falling back to PNG.")
        output_buffer = BytesIO() # Reset buffer
        try:
            pil_image.save(output_buffer, format="PNG", optimize=True)
            image_format_used = "PNG"
        except Exception as e_png:
            logging.error(f"LLM_HANDLER: Failed to save image as PNG after JPEG failure: {e_png}", exc_info=True)
            return "data:text/plain;base64,ZXJyb3I=" # "error"

    img_byte_size = output_buffer.tell()
    current_quality = quality # Only relevant if it was JPEG

    # If JPEG and too large, try to reduce quality
    if is_jpeg:
        while img_byte_size > max_size_kb * 1024 and current_quality > 10:
            current_quality -= 10 # Reduce quality by 10
            output_buffer = BytesIO() # Reset buffer for new save attempt
            try:
                # Ensure we use the RGB converted image for JPEG saving
                rgb_image.save(output_buffer, format="JPEG", quality=current_quality, optimize=True)
                img_byte_size = output_buffer.tell()
                logging.debug(f"LLM_HANDLER: Image re-saved as JPEG. Size: {img_byte_size / 1024:.2f} KB, Quality: {current_quality}")
            except Exception as e_reduce:
                logging.error(f"LLM_HANDLER: Error during JPEG quality reduction: {e_reduce}. Using last successful version.")
                break # Stop if reducing quality causes an error
    
    logging.info(f"LLM_HANDLER: Final image size for base64: {img_byte_size / 1024:.2f} KB, Format: {image_format_used}{f', Quality: {current_quality}' if is_jpeg and image_format_used == 'JPEG' else ''}")
    
    base64_encoded_str = base64.b64encode(output_buffer.getvalue()).decode('utf-8')
    
    # Determine mime type based on the format successfully used
    mime_type = f"image/{image_format_used.lower()}"
    return f"data:{mime_type};base64,{base64_encoded_str}"


# --- LLM Communication ---
def stream_llm_response(
        context_history: list,
        current_user_message_parts: list,
        gui_queue: queue.Queue,
        stop_event: threading.Event,
        # Removed ocr_text_buffer_ref and last_image_buffer_ref
        is_direct_question: bool = False,
        specific_action: typing.Optional[str] = None,
        pil_image_obj: typing.Optional[Image.Image] = None, # Image for the current turn, to be stored in history
        target_language: typing.Optional[str] = None
    ):
    """
    Handles the entire process of forming a request, sending it to an LLM,
    and streaming the response back to the GUI via a queue.

    It updates the `context_history` list (passed by reference) with the new
    user message and the LLM's assistant response.

    Args:
        context_history (list): A list of tuples representing the conversation.
                                Each tuple is (role, content_parts_or_string, pil_image_or_None).
                                This list is modified by appending new turns.
        current_user_message_parts (list): A list of content parts for the current
                                           user message (e.g., text, image_url).
        gui_queue (queue.Queue): The queue used to send messages (chunks, tokens,
                                 errors, info) back to the GUI thread.
        stop_event (threading.Event): An event monitored to gracefully interrupt
                                      the streaming process if the application is shutting down.
        is_direct_question (bool): True if the query is a direct text question,
                                   False if it's based on OCR/image analysis.
                                   (This primarily affects how history might be logged or interpreted,
                                   the LLM payload itself is built from current_user_message_parts).
        specific_action (str, optional): A string key indicating a specific predefined
                                         action (e.g., "summarize", "set_context_for_question").
        pil_image_obj (PIL.Image.Image, optional): The raw PIL Image object associated
                                                   with the current user turn, if any. This is
                                                   stored in history for potential later display.
        target_language (str, optional): The target language code if the action is "translate".
    """
    # --- Special handling for "set_context_for_question" ---
    # This action modifies context_history and informs GUI without a full LLM call.
    if specific_action == "set_context_for_question":
        # The current_user_message_parts (containing context description, OCR, image URL)
        # and the pil_image_obj are added to history.
        context_history.append(("user", current_user_message_parts, pil_image_obj if pil_image_obj else None))
        info_msg = "[Context set. Please type your question to the LLM.]"
        context_history.append(("assistant", info_msg, None)) # Assistant turn is just an info message
        
        gui_queue.put({"type": mt.MSG_TYPE_INFO, "content": info_msg})
        # Calculate tokens for the context-setting message itself for UI display
        prompt_tokens = count_tokens_for_api_messages(
            [{"role": "user", "content": current_user_message_parts}]
        )
        gui_queue.put({"type": mt.MSG_TYPE_LLM_PROMPT_TOKENS_UPDATE, "count": prompt_tokens})
        gui_queue.put({ # Send final token counts for this "pseudo" interaction
            "type": mt.MSG_TYPE_LLM_FINAL_TOKEN_COUNTS,
            "prompt_tokens": prompt_tokens, "completion_tokens": 0, "total_tokens": prompt_tokens
        })
        gui_queue.put(None) # Signal end of this sequence to GUI queue processor
        return

    # --- Construct API Payload (Messages for LLM) ---
    api_payload_messages: typing.List[typing.Dict[str, typing.Any]] = []
    
    # System Prompt Handling
    sys_prompt_global = config_manager.get_config_value("system_prompt_global", "").strip()
    sys_prompt_avatar = config_manager.get_config_value("avatar_system_prompt_override", "").strip()
    final_system_prompt = sys_prompt_avatar if sys_prompt_avatar else sys_prompt_global

    # Determine if this is the start of a new logical conversation to include system prompt
    is_new_logical_convo = not context_history or \
                           (len(context_history) > 0 and isinstance(context_history[-1][1], str) and \
                            context_history[-1][1].startswith("[")) # e.g., last was "[Context set...]"

    if final_system_prompt and is_new_logical_convo:
        api_payload_messages.append({"role": "system", "content": final_system_prompt})
        logging.debug(f"LLM_HANDLER: Using system prompt: '{final_system_prompt[:100]}...'")

    # Add existing conversation history to the payload
    for role, hist_content_parts_or_str, hist_pil_image in context_history:
        current_message_api_parts: typing.List[typing.Dict[str, typing.Any]] = []
        if isinstance(hist_content_parts_or_str, list): # Content is already in API parts format
            current_message_api_parts.extend(hist_content_parts_or_str)
        elif isinstance(hist_content_parts_or_str, str): # Simple string content
            current_message_api_parts.append({"type": "text", "text": hist_content_parts_or_str})
        
        # If there's a PIL image associated with this user history turn, add its base64 representation
        # if not already present in hist_content_parts_or_str (e.g. from a vision call).
        if role == "user" and hist_pil_image:
            if not any(p.get("type") == "image_url" for p in current_message_api_parts):
                base64_img_hist = convert_image_to_base64_str(hist_pil_image)
                current_message_api_parts.append({"type": "image_url", "image_url": {"url": base64_img_hist}})
        
        if current_message_api_parts: # Only add if there's valid content
            api_payload_messages.append({"role": role, "content": current_message_api_parts})

    # Add the current user's message (already in parts format)
    if current_user_message_parts:
        api_payload_messages.append({"role": "user", "content": current_user_message_parts})
    else:
        # This should ideally be caught by the caller (Engine)
        logging.error("LLM_HANDLER: stream_llm_response called with empty current_user_message_parts.")
        gui_queue.put({"type": mt.MSG_TYPE_ERROR, "content": "Internal error: No user message content to send."})
        gui_queue.put(None); return # Signal end of this failed sequence

    # Calculate and send initial prompt token count to GUI
    prompt_tokens = count_tokens_for_api_messages(api_payload_messages)
    gui_queue.put({"type": mt.MSG_TYPE_LLM_PROMPT_TOKENS_UPDATE, "count": prompt_tokens})
    logging.debug(f"LLM_HANDLER: Sending {len(api_payload_messages)} messages to LLM. Prompt tokens: {prompt_tokens}.")
    if logging.getLogger().isEnabledFor(logging.DEBUG) and api_payload_messages:
         logging.debug(f"LLM_HANDLER: Last user message content being sent: {api_payload_messages[-1]['content']}")


    # --- Perform LLM API Call ---
    full_response_text = ""
    completion_tokens_calculated = 0
    server_reported_total_tokens = 0 # For potential server-side token count in stream

    try:
        llm_model_name = config_manager.get_config_value("llm_model")
        request_payload = {
            "model": llm_model_name,
            "messages": api_payload_messages,
            "temperature": float(config_manager.get_config_value("temperature", 0.3)),
            "max_tokens": int(config_manager.get_config_value("max_tokens", 4096)),
            "stream": True
        }
        
        request_headers = {"Content-Type": "application/json"}
        provider = config_manager.get_config_value("llm_provider", "custom")
        api_key = config_manager.get_config_value("llm_api_key", "")
        llm_endpoint_url = config_manager.get_config_value("llm_endpoint")

        # Provider-specific header/endpoint adjustments
        if provider == "openai" and api_key:
            request_headers["Authorization"] = f"Bearer {api_key}"
            # OpenAI standard endpoint, but config might override if user wants to use a proxy
            # if not llm_endpoint_url or "127.0.0.1" in llm_endpoint_url or "localhost" in llm_endpoint_url:
            #    llm_endpoint_url = "https://api.openai.com/v1/chat/completions"
        # Add elif blocks for other providers like "google_vertexai", "anthropic"
        
        if not llm_endpoint_url:
            raise ValueError(f"LLM endpoint URL is not configured for provider '{provider}'.")

        logging.info(f"LLM_HANDLER: Sending request to LLM at '{llm_endpoint_url}' for model '{llm_model_name}'.")
        
        response = requests.post(
            llm_endpoint_url, headers=request_headers, json=request_payload,
            timeout=int(config_manager.get_config_value("llm_request_timeout", 180)),
            stream=True
        )
        response.raise_for_status() # Raises HTTPError for bad responses (4XX or 5XX)

        for line in response.iter_lines():
            if stop_event.is_set(): # Check if application is trying to shut down
                logging.info("LLM_HANDLER: LLM stream processing interrupted by application stop_event.")
                break 
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith("data: "):
                    json_data_str = decoded_line[len("data: "):].strip()
                    if json_data_str == "[DONE]":
                        logging.debug("LLM_HANDLER: Stream [DONE] marker received.")
                        break
                    try:
                        json_data = json.loads(json_data_str)
                        if 'choices' in json_data and len(json_data['choices']) > 0:
                            delta = json_data['choices'][0].get('delta', {})
                            content_chunk = delta.get('content')
                            if content_chunk: # Ensure there's actual content
                                full_response_text += content_chunk
                                chunk_tok_count = count_text_tokens(content_chunk)
                                completion_tokens_calculated += chunk_tok_count
                                gui_queue.put({
                                    "type": mt.MSG_TYPE_LLM_CHUNK,
                                    "content": content_chunk,
                                    "completion_tokens_live": completion_tokens_calculated
                                })
                        # Some streaming APIs might include 'usage' data in non-delta messages or final message
                        if 'usage' in json_data:
                             usage_stats = json_data.get("usage", {})
                             current_server_total = usage_stats.get("total_tokens", 0)
                             if current_server_total > server_reported_total_tokens: # Keep the highest value seen
                                 server_reported_total_tokens = current_server_total
                             logging.debug(f"LLM_HANDLER: Mid-stream server usage stats: {usage_stats}")
                    except json.JSONDecodeError:
                        logging.warning(f"LLM_HANDLER: Could not decode JSON from stream data: '{json_data_str}'")
        
        if stop_event.is_set(): # If loop was broken by stop_event
             gui_queue.put({"type": mt.MSG_TYPE_INFO, "content": "LLM stream was cancelled by application."})
             gui_queue.put(None); return # Signal end and exit

        if not full_response_text.strip() and response.status_code == 200:
            logging.warning("LLM_HANDLER: LLM stream finished, but no textual content was aggregated from chunks.")
            # Optionally send an info message to GUI:
            # gui_queue.put({"type": mt.MSG_TYPE_INFO, "content": "[LLM produced no text output for this query.]"})

        # --- Finalize and Update History ---
        # Append the user's message (current_user_message_parts) and the associated raw PIL image (pil_image_obj)
        # along with the LLM's full response to the shared context_history list.
        context_history.append(("user", current_user_message_parts, pil_image_obj if pil_image_obj else None))
        context_history.append(("assistant", full_response_text, None)) # Assistant response has no image

        # Send final token counts to GUI
        calculated_total_tokens = prompt_tokens + completion_tokens_calculated
        # Use server-reported total if it's available and seems more accurate (e.g., includes image tokens not counted client-side)
        # This is a heuristic; some models might not report total_tokens or report it differently.
        final_tokens_to_report = server_reported_total_tokens \
            if server_reported_total_tokens >= calculated_total_tokens \
            else calculated_total_tokens
        
        logging.info(f"LLM_HANDLER: Stream ended. Client Tokens - P:{prompt_tokens}, C:{completion_tokens_calculated}, Total:{calculated_total_tokens}. Server Reported Total (if any): {server_reported_total_tokens}. Reporting to GUI: {final_tokens_to_report}.")
        gui_queue.put({
            "type": mt.MSG_TYPE_LLM_FINAL_TOKEN_COUNTS,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens_calculated,
            "total_tokens": final_tokens_to_report
        })
        # Optionally, send the full response again if GUI needs it explicitly beyond chunks
        gui_queue.put({"type": mt.MSG_TYPE_LLM_FULL_RESPONSE, "content": full_response_text})

    except requests.exceptions.Timeout:
        timeout_val = config_manager.get_config_value('llm_request_timeout', 180)
        logging.error(f"LLM_HANDLER: LLM request timed out after {timeout_val}s.", exc_info=True)
        gui_queue.put({"type": mt.MSG_TYPE_ERROR, "content": f"LLM request timed out ({timeout_val}s)." })
        gui_queue.put({"type": mt.MSG_TYPE_LLM_FINAL_TOKEN_COUNTS, "prompt_tokens": prompt_tokens, "completion_tokens":0, "total_tokens":prompt_tokens})
    except requests.exceptions.RequestException as e: # Covers ConnectionError, HTTPError, etc.
        logging.error(f"LLM_HANDLER: LLM Connection/Request error: {e}", exc_info=True)
        gui_queue.put({"type": mt.MSG_TYPE_ERROR, "content": f"LLM Connection Error: {str(e)[:150]}"}) # Show more of error
        gui_queue.put({"type": mt.MSG_TYPE_LLM_FINAL_TOKEN_COUNTS, "prompt_tokens": prompt_tokens, "completion_tokens":0, "total_tokens":prompt_tokens})
    except Exception as e: # Catch-all for other unexpected errors
        logging.error(f"LLM_HANDLER: General error during LLM communication: {e}", exc_info=True)
        gui_queue.put({"type": mt.MSG_TYPE_ERROR, "content": f"Unexpected LLM Error: {str(e)[:150]}"})
        gui_queue.put({"type": mt.MSG_TYPE_LLM_FINAL_TOKEN_COUNTS, "prompt_tokens": prompt_tokens, "completion_tokens":0, "total_tokens":prompt_tokens})
    finally:
        gui_queue.put(None) # Crucial: Signal to the GUI queue processor that this request sequence is done.

# --- Example Usage for Direct Testing of this Module ---
if __name__ == '__main__':
    logging.basicConfig(
        level=logging.DEBUG, 
        format='%(asctime)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s'
    )
    
    print("--- LLM Handler Test Script ---")
    if initialize_tokenizer():
        print(f"LLM_HANDLER Test: Tokenizer initialized. Tokens for 'Hello world!': {count_text_tokens('Hello world!')}")
        
        # Test API message token counting
        test_api_msgs = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of Pythonland?"}
        ]
        print(f"LLM_HANDLER Test: Tokens for sample API messages: {count_tokens_for_api_messages(test_api_msgs)}")
    else:
        print("LLM_HANDLER Test: Tokenizer initialization FAILED.")

    # Test image conversion
    try:
        dummy_img = Image.new('RGB', (60, 30), color = 'lightblue')
        b64 = convert_image_to_base64_str(dummy_img, max_size_kb=5) # Test with small max size
        print(f"\nLLM_HANDLER Test: Dummy image to base64 (first 70 chars): {b64[:70]}...")
        print(f"LLM_HANDLER Test: Base64 string length: {len(b64)}")
    except Exception as e_img:
        print(f"LLM_HANDLER Test: Error during image conversion test: {e_img}")
    
    # To test stream_llm_response, you would need a mock LLM server or a live endpoint.
    # Example (requires a running local LLM endpoint like Ollama or LM Studio):
    # print("\nLLM_HANDLER Test: Attempting to stream from a local LLM (ensure one is running)...")
    # test_context_history = []
    # test_user_parts = [{"type": "text", "text": "Tell me a very short story about a fox."}]
    # test_gui_queue = queue.Queue()
    # test_stop_event = threading.Event()
    #
    # # Run in a thread because stream_llm_response is blocking until stream ends
    # test_thread = threading.Thread(target=stream_llm_response, args=(
    #     test_context_history, test_user_parts, test_gui_queue, test_stop_event, True
    # ))
    # test_thread.start()
    #
    # print("LLM_HANDLER Test: Waiting for LLM stream results (up to 10s)...")
    # start_time = time.time()
    # while time.time() - start_time < 10: # Timeout for test
    #     try:
    #         msg = test_gui_queue.get(timeout=0.5)
    #         print(f"  LLM_HANDLER Test Queue: {msg}")
    #         if msg is None:
    #             print("  LLM_HANDLER Test: End of stream sentinel received.")
    #             break
    #         test_gui_queue.task_done()
    #     except queue.Empty:
    #         if not test_thread.is_alive():
    #             print("  LLM_HANDLER Test: LLM thread finished but no sentinel? Or queue processed too fast.")
    #             break
    #     except Exception as e_q:
    #         print(f"  LLM_HANDLER Test Queue Error: {e_q}")
    #         break
    #
    # if test_thread.is_alive():
    #     print("LLM_HANDLER Test: LLM thread still alive, signaling stop...")
    #     test_stop_event.set()
    #     test_thread.join(timeout=2)
    #     if test_thread.is_alive():
    #         print("LLM_HANDLER Test: LLM thread did not stop in time.")
    #
    # print(f"LLM_HANDLER Test: Final context history: {test_context_history}")
    print("\n--- LLM Handler Test Script Finished ---")