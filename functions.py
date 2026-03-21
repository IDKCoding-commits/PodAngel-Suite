import os
import json
import re
import signal
import subprocess
import tempfile
import time
from collections import namedtuple
from pathlib import Path
import pickle

import whisper_timestamped
from detoxify import Detoxify
import multiprocessing

worker_model = None
global_pool = None
shutdown_in_progress = False
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
#Gets the duration of the audio
def get_audio_duration(file_path: str) -> float | None:
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Error: ffprobe failed for {file_path} - {e.stderr}")
        print(f"Make sure ffprobe is installed and the file is a valid audio format")
        return None
    except ValueError:
        print(f"Error: Could not parse duration from ffprobe output for {file_path}")
        return None
#Initializes the worker model to be used later
def worker_initializer(model_size: str) -> None:
    global worker_model
    worker_model = whisper_timestamped.load_model(model_size)
#Boots the workers, hands them the filename and path, and starts transcribing
def process_file(filename: str, input_path: str) -> dict:

    global worker_model
    file_path = os.path.join(input_path, filename)
    transcribed_file = whisper_timestamped.transcribe(worker_model, file_path)
    print(f"Transcribed: {filename}")
    return transcribed_file
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
    print(f"\nReceived signal {signum}, shutting down...")
    cleanup_workers()
    exit()

signal.signal(signal.SIGINT, signal_handler)
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
            subprocess.run(['cp', input_file, output_file], check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error: Failed to copy clean audio file: {e}")
            return False


    all_segments = [(float(w.start), float(w.end)) for w in words_to_mute]
    all_segments.extend([(float(s.start), float(s.end)) for s in segments_to_mute])
    
    if not all_segments:
        try:
            subprocess.run(['cp', input_file, output_file], check=True)
            return True
        except subprocess.CalledProcessError as e:
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
            for i, (start, end) in enumerate(segments_to_keep):
                segment_file = os.path.join(temp_dir, f"segment_{i}{file_ext}")
                segment_files.append(segment_file)
                duration_segment = end - start
                cmd = ['ffmpeg', '-y', '-i', input_file, '-ss', str(start), '-t', str(duration_segment), '-vn', '-c:a', 'copy', segment_file]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"Error: Failed to extract segment {i} from {input_file}:\n{result.stderr}")
                    return False
                if not os.path.exists(segment_file):
                    print(f"Error: Segment file {segment_file} was not created by ffmpeg")
                    return False

            if len(segment_files) == 1:
                try:
                    subprocess.run(['cp', segment_files[0], output_file], check=True)
                except subprocess.CalledProcessError as e:
                    print(f"Error: Failed to copy single segment to output: {e}")
                    return False
            else:
                concat_file = os.path.join(temp_dir, "concat.txt")
                with open(concat_file, 'w') as f:
                    f.write('\n'.join(f"file '{seg}'" for seg in segment_files))
                
                cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_file, '-c:a', 'copy', '-map_metadata', '0', output_file]
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
    for items in working_list:
        file_number =+ 1

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