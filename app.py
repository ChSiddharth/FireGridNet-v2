
import os
import math
import tempfile
from datetime import datetime

import folium
import pandas as pd
import requests
import streamlit as st
from folium.plugins import Draw
from streamlit_folium import st_folium


# --------------------------------------------------
# 1. PAGE CONFIGURATION AND DESIGN
# --------------------------------------------------

st.set_page_config(
    page_title="FireGridNet",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp {
        background: #071321;
        color: #eaf2ff;
    }

    section[data-testid="stSidebar"] {
        background: #0b1a2b;
    }

    div[data-testid="stMetric"] {
        background: #10243a;
        border: 1px solid #24415d;
        padding: 12px;
        border-radius: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🔥 FireGridNet")
st.caption("Wildfire Detection · Weather Risk · GIS Decision Support")


# --------------------------------------------------
# 2. NAVIGATION
# --------------------------------------------------

pages = [
    "1 · World Map & Forest Explorer",
    "2 · Forest Grid Generation",
    "3 · Weather & Grid Risk",
    "4 · Forest Imagery & YOLO",
    "5 · GIS & Incident Dashboard",
]

page = st.sidebar.radio("WORKSPACE", pages)


# --------------------------------------------------
# 3. FOREST LOCATIONS
# --------------------------------------------------

FORESTS = {
    "Jim Corbett National Park": (29.53, 78.77),
    "Amrabad Tiger Reserve": (16.30, 78.60),
    "Nagarhole National Park": (12.00, 76.10),
    "Kanha National Park": (22.33, 80.61),
    "Sundarbans National Park": (21.95, 88.90),
}


# --------------------------------------------------
# 4. SESSION STATE
# --------------------------------------------------

if "forest" not in st.session_state:
    st.session_state.forest = "Jim Corbett National Park"

if "center" not in st.session_state:
    st.session_state.center = list(
        FORESTS[st.session_state.forest]
    )

if "boundary" not in st.session_state:
    st.session_state.boundary = None

if "grid" not in st.session_state:
    st.session_state.grid = pd.DataFrame()

if "incidents" not in st.session_state:
    st.session_state.incidents = []


# --------------------------------------------------
# 5. WEATHER-BASED RISK SCORE
# --------------------------------------------------

def risk_score(temp, humidity, rain, wind):
    values = [temp, humidity, rain, wind]

    if any(v is None or pd.isna(v) for v in values):
        return None, "No data"

    score = (
        max(0, min(30, (temp - 15) * 1.5))
        + max(0, min(35, (60 - humidity) * 0.7))
        + max(0, min(20, wind * 0.45))
        + max(0, 15 - rain * 3)
    )

    score = round(min(score, 100), 1)

    if score >= 70:
        label = "Very High"
    elif score >= 50:
        label = "High"
    elif score >= 30:
        label = "Moderate"
    else:
        label = "Low"

    return score, label


COLORS = {
    "Low": "#27ae60",
    "Moderate": "#f1c40f",
    "High": "#f58231",
    "Very High": "#e53935",
    "No data": "#8995a5",
    "Not assessed": "#8995a5",
}


# --------------------------------------------------
# 6. FETCH WEATHER DATA
# --------------------------------------------------

# --------------------------------------------------
# 6. FETCH WEATHER DATA AND FOREST BOUNDARY
# --------------------------------------------------

def get_weather(lat, lon):
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": (
                "temperature_2m,relative_humidity_2m,"
                "precipitation,wind_speed_10m"
            ),
            "timezone": "auto",
        },
        timeout=20,
    )

    response.raise_for_status()
    return response.json()["current"]




