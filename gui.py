# gui.py
"""
Main GUI module for the LM Buddy application (Classic Window Skin).

This module defines the LMBuddyOverlay class, which creates and manages
the main application window using Tkinter. It interacts with the
LMBuddyCoreEngine for all core functionalities like LLM communication,
OCR, TTS, and hotkey management. Communication with the engine for
asynchronous operations is handled via a queue.
"""
import functools # Added for functools.partial
import tkinter as tk
from tkinter import scrolledtext, messagebox, simpledialog, font as tkFont, Listbox, END, Frame, Menu, Toplevel, Label
from PIL import Image, ImageTk # For displaying images in GUI (e.g., from history)
import threading
import time
# import keyboard # type: ignore # Only for local validation in change_hotkey if desired
import html
import pyperclip
# import json # Not directly needed here
import logging
import os
from tkhtmlview import HTMLLabel # For rendering HTML/Markdown content
import markdown2 # For Markdown to HTML conversion
import queue      # For the GUI update queue
import typing     # For type hinting

# Import from our new core package
from core import config_manager 
from core.engine import LMBuddyCoreEngine
from core import message_types as mt # For interpreting messages from the engine

# --- Global Application Stop Event ---
APP_STOP_EVENT = threading.Event()
"""
A global threading.Event that signals all parts of the application (especially
long-running threads like the hotkey listener or LLM streaming) to shut down.
It's set by the GUI's on_closing method.
"""

# --- Logging Setup ---
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(filename)s:%(lineno)d - %(message)s'
)

