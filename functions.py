import os
import json
import re
import signal
import subprocess
import sys
import tempfile
import time
import threading
import atexit
from collections import namedtuple
from pathlib import Path
import pickle
from urllib import response
import xml.etree.ElementTree as ET
import html
import customtkinter as ctk
import whisper_timestamped
from detoxify import Detoxify
import multiprocessing
import shutil
import PIL
from PIL import Image
from io import BytesIO
import requests
import podcastparser
import urllib.request

MAX_EPISODES = 20

# Path to library.json
LIBRARY_FILE = str(Path(__file__).resolve().parent / "library.json")

worker_model = None
global_pool = None
shutdown_in_progress = False
transcription_in_progress = False
transcription_thread = None
detox_model = None

WordToMute = namedtuple('WordToMute', ['word', 'start', 'end'])
SegmentToMute = namedtuple('SegmentToMute', ['start', 'end'])

#Used to read specifically config
def read_config(file_path):
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
#Used to read any other JSON
def read_json(file_path):
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
#Used to write specifically to config
def write_config(file_path, content):
    try:
        with open(file_path, 'w') as outfile:
            json.dump(content, outfile, indent=4, sort_keys=True)
    except FileNotFoundError:
        print("Config not found")
#Takes input and writes the corresponding model size to JSON
def size_finder(size_input: str, config_dict: dict, script_dir: Path) -> int | str:
    models = {
        "1": ("tiny", 1),
        "2": ("base", 1),
        "3": ("small", 2),
        "4": ("medium", 5),
        "5": ("large", 10),
        "6": ("turbo", 6),
    }
    
    key = size_input.lower().strip()
    if key not in models:
        print("Please select a valid number")
        return "F"
    
    model_name, vram_cost = models[key]
    config_dict["model_size"] = model_name
    write_config(str(script_dir / "config.json"), config_dict)
    return vram_cost
#Takes input and updates the corresponding catagory
def category_finder(input_str: str, config_dict: dict, value: float, script_dir: Path) -> None:
    valid_categories = {"t", "st", "th", "o", "id", "i"}
    key = input_str.lower().strip()
    
    if key not in valid_categories:
        print("Please select a valid category")
        return
    
    config_dict[key] = value
    write_config(str(script_dir / "config.json"), config_dict)
#Takes a string input and makes it into an output suitable for the catagory_finder above
def severity_tweaker(input_str: str, config_dict: dict, script_dir: Path) -> None:
    try:
        category, value = input_str.split("-")
        float_value = float(value)
        
        if not 0 <= float_value <= 1:
            print("Number must be between 0 and 1")
            return
        
        category_finder(category, config_dict, float_value, script_dir)
    except (ValueError, IndexError):
        print("Invalid format. Use: category-value (e.g., st-0.5)")
#Initializes config if the config.json is missing, and loads the config data, if it exists, for future use
def config_menu(script_dir: Path) -> None:
    config_data = {
        "file_path": str(script_dir),
        "worker_count": "1",
        "model_size": "small",
        "t": 0.5, "st": 0.5, "o": 0.5, "th": 0.5, "i": 0.5, "id": 0.5
    }
    
    try:
        with open(str(script_dir / "config.json"), 'r') as f:
            config_data = json.load(f)
    except FileNotFoundError:
        pass
    
    print("\n\nSelect model size:\n 1: tiny (1GB)\n 2: base (1GB)\n 3: small (2GB)\n"
          " 4: medium (5GB)\n 5: large (10GB)\n 6: turbo (6GB)")
    while True:
        model_size = input("\nInput a number: ")
        vram_cost = size_finder(model_size, config_data, script_dir)
        if vram_cost != "F":
            break
    
    if input("\nChange file paths from default? (y/N): ").lower().strip() == "y":
        new_dir = input("Enter path: ").strip()
    else:
        new_dir = str(script_dir)
    
    # Ensure absolute path (relative paths are relative to script_dir, not cwd)
    if not os.path.isabs(new_dir):
        new_dir = str(script_dir / new_dir)
    
    config_data["file_path"] = new_dir
    write_config(str(script_dir / "config.json"), config_data)
    
    while True:
        try:
            worker_count = input("\nNumber of workers: ").strip()
            int_count = int(worker_count)
            vram_total = int_count * vram_cost
            if input(f"\nWarning: Uses {vram_total}GB VRAM. Continue? (Y/n): ").lower().strip() != "n":
                config_data["worker_count"] = worker_count
                write_config(str(script_dir / "config.json"), config_data)
                break
        except ValueError:
            print("Please enter a valid number")
    
    if input("\nConfigure toxicity thresholds? (y/N): ").lower().strip() == "y":
        print("\nSet thresholds (category-value, e.g., st-0.5):\n"
              "t=toxicity, st=severe, o=obscene, th=threats, i=insults, id=identity")
        while True:
            severity_tweaker(input("Enter setting (or press Enter to skip): "), config_data, script_dir)
            if input("Continue? (y/N): ").lower().strip() != "y":
                break
    
    for subdir in ['Input', 'Output', '.bridge']:
        os.makedirs(os.path.join(new_dir, subdir), exist_ok=True)
    
    write_config(str(script_dir / "config.json"), config_data)
    print("\nConfiguration complete!")

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"
MODEL_VRAM_REQUIREMENTS = {
    "tiny": 1,
    "base": 1,
    "small": 2,
    "medium": 5,
    "large": 10,
    "turbo": 6,
}
MODEL_OPTIONS = ["tiny", "base", "small", "medium", "large", "turbo"]
DEFAULT_CONFIG = {
    "file_path": str(Path(__file__).resolve().parent),
    "i": 0.5,
    "id": 0.5,
    "model_size": "tiny",
    "o": 0.5,
    "st": 0.5,
    "t": 0.5,
    "th": 0.5,
    "worker_count": "1",
}
CONFIG_ORDER = ["file_path", "i", "id", "model_size", "o", "st", "t", "th", "worker_count"]


def load_app_config(config_path: Path | str | None = None) -> dict:
    config_file = Path(config_path) if config_path else CONFIG_FILE
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
            if not isinstance(config_data, dict):
                return DEFAULT_CONFIG.copy()
            return {**DEFAULT_CONFIG, **config_data}
    except FileNotFoundError:
        # Config doesn't exist, create it with defaults
        save_app_config(DEFAULT_CONFIG.copy(), config_path)
        return DEFAULT_CONFIG.copy()
    except json.JSONDecodeError:
        return DEFAULT_CONFIG.copy()
    except Exception as e:
        print(f"Error loading app config: {e}", file=sys.stderr)
        return DEFAULT_CONFIG.copy()


