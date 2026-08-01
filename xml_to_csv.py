"""
Convert TransXChange timetable XML files into one combined CSV.

WHAT IT DOES:
    Reads every .xml file in INPUT_DIR, and for each scheduled bus trip,
    works out the scheduled arrival time at every stop along its route
    (by adding up each RunTime from the trip's DepartureTime). Writes
    one row per stop-visit into a single CSV.

FOLDER SETUP (do this first):
    bus_project/
    ├── data/
    │   ├── timetables_raw/     <- put your unzipped XML files here
    │   └── timetables_csv/     <- this script creates the output here
    └── scripts/
        └── xml_to_csv.py       <- this file

HOW TO RUN:
    1. Open a terminal in the bus_project/scripts/ folder
    2. Run:  python xml_to_csv.py
    3. Output appears at: data/timetables_csv/timetables_combined.csv
"""

import os
import csv
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# ---------------- CONFIG ----------------
INPUT_DIR = "data/timetables_raw"
OUTPUT_CSV = "data/timetables_csv/timetables_combined.csv"
NS = {"tx": "http://www.transxchange.org.uk/"}
# -----------------------------------------


def parse_runtime(runtime_str):
    """Convert TransXChange duration like 'PT3M' or 'PT1M30S' into seconds."""
    if not runtime_str:
        return 0
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", runtime_str)
    if not match:
        return 0
    h, m, s = (int(x) if x else 0 for x in match.groups())
    return h * 3600 + m * 60 + s


def parse_file(filepath):
    """Parse a single TransXChange XML file, return a list of row dicts."""
    rows = []
    try:
        tree = ET.parse(filepath)
    except ET.ParseError as e:
        print(f"  SKIPPING (XML parse error): {os.path.basename(filepath)} - {e}")
        return rows

    root = tree.getroot()
    filename = os.path.basename(filepath)

    # 1. Build a lookup of StopPointRef -> (CommonName, Latitude, Longitude)
    stops = {}
    for sp in root.findall(".//tx:StopPoints/tx:AnnotatedStopPointRef", NS):
        ref = sp.findtext("tx:StopPointRef", default="", namespaces=NS)
        name = sp.findtext("tx:CommonName", default="", namespaces=NS)
        lat = sp.findtext("tx:Location/tx:Latitude", default="", namespaces=NS)
        lon = sp.findtext("tx:Location/tx:Longitude", default="", namespaces=NS)
        stops[ref] = (name, lat, lon)

    # 2. Build JourneyPatternSection id -> ordered list of (stop_ref, run_time_seconds)
    #    Each timing link's "From" stop is recorded with the RunTime taken to
    #    reach the "To" stop.
    section_links = {}
    for section in root.findall(".//tx:JourneyPatternSections/tx:JourneyPatternSection", NS):
        section_id = section.get("id")
        links = []
        for link in section.findall("tx:JourneyPatternTimingLink", NS):
            from_stop = link.findtext("tx:From/tx:StopPointRef", default="", namespaces=NS)
            to_stop = link.findtext("tx:To/tx:StopPointRef", default="", namespaces=NS)
            runtime_sec = parse_runtime(link.findtext("tx:RunTime", default="", namespaces=NS))
            links.append((from_stop, to_stop, runtime_sec))
        section_links[section_id] = links

    # 3. Build JourneyPattern id -> list of section ids it references
    pattern_sections = {}
    pattern_direction = {}
    for service in root.findall(".//tx:Services/tx:Service", NS):
        service_code = service.findtext("tx:ServiceCode", default="", namespaces=NS)
        line_name = service.findtext(".//tx:LineName", default="", namespaces=NS)
        for jp in service.findall(".//tx:JourneyPattern", NS):
            jp_id = jp.get("id")
            refs = jp.findtext("tx:JourneyPatternSectionRefs", default="", namespaces=NS)
            pattern_sections[jp_id] = (refs.split() if refs else [], service_code, line_name)
            pattern_direction[jp_id] = jp.findtext("tx:Direction", default="", namespaces=NS)

    # 4. Walk every VehicleJourney, compute scheduled time at each stop
    for vj in root.findall(".//tx:VehicleJourneys/tx:VehicleJourney", NS):
        vj_code = vj.findtext("tx:VehicleJourneyCode", default="", namespaces=NS)
        jp_ref = vj.findtext("tx:JourneyPatternRef", default="", namespaces=NS)
        dep_time_str = vj.findtext("tx:DepartureTime", default="", namespaces=NS)

        if jp_ref not in pattern_sections or not dep_time_str:
            continue

        section_ids, service_code, line_name = pattern_sections[jp_ref]
        direction = pattern_direction.get(jp_ref, "")

        try:
            current_time = datetime.strptime(dep_time_str, "%H:%M:%S")
        except ValueError:
            continue

        sequence = 1
        # First stop = departure time at the very first "From" stop
        first_stop_written = False

        for section_id in section_ids:
            for from_stop, to_stop, runtime_sec in section_links.get(section_id, []):
                if not first_stop_written:
                    name, lat, lon = stops.get(from_stop, ("", "", ""))
                    rows.append({
                        "source_file": filename,
                        "service_code": service_code,
                        "line_name": line_name,
                        "direction": direction,
                        "vehicle_journey_code": vj_code,
                        "stop_sequence": sequence,
                        "stop_ref": from_stop,
                        "stop_name": name,
                        "latitude": lat,
                        "longitude": lon,
                        "scheduled_time": current_time.strftime("%H:%M:%S"),
                    })
                    first_stop_written = True
                    sequence += 1

                current_time += timedelta(seconds=runtime_sec)
                name, lat, lon = stops.get(to_stop, ("", "", ""))
                rows.append({
                    "source_file": filename,
                    "service_code": service_code,
                    "line_name": line_name,
                    "direction": direction,
                    "vehicle_journey_code": vj_code,
                    "stop_sequence": sequence,
                    "stop_ref": to_stop,
                    "stop_name": name,
                    "latitude": lat,
                    "longitude": lon,
                    "scheduled_time": current_time.strftime("%H:%M:%S"),
                })
                sequence += 1

    return rows


def main():
    if not os.path.isdir(INPUT_DIR):
        raise SystemExit(f"Input folder not found: {INPUT_DIR}\n"
                          f"Put your unzipped XML files there first.")

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

    xml_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith(".xml")]
    print(f"Found {len(xml_files)} XML files in {INPUT_DIR}")

    fieldnames = ["source_file", "service_code", "line_name", "direction",
                  "vehicle_journey_code", "stop_sequence", "stop_ref",
                  "stop_name", "latitude", "longitude", "scheduled_time"]

    total_rows = 0
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as out_f:
        writer = csv.DictWriter(out_f, fieldnames=fieldnames)
        writer.writeheader()

        for i, fname in enumerate(xml_files, 1):
            filepath = os.path.join(INPUT_DIR, fname)
            rows = parse_file(filepath)
            for row in rows:
                writer.writerow(row)
            total_rows += len(rows)
            print(f"  [{i}/{len(xml_files)}] {fname}: {len(rows)} rows")

    print(f"\nDone. Wrote {total_rows} total rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()