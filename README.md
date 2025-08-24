# Life Automation Toolkit

Practical, small utilities to automate boring tasks. Each script is standalone and can be used directly or via the unified CLI.

## Features

- **Weather SMS Alert**: Notify yourself if rain is expected in the next 12 hours using OpenWeatherMap + Twilio.
- **File Sorter**: Organize a folder by file extension (e.g., PDFs → `Documents/pdf/`, images → `Pictures/jpg/`).
- **Media Renamer**: Normalize filenames (lowercase, hyphens, safe characters), optional date prefix.

## Quick Start

1. **Clone and set up a virtual environment**
   ```bash
   git clone https://github.com/<your-username>/life-automation-toolkit.git
   cd life-automation-toolkit
   python -m venv .venv && . .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure environment**
   - Copy `.env.example` to `.env` and fill in your values.
   - You will need:
     - OpenWeatherMap API key
     - Twilio Account SID, Auth Token, From number
     - Your destination phone number

3. **Run via CLI**
   ```bash
   python cli.py weather --city "Moncks Corner" --country "US" --units imperial --threshold 0.1
   python cli.py sort --path "/path/to/folder"
   python cli.py rename --path "/path/to/folder" --date-prefix
   ```

## Scripts

### 1) Weather SMS Alert (`weather_alert.py`)
Checks the next ~12 hours and sends a single SMS if precipitation probability exceeds the threshold.

**Example**
```bash
python weather_alert.py --city "Moncks Corner" --country "US" --units imperial --threshold 0.2
```

**Env variables**
- `OWM_API_KEY`: OpenWeatherMap API key
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM`: Twilio phone number (E.164), e.g. +18435551234
- `ALERT_TO`: Destination phone number (E.164)

### 2) File Sorter (`file_sorter.py`)
Creates subfolders by extension and moves files. Dry-run supported.

**Example**
```bash
python file_sorter.py --path "/Downloads" --dry-run
python file_sorter.py --path "/Downloads" --no-empty-dirs
```

### 3) Media Renamer (`media_renamer.py`)
Cleans filenames, removes unsafe characters, and can prefix with today’s date `YYYYMMDD-`.

**Example**
```bash
python media_renamer.py --path "/Photos" --date-prefix
```

### Unified CLI (`cli.py`)
Convenience wrapper:
```bash
python cli.py weather --city "Moncks Corner" --country "US"
python cli.py sort --path "/Downloads"
python cli.py rename --path "/Photos" --date-prefix
```

## Notes

- The weather script uses the OpenWeatherMap **One Call 3.0** endpoints.
- Twilio usage may incur SMS costs. Verify in your Twilio console.
- Always test with `--dry-run` before moving or renaming large batches.

## License

MIT