def save_app_config(config: dict, config_path: Path | str | None = None) -> None:
    config_file = Path(config_path) if config_path else CONFIG_FILE
    ordered = {key: config.get(key, DEFAULT_CONFIG[key]) for key in CONFIG_ORDER}
    ordered["worker_count"] = str(ordered.get("worker_count", "1"))
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(ordered, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving app config: {e}", file=sys.stderr)


class SettingsEntry(ctk.CTkFrame):
    def __init__(self, master, config_path: Path | str | None = None):
        super().__init__(master, fg_color="transparent")
        self.config_path = Path(config_path) if config_path else CONFIG_FILE
        
        # Check if config exists before loading (to detect if we need to create it)
        config_exists = self.config_path.exists()
        
        self._config_data = load_app_config(self.config_path)
        self.field_widgets: dict[str, ctk.CTkEntry | ctk.CTkOptionMenu] = {}
        self.error_label: ctk.CTkLabel
        self.vram_label: ctk.CTkLabel
        
        # Show notification if config was just created
        self._show_notification_on_load = not config_exists
        
        self.build_ui()
        
        # Schedule notification after UI is fully rendered and root window exists
        if self._show_notification_on_load:
            # Use a longer delay to ensure widget is fully set up
            self.after(1500, self._show_config_created_notification)

    def build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

        header_frame = ctk.CTkFrame(self, fg_color="#1f1f1f", border_width=1, border_color="#444444", corner_radius=16)
        header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(16, 10))
        header_frame.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(header_frame, text="Settings", font=ctk.CTkFont(size=24, weight="bold"))
        title.grid(row=0, column=0, padx=20, pady=20, sticky="w")

        description = ctk.CTkLabel(
            header_frame,
            text="Edit the exact JSON settings and review estimated VRAM usage.",
            font=ctk.CTkFont(size=12),
            text_color="#bbbbbb",
            wraplength=700,
            justify="left"
        )
        description.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        content_frame = ctk.CTkFrame(self, fg_color="#1f1f1f", border_width=1, border_color="#444444", corner_radius=16)
        content_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=20, pady=(0, 20))
        content_frame.grid_columnconfigure(0, weight=1)
        content_frame.grid_columnconfigure(1, weight=1)

        self._add_row(0, "File path", "file_path", parent=content_frame)
        self._add_row(1, "Worker count", "worker_count", parent=content_frame)

        model_label = ctk.CTkLabel(content_frame, text="Model size:", anchor="w")
        model_label.grid(row=2, column=0, padx=20, pady=(10, 5), sticky="w")
        model_select = ctk.CTkOptionMenu(
            content_frame,
            values=MODEL_OPTIONS,
            command=lambda _: self.update_vram_label()
        )
        model_select.set(self._config_data.get("model_size", "tiny"))
        model_select.grid(row=2, column=1, padx=20, pady=(10, 5), sticky="ew")
        self.field_widgets["model_size"] = model_select

        self.vram_label = ctk.CTkLabel(content_frame, text="Estimated VRAM usage: -- GB", anchor="w")
        self.vram_label.grid(row=3, column=0, columnspan=2, padx=20, pady=(5, 10), sticky="w")
        self.update_vram_label()

        threshold_frame = ctk.CTkFrame(content_frame, fg_color="#1f1f1f")
        threshold_frame.grid(row=4, column=0, columnspan=2, padx=20, pady=(10, 10), sticky="ew")
        threshold_frame.grid_columnconfigure((0, 1, 2), weight=1)

        threshold_keys = ["t", "st", "o", "th", "i", "id"]
        threshold_titles = {
            "t": "toxicity",
            "st": "severe",
            "o": "obscene",
            "th": "threats",
            "i": "insults",
            "id": "identity",
        }
        for index, key in enumerate(threshold_keys):
            row = index // 3
            col = index % 3
            label = ctk.CTkLabel(threshold_frame, text=f"{threshold_titles[key]}:", anchor="w")
            label.grid(row=row * 2, column=col, padx=10, pady=(10, 0), sticky="w")
            entry = ctk.CTkEntry(
                threshold_frame,
                placeholder_text="0.0 - 1.0",
                width=100,
                border_width=1,
                corner_radius=10
            )
            entry.insert(0, str(self._config_data.get(key, 0.5)))
            entry.grid(row=row * 2 + 1, column=col, padx=10, pady=(5, 10), sticky="ew")
            self.field_widgets[key] = entry

        threshold_keys = ["t", "st", "o", "th", "i", "id"]
        threshold_titles = {
            "t": "toxicity",
            "st": "severe",
            "o": "obscene",
            "th": "threats",
            "i": "insults",
            "id": "identity",
        }
        for index, key in enumerate(threshold_keys):
            row = index // 3
            col = index % 3
            label = ctk.CTkLabel(threshold_frame, text=f"{threshold_titles[key]}:", anchor="w")
            label.grid(row=row * 2, column=col, padx=10, pady=(10, 0), sticky="w")
            entry = ctk.CTkEntry(
                threshold_frame,
                placeholder_text="0.0 - 1.0",
                width=100
            )
            entry.insert(0, str(self._config_data.get(key, 0.5)))
            entry.grid(row=row * 2 + 1, column=col, padx=10, pady=(5, 10), sticky="ew")
            self.field_widgets[key] = entry

        self.error_label = ctk.CTkLabel(self, text="", text_color="#ff5555", anchor="w")
        self.error_label.grid(row=6, column=0, columnspan=2, padx=20, pady=(0, 10), sticky="w")

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.grid(row=7, column=0, columnspan=2, padx=20, pady=(5, 20), sticky="ew")
        button_frame.grid_columnconfigure(0, weight=1)
        button_frame.grid_columnconfigure(1, weight=1)

        save_button = ctk.CTkButton(button_frame, text="Save settings", command=self.save_config)
        save_button.grid(row=0, column=0, padx=(0, 5), sticky="ew")
        reload_button = ctk.CTkButton(button_frame, text="Reload settings", command=self.reload_config)
        reload_button.grid(row=0, column=1, padx=(5, 0), sticky="ew")

    def _add_row(self, row, title, key, parent=None):
        parent_widget = parent if parent is not None else self
        label = ctk.CTkLabel(parent_widget, text=title + ":", anchor="w")
        label.grid(row=row, column=0, padx=20, pady=(10, 5), sticky="w")
        entry = ctk.CTkEntry(parent_widget, border_width=1, corner_radius=10)
        entry.insert(0, str(self._config_data.get(key, "")))
        entry.grid(row=row, column=1, padx=20, pady=(10, 5), sticky="ew")
        self.field_widgets[key] = entry

    def update_vram_label(self):
        model_size = self.field_widgets["model_size"].get()
        worker_count = self.field_widgets["worker_count"].get().strip()
        try:
            workers = max(0, int(worker_count))
        except ValueError:
            workers = 0
        vram_per_model = MODEL_VRAM_REQUIREMENTS.get(model_size, 1)
        total = workers * vram_per_model
        self.vram_label.configure(text=f"Estimated VRAM usage: {total} GB ({workers} worker(s) × {vram_per_model}GB)")

    def _gather_values(self) -> dict:
        config = {
            "file_path": self.field_widgets["file_path"].get().strip() or DEFAULT_CONFIG["file_path"],
            "worker_count": self.field_widgets["worker_count"].get().strip() or DEFAULT_CONFIG["worker_count"],
            "model_size": self.field_widgets["model_size"].get() or DEFAULT_CONFIG["model_size"],
        }
        for key in ["t", "st", "o", "th", "i", "id"]:
            raw = self.field_widgets[key].get().strip()
            config[key] = float(raw) if raw != "" else DEFAULT_CONFIG[key]
        return config

    def save_config(self):
        try:
            config = self._gather_values()
            if config["model_size"] not in MODEL_VRAM_REQUIREMENTS:
                raise ValueError("Invalid model size")
            if int(config["worker_count"]) < 1:
                raise ValueError("Worker count must be 1 or greater")
            for key in ["t", "st", "o", "th", "i", "id"]:
                value = float(config[key])
                if not 0 <= value <= 1:
                    raise ValueError(f"{key} must be between 0 and 1")
            save_app_config(config, self.config_path)
            self._config_data = config
            if self.error_label is not None:
                self.error_label.configure(text="Settings saved.", text_color="#7CFC00")
            self.update_vram_label()
        except Exception as e:
            self.error_label.configure(text=str(e), text_color="#ff5555")

    def reload_config(self):
        self._config_data = load_app_config(self.config_path)
        for key, widget in self.field_widgets.items():
            value = self._config_data.get(key, DEFAULT_CONFIG[key])
            if isinstance(widget, ctk.CTkOptionMenu):
                widget.set(value)
            else:
                widget.delete(0, "end")
                widget.insert(0, str(value))
        self.error_label.configure(text="Settings reloaded.", text_color="#8f8")
        self.update_vram_label()

    def _show_config_created_notification(self):
        """Show a popup notification when config is first created."""
        try:
            popup = ctk.CTkToplevel(self)
            popup.title("Auto-Configuration")
            popup.geometry("500x280")
            popup.resizable(False, False)
            popup.configure(fg_color="#111111")
            
            # Make it stay on top and focus on it
            popup.attributes("-topmost", True)
            popup.focus()
            
            # Wait for widget to update before positioning
            popup.update_idletasks()
            
            # Center the popup on the screen
            x = (popup.winfo_screenwidth() // 2) - (popup.winfo_width() // 2)
            y = (popup.winfo_screenheight() // 2) - (popup.winfo_height() // 2)
            popup.geometry(f"+{max(0, x)}+{max(0, y)}")
            
            # Title
            title_label = ctk.CTkLabel(
                popup,
                text="Default Configuration Created",
                font=ctk.CTkFont(size=18, weight="bold"),
                text_color="#ffffff"
            )
            title_label.pack(pady=(20, 10), padx=20)
            
            # Message
            message_label = ctk.CTkLabel(
                popup,
                text="It is highly advised to tailor your settings according to what you are cleaning, or you may experience over-cutting.",
                font=ctk.CTkFont(size=13),
                text_color="#bbbbbb",
                wraplength=420,
                justify="left"
            )
            message_label.pack(pady=(0, 20), padx=20)
            
            # Secondary message
            secondary_label = ctk.CTkLabel(
                popup,
                text="Please review the Settings tab to adjust model size, worker count, and toxicity thresholds to match your needs.",
                font=ctk.CTkFont(size=11),
                text_color="#888888",
                wraplength=420,
                justify="left"
            )
            secondary_label.pack(pady=(0, 20), padx=20)
            
            # OK button
            ok_button = ctk.CTkButton(
                popup,
                text="Got it",
                width=120,
                height=40,
                fg_color="#2563eb",
                hover_color="#1d4ed8",
                text_color="white",
                corner_radius=10,
                command=popup.destroy
            )
            ok_button.pack(pady=(0, 20))
        except Exception as e:
            print(f"Error showing notification: {e}", file=sys.stderr)

#Gets the duration of the audio
def get_audio_duration(file_path: str) -> float | None:
    """Return audio duration in seconds.

    Tries `ffprobe` first; if it's unavailable, falls back to using the
    `ffmpeg` binary (from imageio-ffmpeg) and parses the stderr for a
    Duration: HH:MM:SS.micro output.
    """
    try:
        # Prefer ffprobe if available
        cmd = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except FileNotFoundError:
        # ffprobe not present, fall through to ffmpeg parsing
        pass
    except subprocess.CalledProcessError:
        # ffprobe present but failed for this file, try ffmpeg parsing
        pass

    # Fallback: use ffmpeg and parse stderr for Duration: HH:MM:SS
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg_exe = 'ffmpeg'

    try:
        proc = subprocess.run([ffmpeg_exe, '-i', file_path], capture_output=True, text=True)
        stderr = proc.stderr or ''
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
        if m:
            h, mm, ss = m.groups()
            return float(int(h) * 3600 + int(mm) * 60 + float(ss))
        return None
    except Exception as e:
        print(f"Error: Could not get duration for {file_path} - {e}")
        return None
    
#Initializes the worker model to be used later

def worker_initializer(model_size: str) -> None:
    global worker_model
    # Monkey patch whisper to use the full ffmpeg path
    import imageio_ffmpeg
    import whisper.audio
    
    original_load_audio = whisper.audio.load_audio
    
    def patched_load_audio(file, sr=whisper.audio.SAMPLE_RATE):
        # Get the original command
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-threads", "0",
            "-i", file,
            "-f", "s16le",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            "-ar", str(sr),
            "-"
        ]
        # Replace "ffmpeg" with full path
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        cmd[0] = ffmpeg_path
        
        try:
            import subprocess
            import numpy as np
            out = subprocess.run(cmd, capture_output=True, check=True).stdout
            return np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to load audio: {e.stderr.decode()}") from e
    
    # Replace the function
    whisper.audio.load_audio = patched_load_audio
    
    worker_model = whisper_timestamped.load_model(model_size)

#Boots the workers, hands them the filename and path, and starts transcribing

def process_file(filename: str, input_path: str) -> dict:
    
    global worker_model
    # Ensure input_path is absolute
    input_path = os.path.abspath(input_path)
    file_path = os.path.join(input_path, filename)
    try:
        transcribed_file = whisper_timestamped.transcribe(worker_model, file_path)
        return transcribed_file
    except Exception as e:
        print(f"Error in whisper_timestamped.transcribe: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        raise

#Cleans up the workers

def cleanup_workers() -> None:
    
    global global_pool
    if global_pool:
        print("Shutting down worker pool...")
        global_pool.terminate()
        global_pool.join()
        global_pool = None

#Handles signals like ctrl c

def signal_handler(signum, frame):
    global shutdown_in_progress
    if shutdown_in_progress:
        return
    shutdown_in_progress = True
    try:
        cleanup_queue()
    except Exception:
        pass
    cleanup_workers()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
try:
    signal.signal(signal.SIGTERM, signal_handler)
except Exception:
    # Some platforms may not support SIGTERM
    pass

#Cleans text so that segment muting won't be inflated by swear words, staying context based

def clean_text(text: str, words_to_remove: list) -> str:
    cleaned = text
    for word in words_to_remove:
        cleaned = re.sub(rf'\b{re.escape(word)}\b', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

#Takes the file, keywords, and configured thresholds, and outputs the exact times needed to cut at

def audio_cleaner(transcribed_file: dict, bad_words: dict, thresholds: list) -> tuple:
    words_to_mute = []
    segments_to_mute = []
    swear_words = {s.lower() for s in bad_words.get('swears', [])}
    

    for segment in transcribed_file.get('segments', []):
        for word in segment.get('words', []):
            word_text = word.get('text', '').lower().strip('.,!?')
            if word_text in swear_words:
                words_to_mute.append(WordToMute(
                    word['text'],
                    float(word['start']),
                    float(word['end'])
                ))
    

    global detox_model
    if detox_model is None:
        print("Warning: detox_model not initialized")
        return words_to_mute, segments_to_mute
    
    swear_set = {m.word.lower().strip('.,!?') for m in words_to_mute}
    
    for segment in transcribed_file.get('segments', []):
        cleaned_text = clean_text(segment.get('text', ''), list(swear_set))
        
        if not cleaned_text.strip():
            continue
        
        ratings = detox_model.predict(cleaned_text)
        toxic = any([
            ratings.get('toxicity', 0) > thresholds[0],
            ratings.get('severe_toxicity', 0) > thresholds[1],
            ratings.get('obscene', 0) > thresholds[2],
            ratings.get('threat', 0) > thresholds[5],
            ratings.get('insult', 0) > thresholds[4],
            ratings.get('identity_attack', 0) > thresholds[3],
        ])
        
        if toxic:
            segments_to_mute.append(SegmentToMute(
                float(segment['start']),
                float(segment['end'])
            ))
    return words_to_mute, segments_to_mute

#Function to read from files such as .txt

def readfromFile(testfile):
    with open(testfile, "r") as file:
        return file.read()
    
#The beating heart of my code. Takes the input file, output path(I know it says file, just trust),
#the word level mutes, and segment level mutes, and runs them all through ffmpeg to cut the file accordingly

def mute_words(input_file: str, output_file: str, words_to_mute: list, segments_to_mute: list) -> bool:
    if not words_to_mute and not segments_to_mute:
        try:
            shutil.copy(input_file, output_file)
            return True
        except Exception as e:
            print(f"Error: Failed to copy clean audio file: {e}")
            return False


    

    all_segments = [(float(w.start), float(w.end)) for w in words_to_mute]
    all_segments.extend([(float(s.start), float(s.end)) for s in segments_to_mute])
    
    if not all_segments:
        try:
            shutil.copy(input_file, output_file)
            return True
        except Exception as e:
            print(f"Error: Failed to copy audio file: {e}")
            return False


    

    all_segments.sort()
    merged = []
    for start, end in all_segments:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    

    duration = get_audio_duration(input_file)
    if duration is None:
        print(f"Error: Could not process {input_file} - unable to get audio duration")
        return False

    

    segments_to_keep = []
    current_pos = 0.0
    
    for start, end in merged:
        if current_pos < start:
            segments_to_keep.append((current_pos, start))
        current_pos = max(current_pos, end)
    
    if current_pos < duration:
        segments_to_keep.append((current_pos, duration))
    
    # Bounds checking: ensure all segment times are within audio duration
    # Add small epsilon for floating-point precision issues
    epsilon = 0.001
    segments_to_keep = [(max(0, start), min(duration, end)) for start, end in segments_to_keep]
    segments_to_keep = [(start, end) for start, end in segments_to_keep if end - start > epsilon]
    
    if not segments_to_keep:
        print("All audio removed, creating silent file")
        try:
            result = subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '0.1', output_file], capture_output=True, text=True, check=True)
            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                return True
            else:
                print(f"Error: Failed to create silent audio file for {output_file}")
                return False
        except subprocess.CalledProcessError as e:
            print(f"Error: ffmpeg failed to create silent file: {e.stderr}")
            return False
    

    
    with tempfile.TemporaryDirectory() as temp_dir:
        file_ext = Path(input_file).suffix
        segment_files = []
        
        try:
            # Use imageio-ffmpeg binary if available to ensure ffmpeg executable is found
            try:
                import imageio_ffmpeg
                ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                ffmpeg_exe = 'ffmpeg'

            for i, (start, end) in enumerate(segments_to_keep):
                segment_file = os.path.join(temp_dir, f"segment_{i}{file_ext}")
                segment_files.append(segment_file)
                duration_segment = end - start
                cmd = [ffmpeg_exe, '-y', '-i', input_file, '-ss', str(start), '-t', str(duration_segment), '-vn', '-c:a', 'copy', segment_file]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"Error: Failed to extract segment {i} from {input_file}:\n{result.stderr}")
                    return False
                if not os.path.exists(segment_file):
                    print(f"Error: Segment file {segment_file} was not created by ffmpeg")
                    return False
            
            if len(segment_files) == 1:
                try:
                    shutil.copy(segment_files[0], output_file)
                except Exception as e:
                    print(f"Error: Failed to copy single segment to output: {e}")
                    return False
            else:
                concat_file = os.path.join(temp_dir, "concat.txt")
                with open(concat_file, 'w') as f:
                    f.write('\n'.join(f"file '{seg}'" for seg in segment_files))
                
                cmd = [ffmpeg_exe, '-y', '-f', 'concat', '-safe', '0', '-i', concat_file, '-c:a', 'copy', '-map_metadata', '0', output_file]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"Error: Concatenation failed for {output_file}:\n{result.stderr}")
                    return False
            
            
            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                print(f"Output written to {output_file}")
                return True
            else:
                print(f"Error: Output file {output_file} was not created or is empty")
                return False
        except Exception as e:
            print(f"Error processing audio: {e}")
            return False
