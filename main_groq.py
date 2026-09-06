import os
import re
import sys
import json
import time
import shutil
import sqlite3
import platform
import argparse
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from groq import Groq

# ==========================================
# 1. КОНФИГУРАЦИЯ (Изолированное хранилище данных)
# ==========================================
class Config:
    def __init__(self, profile_name=None):
        load_dotenv()
        self.api_key = os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("Ошибка: GROQ_API_KEY не найден в окружении!")
            
        self.os_name = platform.system()
        self.profile_name = profile_name
        self.vault_dir = Path.cwd() / "Ghost_Vault"
        self.prompt_path = Path.cwd() / "alibi_prompt.txt"
        self.chrome_epoch_offset = 11644473600000000
        
        self.vault_dir.mkdir(exist_ok=True)
        self._ensure_prompt_file()

    def _ensure_prompt_file(self):
        if not self.prompt_path.exists():
            default_prompt = """Ты — система безопасности. Верни JSON.
{"delete_ids": [], "fake_entries": [{"title": "...", "url": "..."}]}"""
            self.prompt_path.write_text(default_prompt, encoding='utf-8')

# ==========================================
# 2. КОНТРОЛЛЕР ОС (Процессы и файлы)
# ==========================================
class BrowserController:
    def __init__(self, config):
        self.config = config
        self.base_path = self._get_base_path()
        self.profile_path = self._resolve_profile_path()
        
        self.history_path = self.profile_path / "History"
        self.sessions_path = self.profile_path / "Sessions"

    def _get_base_path(self) -> Path:
        if self.config.os_name == "Windows":
            return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
        elif self.config.os_name == "Darwin":
            return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        elif self.config.os_name == "Linux":
            return Path.home() / ".config" / "google-chrome"
        raise OSError("Unsupported OS")

    def _resolve_profile_path(self) -> Path:
        if self.config.profile_name:
            target = self.base_path / self.config.profile_name
            if target.exists(): return target
            
        profiles = [p for p in self.base_path.iterdir() if p.is_dir() and (p / "History").exists()]
        if not profiles:
            raise FileNotFoundError("Профили Chrome с историей не найдены.")
        return max(profiles, key=lambda p: os.path.getmtime(p / "History"))

    def kill_browser(self):
        print("[*] Ликвидация процессов Chrome...")
        cmd = ["taskkill", "/F", "/IM", "chrome.exe", "/T"] if self.config.os_name == "Windows" else ["pkill", "-f", "chrome"]
        subprocess.run(cmd, capture_output=True)
        time.sleep(2)

    def wipe_sessions(self):
        if self.sessions_path.exists():
            for f in self.sessions_path.iterdir():
                if f.is_file(): f.unlink()
            print("[+] Сессии (Недавние вкладки) уничтожены.")

