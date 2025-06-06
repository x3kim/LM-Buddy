# avatar_ui.py
import sys
import logging
import os
import typing

from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QMenu, QLabel
from PySide6.QtGui import (QPixmap, QPainter, QColor, QMouseEvent, 
                           QBitmap, QTransform, QIcon, QPaintEvent, QKeyEvent, QFont)
from PySide6.QtCore import Qt, QPoint, QRect, QSize, QTimer

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(filename)s:%(lineno)d - %(message)s'
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class AvatarComponent:
    def __init__(self, component_name: str, image_folder_path: str,
                 offset_x: int = 0, offset_y: int = 0, z_order: int = 0,
                 can_be_mirrored: bool = True):
        self.component_name = component_name 
        self.image_folder_path = image_folder_path
        self.base_offset_x = offset_x 
        self.base_offset_y = offset_y
        self.z_order = z_order
        self.can_be_mirrored = can_be_mirrored
        self.current_pixmap: QPixmap = QPixmap(1, 1) 
        self.current_pixmap.fill(Qt.GlobalColor.transparent)
        self.current_draw_offset_x = offset_x
        self.current_draw_offset_y = offset_y
        self.loaded_image_was_mirrored = False

    def _load_pixmap_from_file(self, file_name: str) -> QPixmap:
        abs_path = os.path.join(self.image_folder_path, file_name)
        if not os.path.exists(abs_path):
            # logging.warning(f"AVATAR_COMP: Image file not found at: {abs_path}")
            return QPixmap()
        pixmap = QPixmap(abs_path)
        if pixmap.isNull():
            logging.error(f"AVATAR_COMP: Failed to load QPixmap from: {abs_path}")
            return QPixmap()
        return pixmap

    def update_visuals(self, avatar_facing_direction: str, base_avatar_unmirrored_width: int):
        loaded_successfully = False
        self.loaded_image_was_mirrored = False
        specific_image_name = f"{self.component_name}_{avatar_facing_direction}.png"
        temp_pixmap = self._load_pixmap_from_file(specific_image_name)

        if not temp_pixmap.isNull() and temp_pixmap.width() > 1:
            logging.debug(f"AVATAR_COMP [{self.component_name}]: Loaded specific '{specific_image_name}'.")
            loaded_successfully = True
        else:
            base_image_name = f"{self.component_name}.png"
            temp_pixmap = self._load_pixmap_from_file(base_image_name)
            if not temp_pixmap.isNull() and temp_pixmap.width() > 1:
                logging.debug(f"AVATAR_COMP [{self.component_name}]: Loaded base '{base_image_name}'.")
                if avatar_facing_direction == "right" and self.can_be_mirrored:
                    temp_pixmap = temp_pixmap.transformed(QTransform().scale(-1, 1), Qt.TransformationMode.SmoothTransformation)
                    self.loaded_image_was_mirrored = True
                    logging.debug(f"AVATAR_COMP [{self.component_name}]: Mirrored base for 'right'.")
                loaded_successfully = True
            else:
                logging.warning(f"AVATAR_COMP [{self.component_name}]: Could not load '{specific_image_name}' or '{base_image_name}'.")
                temp_pixmap = QPixmap(1,1); temp_pixmap.fill(Qt.GlobalColor.transparent)
        self.current_pixmap = temp_pixmap
        
        self.current_draw_offset_x = self.base_offset_x # This is the offset from config for current avatar direction
        self.current_draw_offset_y = self.base_offset_y

        # If the avatar is facing right AND this component's image itself was mirrored (not loaded as a specific _right image)
        # AND its base_offset_x was defined from the left of an unmirrored avatar,
        # then we need to adjust its X position to maintain its relative position to the avatar's mass.
        if avatar_facing_direction == "right" and self.loaded_image_was_mirrored:
            component_width = self.get_dimensions().width()
            self.current_draw_offset_x = base_avatar_unmirrored_width - self.base_offset_x - component_width
        
        logging.debug(f"AVATAR_COMP '{self.component_name}': Dir '{avatar_facing_direction}', "
                      f"LoadedOK: {loaded_successfully}, ImgMirrored: {self.loaded_image_was_mirrored}, "
                      f"BaseCfgOff:({self.base_offset_x},{self.base_offset_y}), "
                      f"DrawOffsetFinal: ({self.current_draw_offset_x}, {self.current_draw_offset_y}), "
                      f"Size: {self.get_dimensions()}")

    def get_current_pixmap(self) -> QPixmap: return self.current_pixmap
    def get_dimensions(self) -> QSize: return self.current_pixmap.size() if not self.current_pixmap.isNull() else QSize(0,0)
    def get_mask(self) -> typing.Optional[QBitmap]:
        return self.current_pixmap.mask() if not self.current_pixmap.isNull() and self.current_pixmap.hasAlphaChannel() else None

