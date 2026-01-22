# utils.py - Поиск моделей Whisper и работа с реестром Windows (автозагрузка)

import os
import sys
import winreg
from pathlib import Path
from typing import List, Dict

# Название приложения для реестра
APP_NAME = "WhisperQuickType"

# Доступные размеры моделей Whisper
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]


def get_whisper_cache_path() -> Path:
    """Возвращает путь к кэшу моделей Whisper"""
    # OpenAI Whisper хранит модели в ~/.cache/whisper
    home = Path.home()
    return home / ".cache" / "whisper"


def scan_whisper_models() -> Dict[str, str]:
    """
    Сканирует кэш и возвращает словарь найденных моделей.
    Формат: {"model_name": "full_path"}
    """
    models = {}
    cache_path = get_whisper_cache_path()
    
    if cache_path.exists():
        for item in cache_path.iterdir():
            if item.is_file() and item.suffix == ".pt":
                # Извлекаем имя модели из файла (например: base.pt -> base)
                model_name = item.stem
                # Проверяем что это известная модель
                for size in WHISPER_MODELS:
                    if size in model_name.lower():
                        models[size] = str(item)
                        break
    
    # Также проверяем локальную папку models
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).parent
    else:
        app_dir = Path(__file__).parent
    
    local_models = app_dir / "models"
    if local_models.exists():
        for item in local_models.iterdir():
            if item.is_file() and item.suffix == ".pt":
                model_name = item.stem
                for size in WHISPER_MODELS:
                    if size in model_name.lower():
                        if size not in models:  # Не перезаписываем
                            models[size] = str(item)
                        break
    
    return models


def get_available_model_sizes() -> List[str]:
    """Возвращает список размеров моделей, которые можно использовать"""
    return WHISPER_MODELS.copy()


def is_model_downloaded(model_size: str) -> bool:
    """Проверяет, скачана ли модель"""
    models = scan_whisper_models()
    return model_size in models


def set_autostart(enable: bool) -> bool:
    """
    Добавляет или удаляет приложение из автозагрузки Windows.
    Возвращает True при успехе.
    """
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        
        if getattr(sys, 'frozen', False):
            # Запущено как exe
            exe_path = f'"{sys.executable}"'
        else:
            # Запущено как скрипт
            main_script = Path(__file__).parent / "main.py"
            exe_path = f'"{sys.executable}" "{main_script}"'
        
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        )
        
        if enable:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass  # Ключ уже не существует
        
        winreg.CloseKey(key)
        return True
        
    except Exception as e:
        print(f"Ошибка работы с реестром: {e}")
        return False


def is_autostart_enabled() -> bool:
    """Проверяет, включена ли автозагрузка приложения"""
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            winreg.KEY_QUERY_VALUE
        )
        
        try:
            winreg.QueryValueEx(key, APP_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
            
    except Exception:
        return False


def get_app_data_path() -> Path:
    """Возвращает путь для хранения настроек приложения"""
    app_data = Path(os.environ.get('APPDATA', Path.home()))
    app_path = app_data / APP_NAME
    app_path.mkdir(parents=True, exist_ok=True)
    return app_path


def save_settings(settings: dict) -> bool:
    """Сохраняет настройки в JSON файл"""
    import json
    try:
        settings_file = get_app_data_path() / "settings.json"
        with open(settings_file, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Ошибка сохранения настроек: {e}")
        return False


def load_settings() -> dict:
    """Загружает настройки из JSON файла"""
    import json
    default_settings = {
        "model": "base",
        "microphone": None,
        "autostart": False,
        "language": "ru"
    }
    
    try:
        settings_file = get_app_data_path() / "settings.json"
        if settings_file.exists():
            with open(settings_file, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                default_settings.update(loaded)
    except Exception as e:
        print(f"Ошибка загрузки настроек: {e}")
    
    return default_settings


if __name__ == "__main__":
    print("=== Проверка утилит ===")
    print(f"\nПуть к кэшу Whisper: {get_whisper_cache_path()}")
    print(f"\nНайденные модели: {scan_whisper_models()}")
    print(f"\nДоступные размеры: {get_available_model_sizes()}")
    print(f"\nАвтозагрузка включена: {is_autostart_enabled()}")
    print(f"\nПуть настроек: {get_app_data_path()}")