#This function is the 'jumpstart' function. Called in main.py, it calls all the functions as they are needed
def run_program(config_dict: dict, script_dir: Path) -> list | None:
    model_size = config_dict["model_size"]
    path = config_dict["file_path"]
    

    if not os.path.isabs(path):
        path = str(script_dir / path)
    
    worker_counts = int(config_dict["worker_count"])
    
    thresholds = [
        config_dict["t"],
        config_dict["st"],
        config_dict["o"],
        config_dict["id"],
        config_dict["i"],
        config_dict["th"]
    ]
    
    with open(str(script_dir / "bad_words.pkl"), 'rb') as file:
        bad_words = pickle.load(file)
    
    global detox_model
    detox_model = Detoxify('original')
    
    input_path = os.path.join(path, 'Input')
    output_path = os.path.join(path, 'Output')
    
    in_files = {f for f in os.listdir(input_path) if os.path.isfile(os.path.join(input_path, f)) and not f.startswith('.')}
    out_files = {f for f in os.listdir(output_path) if os.path.isfile(os.path.join(output_path, f)) and not f.startswith('.')}
    
    working_list = sorted(in_files - out_files)
    file_number = 0
    for items in working_list:
        file_number += 1
    try:
        print(f"Processing {file_number} files!")
    except:
        pass
    
    if not working_list:
        print("No files to update")
        return None
    
    global global_pool
    results = []
    
    try:
        with multiprocessing.Pool(processes=worker_counts, initializer=worker_initializer, initargs=(model_size,)) as pool:
            global_pool = pool
            
            job_to_filename = {}
            for filename in working_list:
                async_result = pool.apply_async(process_file, (filename, input_path))
                job_to_filename[async_result] = filename
            
            pending = list(job_to_filename.keys())
            processed_files = {"success": [], "failed": []}
            while pending:
                for async_result in list(pending):
                    if async_result.ready():
                        filename = job_to_filename[async_result]
                        try:
                            transcribed_file = async_result.get()
                            results.append(transcribed_file)
                            words_to_mute, segments_to_mute = audio_cleaner(transcribed_file, bad_words, thresholds)
                            success = mute_words(
                                os.path.join(input_path, filename),
                                os.path.join(output_path, filename),
                                words_to_mute,
                                segments_to_mute
                            )
                            if success:
                                processed_files["success"].append(filename)
                                # File is immediately available in Output after mute_words completes
                                # Delete the input file after successful processing
                                input_file_path = os.path.join(input_path, filename)
                                try:
                                    os.remove(input_file_path)
                                    print(f"Sent to Output and removed from Input: {filename}")
                                except Exception as e:
                                    print(f"Warning: Could not delete input file {input_file_path}: {e}")
                            else:
                                processed_files["failed"].append(filename)
                        except Exception as e:
                            print(f"Error processing {filename}: {e}")
                            processed_files["failed"].append(filename)
                        pending.remove(async_result)
                if pending:
                    time.sleep(0.1)
                
            
            global_pool = None
            
            
            
            print(f"\n=== Processing Summary ===")
            print(f"Successfully processed: {len(processed_files['success'])} files")
            if processed_files['success']:
                for f in processed_files['success']:
                    print(f"  ✓ {f}")
            if processed_files['failed']:
                print(f"Failed to process: {len(processed_files['failed'])} files")
                for f in processed_files['failed']:
                    print(f"  ✗ {f}")
            print(f"========================\n")
            
            return results
    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected, shutting down...")
        if global_pool:
            global_pool.terminate()
            global_pool.join()
        raise
    except Exception as e:
        print(f"Error during processing: {e}")
        if global_pool:
            global_pool.terminate()
            global_pool.join()
        raise

