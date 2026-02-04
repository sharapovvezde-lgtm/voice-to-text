"""
Тест захвата аудио - проверка работы микрофона и системного звука (WASAPI Loopback)
Запуск: python dev_test/test_audio.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_microphones():
    """Тест списка микрофонов через sounddevice"""
    print("\n" + "="*60)
    print("🎤 ТЕСТ 1: Микрофоны (sounddevice)")
    print("="*60)
    
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        print(f"Найдено устройств: {len(devices)}\n")
        
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                marker = "✓" if dev['name'] == sd.query_devices(sd.default.device[0])['name'] else " "
                print(f"  [{marker}] {i}: {dev['name']} (каналы: {dev['max_input_channels']})")
        
        print("\n✅ sounddevice работает!")
        return True
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

def test_system_audio():
    """Тест захвата системного звука через soundcard"""
    print("\n" + "="*60)
    print("🔊 ТЕСТ 2: Системный звук (soundcard / WASAPI Loopback)")
    print("="*60)
    
    try:
        import soundcard as sc
        
        # Список устройств воспроизведения (для loopback)
        speakers = sc.all_speakers()
        print(f"Найдено спикеров/выходов: {len(speakers)}\n")
        
        for i, spk in enumerate(speakers):
            default = "✓" if spk.name == sc.default_speaker().name else " "
            print(f"  [{default}] {i}: {spk.name}")
        
        # Список микрофонов через soundcard
        mics = sc.all_microphones(include_loopback=True)
        print(f"\nМикрофоны (включая loopback): {len(mics)}")
        
        loopback_found = False
        for i, mic in enumerate(mics):
            if mic.isloopback:
                print(f"  [🔁] {i}: {mic.name} (LOOPBACK)")
                loopback_found = True
            else:
                print(f"  [🎤] {i}: {mic.name}")
        
        if loopback_found:
            print("\n✅ WASAPI Loopback доступен! Можно захватывать системный звук.")
        else:
            print("\n⚠️ Loopback не найден. Попробуйте включить 'Stereo Mix' в настройках Windows.")
        
        return True
    except ImportError:
        print("❌ soundcard не установлен. Выполните: pip install soundcard")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

def test_recording_short():
    """Короткий тест записи с микрофона"""
    print("\n" + "="*60)
    print("⏺️ ТЕСТ 3: Запись 2 секунды с микрофона")
    print("="*60)
    
    try:
        import sounddevice as sd
        import numpy as np
        
        duration = 2  # секунды
        samplerate = 16000
        
        print(f"Записываю {duration} сек... Говорите!")
        audio = sd.rec(int(duration * samplerate), samplerate=samplerate, channels=1, dtype='float32')
        sd.wait()
        
        max_amplitude = np.max(np.abs(audio))
        print(f"Максимальная амплитуда: {max_amplitude:.4f}")
        
        if max_amplitude > 0.01:
            print("✅ Микрофон работает! Звук записан.")
        else:
            print("⚠️ Очень тихий сигнал. Проверьте микрофон.")
        
        return True
    except Exception as e:
        print(f"❌ Ошибка записи: {e}")
        return False

def test_loopback_recording():
    """Тест записи системного звука"""
    print("\n" + "="*60)
    print("🔁 ТЕСТ 4: Запись 2 сек системного звука (Loopback)")
    print("="*60)
    
    try:
        import soundcard as sc
        import numpy as np
        
        # Найти loopback устройство
        mics = sc.all_microphones(include_loopback=True)
        loopback = None
        for mic in mics:
            if mic.isloopback:
                loopback = mic
                break
        
        if not loopback:
            print("⚠️ Loopback не найден. Пропускаю тест.")
            return False
        
        print(f"Использую: {loopback.name}")
        print("Записываю 2 сек... Включите любой звук на компьютере!")
        
        with loopback.recorder(samplerate=48000, channels=2) as rec:
            data = rec.record(numframes=48000 * 2)
        
        max_amplitude = np.max(np.abs(data))
        print(f"Максимальная амплитуда: {max_amplitude:.4f}")
        
        if max_amplitude > 0.01:
            print("✅ Loopback работает! Системный звук захвачен.")
        else:
            print("⚠️ Тихо. Проиграйте какой-нибудь звук во время теста.")
        
        return True
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "🔬"*30)
    print("   WHISPER QUICK-TYPE — ТЕСТ АУДИО СИСТЕМЫ")
    print("🔬"*30)
    
    results = []
    results.append(("Микрофоны", test_microphones()))
    results.append(("Системный звук", test_system_audio()))
    results.append(("Запись микрофона", test_recording_short()))
    results.append(("Loopback запись", test_loopback_recording()))
    
    print("\n" + "="*60)
    print("📊 ИТОГИ:")
    print("="*60)
    for name, ok in results:
        status = "✅ OK" if ok else "❌ FAIL"
        print(f"  {name}: {status}")
    
    print("\n" + "="*60)
    input("Нажмите Enter для выхода...")
