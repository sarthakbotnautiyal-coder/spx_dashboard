# SPX Dashboard

Real-time options scanner visualization and premium analysis dashboard.

## Features

- Live SPX option premium tracking (10pt and 20pt spreads)
- Technical indicators overlay (RSI, MACD, ADX, Bollinger Bands)
- Interactive filtering by premium, EM, RSI, and proximity
- Responsive web interface with Plotly charts
- Timezone-aware timestamps (EST)

## Installation

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Configuration

See `config/config.yaml` for data source paths.

The dashboard reads from two data sources:
1. Scanner results: `../premium_extractor/data/scanner.db`
2. Technical indicators: `../tradingView_signal_generator/data/tradingview.db`

## Usage

```bash
python run.py
```

Open http://localhost:5555 in your browser.

## API Endpoints

- `GET /` - Dashboard HTML
- `GET /api/scan_results` - Raw scan data (JSON)
- `GET /api/figure` - Plotly figure (JSON)
- `GET /api/dates` - Available date range

## Architecture

- `src/app.py`: Flask server and API endpoints
- `src/chart.py`: Plotly figure generation
- `src/data_loader.py`: Database queries and data preparation
- `templates/`: HTML templates
- `static/`: CSS and JavaScript
