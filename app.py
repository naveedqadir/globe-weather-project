import os
import io
from flask import Flask, render_template, request, jsonify, send_file
import requests
from gtts import gTTS
from dotenv import load_dotenv
import tempfile
import threading
import time

load_dotenv()  # Load .env if present
app = Flask(__name__)

OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
CESIUM_ION_TOKEN = os.environ.get("CESIUM_ION_TOKEN", "")

# Periodically or at startup clean up stale TTS temporary files to avoid filling disk.
def _cleanup_old_tts(tmp_dir=None, older_than_seconds=60*60):
    try:
        td = tmp_dir or tempfile.gettempdir()
        now = time.time()
        for fname in os.listdir(td):
            if not fname.lower().endswith('.mp3'):
                continue
            # Heuristic: project temp files created via NamedTemporaryFile will have tmp prefix
            if not fname.startswith('tmp') and 'tmp' not in fname:
                continue
            path = os.path.join(td, fname)
            try:
                mtime = os.path.getmtime(path)
                if now - mtime > older_than_seconds:
                    os.remove(path)
            except Exception:
                continue
    except Exception:
        pass

# Run cleanup at startup in a background thread (non-blocking)
try:
    threading.Thread(target=_cleanup_old_tts, kwargs={}, daemon=True).start()
except Exception:
    pass

@app.route("/")
def index():
    # Serve Cesium page at root
    return render_template("cesium.html", cesium_token=CESIUM_ION_TOKEN)



@app.route("/favicon.ico")
def favicon():
    # 1x1 transparent PNG
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x02\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return send_file(io.BytesIO(png_bytes), mimetype="image/png")


def _omap_weather_code_desc(code: int) -> str:
    mapping = {
        0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "fog", 48: "depositing rime fog",
        51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
        56: "light freezing drizzle", 57: "dense freezing drizzle",
        61: "slight rain", 63: "moderate rain", 65: "heavy rain",
        66: "light freezing rain", 67: "heavy freezing rain",
        71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
        80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
        85: "slight snow showers", 86: "heavy snow showers",
        95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail"
    }
    return mapping.get(int(code) if code is not None else -1, "weather unavailable")


