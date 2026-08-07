import os
import sys
import time
import signal
import threading
import subprocess
import tkinter as tk
from tkinter import ttk
import feedparser
import requests
from gtts import gTTS


# --- CONFIGURATION & PATHS ---
SOUNDS_DIR = os.path.expanduser("~/Documents/Alarm_Clock/sounds")
APPS_TO_KILL = ["yt-dlp", "mpv", "vlc", "cvlc", "chromium-browser"]

# Stream & RSS Endpoints
WUNC_LIVE_URL = "https://wunc-ice.streamguys1.com/wunc-128-mp3"
DAILY_PODCAST_RSS = "https://feeds.simplecast.com/54nAGcIl"
HEADLINES_PODCAST_RSS = "https://feeds.simplecast.com/ydACIPHO"

if not os.path.exists(SOUNDS_DIR):
    os.makedirs(SOUNDS_DIR, exist_ok=True)


class TouchNumpad(tk.Toplevel):
    def __init__(self, parent, initial_val="07:00"):
        super().__init__(parent)
        self.title("Set Time")
        self.configure(bg="#111111")
        self.attributes("-topmost", True)

        self.geometry("320x420")
        self.transient(parent)

        self.value = initial_val.replace(":", "")
        self.result = None
        self.display_var = tk.StringVar()

        self.update_display()

        self.deiconify()
        self.update()
        self.grab_set()

        display = tk.Label(
            self, textvariable=self.display_var, font=("Helvetica", 32, "bold"),
            fg="#81C784", bg="#222222", width=8, relief="ridge", bd=3
        )
        display.pack(pady=15, ipady=10)

        grid_frame = tk.Frame(self, bg="#111111")
        grid_frame.pack(pady=10)

        buttons = [
            ('1', 0, 0), ('2', 0, 1), ('3', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('7', 2, 0), ('8', 2, 1), ('9', 2, 2),
            ('CLR', 3, 0), ('0', 3, 1), ('⌫', 3, 2)
        ]

        for (text, row, col) in buttons:
            action = lambda t=text: self.on_button_tap(t)
            btn = tk.Button(
                grid_frame, text=text, font=("Helvetica", 18, "bold"),
                width=4, height=1, bg="#333333", fg="white", activebackground="#555555",
                activeforeground="white", command=action
            )
            btn.grid(row=row, column=col, padx=6, pady=6)

        action_frame = tk.Frame(self, bg="#111111")
        action_frame.pack(pady=10, fill="x", padx=20)

        cancel_btn = tk.Button(
            action_frame, text="Cancel", font=("Helvetica", 14),
            bg="#D32F2F", fg="white", command=self.destroy
        )
        cancel_btn.pack(side="left", expand=True, fill="x", padx=5)

        ok_btn = tk.Button(
            action_frame, text="OK", font=("Helvetica", 14, "bold"),
            bg="#2E7D32", fg="white", command=self.confirm
        )
        ok_btn.pack(side="right", expand=True, fill="x", padx=5)

    def on_button_tap(self, char):
        if char == 'CLR':
            self.value = ""
        elif char == '⌫':
            self.value = self.value[:-1]
        else:
            if len(self.value) < 4:
                self.value += char
        self.update_display()

    def update_display(self):
        raw = self.value.ljust(4, "_")
        formatted = f"{raw[0:2]}:{raw[2:4]}"
        self.display_var.set(formatted)

    def confirm(self):
        digits = self.value.zfill(4)[:4]
        h, m = int(digits[:2]), int(digits[2:])
        if h > 23: h = 23
        if m > 59: m = 59

        self.result = f"{h:02d}:{m:02d}"
        self.destroy()

## Smart Alarm Application Class
## Just the general application class structure
class SmartAlarmApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart Alarm Clock")
        self.root.attributes("-fullscreen", True)
        self.root.configure(bg="black")

        self.root.bind("<Escape>", lambda event: self.close_app())
        self.root.bind_all("<Button-1>", self.wake_screen)

        self.alarm_running = False
        self.routine_running = False
        self.is_paused = False
        self.alarm_time_str = "07:00"
        self.alarm_enabled = False
        self.current_proc = None
        self.skip_requested = False

        # Pre-fetched Audio Caches & Weather
        self.cached_headlines_url = None
        self.cached_daily_url = None
        self.cached_weather_report = ""

        self.setup_ui()
        self.update_clock_loop()
        self.wake_screen()

    def setup_ui(self):
        self.main_frame = tk.Frame(self.root, bg="black")
        self.main_frame.pack(expand=True, fill="both", padx=15, pady=10)

        # Main Digital Clock Display
        self.clock_label = tk.Label(
            self.main_frame, text="", font=("Helvetica", 54, "bold"), fg="white", bg="black"
        )
        self.clock_label.pack(pady=(5, 0))

        # Status / Next Alarm Label
        self.next_alarm_label = tk.Label(
            self.main_frame, text="Alarm Off", font=("Helvetica", 14), fg="gray", bg="black"
        )
        self.next_alarm_label.pack(pady=(0, 5))

        # ----------------------------------------------------
        # HOURLY WEATHER CARDS DISPLAY (Hidden on startup)
        # ----------------------------------------------------
        self.weather_cards_frame = tk.Frame(self.main_frame, bg="black")

        self.now_card_val = tk.StringVar(value="Now: --°")
        self.noon_card_val = tk.StringVar(value="12 PM: --°")
        self.five_card_val = tk.StringVar(value="5 PM: --°")

        for title, var in [("Now", self.now_card_val), ("12 PM", self.noon_card_val), ("5 PM", self.five_card_val)]:
            card = tk.Frame(self.weather_cards_frame, bg="#1E1E1E", relief="ridge", bd=1, padx=10, pady=5)
            card.pack(side="left", padx=6)

            lbl = tk.Label(card, textvariable=var, font=("Helvetica", 12, "bold"), fg="#81D4FA", bg="#1E1E1E")
            lbl.pack()

        # Currently Playing Status Indicator
        self.routine_status_label = tk.Label(
            self.main_frame, text="", font=("Helvetica", 18, "bold"), fg="#64B5F6", bg="black"
        )
        self.routine_status_label.pack(pady=(5, 0))
        # "Up Next" Queue Preview Label
        self.up_next_label = tk.Label(
            self.main_frame, text="", font=("Helvetica", 14, "italic"), fg="#FFA726", bg="black"
        )
        self.up_next_label.pack(pady=(2, 0))

        # Routine Control Bar (Pause / Play / Skip)
        self.routine_controls_frame = tk.Frame(self.main_frame, bg="black")
        self.routine_controls_frame.columnconfigure((0, 1, 2), weight=1, uniform="routine_btns")

        self.pause_btn = tk.Button(
            self.routine_controls_frame, text="⏸ Pause", font=("Helvetica", 16, "bold"),
            bg="#333333", fg="white", activebackground="#555555", activeforeground="white",
            pady=12, command=self.pause_routine_step
        )
        self.pause_btn.grid(row=0, column=0, padx=8, sticky="ew")

        self.play_btn = tk.Button(
            self.routine_controls_frame, text="▶ Play", font=("Helvetica", 16, "bold"),
            bg="#2E7D32", fg="white", activebackground="#4CAF50", activeforeground="white",
            pady=12, command=self.resume_routine_step
        )
        self.play_btn.grid(row=0, column=1, padx=8, sticky="ew")

        self.skip_btn = tk.Button(
            self.routine_controls_frame, text="⏭ Skip", font=("Helvetica", 16, "bold"),
            bg="#0288D1", fg="white", activebackground="#03A9F4", activeforeground="white",
            pady=12, command=self.skip_routine_step
        )
        self.skip_btn.grid(row=0, column=2, padx=8, sticky="ew")

        # Controls Container Frame
        self.controls_frame = tk.Frame(self.main_frame, bg="black")
        self.controls_frame.pack(pady=10)

        self.time_entry_var = tk.StringVar(value=self.alarm_time_str)
        self.time_entry = tk.Label(
            self.controls_frame,
            textvariable=self.time_entry_var,
            font=("Helvetica", 24, "bold"),
            fg="#81C784", bg="#222222",
            padx=12, pady=4, relief="ridge", bd=2, cursor="hand2"
        )
        self.time_entry.grid(row=0, column=0, padx=8)
        self.time_entry.bind("<Button-1>", self.open_numpad)

        self.toggle_btn = tk.Button(
            self.controls_frame, text="Enable Alarm", font=("Helvetica", 13),
            command=self.toggle_alarm, bg="#333333", fg="white", width=12
        )
        self.toggle_btn.grid(row=0, column=1, padx=8)

        self.sound_var = tk.StringVar()
        self.refresh_sound_list()
        self.sound_dropdown = ttk.OptionMenu(
            self.controls_frame, self.sound_var, self.sound_var.get(), *self.available_sounds
        )
        self.sound_dropdown.grid(row=1, column=0, columnspan=2, pady=10, sticky="ew")

        # Action Buttons Container
        self.actions_frame = tk.Frame(self.main_frame, bg="black")
        self.actions_frame.pack(fill="x", pady=5)

        self.dismiss_btn = tk.Button(
            self.actions_frame, text="STOP ALARM & START ROUTINE", font=("Helvetica", 16, "bold"),
            bg="#D32F2F", fg="white", command=self.dismiss_alarm, height=2
        )

        # Exit Button
        self.exit_btn = tk.Button(
            self.main_frame, text="✕", font=("Helvetica", 14, "bold"),
            fg="#555555", bg="black", activeforeground="white", activebackground="black",
            bd=0, command=self.close_app
        )
        self.exit_btn.place(relx=1.0, rely=0.0, anchor="ne")

    def open_numpad(self, event=None):
        numpad = TouchNumpad(self.root, initial_val=self.time_entry_var.get())
        self.root.wait_window(numpad)

        if numpad.result:
            self.time_entry_var.set(numpad.result)
            if self.alarm_enabled:
                self.next_alarm_label.config(
                    text=f"Alarm set for {self.time_entry_var.get()}", fg="#81C784"
                )

    # ==========================================
    # BRIGHTNESS & DIMMING CONTROLS
    # ==========================================
    def set_brightness(self, percent):
        try:
            p_val = max(1, percent)
            subprocess.run(["brightnessctl", "set", f"{p_val}%"], check=False, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass

        try:
            backlight_dir = "/sys/class/backlight"
            if os.path.exists(backlight_dir):
                devices = os.listdir(backlight_dir)
                if devices:
                    brightness_path = os.path.join(backlight_dir, devices[0], "brightness")
                    max_path = os.path.join(backlight_dir, devices[0], "max_brightness")

                    max_bright = 255
                    if os.path.exists(max_path):
                        with open(max_path, "r") as f:
                            max_bright = int(f.read().strip())

                    raw_val = int((percent / 100) * max_bright)
                    raw_val = max(1, raw_val)

                    with open(brightness_path, "w") as f:
                        f.write(str(raw_val))
                    return
        except Exception:
            pass

        try:
            brightness_val = max(0.02, percent / 100)
            env = os.environ.copy()
            env["DISPLAY"] = ":0"
            subprocess.run(
                ["xrandr", "--output", "DSI-1", "--brightness", str(brightness_val)],
                env=env, check=False, stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

    def dim_screen(self):
        if not self.alarm_running and not self.routine_running:
            self.set_brightness(6)

    def wake_screen(self, event=None):
        self.set_brightness(100)

        if hasattr(self, 'dim_timer_id'):
            self.root.after_cancel(self.dim_timer_id)

        if not self.alarm_running and not self.routine_running:
            self.dim_timer_id = self.root.after(5000, self.dim_screen)

    # ==========================================
    # AUDIO & ALARM CONTROLS
    # ==========================================
    def stop_all_audio(self):
        for app in APPS_TO_KILL:
            try:
                subprocess.run(["pkill", "-9", "-f", app], check=False, stderr=subprocess.DEVNULL)
            except Exception:
                pass

        if self.current_proc:
            try:
                os.killpg(os.getpgid(self.current_proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    self.current_proc.kill()
                except Exception:
                    pass
            self.current_proc = None

    def refresh_sound_list(self):
        files = [f for f in os.listdir(SOUNDS_DIR) if f.endswith(('.mp3', '.wav', '.ogg', '.flac'))]
        if not files:
            self.available_sounds = ["No sounds found in folder"]
        else:
            self.available_sounds = files
        self.sound_var.set(self.available_sounds[0])

    def update_clock_loop(self):
        now_dt = time.localtime()
        now_str = time.strftime("%H:%M:%S", now_dt)
        time_short = time.strftime("%H:%M", now_dt)
        seconds = time.strftime("%S", now_dt)

        self.clock_label.config(text=now_str)
        target_alarm = self.time_entry_var.get().strip()

        if self.alarm_enabled and not self.alarm_running:
            if time_short == target_alarm and seconds in ["00", "01"]:
                print(f"[ALARM] Triggering alarm for target time: {target_alarm}")
                self.trigger_alarm()

        self.root.after(1000, self.update_clock_loop)

    def toggle_alarm(self):
        self.alarm_enabled = not self.alarm_enabled
        if self.alarm_enabled:
            self.toggle_btn.config(text="Disable Alarm", bg="#2E7D32")
            self.next_alarm_label.config(text=f"Alarm set for {self.time_entry_var.get()}", fg="#81C784")
        else:
            self.toggle_btn.config(text="Enable Alarm", bg="#333333")
            self.next_alarm_label.config(text="Alarm Off", fg="gray")

    def trigger_alarm(self):
        self.alarm_running = True
        self.stop_all_audio()
        self.wake_screen()

        self.controls_frame.pack_forget()
        self.next_alarm_label.config(text="WAKE UP!", fg="yellow")
        self.dismiss_btn.pack(expand=True, fill="both", side="bottom", pady=10)

        sound_file = os.path.join(SOUNDS_DIR, self.sound_var.get())

        def play_loop():
            start_time = time.time()
            max_duration = 1800

            while self.alarm_running and (time.time() - start_time < max_duration):
                if os.path.exists(sound_file):
                    self.current_proc = subprocess.Popen(
                        ["mpv", "--no-terminal", sound_file],
                        preexec_fn=os.setsid
                    )
                    self.current_proc.wait()
                else:
                    time.sleep(1)

            if self.alarm_running:
                self.alarm_running = False
                self.stop_all_audio()
                self.root.after(0, self.auto_silence_ui_reset)

        threading.Thread(target=play_loop, daemon=True).start()

    def auto_silence_ui_reset(self):
        self.dismiss_btn.pack_forget()
        self.controls_frame.pack(pady=10)
        self.toggle_alarm()
        self.next_alarm_label.config(text="Alarm Timed Out (Silenced)", fg="orange")
        self.wake_screen()

    def dismiss_alarm(self):
        self.alarm_running = False
        self.stop_all_audio()

        self.dismiss_btn.pack_forget()
        self.toggle_alarm()

        threading.Thread(target=self.run_morning_routine, daemon=True).start()

    # ==========================================
    # WEATHER BRIEFING & UI CARDS
    # ==========================================
    def fetch_and_update_weather(self, location="Raleigh"):
        """Fetches hourly weather data, populates UI boxes, and constructs spoken report."""
        try:
            url = f"https://wttr.in/{location}?format=j1"
            res = requests.get(url, timeout=6).json()

            current_temp = res['current_condition'][0]['temp_F']
            current_desc = res['current_condition'][0]['weatherDesc'][0]['value']

            today_hourly = res['weather'][0]['hourly']

            noon_data = next((h for h in today_hourly if h['time'] == '1200'), today_hourly[4])
            five_pm_data = next((h for h in today_hourly if h['time'] == '1700'), today_hourly[5])

            noon_temp = noon_data['tempF']
            noon_desc = noon_data['weatherDesc'][0]['value']

            five_pm_temp = five_pm_data['tempF']
            five_pm_desc = five_pm_data['weatherDesc'][0]['value']

            # Update Screen Boxes
            self.root.after(0, lambda: self.now_card_val.set(f"Now: {current_temp}° {current_desc}"))
            self.root.after(0, lambda: self.noon_card_val.set(f"12 PM: {noon_temp}° {noon_desc}"))
            self.root.after(0, lambda: self.five_card_val.set(f"5 PM: {five_pm_temp}° {five_pm_desc}"))

            # Check for non-sunny conditions across the full day
            adverse_conditions = []
            for slot in today_hourly:
                desc = slot['weatherDesc'][0]['value'].lower()
                time_hr = int(slot['time']) // 100

                if any(w in desc for w in ['rain', 'shower', 'thunder', 'storm', 'snow', 'drizzle']):
                    time_fmt = f"{time_hr} AM" if time_hr < 12 else (f"{time_hr - 12} PM" if time_hr > 12 else "12 PM")
                    adverse_conditions.append(f"{slot['weatherDesc'][0]['value']} around {time_fmt}")

            # Build spoken text report
            report = (
                f"Good morning! Here is your daily weather forecast for {location}. "
                f"Right now, it is {current_desc} and {current_temp} degrees. "
                f"At noon, expect {noon_desc} and {noon_temp} degrees. "
                f"By 5 PM, it will be {five_pm_desc} and {five_pm_temp} degrees. "
            )

            if adverse_conditions:
                unique_warnings = list(dict.fromkeys(adverse_conditions))[:3]
                report += f"Take note: inclement weather is expected today with {', '.join(unique_warnings)}."
            else:
                report += "Clear and sunny conditions are expected throughout the rest of the day."

            self.cached_weather_report = report

        except Exception as e:
            print(f"Weather error: {e}")
            self.cached_weather_report = "Good morning! Unable to fetch live weather data at this moment."

    def generate_weather_briefing(self):
        tts_file = "/tmp/morning_briefing.mp3"

        # Fetch weather if we don't have text yet
        if not self.cached_weather_report:
            self.fetch_and_update_weather()

    # ONLY generate if the file doesn't exist yet
        if not os.path.exists(tts_file):
            print("[TTS] Generating new weather audio file...")
            tts = gTTS(text=self.cached_weather_report, lang="en", tld="com")
            tts.save(tts_file)
        else:
            print("[TTS] Using pre-rendered weather audio file.")

        return tts_file    
    # ==========================================
    # ROUTINE CONTROLS & BACKGROUND PRE-FETCHING
    # ==========================================
    def pause_routine_step(self):
        if self.current_proc and self.current_proc.poll() is None and not self.is_paused:
            try:
                os.killpg(os.getpgid(self.current_proc.pid), signal.SIGSTOP)
                self.is_paused = True
                self.pause_btn.config(bg="#E65100")
                self.play_btn.config(bg="#333333")
            except Exception as e:
                print(f"Error pausing: {e}")

    def resume_routine_step(self):
        if self.current_proc and self.current_proc.poll() is None and self.is_paused:
            try:
                os.killpg(os.getpgid(self.current_proc.pid), signal.SIGCONT)
                self.is_paused = False
                self.pause_btn.config(bg="#333333")
                self.play_btn.config(bg="#2E7D32")
            except Exception as e:
                print(f"Error resuming: {e}")

    def skip_routine_step(self):
        self.skip_requested = True
        self.stop_all_audio()

    def resolve_audio_url(self, rss_url, fallback_page_url=None):
        """Resolves podcast RSS feed enclosures and unrolls tracking redirects to get direct MP3 URLs."""
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "audio/mpeg, audio/*;q=0.9, */*;q=0.8"
        }

        # List feeds to check (Primary + Backups)
        feeds_to_check = [rss_url]
        if "O2i31m0I" in rss_url or "headlines" in (fallback_page_url or ""):
            feeds_to_check.extend([
                "https://feeds.simplecast.com/O2i31m0I",
                "https://rss.art19.com/the-headlines"
            ])
        elif "54nAGcIl" in rss_url or "daily" in (fallback_page_url or ""):
            feeds_to_check.extend([
                "https://feeds.simplecast.com/54nAGcIl",
                "https://rss.art19.com/the-daily"
            ])

        # Remove duplicates while preserving order
        feeds_to_check = list(dict.fromkeys(feeds_to_check))

        for target_feed in feeds_to_check:
            try:
                # 1. Parse feed with custom browser headers
                feed = feedparser.parse(target_feed, request_headers=headers)

                if feed.entries:
                    latest = feed.entries[0]
                    enclosures = getattr(latest, 'enclosures', [])

                    if enclosures:
                        raw_audio_url = enclosures[0].get('href') or enclosures[0].get('url')

                        if raw_audio_url:
                            # 2. Unroll Podtrac / Simplecast tracking redirects
                            session = requests.Session()
                            res = session.head(
                                raw_audio_url,
                                headers=headers,
                                allow_redirects=True,
                                timeout=8
                            )
                            
                            resolved_url = res.url
                            print(f"[AUDIO RESOLVER] Successfully resolved: {resolved_url}")
                            return resolved_url

            except Exception as e:
                print(f"[AUDIO RESOLVER] Failed for feed {target_feed}: {e}")

        # 3. Fallback to yt-dlp if RSS enclosure resolution fails entirely
        target_url = fallback_page_url if fallback_page_url else rss_url
        try:
            res = subprocess.run(
                ["yt-dlp", "-g", "-f", "bestaudio/best", "--no-warnings", target_url],
                capture_output=True, text=True, timeout=10
            )
            extracted_url = res.stdout.strip()
            if extracted_url.startswith("http"):
                return extracted_url
        except Exception as e:
            print(f"[AUDIO RESOLVER] yt-dlp fallback failed: {e}")

        return None

    def prefetch_routine_urls(self):
        """Fetches feed URLs asynchronously so playback transitions instantly."""
        self.cached_headlines_url = self.resolve_audio_url(
            HEADLINES_PODCAST_RSS,
            fallback_page_url="https://www.nytimes.com/column/the-headlines"
        )
        self.cached_daily_url = self.resolve_audio_url(
            DAILY_PODCAST_RSS,
            fallback_page_url="https://www.nytimes.com/column/the-daily"
        )

    def set_routine_display(self, playing_text, next_text):
        self.root.after(0, lambda: self.routine_status_label.config(text=f"▶ {playing_text}"))
        self.root.after(0, lambda: self.up_next_label.config(
            text=f"Up Next: {next_text}" if next_text else ""
        ))

    def run_morning_routine(self):
        self.routine_running = True
        self.root.after(0, self.wake_screen)

        # Pre-fetch news audio streams in background
        threading.Thread(target=self.prefetch_routine_urls, daemon=True).start()

        # Hide alarm controls, show weather boxes & routine controls
        self.root.after(0, lambda: self.controls_frame.pack_forget())
        self.root.after(0, lambda: self.weather_cards_frame.pack(pady=8, before=self.routine_status_label))
        self.root.after(0, lambda: self.routine_controls_frame.pack(side="bottom", fill="x", padx=15, pady=20))
        self.root.after(0, lambda: self.routine_controls_frame.lift())

        # ----------------------------------------------------
        # STEP 1: Spoken Weather Briefing & Box Update
        # ----------------------------------------------------
        self.skip_requested = False
        self.is_paused = False
        self.root.after(0, lambda: self.pause_btn.config(bg="#333333"))
        self.root.after(0, lambda: self.play_btn.config(bg="#2E7D32"))
        self.set_routine_display("Spoken Weather Briefing", "WUNC 91.5 FM (NPR)")

        # Fetch fresh weather and populate boxes now that alarm was dismissed
        self.fetch_and_update_weather()

        try:
            briefing_mp3 = self.generate_weather_briefing()
            self.current_proc = subprocess.Popen([
                "mpv", "--no-terminal", briefing_mp3
            ], preexec_fn=os.setsid)

            while self.current_proc and self.current_proc.poll() is None:
                if self.skip_requested:
                    self.stop_all_audio()
                    break
                time.sleep(0.5)

        except Exception as e:
            print(f"Error playing briefing: {e}")
        finally:
            if os.path.exists(briefing_mp3):
                os.remove(briefing_mp3)
                print("[CLEANUP] Deleted temporary briefing audio file.")
        # ----------------------------------------------------
        # STEP 2: WUNC 91.5 FM Live NPR/NC News (10 Minute Limit)
        # ----------------------------------------------------
        self.skip_requested = False
        self.is_paused = False
        self.root.after(0, lambda: self.pause_btn.config(bg="#333333"))
        self.root.after(0, lambda: self.play_btn.config(bg="#2E7D32"))
        self.set_routine_display("WUNC 91.5 FM (NPR & Local News)", "NYT: The Headlines")

        try:
            self.current_proc = subprocess.Popen([
                "mpv", "--no-terminal", WUNC_LIVE_URL
            ], preexec_fn=os.setsid)

            start_time = time.time()
            max_duration = 600  # 10 minutes

            while self.current_proc and self.current_proc.poll() is None:
                if (time.time() - start_time >= max_duration) or self.skip_requested:
                    self.stop_all_audio()
                    break
                time.sleep(0.5)

        except Exception as e:
            print(f"Error playing WUNC: {e}")

        # ----------------------------------------------------
        # STEP 3: NYT "The Headlines" (~10 Min News Summary)
        # ----------------------------------------------------
        self.skip_requested = False
        self.is_paused = False
        self.root.after(0, lambda: self.pause_btn.config(bg="#333333"))
        self.root.after(0, lambda: self.play_btn.config(bg="#2E7D32"))
        self.set_routine_display("NYT: The Headlines", "NYT: The Daily")

        audio_url = self.cached_headlines_url
        if not audio_url:
            audio_url = self.resolve_audio_url(
                HEADLINES_PODCAST_RSS,
                fallback_page_url="https://www.nytimes.com/column/the-headlines"
            )

        try:
            if audio_url:
                # User-Agent header passed directly to MPV to ensure HTTP standard compliance
                self.current_proc = subprocess.Popen([
                    "mpv", "--no-video", "--no-terminal",
                    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", audio_url
                ], preexec_fn=os.setsid)

                while self.current_proc and self.current_proc.poll() is None:
                    if self.skip_requested:
                        self.stop_all_audio()
                        break
                    time.sleep(0.5)
            else:
                print("Could not find audio stream for The Headlines.")

        except Exception as e:
            print(f"Error playing NYT Headlines: {e}")

        # ----------------------------------------------------
        # STEP 4: NYT "The Daily" Podcast
        # ----------------------------------------------------
        self.skip_requested = False
        self.is_paused = False
        self.root.after(0, lambda: self.pause_btn.config(bg="#333333"))
        self.root.after(0, lambda: self.play_btn.config(bg="#2E7D32"))
        self.set_routine_display("NYT: The Daily", None)

        podcast_url = self.cached_daily_url
        if not podcast_url:
            podcast_url = self.resolve_audio_url(
                DAILY_PODCAST_RSS,
                fallback_page_url="https://www.nytimes.com/column/the-daily"
            )

        try:
            if podcast_url:
                self.current_proc = subprocess.Popen([
                    "mpv", "--no-video", "--no-terminal",
                    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", podcast_url
                ], preexec_fn=os.setsid)

                while self.current_proc and self.current_proc.poll() is None:
                    if self.skip_requested:
                        self.stop_all_audio()
                        break
                    time.sleep(0.5)
            else:
                print("Could not find audio stream for The Daily.")

        except Exception as e:
            print(f"Error playing The Daily: {e}")

        # Routine complete cleanup
        self.root.after(0, self.cleanup_routine_ui)

    def cleanup_routine_ui(self):
        self.routine_status_label.config(text="Routine Completed!")
        self.up_next_label.config(text="")
        self.routine_controls_frame.pack_forget()

        # Hide weather cards frame when routine finishes
        self.weather_cards_frame.pack_forget()

        self.controls_frame.pack(pady=10)

        self.pause_btn.config(bg="#333333")
        self.play_btn.config(bg="#2E7D32")
        self.root.after(3000, lambda: self.routine_status_label.config(text=""))

        self.routine_running = False
        self.wake_screen()

    def close_app(self):
        self.stop_all_audio()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = SmartAlarmApp(root)
    root.mainloop()