def fetch_forest_boundary(forest_name):
    """Find a forest polygon using Nominatim and Overpass."""

    forest_queries = {
        "Jim Corbett National Park": [
            "Jim Corbett National Park, Uttarakhand, India",
            "Corbett National Park, India",
        ],
        "Amrabad Tiger Reserve": [
            "Amrabad Tiger Reserve, Telangana, India",
            "Amrabad Wildlife Sanctuary, Telangana, India",
        ],
        "Nagarhole National Park": [
            "Nagarhole National Park, Karnataka, India",
            "Rajiv Gandhi National Park, Karnataka, India",
        ],
        "Kanha National Park": [
            "Kanha National Park, Madhya Pradesh, India",
            "Kanha Tiger Reserve, Madhya Pradesh, India",
        ],
        "Sundarbans National Park": [
            "Sundarban National Park, West Bengal, India",
            "Sundarbans National Park, West Bengal, India",
            "Sundarbans National Park, India",
        ],
    }

    headers = {
        "User-Agent": "FireGridNet/1.0 (student research project)"
    }

    # SOURCE 1: Nominatim search
    for query in forest_queries.get(
        forest_name, [f"{forest_name}, India"]
    ):
        try:
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": query,
                    "format": "jsonv2",
                    "polygon_geojson": 1,
                    "limit": 5,
                    "countrycodes": "in",
                },
                headers=headers,
                timeout=25,
            )
            response.raise_for_status()

            for item in response.json():
                geometry = item.get("geojson")

                if (
                    isinstance(geometry, dict)
                    and geometry.get("type")
                    in ("Polygon", "MultiPolygon")
                    and geometry.get("coordinates")
                ):
                    return geometry

        except (requests.RequestException, ValueError, TypeError):
            continue

    # SOURCE 2: Overpass API, searching mapped boundary ways
    search_terms = {
        "Kanha National Park": ["Kanha"],
        "Sundarbans National Park": ["Sundarban", "Sundarbans"],
        "Jim Corbett National Park": ["Corbett"],
        "Amrabad Tiger Reserve": ["Amrabad"],
        "Nagarhole National Park": ["Nagarhole", "Rajiv Gandhi"],
    }

    terms = search_terms.get(forest_name, [forest_name])

    for term in terms:
        safe_term = term.replace("\\", "\\\\").replace('"', '\\"')

        query = f"""
        [out:json][timeout:30];
        (
          way["name"~"{safe_term}",i]["boundary"~"protected_area|national_park"];
          way["name"~"{safe_term}",i]["leisure"="nature_reserve"];
        );
        out geom;
        """

        try:
            response = requests.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query},
                headers=headers,
                timeout=40,
            )
            response.raise_for_status()
            data = response.json()

            for element in data.get("elements", []):
                points = element.get("geometry", [])

                # A closed way has the same first and last coordinate.
                if len(points) < 4:
                    continue

                first = points[0]
                last = points[-1]

                if (
                    first.get("lat") != last.get("lat")
                    or first.get("lon") != last.get("lon")
                ):
                    continue

                ring = [
                    [point["lon"], point["lat"]]
                    for point in points
                ]

                return {
                    "type": "Polygon",
                    "coordinates": [ring],
                }

        except (requests.RequestException, ValueError, TypeError):
            continue

    return None




# --------------------------------------------------
# 7. CHECK WHETHER A POINT IS INSIDE A POLYGON
# --------------------------------------------------

def inside_polygon(lon, lat, ring):
    inside = False
    j = len(ring) - 1

    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]

        crosses = (yi > lat) != (yj > lat)

        if crosses:
            x_intersect = (
                (xj - xi)
                * (lat - yi)
                / ((yj - yi) or 1e-12)
                + xi
            )

            if lon < x_intersect:
                inside = not inside

        j = i

    return inside


# --------------------------------------------------
# 8. GENERATE FOREST GRID
# --------------------------------------------------


# --------------------------------------------------
# 8. GENERATE FOREST GRID
# --------------------------------------------------

