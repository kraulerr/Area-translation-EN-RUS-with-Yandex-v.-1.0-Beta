"""
OCR Переводчик с экрана
Автор: Yjin Tabet
Описание: Приложение для автоматического перевода текста с экрана через Яндекс.Переводчик
"""

import tkinter as tk
import mss
import pytesseract
from PIL import Image
import threading
import time
import re
import keyboard
import json
import os
import sys
import requests
import jwt
from datetime import datetime, timedelta
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import base64

def resource_path(relative_path):
    """Получение правильного пути к ресурсам для exe файла"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- НАСТРОЙКИ ---
KEY_FILE = resource_path("key.json")
# ЗАМЕНИТЕ НА ВАШ FOLDER_ID ИЗ ЯНДЕКС.ОБЛАКА
FOLDER_ID = "ВАШ_FOLDER_ID_ЗДЕСЬ" 

UPDATE_INTERVAL = 0.1
MIN_WIN_SIZE = 50

def create_jwt(key_data):
    """Создает JWT токен для авторизации в Яндекс.Облаке"""
    now = int(time.time())
    payload = {
        "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        "iss": key_data["service_account_id"],
        "sub": key_data["service_account_id"],
        "iat": now,
        "exp": now + 3600
    }
    
    private_key = key_data["private_key"]
    token = jwt.encode(
        payload,
        private_key,
        algorithm="PS256",
        headers={"kid": key_data["id"]}
    )
    return token

class TranslatorApp:
    """Основной класс приложения"""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Yandex Translator (JWT)")
        self.root.withdraw() 
        
        self.stop_flag = False
        self.last_text = ""
        self.current_text = ""
        self.iam_token = None
        self.token_expires = 0
        self.key_data = None
        self.translation_requested = False
        
        # Загрузка ключа
        if not os.path.exists(KEY_FILE):
            print(f"ОШИБКА: Файл '{KEY_FILE}' не найден!")
        else:
            try:
                with open(KEY_FILE, 'r', encoding='utf-8') as f:
                    self.key_data = json.load(f)
                if "private_key" not in self.key_data:
                    print("ОШИБКА: Неправильный формат ключа")
                else:
                    print("Ключ JWT успешно загружен.")
                    self.refresh_token()
            except Exception as e:
                print(f"Ошибка загрузки ключа: {e}")
        
        # Создание окон
        self.capture_win = CaptureWindow(self.root, self.on_close_capture)
        self.result_win = ResultWindow(self.root, self.on_close_result)
        
        # Запуск основного цикла
        self.thread = threading.Thread(target=self.translation_loop, daemon=True)
        self.thread.start()
        
        self.setup_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_exit_all)

    def refresh_token(self):
        """Получает IAM токен через JWT"""
        if not self.key_data:
            return False
        
        try:
            jwt_token = create_jwt(self.key_data)
            url = "https://iam.api.cloud.yandex.net/iam/v1/tokens"
            headers = {"Content-Type": "application/json"}
            body = {"jwt": jwt_token}
            
            resp = requests.post(url, headers=headers, json=body)
            
            if resp.status_code != 200:
                print(f"!!! ОШИБКА ОТ ЯНДЕКСА ({resp.status_code}): {resp.text}")
                return False
            
            data = resp.json()
            self.iam_token = data["iamToken"]
            self.token_expires = datetime.now() + timedelta(minutes=55)
            print("IAM токен успешно получен.")
            return True
            
        except Exception as e:
            print(f"Критическая ошибка получения токена: {e}")
            import traceback
            traceback.print_exc()
            return False

    def translate_with_yandex(self, text):
        """Переводит текст через API Яндекс.Переводчика"""
        if not self.iam_token or datetime.now() > self.token_expires:
            if not self.refresh_token():
                return "Ошибка авторизации"
        
        url = "https://translate.api.cloud.yandex.net/translate/v2/translate"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.iam_token}"
        }
        body = {
            "targetLanguageCode": "ru",
            "sourceLanguageCode": "en",
            "texts": [text],
            "folderId": FOLDER_ID
        }
        
        try:
            resp = requests.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            if "translations" in data and len(data["translations"]) > 0:
                return data["translations"][0]["text"]
            return "Нет перевода"
        except Exception as e:
            err = str(e)
            if "401" in err or "403" in err:
                self.token_expires = datetime.now()
                return "Обновление токена..."
            return f"Ошибка: {str(e)[:30]}"

    def setup_hotkeys(self):
        """Настройка горячих клавиш"""
        keyboard.add_hotkey('F1', self.on_close_capture)
        keyboard.add_hotkey('F2', self.on_close_result)
        keyboard.add_hotkey('F3', self.on_exit_all)
        keyboard.add_hotkey('t', self.request_translation)
        print("Горячие клавиши: F1 (рамка), F2 (перевод), F3 (выход), T (перевести)")

    def request_translation(self):
        """Запросить перевод текущего текста"""
        self.translation_requested = True
        print("Запрошен перевод...")

    def on_close_capture(self):
        if self.capture_win:
            self.capture_win.close()
            self.capture_win = None

    def on_close_result(self):
        if self.result_win:
            self.result_win.close()
            self.result_win = None

    def on_exit_all(self):
        self.stop_flag = True
        keyboard.unhook_all()
        if self.capture_win: self.capture_win.close()
        if self.result_win: self.result_win.close()
        self.root.quit()
        self.root.destroy()

    def translation_loop(self):
        """Основной цикл обработки изображений"""
        while not self.stop_flag:
            try:
                if not self.capture_win or not self.capture_win.is_open:
                    time.sleep(0.1)
                    continue
                
                if not self.iam_token:
                    time.sleep(0.1)
                    continue

                x, y, w, h = self.capture_win.get_geometry()
                
                if w < MIN_WIN_SIZE or h < MIN_WIN_SIZE:
                    time.sleep(0.1)
                    continue

                with mss.MSS() as sct:
                    monitor = {"left": x, "top": y, "width": w, "height": h}
                    screenshot = sct.grab(monitor)
                    img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

                text = pytesseract.image_to_string(img, lang='eng', config='--psm 6')
                text = re.sub(r'\s+', ' ', text).strip()

                if text and text != self.last_text:
                    self.current_text = text
                    self.last_text = text
                    if self.result_win and self.result_win.is_open:
                        self.root.after(0, self.result_win.update_text, f"[Оригинал] {text}")

                if self.translation_requested and self.current_text:
                    self.translation_requested = False
                    if self.result_win and self.result_win.is_open:
                        self.root.after(0, self.result_win.update_text, "🔄 Перевод...")
                    
                    translated = self.translate_with_yandex(self.current_text)
                    
                    if self.result_win and self.result_win.is_open:
                        self.root.after(0, self.result_win.update_text, translated)
                
                time.sleep(UPDATE_INTERVAL)

            except Exception as e:
                time.sleep(0.1)

# --- Классы окон интерфейса ---
class CaptureWindow:
    """Окно захвата области экрана"""
    def __init__(self, parent, close_callback):
        self.win = tk.Toplevel(parent)
        self.win.title("Capture")
        self.win.attributes('-topmost', True)
        self.win.overrideredirect(True)
        self.transparent_color = '#00FF00'
        self.win.configure(bg=self.transparent_color)
        self.win.wm_attributes('-transparentcolor', self.transparent_color)
        self.is_open = True
        self.close_callback = close_callback
        self.canvas = tk.Canvas(self.win, bg=self.transparent_color, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.rect_id = self.canvas.create_rectangle(0, 0, 0, 0, outline='#808080', width=4, fill='')
        self.canvas.bind("<Configure>", self.resize_rect)
        self.win.geometry("300x100+100+100")
        self.setup_mouse_events()

    def resize_rect(self, event):
        self.canvas.coords(self.rect_id, 0, 0, event.width, event.height)

    def setup_mouse_events(self):
        self.canvas.bind("<ButtonPress-1>", self.start_move)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.grip = tk.Label(self.win, bg='#FFFFFF', cursor='bottom_right_corner', relief='flat')
        self.grip.place(relx=1.0, rely=1.0, anchor='se', width=20, height=20)
        self.grip.bind("<ButtonPress-1>", self.start_resize)
        self.grip.bind("<B1-Motion>", self.on_resize)
        self.grip.lift()

    def start_move(self, event):
        self.drag_start_x = event.x_root - self.win.winfo_x()
        self.drag_start_y = event.y_root - self.win.winfo_y()

    def on_move(self, event):
        self.win.geometry(f"+{event.x_root - self.drag_start_x}+{event.y_root - self.drag_start_y}")

    def start_resize(self, event):
        self.start_width = self.win.winfo_width()
        self.start_height = self.win.winfo_height()
        self.start_x = event.x_root
        self.start_y = event.y_root

    def on_resize(self, event):
        dx = event.x_root - self.start_x
        dy = event.y_root - self.start_y
        new_w = max(MIN_WIN_SIZE, self.start_width + dx)
        new_h = max(MIN_WIN_SIZE, self.start_height + dy)
        self.win.geometry(f"{int(new_w)}x{int(new_h)}")

    def get_geometry(self):
        return self.win.winfo_x(), self.win.winfo_y(), self.win.winfo_width(), self.win.winfo_height()

    def close(self):
        self.is_open = False
        self.win.destroy()

class ResultWindow:
    """Окно отображения результата"""
    def __init__(self, parent, close_callback):
        self.win = tk.Toplevel(parent)
        self.win.title("Result")
        self.win.attributes('-topmost', True)
        self.win.attributes('-alpha', 0.95)
        self.win.overrideredirect(True)
        self.win.configure(bg='#1e1e1e')
        self.is_open = True
        self.close_callback = close_callback
        self.font = ("Segoe UI", 14, "bold")
        self.label = tk.Label(self.win, text="Ожидание...", fg="#ffffff", bg='#1e1e1e',
                              font=self.font, justify=tk.LEFT, anchor="nw",
                              wraplength=400, padx=10, pady=10)
        self.label.pack(fill=tk.BOTH, expand=True)
        self.win.geometry("400x150+100+250")
        self.setup_mouse_events()

    def setup_mouse_events(self):
        self.win.bind("<ButtonPress-1>", self.start_move)
        self.win.bind("<B1-Motion>", self.on_move)
        self.grip = tk.Label(self.win, bg='#555555', cursor='bottom_right_corner')
        self.grip.place(relx=1.0, rely=1.0, anchor='se', width=15, height=15)
        self.grip.bind("<ButtonPress-1>", self.start_resize)
        self.grip.bind("<B1-Motion>", self.on_resize)

    def start_move(self, event):
        self.drag_start_x = event.x_root - self.win.winfo_x()
        self.drag_start_y = event.y_root - self.win.winfo_y()

    def on_move(self, event):
        self.win.geometry(f"+{event.x_root - self.drag_start_x}+{event.y_root - self.drag_start_y}")

    def start_resize(self, event):
        self.start_width = self.win.winfo_width()
        self.start_height = self.win.winfo_height()
        self.start_x = event.x_root
        self.start_y = event.y_root

    def on_resize(self, event):
        dx = event.x_root - self.start_x
        dy = event.y_root - self.start_y
        new_w = max(MIN_WIN_SIZE, self.start_width + dx)
        new_h = max(MIN_WIN_SIZE, self.start_height + dy)
        self.win.geometry(f"{int(new_w)}x{int(new_h)}")
        self.label.config(wraplength=int(new_w) - 20)

    def update_text(self, text):
        if self.is_open:
            self.label.config(text=text)

    def close(self):
        self.is_open = False
        self.win.destroy()

if __name__ == "__main__":
    # Проверка FOLDER_ID
    if FOLDER_ID == "ВАШ_FOLDER_ID_ЗДЕСЬ":
        print("ОШИБКА: Вы не вставили FOLDER_ID в код!")
    else:
        print("FOLDER_ID установлен.")
    
    try:
        app = TranslatorApp()
        app.root.mainloop()
    except KeyboardInterrupt:
        pass