def run_program_gui(config_dict: dict, script_dir: Path, status_callback=None, progress_callback=None) -> list | None:
    """GUI version of run_program that uses callbacks for status updates instead of print statements."""
    model_size = config_dict["model_size"]
    path = config_dict["file_path"]
    

    if not os.path.isabs(path):
        path = str(script_dir / path)
    
    worker_counts = int(config_dict["worker_count"])
    
    thresholds = [
        config_dict["t"],
        config_dict["st"],
        config_dict["o"],
        config_dict["id"],
        config_dict["i"],
        config_dict["th"]
    ]
    
    with open(str(script_dir / "bad_words.pkl"), 'rb') as file:
        bad_words = pickle.load(file)
    
    global detox_model
    detox_model = Detoxify('original')
    
    input_path = str(Path(__file__).resolve().parent / "Input")
    output_path = str(Path(__file__).resolve().parent / "Output")
    
    in_files = {f for f in os.listdir(input_path) if os.path.isfile(os.path.join(input_path, f)) and not f.startswith('.')}
    out_files = {f for f in os.listdir(output_path) if os.path.isfile(os.path.join(output_path, f)) and not f.startswith('.')}
    
    working_list = sorted(in_files - out_files)
    file_number = len(working_list)
    
    if status_callback:
        status_callback(f"Processing {file_number} files!")
    
    if not working_list:
        if status_callback:
            status_callback("No files to update")
        return None
    
    global global_pool
    results = []
    
    try:
        with multiprocessing.Pool(processes=worker_counts, initializer=worker_initializer, initargs=(model_size,)) as pool:
            global_pool = pool
            
            job_to_filename = {}
            for filename in working_list:
                async_result = pool.apply_async(process_file, (filename, input_path))
                job_to_filename[async_result] = filename
            
            pending = list(job_to_filename.keys())
            processed_files = {"success": [], "failed": []}
            completed_count = 0
            
            while pending:
                for async_result in list(pending):
                    if async_result.ready():
                        filename = job_to_filename[async_result]
                        completed_count += 1
                        try:
                            transcribed_file = async_result.get()
                            results.append(transcribed_file)
                            words_to_mute, segments_to_mute = audio_cleaner(transcribed_file, bad_words, thresholds)
                            success = mute_words(
                                os.path.join(input_path, filename),
                                os.path.join(output_path, filename),
                                words_to_mute,
                                segments_to_mute
                            )
                            if success:
                                processed_files["success"].append(filename)
                                # File is immediately available in Output after mute_words completes
                                if status_callback:
                                    status_callback(f"✓ Sent to Output: {filename}")
                                # Delete the input file after successful processing
                                input_file_path = os.path.join(input_path, filename)
                                try:
                                    os.remove(input_file_path)
                                    if status_callback:
                                        status_callback(f"  → Removed from Input: {filename}")
                                    # input file removed
                                except Exception as e:
                                    print(f"Warning: Could not delete input file {input_file_path}: {e}")
                            else:
                                processed_files["failed"].append(filename)
                                if status_callback:
                                    status_callback(f"✗ Failed: {filename}")
                        except Exception as e:
                            processed_files["failed"].append(filename)
                            if status_callback:
                                status_callback(f"✗ Error processing {filename}: {str(e)[:50]}")
                        pending.remove(async_result)
                        
                        # Update progress
                        if progress_callback:
                            progress_callback(completed_count, file_number)
                
                if pending:
                    time.sleep(0.1)
                
            
            global_pool = None
            
            # Final summary
            summary_msg = f"\n=== Processing Summary ===\n"
            summary_msg += f"Successfully processed: {len(processed_files['success'])} files\n"
            if processed_files['success']:
                for f in processed_files['success']:
                    summary_msg += f"  ✓ {f}\n"
            if processed_files['failed']:
                summary_msg += f"Failed to process: {len(processed_files['failed'])} files\n"
                for f in processed_files['failed']:
                    summary_msg += f"  ✗ {f}\n"
            summary_msg += "========================\n"
            
            if status_callback:
                status_callback(summary_msg)
            
            return results
    except KeyboardInterrupt:
        if status_callback:
            status_callback("\nKeyboard interrupt detected, shutting down...")
        if global_pool:
            global_pool.terminate()
            global_pool.join()
        raise
    except Exception as e:
        if status_callback:
            status_callback(f"Error during processing: {str(e)[:100]}")
        if global_pool:
            global_pool.terminate()
            global_pool.join()
        raise

BATCH_SIZE = 10


def _parse_itunes_duration(raw):
    """Convert iTunes duration (HH:MM:SS, MM:SS, or seconds) to milliseconds."""
    if not raw:
        return 0
    raw = raw.strip()
    if raw.isdigit():
        return int(raw) * 1000
    parts = raw.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    if len(parts) == 3:
        return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000
    if len(parts) == 2:
        return (parts[0] * 60 + parts[1]) * 1000
    return 0


