# hotkeys.py - Горячие клавиши: Модификатор + Клавиша1 + Клавиша2
# По умолчанию: CTRL + Z + X

from pynput import keyboard
from pynput.keyboard import Key, Listener
from typing import Callable, Optional, Set, Tuple
import threading


# Модификаторы
MODIFIERS = {
    'CTRL': {Key.ctrl, Key.ctrl_l, Key.ctrl_r},
    'ALT': {Key.alt, Key.alt_l, Key.alt_r},
    'SHIFT': {Key.shift, Key.shift_l, Key.shift_r},
    'NONE': set(),  # Без модификатора
}

# Виртуальные коды букв (независимо от раскладки)
KEY_VK = {
    'A': 0x41, 'B': 0x42, 'C': 0x43, 'D': 0x44, 'E': 0x45,
    'F': 0x46, 'G': 0x47, 'H': 0x48, 'I': 0x49, 'J': 0x4A,
    'K': 0x4B, 'L': 0x4C, 'M': 0x4D, 'N': 0x4E, 'O': 0x4F,
    'P': 0x50, 'Q': 0x51, 'R': 0x52, 'S': 0x53, 'T': 0x54,
    'U': 0x55, 'V': 0x56, 'W': 0x57, 'X': 0x58, 'Y': 0x59,
    'Z': 0x5A,
}

# Списки для UI
MODIFIER_LIST = ['CTRL', 'ALT', 'SHIFT', 'NONE']
KEY_LIST = list(KEY_VK.keys())


class HotkeyListener:
    """
    Слушатель горячих клавиш.
    Формат: Модификатор + Клавиша1 + Клавиша2
    По умолчанию: CTRL + Z + X
    """
    
    def __init__(self):
        self._listener: Optional[Listener] = None
        self._on_press_cb: Optional[Callable] = None
        self._on_release_cb: Optional[Callable] = None
        
        self._hotkey_active = False
        self._lock = threading.Lock()
        
        # Состояние клавиш
        self._modifier_pressed = False
        self._key1_pressed = False
        self._key2_pressed = False
        
        # Настройки (по умолчанию CTRL + Z + X)
        self._modifier = 'CTRL'
        self._key1 = 'Z'
        self._key2 = 'X'
    
    def set_hotkey(self, modifier: str, key1: str, key2: str) -> bool:
        """Устанавливает комбинацию горячих клавиш"""
        modifier = modifier.upper()
        key1 = key1.upper()
        key2 = key2.upper()
        
        if modifier not in MODIFIERS:
            return False
        if key1 not in KEY_VK or key2 not in KEY_VK:
            return False
        if key1 == key2:
            return False
        
        self._modifier = modifier
        self._key1 = key1
        self._key2 = key2
        
        return True
    
    def get_hotkey(self) -> Tuple[str, str, str]:
        """Возвращает (модификатор, клавиша1, клавиша2)"""
        return (self._modifier, self._key1, self._key2)
    
    def get_hotkey_string(self) -> str:
        """Возвращает строку типа 'CTRL + Z + X'"""
        if self._modifier == 'NONE':
            return f"{self._key1} + {self._key2}"
        return f"{self._modifier} + {self._key1} + {self._key2}"
    
    def set_callbacks(self, on_press: Callable = None, on_release: Callable = None):
        self._on_press_cb = on_press
        self._on_release_cb = on_release
    
    def _is_modifier(self, key) -> bool:
        """Проверяет, является ли key нужным модификатором"""
        if self._modifier == 'NONE':
            return True  # Модификатор не нужен
        return key in MODIFIERS.get(self._modifier, set())
    
    def _is_key(self, key, target: str) -> bool:
        """Проверяет, является ли key целевой клавишей"""
        target_vk = KEY_VK.get(target)
        if target_vk and hasattr(key, 'vk'):
            return key.vk == target_vk
        return False
    
    def _update_state(self, key, pressed: bool):
        """Обновляет состояние нажатых клавиш"""
        # Модификатор
        if self._is_modifier(key):
            self._modifier_pressed = pressed
        
        # Клавиша 1
        if self._is_key(key, self._key1):
            self._key1_pressed = pressed
        
        # Клавиша 2
        if self._is_key(key, self._key2):
            self._key2_pressed = pressed
    
    def _check_combo(self) -> bool:
        """Проверяет, нажата ли комбинация"""
        if self._modifier == 'NONE':
            return self._key1_pressed and self._key2_pressed
        return self._modifier_pressed and self._key1_pressed and self._key2_pressed
    
    def _on_press(self, key):
        try:
            self._update_state(key, True)
            
            with self._lock:
                if self._check_combo() and not self._hotkey_active:
                    self._hotkey_active = True
                    if self._on_press_cb:
                        threading.Thread(target=self._on_press_cb, daemon=True).start()
        except:
            pass
    
    def _on_release(self, key):
        try:
            was_active = self._hotkey_active
            
            self._update_state(key, False)
            
            with self._lock:
                if was_active and not self._check_combo():
                    self._hotkey_active = False
                    if self._on_release_cb:
                        threading.Thread(target=self._on_release_cb, daemon=True).start()
        except:
            pass
    
    def _win32_filter(self, msg, data):
        """Подавляем клавиши при активной записи"""
        if self._hotkey_active and hasattr(data, 'vkCode'):
            vk = data.vkCode
            # Подавляем key1 и key2
            if vk in (KEY_VK.get(self._key1), KEY_VK.get(self._key2)):
                try:
                    self._listener.suppress_event()
                except:
                    pass
        return True
    
    def start(self) -> bool:
        """Запускает слушатель"""
        if self._listener:
            return False
        
        try:
            # Сброс состояния
            self._modifier_pressed = (self._modifier == 'NONE')
            self._key1_pressed = False
            self._key2_pressed = False
            self._hotkey_active = False
            
            self._listener = Listener(
                on_press=self._on_press,
                on_release=self._on_release,
                win32_event_filter=self._win32_filter
            )
            self._listener.start()
            return True
        except:
            return False
    
    def stop(self):
        """Останавливает слушатель"""
        if self._listener:
            try:
                self._listener.stop()
            except:
                pass
            self._listener = None
        
        self._modifier_pressed = False
        self._key1_pressed = False
        self._key2_pressed = False
        self._hotkey_active = False
    
    def is_running(self) -> bool:
        return self._listener is not None


# Singleton
_instance: Optional[HotkeyListener] = None

def get_hotkey_listener() -> HotkeyListener:
    global _instance
    if _instance is None:
        _instance = HotkeyListener()
    return _instance
