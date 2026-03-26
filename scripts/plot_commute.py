#!/usr/bin/env python3
"""Plot a commute on an interactive Folium map with a speed-over-time chart.

Usage:
    python scripts/plot_commute.py <path-to-jsonl> [output-dir]

Generates:
    - commute_map.html  (interactive Folium map with GPS trail)
    - commute_speed.html (speed over time chart, inline HTML+SVG)

Phase 0 tracer bullet: see your commute visually and verify the data.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.ingest import load_jsonl


def create_map(df, output_path: Path) -> None:
    """Create an interactive Folium map of the commute."""
    import folium

    lats = df["lat"].to_list()
    lons = df["lon"].to_list()
    speeds = df["computed_speed_kmh"].to_list()
    timestamps = df["timestamp"].to_list()

    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)

    m = folium.Map(location=[center_lat, center_lon], zoom_start=12)

    # Color the trail by speed
    def speed_color(s: float) -> str:
        if s < 1:
            return "#808080"  # gray - stationary
        if s < 7:
            return "#2ecc71"  # green - walking
        if s < 30:
            return "#f39c12"  # orange - slow drive/bus
        if s < 80:
            return "#e74c3c"  # red - driving
        return "#9b59b6"  # purple - train

    # Draw line segments colored by speed
    for i in range(1, len(lats)):
        folium.PolyLine(
            locations=[[lats[i - 1], lons[i - 1]], [lats[i], lons[i]]],
            color=speed_color(speeds[i]),
            weight=4,
            opacity=0.8,
        ).add_to(m)

    # Start marker
    folium.Marker(
        [lats[0], lons[0]],
        popup=f"Start: {timestamps[0]}",
        icon=folium.Icon(color="green", icon="play"),
    ).add_to(m)

    # End marker
    folium.Marker(
        [lats[-1], lons[-1]],
        popup=f"End: {timestamps[-1]}",
        icon=folium.Icon(color="red", icon="stop"),
    ).add_to(m)

    # Legend
    legend_html = """
    <div style="position:fixed;bottom:30px;left:30px;z-index:1000;background:white;
                padding:10px;border-radius:5px;border:2px solid grey;font-size:13px;">
        <b>Speed Legend</b><br>
        <span style="color:#808080">&#9632;</span> Stationary (&lt;1 km/h)<br>
        <span style="color:#2ecc71">&#9632;</span> Walking (1-7 km/h)<br>
        <span style="color:#f39c12">&#9632;</span> Slow vehicle (7-30 km/h)<br>
        <span style="color:#e74c3c">&#9632;</span> Driving (30-80 km/h)<br>
        <span style="color:#9b59b6">&#9632;</span> Train (&gt;80 km/h)<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    m.save(str(output_path))
    print(f"Map saved: {output_path}")


def create_speed_chart(df, output_path: Path) -> None:
    """Create a speed-over-time chart as a standalone HTML file using inline SVG."""
    timestamps = df["timestamp"].to_list()
    speeds = df["computed_speed_kmh"].to_list()

    t0 = timestamps[0]
    minutes = [(t - t0).total_seconds() / 60 for t in timestamps]

    max_min = max(minutes) if minutes else 1
    max_speed = max(speeds) if speeds else 1

    width, height = 900, 350
    margin = 60

    def x(val: float) -> float:
        return margin + (val / max_min) * (width - 2 * margin)

    def y(val: float) -> float:
        return height - margin - (val / max_speed) * (height - 2 * margin)

    points = " ".join(f"{x(m):.1f},{y(s):.1f}" for m, s in zip(minutes, speeds))

    # Speed zone bands
    zones = [
        (0, 1, "#f0f0f0", "Stationary"),
        (1, 7, "#e8f8e8", "Walking"),
        (7, 30, "#fff3e0", "Slow vehicle"),
        (30, 80, "#fde8e8", "Driving"),
        (80, max_speed, "#f3e8f8", "Train"),
    ]

    bands_svg = ""
    for low, high, color, _label in zones:
        if low >= max_speed:
            break
        high = min(high, max_speed)
        bands_svg += f'<rect x="{margin}" y="{y(high):.1f}" width="{width - 2 * margin}" height="{y(low) - y(high):.1f}" fill="{color}" />\n'

    # Grid lines
    grid_svg = ""
    for spd in range(0, int(max_speed) + 20, 20):
        if spd > max_speed:
            break
        yy = y(spd)
        grid_svg += f'<line x1="{margin}" y1="{yy:.1f}" x2="{width - margin}" y2="{yy:.1f}" stroke="#ddd" stroke-width="0.5" />\n'
        grid_svg += f'<text x="{margin - 5}" y="{yy:.1f}" text-anchor="end" font-size="11" fill="#666">{spd}</text>\n'

    for t in range(0, int(max_min) + 5, 5):
        if t > max_min:
            break
        xx = x(t)
        grid_svg += f'<line x1="{xx:.1f}" y1="{margin}" x2="{xx:.1f}" y2="{height - margin}" stroke="#ddd" stroke-width="0.5" />\n'
        grid_svg += f'<text x="{xx:.1f}" y="{height - margin + 18}" text-anchor="middle" font-size="11" fill="#666">{t}</text>\n'

    html = f"""<!DOCTYPE html>
<html><head><title>Commute Speed Profile</title></head>
<body style="font-family:sans-serif;max-width:960px;margin:20px auto;">
<h2>Speed Over Time</h2>
<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
{bands_svg}
{grid_svg}
<polyline points="{points}" fill="none" stroke="#2c3e50" stroke-width="1.5" />
<text x="{width // 2}" y="{height - 10}" text-anchor="middle" font-size="13" fill="#333">Minutes from start</text>
<text x="15" y="{height // 2}" text-anchor="middle" font-size="13" fill="#333" transform="rotate(-90,15,{height // 2})">Speed (km/h)</text>
</svg>
<p style="color:#666;font-size:13px;">Points: {len(speeds)} | Duration: {max_min:.1f} min | Max speed: {max_speed:.1f} km/h</p>
</body></html>"""

    output_path.write_text(html)
    print(f"Speed chart saved: {output_path}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/plot_commute.py <path-to-jsonl> [output-dir]")
        sys.exit(1)

    jsonl_path = sys.argv[1]
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_jsonl(jsonl_path)

    create_map(df, output_dir / "commute_map.html")
    create_speed_chart(df, output_dir / "commute_speed.html")


if __name__ == "__main__":
    main()