def _strip_html(text):
    """Convert HTML to plain text for display in TK labels."""
    if not text:
        return ""

    text = str(text)
    # Convert common block-level tags to newlines.
    text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/div\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*li\s*>", "\n• ", text, flags=re.IGNORECASE)
    text = re.sub(r"<\s*/li\s*>", "", text, flags=re.IGNORECASE)

    # Remove all other tags.
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)

    # Normalize whitespace and blank lines.
    text = re.sub(r"\r\n?|\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def get_podcast_episodes(feed_url: str, offset: int = 0, limit: int = MAX_EPISODES):
    """Fetch episodes by parsing the podcast RSS feed."""
    if not feed_url:
        return []

    try:
        response = requests.get(feed_url, timeout=15)
        response.raise_for_status()
    except requests.RequestException:
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        return []

    ns = {
        "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }

    episodes = []
    for item in root.iter("item"):
        title = item.findtext("title", default="")

        enclosure = item.find("enclosure")
        audio_url = enclosure.get("url", "") if enclosure is not None else ""

        pub_date = item.findtext("pubDate", default="")

        duration_raw = item.findtext("itunes:duration", default="", namespaces=ns)
        duration_ms = _parse_itunes_duration(duration_raw)

        description = item.findtext("description", default="")
        if not description:
            description = item.findtext("itunes:summary", default="", namespaces=ns)
        if not description:
            description = item.findtext("content:encoded", default="", namespaces=ns)
        description = _strip_html(description)

        episodes.append({
            "track_name": title,
            "audio_url": audio_url,
            "track_time_ms": duration_ms,
            "release_date": pub_date,
            "description": description,
        })

    # Return episodes starting from offset, limited to the specified limit
    return episodes[offset:offset + limit]


def get_podcast_metadata(feed_url: str):
    if not feed_url:
        return {
            "title": "",
            "description": "",
            "author": "",
            "image_url": "",
        }

    try:
        response = requests.get(feed_url, timeout=15)
        response.raise_for_status()
    except requests.RequestException:
        return {
            "title": "",
            "description": "",
            "author": "",
            "image_url": "",
        }

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        return {
            "title": "",
            "description": "",
            "author": "",
            "image_url": "",
        }

    ns = {
        "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }

    channel = root.find("channel")
    if channel is None:
        channel = root

    title = channel.findtext("title", default="")
    description = channel.findtext("description", default="")
    if not description:
        description = channel.findtext("itunes:summary", default="", namespaces=ns)
    author = channel.findtext("itunes:author", default="", namespaces=ns)
    if not author:
        author = channel.findtext("author", default="")

    image_url = ""
    itunes_image = channel.find("itunes:image", namespaces=ns)
    if itunes_image is not None:
        image_url = itunes_image.get("href", "")
    if not image_url:
        image_tag = channel.find("image")
        if image_tag is not None:
            image_url = image_tag.findtext("url", default="")
    album_metadata = {
        "title": title,
        "description": _strip_html(description),
        "author": author,
        "image_url": image_url,
    }
    return album_metadata

def get_search_results(search_input: str, offset: int = 0):
    search_input = search_input.replace(" ", "+")
    search = f"https://itunes.apple.com/search?term={search_input}&media=podcast&limit={BATCH_SIZE}&offset={offset}"
    response = requests.get(search)
    data = response.json()
    results = []
    for item in data['results']:
        results.append({
            "id": item.get("collectionId"),
            "name": f"{item['artistName']} - {item['collectionName']}",
            "podcast_name": item.get("collectionName", ""),
            "artist_name": item.get("artistName", ""),
            "image_url": item.get("artworkUrl100", ""),
            "feed_url": item.get("feedUrl", ""),
        })
    return results

class TabButton(ctk.CTkButton):
    def __init__(self, master, tab_view, tab_name):
        super().__init__(master, text=tab_name, width=150, height=40,
            fg_color="#2563eb", hover_color="#1d4ed8", text_color="white",
            corner_radius=12,
            command=lambda: tab_view.set(tab_name))
        self.tab_view = tab_view
        self.tab_name = tab_name
        self.normal_fg_color = "#2563eb"
        self.normal_hover_color = "#1d4ed8"
        self.active_fg_color = "#1f2937"
        self.active_hover_color = "#111827"
        
    def set_active(self, is_active):
        if is_active:
            self.configure(
                fg_color=self.active_fg_color,
                hover_color=self.active_hover_color,
                state="disabled"
            )
        else:
            self.configure(
                fg_color=self.normal_fg_color,
                hover_color=self.normal_hover_color,
                state="normal"
            )
class TabEntry(ctk.CTkEntry):
    def __init__(self, master):
        super().__init__(master, placeholder_text="Search podcasts...", width=200, height=40)

class QuitButton(ctk.CTkButton):
    def __init__(self, master, app):
        self._app_ref = app
        super().__init__(master, text="Quit", width=150, height=40,
            fg_color="#cc0000", hover_color="#990000", text_color="white",
            command=self._on_quit)

    def _on_quit(self):
        try:
            cleanup_queue()
        except Exception:
            pass
        try:
            cleanup_workers()
        except Exception:
            pass
        try:
            self._app_ref.destroy()
        except Exception:
            try:
                sys.exit(0)
            except Exception:
                os._exit(0)

class TabView(ctk.CTkTabview):
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)


        self.add("Search")
        self.add("Library")
        self.add("Settings")


        self._segmented_button.grid_remove()


        self.button_widgets = {}
        self._library_entry: "LibraryEntry | None" = None


        self.set("Search")

    def register_button(self, tab_name, button_widget):
        self.button_widgets[tab_name] = button_widget

    def set(self, tab_name): # type: ignore
        super().set(tab_name)
        self._update_buttons(tab_name)
        
        # Refresh library when switching to library tab
        if tab_name == "Library" and self._library_entry is not None:
            self._library_entry.load_library()

    def _update_buttons(self, current_tab):
        for tab_name, button in self.button_widgets.items():
            if isinstance(button, TabButton):
                if tab_name == current_tab:
                    button.set_active(True)
                else:
                    button.set_active(False)

PODLIST = []
SELECTED_EPISODE_URLS = []  # Now stores dicts with 'url' and 'title'
LIBRARY_CHANGE_LISTENERS = []


def cleanup_queue():
    """Clear in-memory episode selections and remove all files in the Input folder."""
    global SELECTED_EPISODE_URLS, transcription_in_progress, transcription_thread

    # Clear in-memory selection list
    try:
        SELECTED_EPISODE_URLS.clear()
    except Exception:
        try:
            SELECTED_EPISODE_URLS = []
        except Exception:
            pass

    transcription_in_progress = False

    # Remove any files left in the Input directory
    try:
        input_dir = Path(__file__).resolve().parent / "Input"
        if input_dir.exists() and input_dir.is_dir():
            for entry in input_dir.iterdir():
                try:
                    if entry.is_file() or entry.is_symlink():
                        entry.unlink()
                    elif entry.is_dir():
                        shutil.rmtree(entry)
                except Exception as e:
                    print(f"Warning: could not remove {entry}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: cleanup_queue failed to clear Input folder: {e}", file=sys.stderr)


# Ensure queue is cleared on normal program exit
atexit.register(cleanup_queue)


def register_library_change_listener(listener):
    if callable(listener):
        LIBRARY_CHANGE_LISTENERS.append(listener)


def notify_library_change_listeners():
    for listener in LIBRARY_CHANGE_LISTENERS:
        try:
            listener()
        except Exception as e:
            print(f"Error notifying library listener: {e}", file=sys.stderr)

_SAFE_JSON_SENTINEL = object()

def _sanitize_for_json(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        sanitized = {}
        for key, val in value.items():
            if isinstance(key, str) and key.startswith("_"):
                continue
            nested = _sanitize_for_json(val)
            if nested is not _SAFE_JSON_SENTINEL:
                sanitized[key] = nested
        return sanitized
    if isinstance(value, (list, tuple)):
        sanitized_items = [_sanitize_for_json(item) for item in value]
        return [item for item in sanitized_items if item is not _SAFE_JSON_SENTINEL]
    return _SAFE_JSON_SENTINEL


def _sanitize_podcast_for_storage(podcast):
    if not isinstance(podcast, dict):
        return {}
    sanitized = _sanitize_for_json(podcast)
    return sanitized if isinstance(sanitized, dict) else {}


def load_library():
    """Load saved podcasts from library.json"""
    try:
        if os.path.exists(LIBRARY_FILE):
            with open(LIBRARY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
        else:
            return []
    except json.JSONDecodeError as e:
        backup_file = f"{LIBRARY_FILE}.corrupt"
        print(f"Error loading library: {e}. Renaming invalid file to {backup_file}", file=sys.stderr)
        try:
            os.replace(LIBRARY_FILE, backup_file)
        except OSError:
            pass
    except Exception as e:
        print(f"Error loading library: {e}", file=sys.stderr)
    return []


def save_library(library):
    """Save podcasts to library.json"""
    try:
        sanitized_library = [_sanitize_podcast_for_storage(item) for item in library if isinstance(item, dict)]
        with open(LIBRARY_FILE, 'w', encoding='utf-8') as f:
            json.dump(sanitized_library, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving library: {e}", file=sys.stderr)


def add_to_library(podcast):
    """Add a podcast to the library"""
    library = load_library()
    feed_url = podcast.get('feed_url')
    if not feed_url:
        return
    for item in library:
        if item.get('feed_url') == feed_url:
            return  # Already exists
    library.append(_sanitize_podcast_for_storage(podcast))
    save_library(library)
    notify_library_change_listeners()

def remove_from_library(feed_url):
    """Remove a podcast from the library by feed_url"""
    library = load_library()
    library = [item for item in library if item.get('feed_url') != feed_url]
    save_library(library)
    notify_library_change_listeners()

class PodcastAlbum(ctk.CTkFrame):
    def __init__(self, master, podcast_info, on_close=None):
        try:
            super().__init__(master, fg_color="#111111")
            
            # Top bar with back button
            top_bar = ctk.CTkFrame(self, fg_color="#1e1e1e", border_width=1, border_color="#333333", corner_radius=0)
            top_bar.pack(fill="x", padx=0, pady=0)
            top_bar.columnconfigure(0, weight=1)
            
            back_button = ctk.CTkButton(
                top_bar,
                text="← Back",
                width=100,
                height=40,
                fg_color="#2563eb",
                hover_color="#1d4ed8",
                text_color="white",
                corner_radius=8,
                command=on_close if on_close else self.destroy
            )
            back_button.pack(side="left", padx=12, pady=10)
            
            # Main content frame
            content_frame = ctk.CTkFrame(self, fg_color="#111111")
            content_frame.pack(fill="both", expand=True, padx=16, pady=16)
            content_frame.columnconfigure(0, weight=0)
            content_frame.columnconfigure(1, weight=1)
            
            # Header section with image, title, author, description
            header_frame = ctk.CTkFrame(content_frame, fg_color="#1f1f1f", border_width=1, border_color="#444444", corner_radius=16)
            header_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 16))
            header_frame.columnconfigure(0, weight=0)
            header_frame.columnconfigure(1, weight=1)
            
            try:
                image = Image.open(BytesIO(requests.get(podcast_info['image_url']).content))
                resized = image.resize((150, 150))
                image = ctk.CTkImage(resized, size=(150, 150))
                image_label = ctk.CTkLabel(header_frame, image=image, text="")
            except Exception as e:
                print(f"Error loading image: {e}", file=sys.stderr)
                image_label = ctk.CTkLabel(
                    header_frame,
                    text="No Image",
                    font=ctk.CTkFont(size=14),
                    fg_color="#2a2a2a",
                    width=150,
                    height=150,
                    corner_radius=12
                )
            image_label.grid(row=0, column=0, padx=16, pady=16, sticky="nw")
            
            # Text content
            text_frame = ctk.CTkFrame(header_frame, fg_color="transparent")
            text_frame.grid(row=0, column=1, sticky="nsew", padx=16, pady=16)
            text_frame.columnconfigure(0, weight=1)
            
            podcast_metadata = get_podcast_metadata(podcast_info['feed_url'])
            
            title_label = ctk.CTkLabel(
                text_frame,
                text=podcast_metadata['title'],
                font=ctk.CTkFont(size=24, weight="bold"),
                anchor="w",
                wraplength=600,
                justify="left"
            )
            title_label.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            
            author_label = ctk.CTkLabel(
                text_frame,
                text=f"By {podcast_metadata['author']}",
                font=ctk.CTkFont(size=13, slant="italic"),
                text_color="#bbbbbb",
                anchor="w"
            )
            author_label.grid(row=1, column=0, sticky="ew", pady=(0, 12))
            
            description_label = ctk.CTkLabel(
                text_frame,
                text=podcast_metadata['description'],
                font=ctk.CTkFont(size=12),
                text_color="#aaaaaa",
                anchor="nw",
                justify="left",
                wraplength=600
            )
            description_label.grid(row=2, column=0, sticky="nsew", pady=(0, 0))
            
            # Episodes section header
            episodes_header = ctk.CTkFrame(content_frame, fg_color="transparent")
            episodes_header.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
            episodes_header.columnconfigure(0, weight=1)
            
            episodes_title = ctk.CTkLabel(
                episodes_header,
                text="Episodes",
                font=ctk.CTkFont(size=18, weight="bold")
            )
            episodes_title.grid(row=0, column=0, sticky="w")
            
            # Transcribe button
            self.transcribe_button = ctk.CTkButton(
                episodes_header,
                text="Transcribe Selected",
                width=160,
                height=36,
                fg_color="#007acc",
                hover_color="#0059a3",
                text_color="white",
                corner_radius=10
            )
            self.transcribe_button.grid(row=0, column=1, sticky="e", padx=(10, 0))
            self.transcribe_button.grid_remove()  # Hide initially
            
            # Status area
            self.status_frame = ctk.CTkFrame(content_frame, fg_color="#1f1f1f", border_width=1, border_color="#444444", corner_radius=10, height=80)
            self.status_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
            self.status_frame.grid_remove()  # Hide initially
            self.status_frame.grid_propagate(False)
            
            self.status_label = ctk.CTkLabel(
                self.status_frame,
                text="",
                font=ctk.CTkFont(size=12),
                wraplength=600,
                justify="left"
            )
            self.status_label.pack(pady=(10, 5), padx=10, anchor="w")
            
            self.progress_bar = ctk.CTkProgressBar(
                self.status_frame,
                width=400,
                height=10
            )
            self.progress_bar.pack(pady=(0, 10), padx=10)
            self.progress_bar.set(0)
            
            # Episodes list
            episodes_frame = ctk.CTkFrame(content_frame, fg_color="#1f1f1f", border_width=1, border_color="#444444", corner_radius=16)
            episodes_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(0, 0))
            episodes_frame.columnconfigure(0, weight=1)
            episodes_frame.rowconfigure(0, weight=1)
            content_frame.rowconfigure(2, weight=1)
            
            parsed_feed = get_podcast_episodes(podcast_info['feed_url']) or []
            if not parsed_feed:
                parsed_feed = parse_podcast_feed(podcast_info['feed_url']) or []
            episode_list = EpisodeList(episodes_frame, parsed_feed, feed_url=podcast_info['feed_url'], podcast_info=podcast_info, on_selection_change=self.update_transcribe_button, parent_album=self)
            episode_list.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
            
            # Connect button to episode list's print function
            self.transcribe_button.configure(command=episode_list.print_selected_urls)
        except Exception as e:
            print(f"Error creating PodcastAlbum: {e}", file=sys.stderr)
            # Create a minimal error display
            error_label = ctk.CTkLabel(self, text=f"Error loading album: {str(e)[:100]}", font=ctk.CTkFont(size=16))
            error_label.pack(pady=20, padx=20)

    def update_transcribe_button(self):
        """Show/hide transcribe button based on selection count."""
        global SELECTED_EPISODE_URLS
        if SELECTED_EPISODE_URLS:
            self.transcribe_button.grid()
        else:
            self.transcribe_button.grid_remove()



class EpisodeList(ctk.CTkScrollableFrame):
    def __init__(self, master, initial_feed=None, feed_url: str | None = None, podcast_info: dict | None = None, on_selection_change=None, parent_album=None):
        super().__init__(master)
        if parent_album is None:
            raise ValueError("parent_album must be provided")
        self.feed_url = feed_url
        self.podcast_info = podcast_info
        self.on_selection_change = on_selection_change
        self.parent_album = parent_album
        self.episodes = list(initial_feed or [])
        self.offset = len(self.episodes)
        self.loading = False
        self.loading_label = None
        self._last_episode_count = len(self.episodes)
        
        self.ep_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.ep_frame.pack(fill="both", expand=True, pady=10, padx=10)
        self.ep_frame.bind("<Configure>", lambda e: (self._parent_canvas.configure(scrollregion=self._parent_canvas.bbox("all")), self._parent_canvas.update_idletasks()))
        self.checkbox_vars = []
        self.selected_episode_indices = set()

        if not self.episodes:
            ctk.CTkLabel(
                self.ep_frame,
                text="No episodes available.",
                font=ctk.CTkFont(size=16),
                wraplength=800,
                justify="left"
            ).pack(anchor="w", padx=10, pady=10)
            return

        self.display_episodes()
        self._parent_canvas.configure(scrollregion=self._parent_canvas.bbox("all"))
        
        # Add load more button at bottom
        if self.feed_url:
            self.load_more_button = ctk.CTkButton(
                self.ep_frame,
                text="Load More Episodes",
                width=200,
                height=40,
                fg_color="#007acc",
                hover_color="#0059a3",
                text_color="white",
                command=self.load_more_episodes
            )
            self.load_more_button.pack(pady=10)
        

    def display_episodes(self):
        self.checkbox_vars = []
        # Clear all children except the load more button
        for child in self.ep_frame.winfo_children():
            if hasattr(self, 'load_more_button') and child is self.load_more_button:
                continue
            child.destroy()
            
        for idx, episode in enumerate(self.episodes):
            try:
                title = "Untitled Episode"
                published = ""
                description = ""
                if isinstance(episode, dict):
                    title = episode.get('title', episode.get('track_name', title))
                    published = episode.get('published', episode.get('release_date', published))
                    description = episode.get('description', description)
                else:
                    title = str(episode)

                item_frame = ctk.CTkFrame(
                    self.ep_frame,
                    fg_color="#2a2a2a",
                    border_width=1,
                    border_color="#555555",
                    corner_radius=10
                )
                item_frame.pack(fill="x", pady=4)

                header_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
                header_frame.pack(fill="x", padx=10, pady=(10, 0))

                title_label = ctk.CTkLabel(
                    header_frame,
                    text=title,
                    font=ctk.CTkFont(size=14, weight="bold"),
                    anchor="w",
                    justify="left"
                )
                title_label.pack(side="left", fill="x", expand=True)

                check_var = ctk.BooleanVar(value=(idx in self.selected_episode_indices))
                checkbox = ctk.CTkCheckBox(
                    header_frame,
                    variable=check_var,
                    text="",
                    command=lambda idx=idx, var=check_var: self.on_checkbox_toggle(idx, var)
                )

                checkbox.pack(side="right", padx=(10, 0))
                self.checkbox_vars.append(check_var)

                date_label = ctk.CTkLabel(
                    item_frame,
                    text=f"Published: {published}",
                    font=ctk.CTkFont(size=10, slant="italic"),
                    anchor="w",
                    justify="left"
                )
                date_label.pack(fill="x", padx=10, pady=(4, 0))

                description_text = str(description).strip()
                truncated_text = description_text
                has_more = False
                if len(description_text) > 180:
                    truncated_text = description_text[:180].rstrip() + "..."
                    has_more = True

                description_var = ctk.StringVar(value=truncated_text)
                description_label = ctk.CTkLabel(
                    item_frame,
                    textvariable=description_var,
                    wraplength=760,
                    justify="left",
                    font=ctk.CTkFont(size=12),
                    anchor="w"
                )
                description_label.pack(fill="x", padx=10, pady=(6, 8))

                if has_more:
                    toggle_state = {"expanded": False}
                    toggle_button_ref = {}

                    def toggle_description(var=description_var, full=description_text, short=truncated_text, state=toggle_state, button_ref=toggle_button_ref):
                        if state["expanded"]:
                            var.set(short)
                            button_ref["btn"].configure(text="More")
                        else:
                            var.set(full)
                            button_ref["btn"].configure(text="Less")
                        state["expanded"] = not state["expanded"]

                    toggle_button = ctk.CTkButton(
                        item_frame,
                        text="More",
                        width=80,
                        height=24,
                        fg_color="#007acc",
                        hover_color="#0059a3",
                        text_color="white",
                        command=toggle_description
                    )
                    toggle_button.pack(anchor="w", padx=10, pady=(0, 10))
                    toggle_button_ref["btn"] = toggle_button
                    
            except Exception as e:
                print(f"Error processing episode: {e}", file=sys.stderr)
                continue
        
        # Repack load more button at the bottom
        if hasattr(self, 'load_more_button'):
            try:
                if self.load_more_button.winfo_exists():
                    self.load_more_button.pack(pady=10)
            except Exception:
                pass

        # Ensure the scroll region matches the new content size
        try:
            self._parent_canvas.configure(scrollregion=self._parent_canvas.bbox("all"))
            self._parent_canvas.update_idletasks()
        except Exception:
            pass
    

    def load_more_episodes(self):
        if self.loading or self.feed_url is None:
            return
            
        self.loading = True
        
        # Load more episodes in a separate thread
        import threading
        def load_thread():
            try:
                # Load more episodes starting from current offset
                feed_url = self.feed_url
                if feed_url is None:
                    return
                new_episodes = get_podcast_episodes(feed_url, offset=self.offset, limit=MAX_EPISODES)
                if new_episodes:
                    self.episodes.extend(new_episodes)
                    self.offset += len(new_episodes)
                        
                # Update UI in main thread
                self.after(0, self.on_episodes_loaded)
            except Exception as e:
                print(f"Error loading more episodes: {e}", file=sys.stderr)
                self.after(0, lambda: setattr(self, 'loading', False))
                
        threading.Thread(target=load_thread, daemon=True).start()
    
    def on_episodes_loaded(self):
        self.loading = False
        # Check if we actually loaded new episodes
        if hasattr(self, '_last_episode_count') and len(self.episodes) == self._last_episode_count:
            # No new episodes were loaded, show end message
            self.display_episodes()
            # Replace load more button with end message
            if hasattr(self, 'load_more_button'):
                self.load_more_button.destroy()
            end_label = ctk.CTkLabel(
                self.ep_frame,
                text="No more episodes available",
                font=ctk.CTkFont(size=14),
                text_color="#888888"
            )
            end_label.pack(pady=10)
        else:
            self._last_episode_count = len(self.episodes)
            self.display_episodes()

    def on_checkbox_toggle(self, episode_index, check_var):
        """Handle checkbox state changes and update selected episode URLs list."""
        global SELECTED_EPISODE_URLS

        episode = self.episodes[episode_index] if 0 <= episode_index < len(self.episodes) else None
        episode_url = None
        episode_title = "Untitled Episode"
        if isinstance(episode, dict):
            episode_url = episode.get("audio_url") or episode.get("link") or episode.get("url")
            episode_title = episode.get('title', episode.get('track_name', episode_title))

        if check_var.get():
            self.selected_episode_indices.add(episode_index)
            if episode_url:
                episode_data = {'url': episode_url, 'title': episode_title}
                if episode_data not in SELECTED_EPISODE_URLS:
                    SELECTED_EPISODE_URLS.append(episode_data)
        else:
            self.selected_episode_indices.discard(episode_index)
            if episode_url:
                episode_data = {'url': episode_url, 'title': episode_title}
                if episode_data in SELECTED_EPISODE_URLS:
                    SELECTED_EPISODE_URLS.remove(episode_data)

        # Notify parent of selection change
        if self.on_selection_change:
            self.on_selection_change()

    def print_selected_urls(self):
        """Print the list of selected feed URLs and download them."""
        global SELECTED_EPISODE_URLS, transcription_in_progress, transcription_thread
        
        if transcription_in_progress:
            print("Transcription already in progress. Please wait.")
            return
            
        print("\n=== Selected Episode URLs ===")
        if SELECTED_EPISODE_URLS:
            for episode_data in SELECTED_EPISODE_URLS:
                print(f"  • {episode_data['title']}: {episode_data['url']}")
            print(f"Total: {len(SELECTED_EPISODE_URLS)} episodes selected")
        else:
            print("  No episodes selected")
        print("==================================\n")
        
        # Download each selected episode
        if SELECTED_EPISODE_URLS:
            # Ensure Input directory exists inside PodAngel folder
            input_dir = Path("./PodAngel/Input")
            if input_dir.exists() and not input_dir.is_dir():
                input_dir.unlink()  # Remove the file if it exists
            input_dir.mkdir(parents=True, exist_ok=True)
            
            # Make a copy to avoid modifying the list during iteration
            episodes_to_download = list(SELECTED_EPISODE_URLS)
            
            for idx, episode_data in enumerate(episodes_to_download, 1):
                url = episode_data['url']
                title = episode_data['title']
                try:
                    # Sanitize title for filename
                    import re
                    safe_title = re.sub(r'[<>:"/\\|?*]', '', title)  # Remove invalid filename chars
                    safe_title = safe_title.strip()
                    if not safe_title:
                        safe_title = f"episode_{idx}"
                    
                    # Limit filename length
                    if len(safe_title) > 100:
                        safe_title = safe_title[:97] + "..."
                    
                    filename = f"{safe_title}.mp3"
                    file_path = input_dir / filename
                    
                    print(f"Downloading ({idx}/{len(episodes_to_download)}): {filename}...")
                    urllib.request.urlretrieve(url, str(file_path))
                    print(f"  ✓ Downloaded to {file_path}")
                    
                except Exception as e:
                    print(f"  ✗ Failed to download episode {idx}: {str(e)[:100]}")
        
        # Start transcription in a separate thread
        if SELECTED_EPISODE_URLS:  # Only if we downloaded something
            transcription_in_progress = True
            self.parent_album.transcribe_button.configure(state="disabled", text="Transcribing...")  # type: ignore
            transcription_thread = threading.Thread(target=self._run_transcription_thread, daemon=True)
            transcription_thread.start()

    def _run_transcription_thread(self):
        """Run transcription in a separate thread with GUI updates."""
        try:
            # Show status frame first
            self.parent_album.status_frame.after(0, lambda: self.parent_album.status_frame.grid())  # type: ignore
            self.parent_album.status_label.after(0, lambda: self.parent_album.status_label.configure(text="Initializing transcription..."))  # type: ignore
            self.parent_album.progress_bar.after(0, lambda: self.parent_album.progress_bar.set(0))  # type: ignore
            
            script_dir = Path(__file__).resolve().parent
            config_path = script_dir / "config.json"
            config_dict = read_config(config_path)

            if config_dict is None:
                self.parent_album.status_label.after(0, lambda: self.parent_album.status_label.configure(text="Error: Could not load configuration"))  # type: ignore
                return

            self.parent_album.status_label.after(0, lambda: self.parent_album.status_label.configure(text="Starting transcription..."))  # type: ignore

            def status_callback(message):
                self.parent_album.status_label.after(0, lambda: self.parent_album.status_label.configure(text=message))  # type: ignore

            def progress_callback(completed, total):
                progress = completed / total if total > 0 else 0
                self.parent_album.progress_bar.after(0, lambda: self.parent_album.progress_bar.set(progress))  # type: ignore

            results = run_program_gui(
                config_dict=config_dict, 
                script_dir=script_dir,
                status_callback=status_callback,
                progress_callback=progress_callback
            )
            
            # Hide status frame after completion
            self.parent_album.status_frame.after(0, lambda: self.parent_album.status_frame.grid_remove())  # type: ignore
            
        except Exception as e:
            print(f"Transcription error: {e}")
            import traceback
            traceback.print_exc()
            self.parent_album.status_label.after(0, lambda: self.parent_album.status_label.configure(text=f"Error: {str(e)[:100]}"))  # type: ignore
        finally:
            global transcription_in_progress
            transcription_in_progress = False
            self.parent_album.transcribe_button.after(0, lambda: self.parent_album.transcribe_button.configure(state="normal", text="Transcribe Selected"))  # type: ignore
        


class SearchEntry(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.search_var = ctk.StringVar()

        self.search_bar = ctk.CTkFrame(self, fg_color="#1f1f1f", border_width=1, border_color="#444444", corner_radius=16)
        self.search_bar.pack(fill="x", padx=20, pady=(10, 12))
        self.search_bar.grid_columnconfigure(0, weight=1)
        self.search_bar.grid_columnconfigure(1, weight=0)

        self.search_entry = ctk.CTkEntry(
            self.search_bar,
            textvariable=self.search_var,
            placeholder_text="Search podcasts...",
            width=400,
            height=40,
            border_width=1,
            corner_radius=12
        )
        self.search_entry.grid(row=0, column=0, padx=(10, 8), pady=10, sticky="ew")
        self.search_entry.bind("<Return>", self.search)

        self.search_button = ctk.CTkButton(
            self.search_bar,
            text="Search",
            width=120,
            height=40,
            fg_color="#3b82f6",
            hover_color="#2563eb",
            command=self.search
        )
        self.search_button.grid(row=0, column=1, padx=(0, 10), pady=10)

        # Error message label
        self.error_label = ctk.CTkLabel(
            self,
            text="",
            text_color="#ff9999",
            font=ctk.CTkFont(size=11),
            anchor="w"
        )

        self.results_frame = ctk.CTkScrollableFrame(self, fg_color="#181818", border_width=1, corner_radius=16)
        self.results_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        self.album_frame = None
        self._hidden_root_widgets = []
        self._toggle_buttons: dict[str, ctk.CTkButton] = {}
        register_library_change_listener(self._sync_library_buttons)

    def search(self, event=None):
        query = self.search_var.get().strip().lower()
        if not query:
            return

        try:
            # iTunes API requires country parameter. Using "US" as default.
            response = requests.get(
                "https://itunes.apple.com/search",
                params={
                    "term": query,
                    "media": "podcast",
                    "country": "US",
                    "limit": 10,
                },
                timeout=10
            )
            
            response.raise_for_status()
            returned_results = response.json()
            
        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "Not Found" in error_msg:
                error_msg = "Podcast search service temporarily unavailable. Please try again later or check your network connection."
            elif "timeout" in error_msg.lower():
                error_msg = "Search request timed out. Please check your network connection."
            
            print(f"Search error: {error_msg}", file=sys.stderr)
            self.error_label.configure(
                text=f"Search failed: {error_msg}",
                text_color="#ff9999"
            )
            self.error_label.pack(pady=10)
            
            # Schedule error message to disappear after 5 seconds
            self.after(5000, lambda: self.error_label.pack_forget() if self.error_label.winfo_exists() else None)
            returned_results = {"results": []}

        PODLIST.clear()
        self.search_var.set("")
        self.display_results(returned_results)

    def display_results(self, returned_results):
        for child in self.results_frame.winfo_children():
            child.destroy()

        self._toggle_buttons.clear()
        for item in returned_results.get('results', []):
            PODLIST.append({
                "id": item.get("collectionId"),
                "name": f"{item.get('artistName', '')} - {item.get('collectionName', '')}",
                "podcast_name": item.get("collectionName", ""),
                "artist_name": item.get("artistName", ""),
                "image_url": item.get("artworkUrl100", ""),
                "feed_url": item.get("feedUrl", ""),
            })

        for podcast in PODLIST:
            frame = ctk.CTkFrame(
                self.results_frame,
                fg_color="#242526",
                border_width=1,
                border_color="#3a3a3a",
                corner_radius=14
            )
            frame.pack(fill="x", pady=10, padx=10)
            frame.configure(cursor="hand2")
            frame.bind("<Double-Button-1>", lambda e, p=podcast: self.open_album(p))

            # Check if podcast is in library
            library = load_library()
            in_library = any(item.get('feed_url') == podcast.get('feed_url') for item in library)
            
            # Add library toggle button (+/-)
            toggle_button = ctk.CTkButton(
                frame,
                text="-" if in_library else "+",
                width=30,
                height=30,
                fg_color="#00aa00" if not in_library else "#cc0000",
                hover_color="#008800" if not in_library else "#990000",
                text_color="white",
                command=lambda p=podcast: self.toggle_library(p)
            )
            toggle_button.pack(side="right", padx=(0, 10), pady=10)
            feed_url = podcast.get('feed_url')
            if isinstance(feed_url, str):
                self._toggle_buttons[feed_url] = toggle_button

            image_url = podcast.get('image_url')
            if image_url:
                try:
                    response = requests.get(image_url, stream=True)
                    image = ctk.CTkImage(Image.open(BytesIO(response.content)), size=(100, 100))
                except Exception:
                    image = None
            else:
                image = None

            if image:
                image_label = ctk.CTkLabel(frame, image=image, text="")
                image_label.pack(side="left", padx=10, pady=10)
                image_label.bind("<Double-Button-1>", lambda e, p=podcast: self.open_album(p))

            text_label = ctk.CTkLabel(
                frame,
                text=podcast['name'],
                anchor="w",
                font=ctk.CTkFont(size=16, weight="bold")
            )
            text_label.pack(side="left", fill="x", expand=True, padx=10, pady=10)
            text_label.bind("<Double-Button-1>", lambda e, p=podcast: self.open_album(p))
    
    def toggle_library(self, podcast):
        """Add or remove podcast from library"""
        feed_url = podcast.get('feed_url')
        if not isinstance(feed_url, str):
            return

        library = load_library()
        in_library = any(item.get('feed_url') == feed_url for item in library)
        
        if in_library:
            remove_from_library(feed_url)
        else:
            add_to_library(podcast)
        
        # Update button appearance
        self.update_toggle_button(feed_url)
    
    def update_toggle_button(self, feed_url):
        """Update the toggle button for a specific podcast"""
        button = self._toggle_buttons.get(feed_url)
        if button and button.winfo_exists():
            library = load_library()
            in_library = any(item.get('feed_url') == feed_url for item in library)
            button.configure(
                text="-" if in_library else "+",
                fg_color="#00aa00" if not in_library else "#cc0000",
                hover_color="#008800" if not in_library else "#990000"
            )

    def _sync_library_buttons(self):
        if not self._toggle_buttons:
            return

        library = load_library()
        for feed_url, button in list(self._toggle_buttons.items()):
            if not button.winfo_exists():
                continue
            in_library = any(item.get('feed_url') == feed_url for item in library)
            button.configure(
                text="-" if in_library else "+",
                fg_color="#00aa00" if not in_library else "#cc0000",
                hover_color="#008800" if not in_library else "#990000"
            )

        if self.album_frame:
            self.album_frame.destroy()
            self.album_frame = None

        for widget, pack_info in self._hidden_root_widgets:
            widget.pack(**pack_info)
        self._hidden_root_widgets = []

    def show_results(self):
        if self.album_frame:
            self.album_frame.destroy()
            self.album_frame = None

        for widget, pack_info in self._hidden_root_widgets:
            widget.pack(**pack_info)
        self._hidden_root_widgets = []

    def open_album(self, podcast):
        root = self.winfo_toplevel()
        self._hidden_root_widgets = []

        for child in root.winfo_children():
            if child is self.album_frame:
                continue
            pack_info_fn = getattr(child, "pack_info", None)
            pack_forget_fn = getattr(child, "pack_forget", None)
            if not callable(pack_info_fn) or not callable(pack_forget_fn):
                continue
            try:
                pack_info = pack_info_fn()
            except Exception:
                continue
            self._hidden_root_widgets.append((child, pack_info))
            pack_forget_fn()

        if self.album_frame:
            self.album_frame.destroy()

        self.album_frame = PodcastAlbum(root, podcast, on_close=self.show_results)
        self.album_frame.pack(fill="both", expand=True, padx=0, pady=0)


class LibraryEntry(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.library_frame = ctk.CTkScrollableFrame(self, fg_color="#181818", border_width=1, corner_radius=16)
        self.library_frame.pack(fill="both", expand=True, padx=20, pady=(10, 10))
        self.album_frame = None
        self._hidden_root_widgets = []
        self.load_library()

    def load_library(self):
        """Load and display saved podcasts"""
        for child in self.library_frame.winfo_children():
            child.destroy()

        library = load_library()
        
        if not library:
            ctk.CTkLabel(
                self.library_frame,
                text="No podcasts in library.\nSearch for podcasts and add them with the + button.",
                font=ctk.CTkFont(size=16),
                wraplength=600,
                justify="center"
            ).pack(pady=50)
            return

        for podcast in library:
            frame = ctk.CTkFrame(
                self.library_frame,
                fg_color="#242526",
                border_width=1,
                border_color="#3a3a3a",
                corner_radius=14
            )
            frame.pack(fill="x", pady=10, padx=10)
            frame.configure(cursor="hand2")
            frame.bind("<Double-Button-1>", lambda e, p=podcast: self.open_album(p))

            # Add remove button (-)
            remove_button = ctk.CTkButton(
                frame,
                text="-",
                width=30,
                height=30,
                fg_color="#cc0000",
                hover_color="#990000",
                text_color="white",
                command=lambda p=podcast: self.remove_from_library(p)
            )
            remove_button.pack(side="right", padx=(0, 10), pady=10)

            image_url = podcast.get('image_url')
            if image_url:
                try:
                    response = requests.get(image_url, stream=True)
                    image = ctk.CTkImage(Image.open(BytesIO(response.content)), size=(100, 100))
                except Exception:
                    image = None
            else:
                image = None

            if image:
                image_label = ctk.CTkLabel(frame, image=image, text="")
                image_label.pack(side="left", padx=10, pady=10)
                image_label.bind("<Double-Button-1>", lambda e, p=podcast: self.open_album(p))

            text_label = ctk.CTkLabel(
                frame,
                text=podcast['name'],
                anchor="w",
                font=ctk.CTkFont(size=16, weight="bold")
            )
            text_label.pack(side="left", fill="x", expand=True, padx=10, pady=10)
            text_label.bind("<Double-Button-1>", lambda e, p=podcast: self.open_album(p))

    def remove_from_library(self, podcast):
        """Remove podcast from library and refresh display"""
        remove_from_library(podcast.get('feed_url'))
        self.load_library()

    def show_results(self):
        if self.album_frame:
            self.album_frame.destroy()
            self.album_frame = None

        for widget, pack_info in self._hidden_root_widgets:
            widget.pack(**pack_info)
        self._hidden_root_widgets = []

    def open_album(self, podcast):
        root = self.winfo_toplevel()
        self._hidden_root_widgets = []

        for child in root.winfo_children():
            if child is self.album_frame:
                continue
            pack_info_fn = getattr(child, "pack_info", None)
            pack_forget_fn = getattr(child, "pack_forget", None)
            if not callable(pack_info_fn) or not callable(pack_forget_fn):
                continue
            try:
                pack_info = pack_info_fn()
            except Exception:
                continue
            self._hidden_root_widgets.append((child, pack_info))
            pack_forget_fn()

        if self.album_frame:
            self.album_frame.destroy()

        self.album_frame = PodcastAlbum(root, podcast, on_close=self.show_results)
        self.album_frame.pack(fill="both", expand=True, padx=0, pady=0)


def parse_podcast_feed(url):
    try:
        with urllib.request.urlopen(url) as response:
            feed = podcastparser.parse(url, response)

        podcast_data = feed.get('episodes', [])
        if not isinstance(podcast_data, list):
            podcast_data = []

        sanitized = []
        for episode in podcast_data[:MAX_EPISODES]:
            if isinstance(episode, dict):
                episode = {
                    'title': _strip_html(episode.get('title', '')),
                    'published': _strip_html(episode.get('published', '')),
                    'description': _strip_html(episode.get('description', '')),
                    **episode,
                }
            sanitized.append(episode)

        return sanitized

    except Exception as e:
        print(f"Error parsing feed: {e}", file=sys.stderr)
    
    return []