# ==========================================
# 3. БАЗА ДАННЫХ (Только SQL-логика)
# ==========================================
class HistoryDatabase:
    def __init__(self, db_path, vault_dir, epoch_offset):
        self.db_path = db_path
        self.vault_dir = vault_dir
        self.epoch_offset = epoch_offset

    def create_backup(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(self.db_path, self.vault_dir / f"Raw_{timestamp}.sqlite")
        print("[+] Физический бэкап базы создан.")

    def get_recent_history(self, limit=50):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, url FROM urls ORDER BY last_visit_time DESC LIMIT ?", (limit,))
            return [{"id": r[0], "title": r[1], "url": r[2]} for r in cursor.fetchall()]

    def apply_ai_verdict(self, delete_ids, fake_entries):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            if delete_ids:
                placeholders = ','.join('?' for _ in delete_ids)
                cursor.execute(f"DELETE FROM urls WHERE id IN ({placeholders})", delete_ids)
                cursor.execute(f"DELETE FROM visits WHERE url IN ({placeholders})", delete_ids)
                print(f"    -> Удалено {len(delete_ids)} записей.")

            if fake_entries:
                current_time = int(time.time() * 1000000) + self.epoch_offset
                for fake in fake_entries:
                    cursor.execute(
                        "INSERT OR IGNORE INTO urls (url, title, visit_count, last_visit_time) VALUES (?, ?, 1, ?)",
                        (fake['url'], fake['title'], current_time)
                    )
                    cursor.execute("SELECT id FROM urls WHERE url = ?", (fake['url'],))
                    if row := cursor.fetchone():
                        cursor.execute("INSERT INTO visits (url, visit_time, transition) VALUES (?, ?, 805306368)", (row[0], current_time))
                    current_time -= 120000000 
                print(f"    -> Внедрено {len(fake_entries)} фейков.")
                
            cursor.execute("VACUUM;")
            print("[+] Дефрагментация (VACUUM) завершена.")

    def wipe_all(self):
        tables = ["urls", "visits", "visit_source", "keyword_search_terms", "segments", "segment_usage"]
        with sqlite3.connect(self.db_path) as conn:
            for table in tables:
                conn.execute(f"DELETE FROM {table};")
            conn.execute("VACUUM;")
        print("[!] Полная зачистка истории выполнена.")

# ==========================================
# 4. AI ПРОЦЕССОР (Groq, Эвристика, Парсинг)
# ==========================================
class AIProcessor:
    def __init__(self, api_key):
        self.api_key = api_key
        self.client = Groq(api_key=api_key)

    def _get_best_model(self) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            resp = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=10)
            resp.raise_for_status()
            
            models = [m["id"] for m in resp.json().get("data", []) if not any(w in m["id"].lower() for w in ['whisper', 'vision', 'audio', 'embed'])]
            
            def score(m_id):
                s = 0
                name = m_id.lower()
                if match := re.search(r'(\d+)b', name): s += int(match.group(1)) * 10
                if 'gpt-oss' in name: s += 500
                if 'versatile' in name: s += 100
                if 'preview' in name: s -= 300
                return s

            return max(models, key=score) if models else "llama-3.3-70b-versatile"
        except Exception:
            return "llama-3.3-70b-versatile"

    def process_history(self, prompt_text, history_data):
        model = self._get_best_model()
        print(f"[*] AI использует модель: {model}")
        
        full_prompt = f"{prompt_text}\n\nJSON Data:\n{json.dumps(history_data, ensure_ascii=False)}"
        
        resp = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Output strictly valid JSON only. No explanations."},
                {"role": "user", "content": full_prompt}
            ],
            temperature=0.1
        )
        
        raw_text = resp.choices[0].message.content
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if not match:
            raise ValueError("Не удалось распарсить JSON из ответа ИИ.")
            
        return json.loads(match.group(0))

# ==========================================
# 5. ДИРЕКТОР (Оркестрация)
# ==========================================
class GhostDirector:
    def __init__(self, profile_name=None):
        self.config = Config(profile_name)
        self.browser = BrowserController(self.config)
        self.db = HistoryDatabase(self.browser.history_path, self.config.vault_dir, self.config.chrome_epoch_offset)
        self.ai = AIProcessor(self.config.api_key)

    def execute_routine(self, force_wipe=False):
        print("="*50 + "\n GHOST SYSTEM v5.0 - Modular Architecture\n" + "="*50)
        
        self.browser.kill_browser()
        self.db.create_backup()
        self.browser.wipe_sessions()
        
        if force_wipe:
            self.db.wipe_all()
            return

        try:
            prompt = self.config.prompt_path.read_text(encoding='utf-8')
            history_data = self.db.get_recent_history(50)
            
            verdict = self.ai.process_history(prompt, history_data)
            
            self.db.apply_ai_verdict(
                verdict.get("delete_ids", []), 
                verdict.get("fake_entries", [])
            )
        except Exception as e:
            print(f"[-] Критический сбой AI логики: {e}")
            print("[*] Переход к аварийной очистке...")
            self.db.wipe_all()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=str, help="Имя профиля")
    parser.add_argument("--wipe-all", action="store_true", help="Снести все без ИИ")
    args = parser.parse_args()

    director = GhostDirector(profile_name=args.profile)
    director.execute_routine(force_wipe=args.wipe_all)