def _open_meteo_fallback(lat: float, lon: float):
    # Debug logging removed
    
    # Current weather
    wx_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code&timezone=auto"
    )
    wx = requests.get(wx_url, timeout=10)
    wx.raise_for_status()
    wj = wx.json()
    cur = (wj or {}).get("current", {})

    # Enhanced reverse geocoding for name/country
    name, country = None, None
    
    # Try Nominatim first for better location accuracy
    try:
        nom_url = (
            "https://nominatim.openstreetmap.org/reverse"
            f"?format=jsonv2&lat={lat}&lon={lon}&zoom=12&accept-language=en&countrycodes=in"
        )
        # Debug logging removed
        nr = requests.get(nom_url, headers={"User-Agent": "globe-weather-app/1.0"}, timeout=10)
        if nr.ok:
            nj = nr.json()
            addr = nj.get("address", {}) if isinstance(nj, dict) else {}

            # Build location name prioritizing local areas
            name_parts = []

            # Check for specific locality identifiers
            if addr.get("suburb"):
                name_parts.append(addr.get("suburb"))
            elif addr.get("neighbourhood"):
                name_parts.append(addr.get("neighbourhood"))
            elif addr.get("quarter"):
                name_parts.append(addr.get("quarter"))
            elif addr.get("residential"):
                name_parts.append(addr.get("residential"))
            elif nj.get("name"):
                name_parts.append(nj.get("name"))

            # Add city/town
            if addr.get("city"):
                name_parts.append(addr.get("city"))
            elif addr.get("town"):
                name_parts.append(addr.get("town"))
            elif addr.get("municipality"):
                name_parts.append(addr.get("municipality"))

            # Add state if we don't have enough parts
            if len(name_parts) < 2 and addr.get("state"):
                name_parts.append(addr.get("state"))

            name = ", ".join(dict.fromkeys(name_parts)) if name_parts else None
            country = addr.get("country_code") or addr.get("country")
            # Debug logging removed
    except Exception:
        pass  # Debug logging removed

    # Fallback to Open-Meteo if Nominatim didn't work well
    if not name:
        try:
            geo_url = (
                "https://geocoding-api.open-meteo.com/v1/reverse"
                f"?latitude={lat}&longitude={lon}&language=en&count=1&format=json"
            )
            # Debug logging removed
            
            gr = requests.get(geo_url, timeout=10)
            if gr.ok:
                gj = gr.json()
                # Debug logging removed
                
                if gj.get("results"):
                    first = gj["results"][0]
                    # Compose a nicer place label
                    parts = [
                        first.get("name"),
                        first.get("admin3"),  # More specific admin level
                        first.get("admin2"),
                        first.get("admin1"),
                    ]
                    parts = [p for p in parts if p]
                    lbl = ", ".join(dict.fromkeys(parts)[:3]) if parts else None  # Limit to 3 parts
                    name = lbl
                    country = first.get("country_code") or first.get("country")
                    
                    # Debug logging removed
        except Exception:
            pass  # Debug logging removed

    out = {
        "name": name,
        "coord": {"lat": lat, "lon": lon},
        "weather": _omap_weather_code_desc(cur.get("weather_code")),
        "temp_c": cur.get("temperature_2m"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind_speed": cur.get("wind_speed_10m"),
        "country": country,
        "debug_coordinates": f"lat={lat:.6f}, lon={lon:.6f}"
    }
    
    # Debug logging removed
    return out


def _get_timezone_and_localtime(lat: float, lon: float, fallback_offset_seconds: int = None):
    """Return timezone info and local time for given coordinates.

    Tries Open-Meteo timezone API for a tz name; if that works we use zoneinfo
    to produce an ISO local timestamp. If that fails but an offset in seconds
    is available (for example from OpenWeather), we use a fixed offset tz.
    Returns a dict with keys: timezone, local_time, utc_offset_seconds.
    """
    try:
        tz_url = (
            "https://api.open-meteo.com/v1/timezone"
            f"?latitude={lat}&longitude={lon}"
        )
        r = requests.get(tz_url, timeout=5)
        if r.ok:
            j = r.json() or {}
            tzname = j.get("timezone")
            utc_off = j.get("utc_offset_seconds")
            if tzname:
                try:
                    from zoneinfo import ZoneInfo
                    import datetime as _dt
                    tz = ZoneInfo(tzname)
                    local = _dt.datetime.now(tz).isoformat()
                    return {"timezone": tzname, "local_time": local, "utc_offset_seconds": utc_off}
                except Exception:
                    # fallback to using offset if zoneinfo fails
                    pass
            if utc_off is not None:
                from datetime import timezone, timedelta
                import datetime as _dt
                tz = timezone(timedelta(seconds=int(utc_off)))
                local = _dt.datetime.now(tz).isoformat()
                return {"timezone": None, "local_time": local, "utc_offset_seconds": int(utc_off)}
    except Exception:
        pass

    # Last resort: use provided fallback offset (e.g. OpenWeather's timezone seconds)
    if fallback_offset_seconds is not None:
        try:
            from datetime import timezone, timedelta
            import datetime as _dt
            tz = timezone(timedelta(seconds=int(fallback_offset_seconds)))
            local = _dt.datetime.now(tz).isoformat()
            return {"timezone": None, "local_time": local, "utc_offset_seconds": int(fallback_offset_seconds)}
        except Exception:
            pass

    return {"timezone": None, "local_time": None, "utc_offset_seconds": None}


@app.route("/api/weather", methods=["POST"]) 
def api_weather():
    data = request.get_json(force=True)
    lat = data.get("lat")
    lon = data.get("lon")
    search_name = data.get("search_name")  # Optional: preserve original search location name

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon required"}), 400

    # Debug logging removed

    # Try OpenWeather if key exists; otherwise or on failure, use Open-Meteo fallback
    if OPENWEATHER_API_KEY:
        try:
            url = (
                f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
            )
            # Debug logging removed
            
            r = requests.get(url, timeout=10)
            if r.ok:
                payload = r.json()
                # Debug logging removed
                
                # Use search_name if available and more specific than OpenWeather's name
                weather_name = payload.get("name")
                final_name = search_name if search_name else weather_name
                
                # If we have both, prefer the more specific one
                if search_name and weather_name:
                    # If search name contains more detail (commas, sectors, etc.), prefer it
                    if ("sector" in search_name.lower() or "," in search_name or len(search_name.split()) > len(weather_name.split())):
                        final_name = search_name
                    else:
                        final_name = weather_name
                
                out = {
                    "name": final_name,
                    "coord": payload.get("coord"),
                    "weather": payload.get("weather", [{}])[0].get("description"),
                    "temp_c": payload.get("main", {}).get("temp"),
                    "humidity": payload.get("main", {}).get("humidity"),
                    "wind_speed": payload.get("wind", {}).get("speed"),
                    "country": payload.get("sys", {}).get("country"),
                    "debug_source": "openweather",
                    "debug_original_name": weather_name,
                    "debug_search_name": search_name
                }
                # Add timezone info using OpenWeather's timezone offset if present
                tz_offset = payload.get("timezone")  # seconds offset from UTC in OpenWeather
                tz_info = _get_timezone_and_localtime(lat, lon, fallback_offset_seconds=tz_offset)
                out.update(tz_info)
                return jsonify(out)
            else:
                pass  # Debug logging removed
        except Exception:
            pass  # Debug logging removed

    # Fallback
    # Fallback to Open-Meteo-based provider
    try:
        out = _open_meteo_fallback(lat, lon)
        out["debug_source"] = "open-meteo"
        # Apply same logic for search_name with fallback
        if search_name:
            original_name = out.get("name")
            if ("sector" in search_name.lower() or "," in search_name or (original_name and len(search_name.split()) > len(original_name.split()))):
                out["name"] = search_name
                out["debug_original_name"] = original_name
                out["debug_search_name"] = search_name

        # Try to fetch timezone info from Open-Meteo timezone API
        tz_info = _get_timezone_and_localtime(lat, lon)
        out.update(tz_info)
        return jsonify(out)
    except Exception:
        return jsonify({"error": "Weather service unavailable at the moment."}), 502


@app.route("/api/geocode", methods=["GET"])
def api_geocode():
    q = request.args.get("q", "").strip()
    # If autocomplete param present return multiple suggestions
    autocomplete = request.args.get("autocomplete", "0").lower() in ("1", "true", "yes")
    if not q:
        return jsonify({"error": "Missing query parameter q"}), 400
    
    # Debug logging removed
    
    try:
        # For Indian locations with specific terms, try Nominatim first for better accuracy
        is_indian_specific = any(term in q.lower() for term in [
            "sector", "block", "phase", "colony", "nagar", "vihar", "delhi", 
            "gurgaon", "gurugram", "faridabad", "noida", "ghaziabad", "haryana", 
            "punjab", "uttar pradesh", "up", "india"
        ])
        if is_indian_specific:
            # Try Nominatim first for Indian locations
            nom_url = (
                "https://nominatim.openstreetmap.org/search"
                f"?format=jsonv2&q={requests.utils.quote(q)}&limit=5&accept-language=en&countrycodes=in&addressdetails=1"
            )
            nr = requests.get(nom_url, headers={"User-Agent": "globe-weather-app/1.0"}, timeout=10)
            if nr.ok:
                arr = nr.json() or []
                if arr:
                    # If autocomplete requested, return a list of suggestions
                    if autocomplete:
                        suggestions = []
                        for item in arr[:8]:
                            try:
                                lat = float(item.get("lat"))
                                lon = float(item.get("lon"))
                            except (TypeError, ValueError):
                                lat = lon = None
                            addr = item.get("address", {})
                            # Build a readable label
                            parts = []
                            if addr.get("suburb"):
                                parts.append(addr.get("suburb"))
                            elif addr.get("neighbourhood"):
                                parts.append(addr.get("neighbourhood"))
                            elif item.get("name"):
                                parts.append(item.get("name"))
                            if addr.get("city"):
                                parts.append(addr.get("city"))
                            elif addr.get("town"):
                                parts.append(addr.get("town"))
                            if addr.get("state"):
                                parts.append(addr.get("state"))
                            label = ", ".join(dict.fromkeys([p for p in parts if p])) or (item.get("display_name") or q)
                            suggestions.append({"name": label, "lat": lat, "lon": lon, "country": addr.get("country_code") or addr.get("country"), "source": "nominatim"})
                        return jsonify({"suggestions": suggestions})
                    else:
                        first = arr[0]
                        try:
                            lat = float(first.get("lat"))
                            lon = float(first.get("lon"))
                        except (TypeError, ValueError):
                            lat = lon = None
                        # Build better name from address components
                        addr = first.get("address", {})
                        name_parts = []
                        # Include specific locality details
                        if addr.get("suburb"):
                            name_parts.append(addr.get("suburb"))
                        elif addr.get("neighbourhood"):
                            name_parts.append(addr.get("neighbourhood"))
                        elif addr.get("quarter"):
                            name_parts.append(addr.get("quarter"))
                        elif first.get("name"):
                            name_parts.append(first.get("name"))
                        # Add city
                        if addr.get("city"):
                            name_parts.append(addr.get("city"))
                        elif addr.get("town"):
                            name_parts.append(addr.get("town"))
                        # Add state for clarity
                        if addr.get("state") and len(name_parts) < 3:
                            name_parts.append(addr.get("state"))
                        # If still no good name, use display_name with some processing
                        if not name_parts:
                            disp = first.get("display_name") or first.get("name") or q
                            if disp and "," in disp:
                                parts = [p.strip() for p in disp.split(",")]
                                name_parts = parts[:3]  # Take first 3 parts
                        final_name = ", ".join(dict.fromkeys([p for p in name_parts if p])) if name_parts else q
                        out = {
                            "name": final_name,
                            "lat": lat,
                            "lon": lon,
                            "country": addr.get("country_code") or addr.get("country"),
                            "debug_source": "nominatim"
                        }
                        return jsonify(out)
        # Try Open-Meteo forward geocoding if Nominatim didn't work or for non-Indian locations
        url = (
            "https://geocoding-api.open-meteo.com/v1/search"
            f"?name={requests.utils.quote(q)}&count=5&language=en&format=json"
        )
        r = requests.get(url, timeout=10)
        if r.ok:
            data = r.json()
            res = data.get("results") or []
            if res:
                if autocomplete:
                    suggestions = []
                    for first in res[:8]:
                        name_parts = []
                        if first.get("name"):
                            name_parts.append(first.get("name"))
                        if first.get("admin3"):
                            name_parts.append(first.get("admin3"))
                        elif first.get("admin2"):
                            name_parts.append(first.get("admin2"))
                        if first.get("admin1") and first.get("admin1") not in name_parts:
                            name_parts.append(first.get("admin1"))
                        label = ", ".join(dict.fromkeys(name_parts)) if name_parts else first.get("name")
                        suggestions.append({"name": label, "lat": first.get("latitude"), "lon": first.get("longitude"), "country": first.get("country_code") or first.get("country"), "source": "open-meteo"})
                    return jsonify({"suggestions": suggestions})
                else:
                    first = res[0]
                    # Build more accurate location name prioritizing the search query
                    name_parts = []
                    if first.get("name"):
                        name_parts.append(first.get("name"))
                    if first.get("admin3"):  # More specific admin level (like sector, district)
                        name_parts.append(first.get("admin3"))
                    elif first.get("admin2"):
                        name_parts.append(first.get("admin2"))
                    if first.get("admin1") and first.get("admin1") not in name_parts:
                        name_parts.append(first.get("admin1"))
                    label = ", ".join(dict.fromkeys(name_parts)) if name_parts else first.get("name")
                    out = {
                        "name": label,
                        "lat": first.get("latitude"),
                        "lon": first.get("longitude"),
                        "country": first.get("country_code") or first.get("country"),
                        "debug_source": "open-meteo"
                    }
                    return jsonify(out)
        # Final fallback: Nominatim without country restriction
        if not is_indian_specific:
            nom_url = (
                "https://nominatim.openstreetmap.org/search"
                f"?format=jsonv2&q={requests.utils.quote(q)}&limit=5&accept-language=en"
            )
            nr = requests.get(nom_url, headers={"User-Agent": "globe-weather-app/1.0"}, timeout=10)
            if nr.ok:
                arr = nr.json() or []
                if arr:
                    first = arr[0]
                    try:
                        lat = float(first.get("lat"))
                        lon = float(first.get("lon"))
                    except (TypeError, ValueError):
                        lat = lon = None
                    # Extract more specific location from display_name
                    disp = first.get("display_name") or first.get("name") or q
                    if disp and "," in disp:
                        parts = [p.strip() for p in disp.split(",")]
                        # For Indian locations, try to keep sector/locality info
                        if "sector" in q.lower() or "block" in q.lower():
                            disp = ", ".join(parts[:4])  # Keep more detail for sectors
                        else:
                            disp = ", ".join(parts[:3])
                    out = {
                        "name": disp,
                        "lat": lat,
                        "lon": lon,
                        "country": (first.get("address") or {}).get("country_code") or (first.get("address") or {}).get("country"),
                        "debug_source": "nominatim"
                    }
                    return jsonify(out)
        # Debug logging removed
        # If no results and the query looks like an Indian locality, retry with helpful suffixes
        if not is_indian_specific and q and not q.lower().endswith("india"):
            # Try a sequence of helpful suffixes; first ', India' then a district hint like ', Kupwara, India'
            retries = [q + ", India", q + ", Kupwara, India"]
            for try_q in retries:
                url = (
                    "https://geocoding-api.open-meteo.com/v1/search"
                    f"?name={requests.utils.quote(try_q)}&count=5&language=en&format=json"
                )
                r = requests.get(url, timeout=10)
                if not r.ok:
                    continue
                data = r.json()
                res = data.get("results") or []
                if not res:
                    continue
                first = res[0]
                name_parts = []
                if first.get("name"):
                    name_parts.append(first.get("name"))
                if first.get("admin3"):
                    name_parts.append(first.get("admin3"))
                elif first.get("admin2"):
                    name_parts.append(first.get("admin2"))
                if first.get("admin1") and first.get("admin1") not in name_parts:
                    name_parts.append(first.get("admin1"))
                label = ", ".join(dict.fromkeys(name_parts)) if name_parts else first.get("name")
                out = {
                    "name": label,
                    "lat": first.get("latitude"),
                    "lon": first.get("longitude"),
                    "country": first.get("country_code") or first.get("country"),
                    "debug_source": "open-meteo-retry"
                }
                return jsonify(out)
        return jsonify({"error": "No results"}), 404
    except Exception:
        return jsonify({"error": "Geocoding failed"}), 502


@app.route("/api/tts", methods=["POST"])
def api_tts():
    import subprocess
    data = request.get_json(force=True)
    text = data.get("text", "")
    # engine option removed: we force-use gTTS for concurrency safety

    if not text:
        return jsonify({"error": "text required"}), 400

    import traceback
    try:
        print(f"[TTS] Received text: '{text[:50]}' (engine forced to gTTS)")
        # Always use gTTS for concurrency safety
        print("[TTS] Using gTTS engine")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp_file:
            tmp_mp3 = tmp_file.name
        try:
            tts = gTTS(text)
            tts.save(tmp_mp3)
            print(f"[TTS] Saved MP3 to {tmp_mp3}")
        except Exception as gtts_exc:
            import traceback
            print(f"[TTS] gTTS failed: {gtts_exc}\n{traceback.format_exc()}")
            # Clean up the file if partially written
            try:
                if os.path.exists(tmp_mp3):
                    os.remove(tmp_mp3)
            except Exception:
                pass
            return jsonify({"error": f"gTTS failed: {gtts_exc}"}), 500

        import platform
        plat = platform.system()
        play_cmd = None
        if plat == "Darwin":
            play_cmd = ["afplay", tmp_mp3]
        elif plat == "Windows":
            # Use Windows Media Player in background (minimized, non-blocking)
            play_cmd = ["cmd", "/c", "start", "/min", "wmplayer", tmp_mp3]
        else:
            from shutil import which
            if which("mpg123"):
                play_cmd = ["mpg123", tmp_mp3]
            else:
                play_cmd = ["aplay", tmp_mp3]
        print(f"[TTS] Playing with: {play_cmd}")
        try:
            # Start playback in background
            subprocess.Popen(play_cmd)
            # Schedule file removal later to avoid disk filling
            def _schedule_delete(path, delay=120):
                try:
                    time.sleep(delay)
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                threading.Thread(target=_schedule_delete, args=(tmp_mp3, 120), daemon=True).start()
            except Exception:
                pass
        except Exception as play_exc:
            print(f"[TTS] Playback failed: {play_exc}\n{traceback.format_exc()}")
            # Clean up the file immediately if playback couldn't be started
            try:
                if os.path.exists(tmp_mp3):
                    os.remove(tmp_mp3)
            except Exception:
                pass
        return jsonify({"status": "playing", "engine": "gtts"})
    except Exception as e:
        import traceback
        print(f"[TTS] Exception: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ambient", methods=["POST"]) 
def api_ambient():
    """Play ambient weather sounds from static files on Raspberry Pi/macOS.

    Expected files in static/audio/ambient:
    - rain.(wav|mp3), storm.(wav|mp3), wind.(wav|mp3), snow.(wav|mp3), sunny.(wav|mp3), fog.(wav|mp3), cloudy.(wav|mp3)
    - ambient.(wav|mp3) as a fallback
    """
    data = request.get_json(force=True)
    weather_condition = (data.get("weather") or "").lower()
    action = data.get("action", "play")  # play or stop
    # optional intensity (precipitation mm/h) to prefer heavier sounds
    intensity = None
    try:
        if data.get('precip_mm') is not None:
            intensity = float(data.get('precip_mm'))
        elif data.get('intensity') is not None:
            intensity = float(data.get('intensity'))
    except Exception:
        intensity = None
    
    print(f"[Ambient] Action: {action}, Weather: '{weather_condition}'")

    # Handle stop immediately
    if action == "stop":
        try:
            import subprocess
            # Kill shell loops and players referencing our static ambient folder
            patterns = ["static/audio/ambient"]
            for p in patterns:
                subprocess.run(["pkill", "-f", p], check=False)
            for p in ("afplay", "aplay", "mpg123"):
                subprocess.run(["pkill", p], check=False)
            return jsonify({"status": "stopped"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Map and adjust sound type
    sound_type = _get_weather_sound_type(weather_condition)
    if intensity is not None:
        try:
            if intensity > 10 and sound_type == 'rain':
                sound_type = 'storm'
            elif intensity > 2 and sound_type == 'ambient':
                sound_type = 'rain'
        except Exception:
            pass

    try:
        ambient_dir = os.path.join(app.root_path, "static", "audio", "ambient")
        base_candidates = [sound_type, "ambient"]
        import platform
        is_mac = platform.system() == "Darwin"
        preferred_exts = [".wav", ".mp3"]

        selected_path = None
        selected_ext = None
        selected_name = None
        for name in base_candidates:
            for ext in preferred_exts:
                candidate = os.path.join(ambient_dir, name + ext)
                if os.path.exists(candidate):
                    selected_path = candidate
                    selected_ext = ext
                    selected_name = name + ext
                    break
            if selected_path:
                break

        if not selected_path:
            return jsonify({"error": f"No audio file found for '{sound_type}'. Place files in static/audio/ambient."}), 404

        static_audio_path = selected_path

        # Stop any existing ambient sound first
        import subprocess
        subprocess.run(["pkill", "-f", "ambient_weather"], check=False)
        subprocess.run(["pkill", "-f", "static/audio/ambient"], check=False)

        # Build playback command
        play_cmd = None
        if is_mac:
            play_cmd = ["sh", "-c", f"while true; do afplay '{static_audio_path}'; sleep 0.25; done"]
        else:
            # Prefer wav via aplay, else mpg123 for mp3
            if selected_ext == ".wav":
                play_cmd = ["sh", "-c", f"while true; do aplay -q '{static_audio_path}'; sleep 0.25; done"]
            elif selected_ext == ".mp3":
                from shutil import which
                if which("mpg123"):
                    play_cmd = ["sh", "-c", f"while true; do mpg123 -q '{static_audio_path}'; sleep 0.25; done"]
                else:
                    return jsonify({"error": "MP3 playback not available on platform. Install 'mpg123' or provide a WAV file."}), 415

        # Start the process in background
        process = subprocess.Popen(
            play_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid if hasattr(os, 'setsid') else None
        )
        # Wait briefly to ensure it started
        time.sleep(0.5)
        poll_result = process.poll()
        if poll_result is not None:
            stdout, stderr = process.communicate()
            error_msg = f"Audio process failed: stdout={stdout.decode()}, stderr={stderr.decode()}"
            return jsonify({"error": error_msg}), 500

        return jsonify({
            "status": "playing",
            "sound_type": sound_type,
            "weather": weather_condition,
            "pid": process.pid,
            "command": " ".join(play_cmd),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _get_weather_sound_type(weather):
    """Map weather condition to ambient sound type"""
    # Accept several input shapes: numeric WMO / OpenWeather id, dict, or string
    if weather is None:
        return "ambient"

    # If a dict-like object is passed, try to extract useful fields
    try:
        if isinstance(weather, dict):
            # Try common keys used by OpenWeather/Open-Meteo
            if "id" in weather:
                try:
                    weather = int(weather.get("id"))
                except Exception:
                    weather = weather.get("description") or weather.get("weather") or ""
            elif "weather_code" in weather or "code" in weather:
                try:
                    weather = int(weather.get("weather_code") or weather.get("code"))
                except Exception:
                    weather = weather.get("description") or weather.get("weather") or ""
            else:
                weather = weather.get("description") or weather.get("weather") or ""
    except Exception:
        # Fall back to string handling below
        weather = str(weather)

    # If numeric code provided, map by known ranges (OpenWeather ids and WMO codes)
    try:
        code = int(weather)
        # OpenWeather thunderstorm range and Open-Meteo thunderstorm codes
        if 200 <= code <= 232 or 95 <= code <= 99:
            return "storm"
        # Drizzle
        if 300 <= code <= 321 or 51 <= code <= 57:
            return "rain"
        # Rain
        if 500 <= code <= 531 or 61 <= code <= 67 or 80 <= code <= 82:
            return "rain"
        # Snow
        if 600 <= code <= 622 or 71 <= code <= 77 or 85 <= code <= 86:
            return "snow"
        # Atmosphere / fog-like
        if 701 <= code <= 762 or code in (45, 48):
            return "fog"
        # Clear
        if code == 0 or code == 800:
            return "sunny"
        # Clouds (OpenWeather 801-804, Open-Meteo 1-3)
        if (801 <= code <= 804) or (1 <= code <= 3):
            return "cloudy"
    except Exception:
        pass

    # Textual matching (lowercase)
    w = str(weather).lower()
    if not w.strip():
        return "ambient"

    # Priority checks
    if "thunder" in w or "storm" in w or "tornado" in w:
        return "storm"
    if "hail" in w:
        return "storm"
    if "rain" in w or "drizzle" in w or "shower" in w or "precipitation" in w:
        return "rain"
    if "snow" in w or "sleet" in w or "blizzard" in w:
        return "snow"
    if "fog" in w or "mist" in w or "haze" in w or "smoke" in w or "dust" in w:
        return "fog"
    if "wind" in w or "breezy" in w or "gust" in w:
        return "wind"
    if "clear" in w or "sunny" in w:
        return "sunny"
    if "cloud" in w or "overcast" in w or "broken" in w or "scattered" in w:
        return "cloudy"

    return "ambient"


if __name__ == "__main__":
    # Bind to all interfaces for Pi/tablet access
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