def generate_grid(center, boundary, rows, cols):
    """Generate grid cells across the selected forest boundary."""

    lat, lon = center
    rings = []

    # Extract exterior boundary rings
    if boundary and boundary.get("type") == "Polygon":
        rings = boundary.get("coordinates", [])[:1]

    elif boundary and boundary.get("type") == "MultiPolygon":
        rings = [
            polygon[0]
            for polygon in boundary.get("coordinates", [])
            if polygon
        ]

    # If no usable boundary exists, use an approximate study area
    if not rings:
        lat_span = 0.12
        lon_span = lat_span / max(
            math.cos(math.radians(lat)), 0.25
        )

        min_lat = lat - lat_span / 2
        max_lat = lat + lat_span / 2
        min_lon = lon - lon_span / 2
        max_lon = lon + lon_span / 2

    else:
        all_points = [
            point
            for ring in rings
            for point in ring
        ]

        min_lon = min(point[0] for point in all_points)
        max_lon = max(point[0] for point in all_points)
        min_lat = min(point[1] for point in all_points)
        max_lat = max(point[1] for point in all_points)

    # Prevent invalid grid dimensions
    if min_lat >= max_lat or min_lon >= max_lon:
        return pd.DataFrame()

    cell_lat = (max_lat - min_lat) / rows
    cell_lon = (max_lon - min_lon) / cols

    records = []

    for r in range(rows):
        for c in range(cols):
            cell_lat_center = min_lat + (r + 0.5) * cell_lat
            cell_lon_center = min_lon + (c + 0.5) * cell_lon

            # Keep cells whose centers are inside any boundary ring
            if rings and not any(
                inside_polygon(
                    cell_lon_center,
                    cell_lat_center,
                    ring,
                )
                for ring in rings
            ):
                continue

            records.append({
                "Grid ID": f"{chr(65 + r)}{c + 1:02d}",
                "Latitude": round(cell_lat_center, 6),
                "Longitude": round(cell_lon_center, 6),
                "Cell Height": cell_lat,
                "Cell Width": cell_lon,
                "Temperature °C": None,
                "Humidity %": None,
                "Rain mm": None,
                "Wind km/h": None,
                "Risk Score": None,
                "Risk Level": "Not assessed",
            })

    return pd.DataFrame(records)



# --------------------------------------------------
# 9. BUILD MAPS
# --------------------------------------------------