# --- Tooltip Class ---
class ToolTip:
    """Simple tooltip class for Tkinter widgets."""
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tooltip_window: typing.Optional[tk.Toplevel] = None
        self.id: typing.Optional[str] = None # Stores the .after() id
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.widget.bind("<ButtonPress>", self.leave) # Hide on click too

    def enter(self, event=None): self.schedule()
    def leave(self, event=None): self.unschedule(); self.hide_tooltip()
    def schedule(self): self.unschedule(); self.id = self.widget.after(700, self.show_tooltip) # 700ms delay
    
    def unschedule(self):
        scheduled_id = self.id
        self.id = None
        if scheduled_id:
            self.widget.after_cancel(scheduled_id)

    def show_tooltip(self, event=None):
        if self.tooltip_window: return
        x, y = self.widget.winfo_rootx() + 20, self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tooltip_window = tk.Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True) # No window decorations
        self.tooltip_window.attributes("-topmost", True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(self.tooltip_window, text=self.text, justify='left',
                       background="#ffffe0", relief='solid', borderwidth=1,
                       wraplength=300, font=("tahoma", "8", "normal"))
        lbl.pack(ipadx=2, ipady=2)

    def hide_tooltip(self):
        if self.tooltip_window:
            self.tooltip_window.destroy()
        self.tooltip_window = None

# --- Markdown to HTML Conversion ---
def markdown_to_html_custom(md_text: str) -> str:
    """Converts Markdown text to HTML using custom extras."""
    return markdown2.markdown(md_text, extras=[
        "fenced-code-blocks", "tables", "nofollow", "cuddled-lists",
        "break-on-newline", "code-friendly", "smarty-pants"
    ])

# --- Main Application GUI Class (Classic Window) ---
class LMBuddyOverlay(tk.Tk):
    """
    The main application window for LM Buddy (Classic Skin).
    It provides UI elements for interacting with the LMBuddyCoreEngine.
    """

    # --- Method Definitions (Callbacks & UI Updaters are now defined before __init__) ---
    def on_history_motion(self, event):
        """Handles mouse motion over the history listbox to show tooltips."""
        if self.history_tooltip_active:
            self.history_tooltip_active.destroy()
            self.history_tooltip_active = None
        try:
            idx = self.hist_lb.nearest(event.y)
            box = self.hist_lb.bbox(idx)
            if not(box and 0 <= idx < self.hist_lb.size() and box[1] <= event.y < box[1] + box[3]):
                return
            
            role, content_or_parts, _ = self.engine.context_history[idx]
            tooltip_text = ""
            if isinstance(content_or_parts, str):
                tooltip_text = content_or_parts
            elif isinstance(content_or_parts, list): 
                text_parts = [p["text"] for p in content_or_parts if p.get("type") == "text" and p.get("text")]
                tooltip_text = "\n".join(text_parts)
                if any(p.get("type") == "image_url" for p in content_or_parts): 
                    tooltip_text = "[Image Data Sent]\n" + tooltip_text
            
            if role == "user": 
                action_desc = tooltip_text.split("Extrahierter Text:")[0].split("Der folgende Inhalt")[0].strip()
                if len(action_desc) < 100 and action_desc and action_desc != tooltip_text: 
                    tooltip_text = f"Action: {action_desc}\n(Full details in main view)"
            
            if len(tooltip_text) > 400: tooltip_text = tooltip_text[:400] + "..."
            
            x_pos, y_pos = event.x_root + 15, event.y_root + 10
            self.history_tooltip_active = Toplevel(self.hist_lb)
            self.history_tooltip_active.wm_overrideredirect(True)
            self.history_tooltip_active.attributes("-topmost", True)
            Label(self.history_tooltip_active, text=tooltip_text, justify='left', 
                  bg="#ffffe0", relief='solid', bd=1, wraplength=400, 
                  font=("tahoma", "8", "normal")).pack(ipadx=2, ipady=2)
            self.history_tooltip_active.update_idletasks()
            self.history_tooltip_active.wm_geometry(f"+{x_pos}+{y_pos}")
        except tk.TclError: pass 
        except Exception as e: logging.error(f"GUI: History tooltip error: {e}", exc_info=True)

    def on_history_leave(self,event):
        """Hides the history tooltip when the mouse leaves an item."""
        if self.history_tooltip_active:
            self.history_tooltip_active.destroy()
            self.history_tooltip_active = None

    def on_history_select(self,event):
        """Handles selection of an item in the history listbox."""
        selection = event.widget.curselection()
        if selection:
            index = selection[0]
            role, content_or_parts, img_obj = self.engine.context_history[index]
            display_text = ""
            if isinstance(content_or_parts, str): display_text = content_or_parts
            elif isinstance(content_or_parts, list):
                text_parts = [p["text"] for p in content_or_parts if p.get("type") == "text" and p.get("text")]
                display_text = "\n\n".join(text_parts)
            
            self.display_message_in_gui(display_text, 
                                        is_raw_text=(role=="assistant" and display_text.startswith("[")), 
                                        update_history=False)
            if img_obj and role == "user":
                self.show_image_from_history(img_obj)

    def update_context_status_display(self): # KORRIGIERT: Definition ist jetzt auf Klassenebene
        """Updates the status bar with context length and token counts."""
        num_total_messages = len(self.engine.context_history)
        dialog_turns = num_total_messages // 2
        
        total_calc_tokens = self.current_prompt_tokens + self.current_completion_tokens
        token_info_str = f"P: {self.current_prompt_tokens}, C: {self.current_completion_tokens} = Total: {total_calc_tokens}"
        
        if not self.engine.get_config_value("tokenizer_model_name"):
            token_info_str = "Tokens: N/A (Tokenizer not configured)"
        
        image_in_history = any(role == 'user' and img is not None for role, _, img in self.engine.context_history)
        if image_in_history: token_info_str += " (+Img in hist.)"
        
        status_text = f"Context: {dialog_turns} Turns ({num_total_messages} Msgs) / {token_info_str}"
        
        warn_msg_thresh = int(self.engine.get_config_value("max_context_messages", 30))
        warn_token_thresh = int(self.engine.get_config_value("max_context_tokens_warning", 6000))
        
        is_warning = (num_total_messages > warn_msg_thresh) or \
                     (self.engine.get_config_value("tokenizer_model_name") and total_calc_tokens > warn_token_thresh)
        
        self.stat_lbl.config(text=f"{status_text}{' - LONG!' if is_warning else ''}", 
                             fg="red" if is_warning else "black")

    def update_history_display(self): # KORRIGIERT: Definition ist jetzt auf Klassenebene
        """Updates the history listbox with content from the engine's context_history."""
        self.hist_lb.delete(0, END)
        avatar_name = self.engine.get_config_value('avatar_name', 'Sherlox')
        for i, (role, content_or_parts, img_obj) in enumerate(self.engine.context_history):
            prefix = "👤 User: " if role == "user" else f"🦊 {avatar_name}: "
            display_text = ""
            if isinstance(content_or_parts, str):
                display_text = content_or_parts
            elif isinstance(content_or_parts, list):
                text_parts = [p["text"] for p in content_or_parts if p.get("type") == "text" and p.get("text")]
                display_text = " ".join(text_parts)
                # KORRIGIERTE EINRÜCKUNG für das if any(...)
                if any(p.get("type") == "image_url" for p in content_or_parts):
                    prefix += "[🖼️] "
            if img_obj and role == "user" and "[🖼️]" not in prefix:
                prefix += "[🖼️] "
            
            shortened_text = display_text[:80].replace('\n', ' ') + ("..." if len(display_text) > 80 else "")
            self.hist_lb.insert(END, f"{prefix}{shortened_text}")
            self.hist_lb.itemconfig(i, {'fg': 'blue' if role == "user" else '#006400'})
        
        if self.hist_lb.size() > 0:
            self.hist_lb.yview(END)
        self.update_context_status_display()

    def set_thinking_status(self,is_thinking): # KORRIGIERT: Definition ist jetzt auf Klassenebene
        self.llm_is_thinking=is_thinking
        avatar=self.engine.get_config_value('avatar_name','Sherlox')
        if is_thinking:
            self.current_prompt_tokens=0
            self.current_completion_tokens=0
            # KORREKTER AUFRUF VON display_message_in_gui
            self.after(0,lambda: self.display_message_in_gui(f"🦊 {avatar} is thinking...",is_raw_text=True,update_history=False))
        self.update_context_status_display()

    # --- Initialization and UI Setup ---
    def __init__(self):
        super().__init__()
        # print(f"DEBUG: LMBuddyOverlay instance __init__, id(self): {id(self)}") # Debug-Ausgabe kann bleiben oder weg
        
        self.gui_update_queue = queue.Queue()
        self.engine = LMBuddyCoreEngine(gui_queue=self.gui_update_queue, app_stop_event=APP_STOP_EVENT)

        app_version_str = self.engine.get_config_value("app_version", "v0.0.0")
        base_window_title = self.engine.get_config_value("classic_ui_title", "LM Buddy")
        self.title(f"{base_window_title} {app_version_str}")
        
        self.current_raw_response_text: str = ""
        self.llm_is_thinking: bool = False
        self.history_tooltip_active: typing.Optional[tk.Toplevel] = None
        self.current_prompt_tokens: int = 0
        self.current_completion_tokens: int = 0
        
        self.geometry(self.engine.get_config_value("classic_ui_initial_geometry"))
        self.attributes("-topmost", True)
        self.attributes("-alpha", float(self.engine.get_config_value("classic_ui_alpha", 0.9)))
        self.configure(bg='white')
        self.resizable(True, True)

        self.setup_menu()
        self.setup_gui_layout() 
        self._display_main_buttons()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.update_context_status_display() 
        self.after(50, functools.partial(self._process_gui_update_queue)) # KORRIGIERT: functools.partial

    def setup_menu(self):
        """Sets up the main application menu."""
        self.menubar = Menu(self)
        self.config(menu=self.menubar)
        settings_menu = Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(label="Change Hotkey...", command=self.change_hotkey)
        settings_menu.add_command(label="Change System Prompt...", command=self.change_system_prompt)
        settings_menu.add_command(label="Change Temperature...", command=self.change_temperature)
        settings_menu.add_command(label="Change OCR Language...", command=self.change_ocr_language)
        settings_menu.add_separator()
        settings_menu.add_command(label="Exit", command=self.on_closing)

    def setup_gui_layout(self):
        """Constructs the main GUI layout and widgets."""
        self.main_pane = tk.PanedWindow(self, orient=tk.VERTICAL, sashrelief=tk.RAISED, bg='white')
        self.main_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))
        
        hist_f = tk.Frame(self.main_pane, bg='lightgrey')
        self.main_pane.add(hist_f, stretch="first", height=150, minsize=50)
        tk.Label(hist_f, text="Conversation History:", font=("Arial", 10, "italic"), bg='lightgrey').pack(pady=(5, 0), padx=5, anchor='w')
        self.hist_lb = Listbox(hist_f, font=("Arial", 9), bg="white", selectbackground="#a6a6a6", activestyle="none")
        self.hist_lb.pack(expand=True, fill="both", padx=5, pady=(0, 5))
        self.hist_lb.bind("<<ListboxSelect>>", self.on_history_select)
        
        # Entferne die DEBUG-Ausgaben hier, wenn der Fehler behoben ist.
        # print(f"DEBUG: Checking 'on_history_motion':")
        if hasattr(self, 'on_history_motion') and callable(self.on_history_motion):
            self.hist_lb.bind("<Motion>", self.on_history_motion)
            # print("  DEBUG: Successfully bound <Motion> to self.on_history_motion")
        else:
            # print("  DEBUG: self DOES NOT HAVE attribute 'on_history_motion'") # Diese Zeile sollte nicht mehr erscheinen
            logging.error("GUI_SETUP: CRITICAL - self.on_history_motion is NOT an attribute or not callable!")
        
        if hasattr(self, 'on_history_leave') and callable(self.on_history_leave):
            self.hist_lb.bind("<Leave>", self.on_history_leave)
        else:
            logging.error("GUI_SETUP: CRITICAL - self.on_history_leave is NOT an attribute or not callable!")
        
        main_f = tk.Frame(self.main_pane, bg='white')
        self.main_pane.add(main_f, stretch="always", minsize=200)
        avatar_name = self.engine.get_config_value("avatar_name", "Sherlox")
        self.title_lbl = tk.Label(main_f, text=f"🦊 {avatar_name}", font=("Arial", 18, "bold"), bg='white', fg='#FF8C00')
        self.title_lbl.pack(pady=(10, 5))
        in_f = tk.Frame(main_f, bg='white')
        in_f.pack(fill="x", padx=10, pady=(0, 5))
        self.in_var = tk.StringVar()
        self.in_entry = tk.Entry(in_f, textvariable=self.in_var, font=("Arial", 11), relief=tk.SOLID, borderwidth=1)
        self.in_entry.pack(side="left", fill="x", expand=True, ipady=3, padx=(0, 5))
        self.in_entry.bind("<Return>", self.send_direct_question_event)
        ask_btn = tk.Button(in_f, text="Ask 💬", command=self.send_direct_question, bg='#4CAF50', fg='white', relief=tk.FLAT, font=("Arial", 10, "bold"), padx=10)
        ask_btn.pack(side="left"); ToolTip(ask_btn, "Send question to LLM (or press Enter).")
        hotkey_d = self.engine.get_config_value('hotkey', 'ctrl+shift+f')
        self.html_out = HTMLLabel(main_f, html=f"<i>Press <b>{hotkey_d}</b> to analyze or type question.</i>", background="white")
        self.html_out.pack(expand=True, fill="both", padx=10, pady=5); self.html_out.fit_height()
        self.stat_f = tk.Frame(self, bg='lightgrey', height=20)
        self.stat_f.pack(side=tk.BOTTOM, fill=tk.X, pady=(2, 0), padx=5)
        self.stat_lbl = Label(self.stat_f, text="Context: 0 Msgs", font=("Arial", 8), bg='lightgrey', anchor='w')
        self.stat_lbl.pack(side=tk.LEFT, padx=5)
        self.btn_cont = tk.Frame(self, bg='white')
        self.btn_cont.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 5), padx=5)
        self.dyn_btn_f = tk.Frame(self.btn_cont, bg='white')
        self.dyn_btn_f.pack(fill="x", expand=True)

    def _process_gui_update_queue(self):
        # print(f"DEBUG: _process_gui_update_queue called. Type of self: {type(self)}, id(self): {id(self)}")
        try:
            while True:
                message = self.gui_update_queue.get_nowait()
                # --- DEBUG-PRINTS VOR DEM FEHLERHAFTEN AUFRUF (kann später entfernt werden) ---
                # if message is None or (message and message.get("type") == mt.MSG_TYPE_OCR_RESULT_FOR_ACTIONS):
                #     print(f"DEBUG: In _process_gui_update_queue, BEFORE calling set_thinking_status:")
                #     print(f"  DEBUG: id(self) is {id(self)}")
                #     print(f"  DEBUG: type(self) is {type(self)}")
                #     print(f"  DEBUG: hasattr(self, 'set_thinking_status') is {hasattr(self, 'set_thinking_status')}")
                #     if hasattr(self, 'set_thinking_status'):
                #         print(f"  DEBUG: type(self.set_thinking_status) is {type(self.set_thinking_status)}")
                # --- ENDE DEBUG-PRINTS ---
                
                if message is None: 
                    self.set_thinking_status(False) # Sollte jetzt funktionieren
                    if self.current_raw_response_text:
                        self.html_out.set_html(markdown_to_html_custom(self.current_raw_response_text))
                        self.engine.speak(self.current_raw_response_text)
                    self.update_history_display()
                    self.gui_update_queue.task_done(); continue
                
                msg_type = message.get("type")
                if msg_type == mt.MSG_TYPE_LLM_CHUNK:
                    if self.llm_is_thinking: self.current_raw_response_text = ""; self.html_out.set_html("<i>Streaming...</i>"); self.llm_is_thinking = False
                    self.current_raw_response_text += message.get("content", "")
                    self.html_out.set_html(f"<pre style='white-space:pre-wrap;word-wrap:break-word;'>{html.escape(self.current_raw_response_text)}</pre>")
                    if "completion_tokens_live" in message: self.current_completion_tokens = message["completion_tokens_live"]; self.update_context_status_display()
                elif msg_type == mt.MSG_TYPE_LLM_PROMPT_TOKENS_UPDATE:
                    self.current_prompt_tokens = message.get("count",0); self.current_completion_tokens=0; self.update_context_status_display()
                elif msg_type == mt.MSG_TYPE_LLM_FINAL_TOKEN_COUNTS:
                    self.current_prompt_tokens = message.get("prompt_tokens",self.current_prompt_tokens)
                    self.current_completion_tokens = message.get("completion_tokens",self.current_completion_tokens)
                    logging.info(f"GUI: Final tokens P:{self.current_prompt_tokens},C:{self.current_completion_tokens}")
                elif msg_type == mt.MSG_TYPE_ERROR:
                    self.display_message_in_gui(message.get("content","Error from engine"),is_error=True)
                    self.set_thinking_status(False); self.hide_ocr_action_buttons_and_show_main()
                elif msg_type == mt.MSG_TYPE_INFO:
                    self.display_message_in_gui(message.get("content",""),is_raw_text=True)
                    if message.get("content","").startswith("Context and buffers cleared"):
                        self.update_history_display(); self.update_context_status_display()
                elif msg_type == mt.MSG_TYPE_OCR_RESULT_FOR_ACTIONS:
                    self.set_thinking_status(False)
                    self.show_ocr_action_buttons(message.get("ocr_text",""), message.get("image_pil"))
                elif msg_type == mt.MSG_TYPE_OCR_ACTIONS_HIDE:
                    self.hide_ocr_action_buttons_and_show_main()
                self.gui_update_queue.task_done()
        except queue.Empty: pass
        finally:
            self.after(50, functools.partial(self._process_gui_update_queue)) # KORRIGIERT: functools.partial

    def change_hotkey(self):
        curr = self.engine.get_config_value("hotkey","ctrl+shift+f")
        new_hk = simpledialog.askstring("Change Hotkey",f"Current:{curr}\nEnter new:",initialvalue=curr,parent=self)
        if new_hk:
            new_hk = new_hk.lower().strip()
            try:
                import keyboard as temp_keyboard 
                temp_keyboard.parse_hotkey(new_hk)
                self.engine.set_config_value("hotkey",new_hk)
                if self.engine.update_hotkey_listener_config():
                     messagebox.showinfo("Hotkey Changed",f"Hotkey: '{new_hk}'.\nListener updated. Restart if issues.",parent=self)
                else: messagebox.showinfo("Hotkey Changed",f"Hotkey: '{new_hk}'.\nRestart LM Buddy for changes.",parent=self)
            except ValueError: messagebox.showerror("Invalid Hotkey",f"'{new_hk}' not valid.",parent=self)
            except ImportError: messagebox.showerror("Error","Keyboard lib needed for validation.",parent=self)

    def change_system_prompt(self):
        curr=self.engine.get_config_value("system_prompt_global","You are helpful.")
        new_p=simpledialog.askstring("Change Global System Prompt","Enter prompt (\\n for newlines):",initialvalue=curr.replace("\n","\\n"),parent=self)
        if new_p is not None:self.engine.set_config_value("system_prompt_global",new_p.replace("\\n","\n").strip());messagebox.showinfo("System Prompt Changed","Global prompt updated.",parent=self)

    def change_temperature(self):
        curr=float(self.engine.get_config_value("temperature",0.3))
        new_t=simpledialog.askfloat("Change Temperature",f"Current:{curr}\nEnter new (0.0-2.0):",initialvalue=curr,minvalue=0.0,maxvalue=2.0,parent=self)
        if new_t is not None:self.engine.set_config_value("temperature",round(new_t,2));messagebox.showinfo("Temperature Changed",f"Temp set to {self.engine.get_config_value('temperature')}.",parent=self)

    def change_ocr_language(self):
        try: import pytesseract as tp;langs=sorted([l for l in tp.get_languages(config='') if l!='osd'])
        except Exception as e:logging.error(f"OCR langs err:{e}");langs=["deu","eng"];messagebox.showwarning("OCR Languages","Could not get Tesseract langs.",parent=self)
        curr=self.engine.get_config_value("ocr_language","deu");opts=", ".join(langs)
        new_l=simpledialog.askstring("Change OCR Language",f"Current:{curr}\nAvailable:{opts[:100]}...\nEnter code:",initialvalue=curr,parent=self)
        if new_l:
            new_l=new_l.lower().strip()
            if new_l in langs or (len(new_l)==3 and new_l.isalpha()):self.engine.set_config_value("ocr_language",new_l);messagebox.showinfo("OCR Language Changed",f"OCR lang set to '{new_l}'.",parent=self)
            else:messagebox.showerror("Invalid OCR Language",f"'{new_l}' not recognized.",parent=self)

    def _clear_dynamic_buttons(self):[w.destroy() for w in self.dyn_btn_f.winfo_children()]
    def _display_main_buttons(self):
        self._clear_dynamic_buttons(); conf={"relief":tk.GROOVE,"borderwidth":1,"font":("Arial",10),"padx":5,"pady":2}
        btn_r=tk.Button(self.dyn_btn_f,text="🔄 Read Aloud",command=self.read_aloud_again,**conf);btn_r.pack(side="left",expand=True,fill="x",padx=3);ToolTip(btn_r,"Read last response.")
        btn_s=tk.Button(self.dyn_btn_f,text="🤫 Stop Speech",command=self.stop_current_speech,**conf);btn_s.pack(side="left",expand=True,fill="x",padx=3);ToolTip(btn_s,"Stop speech.")
        btn_c=tk.Button(self.dyn_btn_f,text="📋 Copy",command=self.copy_response,**conf);btn_c.pack(side="left",expand=True,fill="x",padx=3);ToolTip(btn_c,"Copy response.")
        btn_cl=tk.Button(self.dyn_btn_f,text="🧹 Clear Context",command=self.clear_llm_context_user_initiated,**conf);btn_cl.pack(side="left",expand=True,fill="x",padx=3);ToolTip(btn_cl,"Clear history.")
        self.dyn_btn_f.update_idletasks()

    def on_closing(self):
        if self.engine.get_config_value("classic_ui_save_window_geometry",True):config_manager.save_configuration(geometry_to_save=self.geometry())
        APP_STOP_EVENT.set();self.engine.shutdown();self.destroy()

    def show_image_from_history(self,pil_img):
        if not pil_img:return
        try:
            win=Toplevel(self);win.title("Image from History");win.attributes("-topmost",True)
            copy=pil_img.copy();copy.thumbnail((600,500),Image.Resampling.LANCZOS);tk_img=ImageTk.PhotoImage(copy)
            Label(win,image=tk_img).pack(padx=10,pady=10);win.image=tk_img # Keep reference
            self.update_idletasks();px,py,pw,ph=self.winfo_x(),self.winfo_y(),self.winfo_width(),self.winfo_height()
            ww,wh=copy.width+20,copy.height+20;x,y=px+(pw-ww)//2,py+(ph-wh)//2;win.geometry(f"{ww}x{wh}+{x}+{y}")
        except Exception as e:logging.error(f"Show img err:{e}");messagebox.showerror("Image Error","Could not display image.",parent=self)

    def display_message_in_gui(self,txt,is_raw_text=False,is_error=False,update_history=True):
        avatar=self.engine.get_config_value('avatar_name','Sherlox')
        if self.llm_is_thinking and not txt.startswith(f"🦊 {avatar} is thinking..."):self.llm_is_thinking=False
        self.current_raw_response_text=txt
        html_c=f"<i>{html.escape(txt)}</i>" if is_raw_text else markdown_to_html_custom(txt)
        if is_error:html_c=f"<div style='color:red;border:1px solid red;padding:5px;background-color:#ffeeee;'><b>Error:</b><br>{html.escape(txt)}</div>"
        self.html_out.set_html(html_c)
        if not self.winfo_viewable():self.deiconify()
        self.lift()
        if update_history:self.update_history_display()

    def show_ocr_action_buttons(self,ocr_txt,img_pil=None):
        self._clear_dynamic_buttons()
        ocr_disp=(html.escape(ocr_txt[:200])+"..." if ocr_txt and len(ocr_txt)>200 else html.escape(ocr_txt or ""))
        hdr="<b>Image Captured. Action?</b>" if img_pil else "<b>Content Captured. Action?</b>"
        if ocr_txt:hdr+=f"<br><div style='font-size:0.8em;max-height:60px;overflow-y:auto;border:1px solid #ccc;padding:3px;margin-top:3px;'><i>OCR:{ocr_disp}</i></div>"
        self.display_message_in_gui(hdr,is_raw_text=False,update_history=False)
        conf={"relief":tk.RAISED,"font":("Arial",9,"bold"),"pady":3};acts=[("Summarize📝","summarize","Sum")]
        if self.engine.get_config_value("enable_vision_if_available",False) and img_pil:acts.append(("AnalyzeImg🖼️","analyze_image","AnalyzeImg"))
        acts.extend([("Bullets📋","bullet_points","Bullets"),("Translate🌐","translate","Translate")])
        if ocr_txt and ocr_txt.strip():
            pos=1 if not any(a[1]=="analyze_image" for a in acts) else [i for i,a in enumerate(acts) if a[1]=="analyze_image"][0]+1
            acts.insert(pos,("ImproveTxt✨","improve_text","ImproveTxt"))
        acts.extend([("Help💡","help","Help"),("Ask🤔","set_context_for_question","SetContext"),("Cancel❌","cancel","Cancel")])
        for t,k,tip in acts:
            b=tk.Button(self.dyn_btn_f,text=t,command=lambda ky=k,ot=ocr_txt,im=img_pil:self.handle_ocr_action(ky,ot,im),**conf,bg="#E8F8F5",fg="#117A65")
            b.pack(side="top",fill="x",padx=20,pady=1);ToolTip(b,tip)
        self.dyn_btn_f.update_idletasks()

    def hide_ocr_action_buttons_and_show_main(self):self._display_main_buttons()

    def handle_ocr_action(self,key,ocr,img=None):
        lang=None
        if key=="translate":
            lang=simpledialog.askstring("Target Language","Translate to (e.g., English, fr):",parent=self)
            if not lang or not lang.strip():self.display_message_in_gui("Translation cancelled.",is_raw_text=True);self.hide_ocr_action_buttons_and_show_main();return
            lang=lang.strip()
        if key=="cancel":self.display_message_in_gui("Action cancelled.",is_raw_text=True);self.hide_ocr_action_buttons_and_show_main();return
        self.set_thinking_status(True);self.engine.process_ocr_action(key,ocr,img,lang)

    def clear_llm_context_user_initiated(self):
        self.engine.clear_all_context_and_buffers();self.hide_ocr_action_buttons_and_show_main()
        messagebox.showinfo("LM Buddy","Context cleared.",parent=self)

    def read_aloud_again(self):
        if self.current_raw_response_text and not self.current_raw_response_text.startswith("Error:"):self.engine.speak(self.current_raw_response_text)
        else:self.display_message_in_gui("No text to read.",is_raw_text=True,update_history=False)

    def stop_current_speech(self):self.engine.stop_speech()

    def copy_response(self):
        if self.current_raw_response_text:
            try:pyperclip.copy(self.current_raw_response_text)
            except pyperclip.PyperclipException as e:messagebox.showerror("LM Buddy",f"Copy error:\n{e}",parent=self);return
            messagebox.showinfo("LM Buddy","Copied to clipboard.",parent=self)
        else:messagebox.showwarning("LM Buddy","Nothing to copy.",parent=self)

    def send_direct_question_event(self,event):self.send_direct_question()
    def send_direct_question(self):
        q=self.in_var.get().strip()
        if not q:self.display_message_in_gui("Please enter a question.",is_raw_text=True,is_error=True);return
        self.in_var.set("");self.hide_ocr_action_buttons_and_show_main()
        self.set_thinking_status(True);self.engine.process_direct_question(q)

def main():
    try:import pytesseract as tp;logging.info(f"Tesseract {tp.get_tesseract_version()} available.")
    except Exception:logging.warning("Could not verify Tesseract version early.")
    app=LMBuddyOverlay();app.mainloop()

if __name__ == "__main__":
    main()