class SherloxAvatarWindow(QWidget):
    def __init__(self, engine_ref=None):
        super().__init__()
        self.engine = engine_ref
        self.avatar_facing_direction = self.engine.get_config_value("avatar_initial_direction", "left") if self.engine else "left"
        self.window_drag_offset = QPoint()
        self.components: typing.List[AvatarComponent] = []
        self.base_avatar_component: typing.Optional[AvatarComponent] = None
        self.display_component: typing.Optional[AvatarComponent] = None
        self.window_render_width, self.window_render_height = 0, 0
        self.window_content_offset_x, self.window_content_offset_y = 0, 0

        self.display_text_label: typing.Optional[QLabel] = None
        self.typewriter_timer: typing.Optional[QTimer] = None
        self.full_text_to_type: str = ""
        self.current_typed_text: str = ""
        self.typewriter_char_index: int = 0
        self.typewriter_speed: int = 50 

        self._load_and_setup_components()
        self._setup_display_text_label() 
        self._initialize_ui_properties()
        
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.show()
        logging.info(f"AVATAR_UI: SherloxAvatarWindow initialized, facing {self.avatar_facing_direction}.")

    def _load_and_setup_components(self):
        self.components.clear()
        cfg_val = self.engine.get_config_value if self.engine else lambda k,d=None: d
        avatar_skin = cfg_val("avatar_skin", "sherlox")
        avatar_state = "idle"
        base_avatar_folder = os.path.join(BASE_DIR, "data", "avatar", avatar_skin)
        self.base_avatar_component = AvatarComponent(
            component_name=avatar_state, image_folder_path=base_avatar_folder,
            offset_x=0, offset_y=0, z_order=0, can_be_mirrored=True
        )
        self.components.append(self.base_avatar_component)

        display_type_id = cfg_val("display_element_type", "blackboard_green")
        display_state_name = "min"
        display_folder = os.path.join(BASE_DIR, "data", "display", display_type_id)
        
        # Get the offset profile key based on where the display should be relative to the avatar
        offset_profile_key_suffix = "left" if self.avatar_facing_direction == "left" else "right"
        offset_prefix = f"display_{display_type_id}_{display_state_name}_{offset_profile_key_suffix}"
        
        display_offset_x = cfg_val(f"{offset_prefix}_offset_x", 0)
        display_offset_y = cfg_val(f"{offset_prefix}_offset_y", 0)
        display_z_order  = cfg_val(f"{offset_prefix}_z_order", -1)
        display_can_mirror = cfg_val(f"{offset_prefix}_can_mirror", True)
        # component_name for display is its state, e.g., "min". AvatarComponent handles _left/_right or mirroring.
        
        self.display_component = AvatarComponent(
            component_name=display_state_name, 
            image_folder_path=display_folder,
            offset_x=display_offset_x, 
            offset_y=display_offset_y,
            z_order=display_z_order,
            can_be_mirrored=display_can_mirror
        )
        self.components.append(self.display_component)
        self.components.sort(key=lambda c: c.z_order)
        self.update_component_visuals()

        if self.base_avatar_component and (self.base_avatar_component.get_current_pixmap().isNull() or self.base_avatar_component.get_current_pixmap().width() <=1):
            logging.critical(f"AVATAR_UI: Base avatar '{avatar_skin}/{avatar_state}' NOT LOADED properly.")
            fb_pixmap = QPixmap(150,200); fb_pixmap.fill(QColor("darkred"))
            p = QPainter(fb_pixmap); p.setPen(Qt.GlobalColor.white); p.drawText(fb_pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "AVATAR\nERROR!"); p.end()
            self.base_avatar_component.current_pixmap = fb_pixmap
        if self.display_component and (self.display_component.get_current_pixmap().isNull() or self.display_component.get_current_pixmap().width() <= 1):
            logging.warning(f"AVATAR_UI: Display '{display_type_id}/{display_state_name}' (profile: {offset_profile_key_suffix}) not loaded properly.")

    def _setup_display_text_label(self):
        if not self.display_component or self.display_component.get_current_pixmap().isNull():
            logging.warning("AVATAR_UI: Cannot setup display text label, display_component (tafel) not loaded or invalid.")
            if self.display_text_label: self.display_text_label.hide()
            return

        if not self.display_text_label:
            self.display_text_label = QLabel(self) 
            self.display_text_label.setAttribute(Qt.WA_TranslucentBackground)
            self.display_text_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.display_text_label.setWordWrap(True)
            font = QFont("Segoe Print", 13) # EXAMPLE FONT - ADJUST
            self.display_text_label.setFont(font)
            self.display_text_label.setStyleSheet("color: white; background-color: transparent; padding: 2px;")
            logging.debug("AVATAR_UI: Display text label created.")
        
        self._update_display_text_label_geometry()
        self.display_text_label.setText("Willkommen bei LM Buddy!\n\nIch bin Sherlox und helfe dir gerne bei allen Fragen und Themen.") # Initial text
        self.display_text_label.show()

    def _update_display_text_label_geometry(self):
        if not self.display_text_label or not self.display_component or \
           self.display_component.get_current_pixmap().isNull() or \
           self.display_component.get_current_pixmap().width() <= 1:
            if self.display_text_label: self.display_text_label.hide()
            return

        cfg_val = self.engine.get_config_value if self.engine else lambda k,d=None: d
        display_type_id = cfg_val("display_element_type", "blackboard_green")
        display_state_name = "min"
        offset_profile_key_suffix = "left" if self.avatar_facing_direction == "left" else "right"
        text_rect_prefix = f"display_{display_type_id}_{display_state_name}_{offset_profile_key_suffix}"

        text_rect_x_rel = cfg_val(f"{text_rect_prefix}_text_rect_x", 10)
        text_rect_y_rel = cfg_val(f"{text_rect_prefix}_text_rect_y", 10)
        text_rect_width = cfg_val(f"{text_rect_prefix}_text_rect_width", 140)
        text_rect_height = cfg_val(f"{text_rect_prefix}_text_rect_height", 70)

        tafel_abs_x = self.window_content_offset_x + self.display_component.current_draw_offset_x
        tafel_abs_y = self.window_content_offset_y + self.display_component.current_draw_offset_y
        
        label_abs_x = tafel_abs_x + text_rect_x_rel
        label_abs_y = tafel_abs_y + text_rect_y_rel
        label_width = max(10, int(text_rect_width))
        label_height = max(10, int(text_rect_height))
            
        self.display_text_label.setGeometry(int(label_abs_x), int(label_abs_y), label_width, label_height)
        self.display_text_label.show()
        self.display_text_label.raise_()
        logging.debug(f"AVATAR_UI: TextLabel Geom: X={label_abs_x},Y={label_abs_y},W={label_width},H={label_height}")

    def update_component_visuals(self):
        base_avatar_unmirrored_width = 0
        if self.base_avatar_component:
            temp_base_pm = self.base_avatar_component._load_pixmap_from_file(f"{self.base_avatar_component.component_name}.png")
            if temp_base_pm.isNull() or temp_base_pm.width() <=1:
                 temp_base_pm = self.base_avatar_component._load_pixmap_from_file(f"{self.base_avatar_component.component_name}_left.png")
            if not temp_base_pm.isNull(): base_avatar_unmirrored_width = temp_base_pm.width()
        for comp in self.components:
            comp.update_visuals(self.avatar_facing_direction, base_avatar_unmirrored_width)

    def _calculate_bounding_box_and_set_size(self):
        if not self.components: self.setFixedSize(150,200); logging.warning("AVATAR_UI: No components for bounding box."); return
        min_x, min_y, max_x, max_y = float('inf'),float('inf'),float('-inf'),float('-inf')
        for comp in self.components:
            pixmap = comp.get_current_pixmap()
            if pixmap.isNull() or pixmap.width() <= 1: continue
            comp_left = comp.current_draw_offset_x; comp_top = comp.current_draw_offset_y
            comp_right = comp_left + pixmap.width(); comp_bottom = comp_top + pixmap.height()
            min_x=min(min_x,comp_left); min_y=min(min_y,comp_top)
            max_x=max(max_x,comp_right); max_y=max(max_y,comp_bottom)
        if min_x == float('inf'):
            self.setFixedSize(10,10); self.window_content_offset_x=0; self.window_content_offset_y=0; 
            logging.warning("AVATAR_UI: No sizable components for bounding box."); return
        self.window_content_offset_x = -min_x if min_x < 0 else 0
        self.window_content_offset_y = -min_y if min_y < 0 else 0
        self.window_render_width = int(max_x - min_x)
        self.window_render_height = int(max_y - min_y)
        self.setFixedSize(max(1,self.window_render_width), max(1,self.window_render_height))
        logging.debug(f"AVATAR_UI: WinSize: {self.width()}x{self.height()}, ContentOffset: ({self.window_content_offset_x},{self.window_content_offset_y})")

    def _initialize_ui_properties(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._calculate_bounding_box_and_set_size()
        if self.display_text_label: self._update_display_text_label_geometry() # Update label after size calc
            
        if self.base_avatar_component:
            base_mask_bitmap = self.base_avatar_component.get_mask()
            if base_mask_bitmap:
                final_mask = QBitmap(self.size()); final_mask.fill(Qt.transparent)
                p = QPainter(final_mask)
                base_final_draw_x = self.window_content_offset_x + self.base_avatar_component.current_draw_offset_x
                base_final_draw_y = self.window_content_offset_y + self.base_avatar_component.current_draw_offset_y
                p.drawPixmap(QPoint(int(base_final_draw_x), int(base_final_draw_y)), base_mask_bitmap)
                p.end(); self.setMask(final_mask)
            else: self.clearMask()
        else: self.clearMask()
        if QApplication.instance():
            screen = QApplication.instance().primaryScreen().availableGeometry()
            x_pos = screen.width()-self.width()-20 if self.avatar_facing_direction=="left" else 20
            y_pos = screen.height()-self.height()-50 
            self.move(x_pos,y_pos)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        for component in self.components:
            pixmap = component.get_current_pixmap()
            if pixmap.isNull() or pixmap.width() <= 1: continue
            final_x = self.window_content_offset_x + component.current_draw_offset_x
            final_y = self.window_content_offset_y + component.current_draw_offset_y
            painter.drawPixmap(QPoint(int(final_x), int(final_y)), pixmap)
        painter.end()
        # QLabel (self.display_text_label) malt sich selbst, wenn es ein Kind des QWidget ist.

    def switch_avatar_direction(self, new_direction: typing.Optional[str] = None):
        if new_direction:
            if new_direction not in ["left", "right"]: logging.warning(f"Invalid direction: {new_direction}"); return
            if new_direction == self.avatar_facing_direction: return
            self.avatar_facing_direction = new_direction
        else: self.avatar_facing_direction = "right" if self.avatar_facing_direction == "left" else "left"
        logging.info(f"AVATAR_UI: Switching avatar to face {self.avatar_facing_direction}.")
        self._load_and_setup_components() 
        self._initialize_ui_properties()  # This will also call _update_display_text_label_geometry
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button()==Qt.LeftButton: self.window_drag_offset=event.globalPosition().toPoint()-self.frameGeometry().topLeft(); event.accept()
    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self.window_drag_offset; self.move(new_pos)
            if QApplication.instance():
                screen_center = QApplication.instance().primaryScreen().availableGeometry().width()/2
                window_center = new_pos.x() + self.width()/2
                desired_dir = "left" if window_center > screen_center else "right"
                if desired_dir != self.avatar_facing_direction: self.switch_avatar_direction(desired_dir)
            event.accept()
    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button()==Qt.LeftButton: self.window_drag_offset=QPoint(); event.accept()

    def show_context_menu(self, position: QPoint):
        menu = QMenu(self); current_face = self.avatar_facing_direction.capitalize()
        toggle_act = menu.addAction(f"Face {'Left' if current_face=='Right' else 'Right'}")
        toggle_act.triggered.connect(lambda: self.switch_avatar_direction()) # type: ignore
        menu.addSeparator(); close_act = menu.addAction("Close Sherlox"); close_act.triggered.connect(self.close_application) # type: ignore
        menu.exec(self.mapToGlobal(position))

    def close_application(self):
        logging.info("AVATAR_UI: Closing application."); self.close()
        if QApplication.instance(): QApplication.instance().quit()

    def start_typewriter_effect(self, text: str, speed: int = 50):
        if not self.display_text_label: logging.warning("AVATAR_UI: No display label for typewriter."); return
        self.full_text_to_type = text; self.current_typed_text = ""; self.typewriter_char_index = 0
        self.typewriter_speed = speed
        if self.typewriter_timer: self.typewriter_timer.stop()
        if not self.typewriter_timer:
            self.typewriter_timer = QTimer(self)
            self.typewriter_timer.timeout.connect(self._typewriter_tick)
        self.display_text_label.setText(""); self.typewriter_timer.start(self.typewriter_speed)
        logging.debug(f"AVATAR_UI: Typewriter started: '{text[:30]}...'")

    def _typewriter_tick(self):
        if not self.display_text_label or not self.typewriter_timer: return
        if self.typewriter_char_index < len(self.full_text_to_type):
            self.current_typed_text += self.full_text_to_type[self.typewriter_char_index]
            self.display_text_label.setText(self.current_typed_text)
            self.typewriter_char_index += 1
        else:
            self.typewriter_timer.stop(); logging.debug("AVATAR_UI: Typewriter finished.")

def run_avatar_ui_standalone():
    app = QApplication.instance() or QApplication(sys.argv)
    class MockEngine:
        def get_config_value(self, key, default=None):
            cfg = {
                "avatar_skin": "sherlox", "avatar_initial_direction": "left",
                "display_element_type": "blackboard_green",
                
                "display_blackboard_green_min_left_offset_x": -465, # Tafel links vom (links schauenden) Fuchs
                "display_blackboard_green_min_left_offset_y": -35,
                "display_blackboard_green_min_left_z_order": -1,
                "display_blackboard_green_min_left_can_mirror": True,
                "display_blackboard_green_min_left_image_name": "min", # Basisname des Tafelbilds
                "display_blackboard_green_min_left_text_rect_x": 111, # Text-Offset X innerhalb der Tafel
                "display_blackboard_green_min_left_text_rect_y": 68, # Text-Offset Y
                "display_blackboard_green_min_left_text_rect_width": 410, # Text-Breite
                "display_blackboard_green_min_left_text_rect_height": 250, # Text-Höhe

                "display_blackboard_green_min_right_offset_x": -465, # Tafel rechts vom (rechts schauenden) Fuchs
                "display_blackboard_green_min_right_offset_y": -35,
                "display_blackboard_green_min_right_z_order": -1,
                "display_blackboard_green_min_right_can_mirror": True,
                "display_blackboard_green_min_right_image_name": "min",
                "display_blackboard_green_min_right_text_rect_x": 112, # Dieselben relativen Text-Offsets für die Tafel
                "display_blackboard_green_min_right_text_rect_y": 68,
                "display_blackboard_green_min_right_text_rect_width": 410,
                "display_blackboard_green_min_right_text_rect_height": 250,
            }
            return cfg.get(key, default)
        def speak(self, text): logging.info(f"MOCK_ENGINE_SPEAK: {text}")

    avatar_window = SherloxAvatarWindow(engine_ref=MockEngine())
    
    def test_type(): # Closure to access avatar_window
        avatar_window.start_typewriter_effect("Welcome to LM Buddy!\nMy name is Sherlox, and I’m here to help you with any request. You can call on me in any app and ask questions on any topic. Just press Shift + Ctrl + F, and I can answer questions about what's happening on your screen, summarize, translate, and much more.\nSherlox.", 30)

    type_btn = QPushButton("Type Test", avatar_window)
    type_btn.setFixedSize(80,20)
    if avatar_window.height() > 50 : type_btn.move(10, avatar_window.height() - 30)
    else: type_btn.move(5,5)
    type_btn.clicked.connect(test_type) # type: ignore
    type_btn.setStyleSheet("background-color:rgba(150,200,150,180);color:black;border-radius:3px;")
    type_btn.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    sherlox_folder = os.path.join(BASE_DIR, "data", "avatar", "sherlox")
    display_folder = os.path.join(BASE_DIR, "data", "display", "blackboard_green")
    os.makedirs(sherlox_folder, exist_ok=True); os.makedirs(display_folder, exist_ok=True)
    sherlox_idle_img_path = os.path.join(sherlox_folder, "idle.png")
    tafel_min_img_path = os.path.join(display_folder, "min.png")

    try:
        from PIL import Image as PILImage, ImageDraw, ImageFont
        try: font = ImageFont.truetype("arial.ttf", 12) # Adjust font size as needed
        except IOError: font = ImageFont.load_default()
        if not os.path.exists(sherlox_idle_img_path):
            logging.info(f"Creating dummy: {sherlox_idle_img_path}"); img = PILImage.new('RGBA',(100,150),(0,0,0,0))
            d=ImageDraw.Draw(img); d.ellipse((10,10,90,140),fill=(255,120,0,200)); d.ellipse((30,20,45,35),fill='white'); d.ellipse((55,20,70,35),fill='white')
            d.ellipse((35,25,40,30),fill='black'); d.ellipse((60,25,65,30),fill='black'); img.save(sherlox_idle_img_path)
        if not os.path.exists(tafel_min_img_path):
            logging.info(f"Creating dummy: {tafel_min_img_path}"); img_t=PILImage.new('RGBA',(160,90),(0,0,0,0))
            d_t=ImageDraw.Draw(img_t); d_t.rectangle((5,5,155,85),fill=(80,50,30,220),outline="black"); d_t.rectangle((10,10,150,80),fill=(0,80,20,230))
            d_t.text((15,15),"LM Buddy\nTafel",font=font,fill="white",align="center"); img_t.save(tafel_min_img_path)
    except ImportError: logging.error("Pillow not found for dummy images.")
    except Exception as e: logging.error(f"Error dummy images: {e}", exc_info=True)
    
    run_avatar_ui_standalone()