def build_map(global_view=False, with_grid=False):

    # World map
    if global_view:
        m = folium.Map(
            location=[20, 0],
            zoom_start=2,
            tiles="OpenStreetMap",
            control_scale=True,
        )

        folium.Marker(
            [20.59, 78.96],
            tooltip="India · Initial study region",
            icon=folium.Icon(
                color="green",
                icon="leaf",
            ),
        ).add_to(m)

        for name, coords in FORESTS.items():
            folium.CircleMarker(
                location=coords,
                radius=5,
                tooltip=name,
                color="#35df88",
                fill=True,
                fill_opacity=0.9,
            ).add_to(m)

        return m

    # Selected forest map
    lat, lon = st.session_state.center

    m = folium.Map(
        location=[lat, lon],
        zoom_start=10,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    boundary = st.session_state.boundary

    # Draw forest boundary
    if boundary:
        folium.GeoJson(
            {
                "type": "Feature",
                "properties": {
                    "name": st.session_state.forest
                },
                "geometry": boundary,
            },
            name="Forest boundary",
            tooltip=st.session_state.forest,
            style_function=lambda _: {
                "color": "#35f58a",
                "weight": 3,
                "fillColor": "#16834b",
                "fillOpacity": 0.12,
            },
        ).add_to(m)

    # Draw grid cells
    
    # Draw grid cells using their actual generated dimensions
    if with_grid and not st.session_state.grid.empty:
        df = st.session_state.grid

        for _, cell in df.iterrows():
            color = COLORS.get(
                cell["Risk Level"],
                COLORS["Not assessed"],
            )

            clat = cell["Latitude"]
            clon = cell["Longitude"]
            cell_lat = cell["Cell Height"]
            cell_lon = cell["Cell Width"]

            folium.Rectangle(
                bounds=[
                    [
                        clat - cell_lat / 2,
                        clon - cell_lon / 2,
                    ],
                    [
                        clat + cell_lat / 2,
                        clon + cell_lon / 2,
                    ],
                ],
                color=color,
                weight=1,
                fill=True,
                fill_color=color,
                fill_opacity=0.65,
                tooltip=(
                    f'{cell["Grid ID"]} · '
                    f'{cell["Risk Level"]} · '
                    f'{cell["Risk Score"]}'
                ),
            ).add_to(m)


# --------------------------------------------------
# 10. MODULE 1: WORLD MAP & FOREST EXPLORER
# --------------------------------------------------


# --------------------------------------------------
# 10. MODULE 1: WORLD MAP & FOREST EXPLORER
# --------------------------------------------------

if page == pages[0]:

    st.subheader("World Map & Forest Explorer")

    st.write(
        "The world map is visible; India is the initial study region."
    )

    left, right = st.columns([2, 1])

    with left:
        st_folium(
            build_map(global_view=True),
            height=480,
            use_container_width=True,
            key="world_map",
        )

    with right:
        forest_names = list(FORESTS.keys())

        selected = st.selectbox(
            "Select an Indian forest",
            forest_names,
            index=forest_names.index(st.session_state.forest),
            key="forest_selector",
        )

        if st.button(
            "Open forest",
            type="primary",
            key="open_forest_button",
        ):
            if selected != st.session_state.forest:
                st.session_state.forest = selected
                st.session_state.center = list(FORESTS[selected])
                st.session_state.boundary = None
                st.session_state.grid = pd.DataFrame()
                st.session_state.pop("grid_rows", None)
                st.session_state.pop("grid_cols", None)

            st.success(f"Selected forest: {selected}")

    st.markdown(
        f"**Selected:** {st.session_state.forest}"
    )

    st.caption(
        "Forest names are starting examples. Verify boundaries "
        "before using results for real-world decisions."
    )

    if st.button(
        "Fetch forest boundary",
        type="secondary",
        key="fetch_forest_boundary_button",
    ):
        with st.spinner(
            "Searching OpenStreetMap for the forest boundary..."
        ):
            geometry = fetch_forest_boundary(
                st.session_state.forest
            )

        if geometry:
            st.session_state.boundary = geometry
            st.success("Forest boundary fetched successfully!")
        else:
            st.warning(
                "No boundary polygon was found. "
                "Please draw the boundary manually below."
            )

    st.subheader("Draw the forest boundary")

    st.write(
        "Zoom to your forest and draw a polygon around its boundary. "
        "The drawn outline will be used for grid generation."
    )

    draw_map = folium.Map(
        location=st.session_state.center,
        zoom_start=10,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    # Display the fetched boundary on the drawing map
    if st.session_state.boundary:
        folium.GeoJson(
            {
                "type": "Feature",
                "properties": {
                    "name": st.session_state.forest
                },
                "geometry": st.session_state.boundary,
            },
            name="Fetched forest boundary",
            tooltip=st.session_state.forest,
            style_function=lambda _: {
                "color": "#35f58a",
                "weight": 3,
                "fillColor": "#16834b",
                "fillOpacity": 0.15,
            },
        ).add_to(draw_map)

    # Keep the manual drawing tools available
    Draw(
        export=True,
        draw_options={
            "polyline": False,
            "rectangle": True,
            "circle": False,
            "marker": False,
            "circlemarker": False,
            "polygon": True,
        },
        edit_options={
            "edit": True,
            "remove": True,
        },
    ).add_to(draw_map)

    result = st_folium(
        draw_map,
        height=430,
        use_container_width=True,
        key="boundary_draw",
    )

    if result and result.get("last_active_drawing"):
        geometry = result["last_active_drawing"].get("geometry")

        if geometry and geometry.get("type") in (
            "Polygon",
            "MultiPolygon",
        ):
            st.session_state.boundary = geometry

            st.success(
                "Forest boundary captured for this session."
            )



# --------------------------------------------------
# 11. MODULE 2: FOREST GRID GENERATION
# --------------------------------------------------

elif page == pages[1]:

    st.subheader("Forest Grid Generation")

    selected = st.selectbox(
        "Forest",
        list(FORESTS),
        index=list(FORESTS).index(
            st.session_state.forest
        ),
    )

    if selected != st.session_state.forest:
        st.session_state.forest = selected
        st.session_state.center = list(FORESTS[selected])
        st.session_state.boundary = None
        st.session_state.grid = pd.DataFrame()

    a, b = st.columns(2)

    with a:
        rows = st.slider(
            "Grid rows",
            4,
            16,
            8,
        )

    with b:
        cols = st.slider(
            "Grid columns",
            4,
            16,
            8,
        )

    if not st.session_state.boundary:
        st.warning(
            "Draw the forest boundary in Module 1 first. "
            "Without a boundary, cells cover only an approximate "
            "study area."
        )

    if st.button("Generate grid", type="primary"):
        st.session_state.grid_rows = rows
        st.session_state.grid_cols = cols

        st.session_state.grid = generate_grid(
            st.session_state.center,
            st.session_state.boundary,
            rows,
            cols,
        )

    if not st.session_state.grid.empty:
        st.metric(
            "Grid cells",
            len(st.session_state.grid),
        )

        st_folium(
            build_map(with_grid=True),
            height=500,
            use_container_width=True,
            key="grid_display",
        )

        st.dataframe(
            st.session_state.grid,
            use_container_width=True,
        )

        st.download_button(
            "Download grid CSV",
            st.session_state.grid.to_csv(index=False),
            "firegridnet_grid.csv",
            "text/csv",
        )


# --------------------------------------------------
# 12. MODULE 3: WEATHER & GRID RISK
# --------------------------------------------------

elif page == pages[2]:

    st.subheader("Weather & Grid Risk")

    if st.session_state.grid.empty:
        st.info(
            "Generate the forest grid in Module 2 first."
        )

    else:
        df = st.session_state.grid.copy()

        limit = st.slider(
            "Cells to assess",
            1,
            len(df),
            min(16, len(df)),
        )

        if st.button(
            "Fetch live weather and assess risk",
            type="primary",
        ):
            progress = st.progress(0)

            for i in range(limit):
                try:
                    weather = get_weather(
                        float(df.loc[i, "Latitude"]),
                        float(df.loc[i, "Longitude"]),
                    )

                    temp = weather.get("temperature_2m")
                    humidity = weather.get(
                        "relative_humidity_2m"
                    )
                    rain = weather.get("precipitation")
                    wind = weather.get("wind_speed_10m")

                    score, level = risk_score(
                        temp,
                        humidity,
                        rain,
                        wind,
                    )

                    df.loc[i, "Temperature °C"] = temp
                    df.loc[i, "Humidity %"] = humidity
                    df.loc[i, "Rain mm"] = rain
                    df.loc[i, "Wind km/h"] = wind
                    df.loc[i, "Risk Score"] = score
                    df.loc[i, "Risk Level"] = level

                except Exception:
                    df.loc[i, "Risk Level"] = "No data"

                progress.progress(
                    (i + 1) / limit
                )

            st.session_state.grid = df

            st.success(
                f"Weather checked for {limit} cells."
            )

        df = st.session_state.grid

        assessed = df[df["Risk Score"].notna()]

        high = assessed["Risk Level"].isin(
            ["High", "Very High"]
        ).sum()

        a, b, c = st.columns(3)

        a.metric(
            "Assessed cells",
            len(assessed),
        )

        b.metric(
            "High-risk cells",
            int(high),
        )

        c.metric(
            "Unassessed cells",
            len(df) - len(assessed),
        )

        st_folium(
            build_map(with_grid=True),
            height=460,
            use_container_width=True,
            key="risk_map",
        )

        st.dataframe(
            df,
            use_container_width=True,
        )

        st.caption(
            "Risk scores are an experimental heuristic, not an "
            "official Fire Weather Index or validated wildfire forecast."
        )

        st.download_button(
            "Download weather and risk",
            df.to_csv(index=False),
            "firegridnet_weather_risk.csv",
            "text/csv",
        )


# --------------------------------------------------
# 13. MODULE 4: FOREST IMAGERY & YOLO DETECTION
# --------------------------------------------------

elif page == pages[3]:

    st.subheader("Forest Imagery & YOLO Detection")

    st.write(
        "Upload a recent forest image. The app will run your trained "
        "YOLO model and show the detection result."
    )

    image_file = st.file_uploader(
        "Forest image",
        type=["jpg", "jpeg", "png", "webp"],
    )

    model_file = st.file_uploader(
        "Optional YOLO weights (.pt)",
        type=["pt"],
    )

    if image_file:
        from PIL import Image

        image = Image.open(image_file).convert("RGB")

        st.image(
            image,
            caption="Input image",
            use_container_width=True,
        )

        if st.button(
            "Run YOLO detection",
            type="primary",
        ):
            try:
                from ultralytics import YOLO

                model_path = "best.pt"

                if model_file:
                    with tempfile.NamedTemporaryFile(
                        delete=False,
                        suffix=".pt",
                    ) as temp:
                        temp.write(
                            model_file.getbuffer()
                        )
                        model_path = temp.name

                if not os.path.exists(model_path):
                    st.error(
                        "Upload your trained best.pt file above "
                        "or add it to the repository."
                    )

                else:
                    with st.spinner(
                        "Running detection..."
                    ):
                        model = YOLO(model_path)

                        prediction = model.predict(
                            image,
                            conf=0.25,
                            verbose=False,
                        )[0]

                    st.image(
                        prediction.plot()[:, :, ::-1],
                        caption="YOLO result",
                        use_container_width=True,
                    )

                    names = prediction.names
                    detections = []

                    if prediction.boxes is not None:
                        for box in prediction.boxes:
                            class_id = int(box.cls[0])

                            detections.append(
                                {
                                    "Class": names.get(
                                        class_id,
                                        str(class_id),
                                    ),
                                    "Confidence": round(
                                        float(box.conf[0]),
                                        3,
                                    ),
                                }
                            )

                    st.dataframe(
                        pd.DataFrame(detections),
                        use_container_width=True,
                    )

                    st.session_state.incidents.append(
                        {
                            "Time": datetime.now().isoformat(
                                timespec="seconds"
                            ),
                            "Forest": st.session_state.forest,
                            "Image": image_file.name,
                            "Detections": len(detections),
                        }
                    )

            except Exception as error:
                st.error(
                    f"Detection failed: {error}"
                )

    else:
        st.info(
            "No live imagery feed is connected yet. Upload an image "
            "to test the model; a live feed requires an imagery provider."
        )


# --------------------------------------------------
# 14. MODULE 5: GIS & INCIDENT DASHBOARD
# --------------------------------------------------

elif page == pages[4]:

    st.subheader("GIS & Incident Dashboard")

    if not st.session_state.grid.empty:
        df = st.session_state.grid

        assessed = df[df["Risk Score"].notna()]

        a, b, c = st.columns(3)

        a.metric(
            "Grid cells",
            len(df),
        )

        b.metric(
            "Assessed cells",
            len(assessed),
        )

        c.metric(
            "High / very high",
            int(
                assessed["Risk Level"].isin(
                    ["High", "Very High"]
                ).sum()
            ),
        )

        st_folium(
            build_map(with_grid=True),
            height=480,
            use_container_width=True,
            key="gis_map",
        )

    else:
        st.info(
            "Generate a forest grid to see GIS results."
        )

    st.subheader("Detection history")

    if st.session_state.incidents:
        history = pd.DataFrame(
            st.session_state.incidents
        )

        st.dataframe(
            history,
            use_container_width=True,
        )

        st.download_button(
            "Download history",
            history.to_csv(index=False),
            "firegridnet_history.csv",
            "text/csv",
        )

    else:
        st.info(
            "No detections recorded in this session."
        )


# --------------------------------------------------
# 15. FOOTER
# --------------------------------------------------

st.divider()

st.caption(
    "FireGridNet research prototype. Confirm geographic boundaries "
    "and data quality before operational use."
)
