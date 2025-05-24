import streamlit as st
import ee
import datetime
import logging
import csv
import json
from io import StringIO
import plotly.graph_objects as go
import folium
from streamlit_folium import st_folium

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize Earth Engine using service account credentials from st.secrets
try:
    # Get Earth Engine credentials from secrets.toml
    credentials_dict = st.secrets["earthengine"]
    
    # Initialize EE with ServiceAccountCredentials directly from secrets
    service_account = credentials_dict["client_email"]
    private_key = credentials_dict["private_key"]
    credentials = ee.ServiceAccountCredentials(service_account, key_data=private_key)
    ee.Initialize(credentials)
    logger.info("Earth Engine initialized successfully.")
except Exception as e:
    st.error(f"Earth Engine initialization failed: {e}")
    logger.error(f"Earth Engine initialization failed: {e}")

# Streamlit app layout
st.title("NEX-GDDP-CMIP6 Future Climate Projections Explorer")
st.write("Click a location on the map, select a future date range, model, scenario, and click 'Fetch Climate Data'.")

# Create two columns for map and controls
col1, col2 = st.columns([3, 1])

# Map for selecting location in first column
with col1:
    m = folium.Map(location=[0, 0], zoom_start=2)
    m.add_child(folium.ClickForMarker(popup="Selected Location"))
    map_data = st_folium(m, width=500, height=400)

# Date inputs, model, and scenario in second column
with col2:
    st.subheader("Future Date Range")
    min_date = datetime.date(2025, 1, 1)
    max_date = datetime.date(2100, 12, 31)
    start_date = st.date_input("Start Date", value=datetime.date(2025, 1, 1), min_value=min_date, max_value=max_date)
    end_date = st.date_input("End Date", value=datetime.date(2025, 12, 31), min_value=min_date, max_value=max_date)
    
    # Model and scenario selection
    models = ['ACCESS-CM2', 'CanESM5', 'GFDL-CM4', 'GISS-E2-1-G']  # Example models
    scenarios = ['ssp245', 'ssp585']  # Future scenarios only
    selected_model = st.selectbox("Select Model", models, index=0)
    selected_scenario = st.selectbox("Select Scenario", scenarios, index=0)
    
    # Display selected coordinates
    lat = None
    lon = None
    if map_data.get("last_clicked"):
        lat = map_data["last_clicked"]["lat"]
        lon = map_data["last_clicked"]["lng"]
        st.write(f"Selected: Lat {lat:.4f}, Lon {lon:.4f}")

# Button to fetch data
if st.button("Fetch Climate Data"):
    if lat is None or lon is None:
        st.error("Please click a location on the map.")
    elif start_date >= end_date:
        st.error("Start date must be before end date.")
    elif start_date < datetime.date(2025, 1, 1):
        st.error("Start date must be on or after 2025 for future projections.")
    elif end_date > datetime.date(2100, 12, 31):
        st.error("End date cannot be after 2100.")
    else:
        try:
            delta_days = (end_date - start_date).days
            point = ee.Geometry.Point([lon, lat])
            dataset = ee.ImageCollection('NASA/GDDP-CMIP6') \
                .filterDate(str(start_date), str(end_date)) \
                .filter(ee.Filter.eq('model', selected_model)) \
                .filter(ee.Filter.eq('scenario', selected_scenario)) \
                .filterBounds(point)

            # Get list of images
            image_list = dataset.toList(delta_days + 1)
            dates = []
            precip_values = []
            tasmin_values = []
            tasmax_values = []
            csv_data = []

            for i in range(delta_days + 1):
                day = start_date + datetime.timedelta(days=i)
                try:
                    img = ee.Image(image_list.get(i))
                    data = img.reduceRegion(
                        reducer=ee.Reducer.first(),
                        geometry=point,
                        scale=25000  # NEX-GDDP resolution (~25 km)
                    ).getInfo()
                    
                    precip = data.get('pr')
                    tasmin = data.get('tasmin')
                    tasmax = data.get('tasmax')
                    
                    if all(v is not None for v in [precip, tasmin, tasmax]):
                        # Convert precipitation from kg/m^2/s to mm/day
                        precip_mm = float(precip) * 86400  # 86400 seconds in a day
                        # Convert temperatures from Kelvin to Celsius
                        tasmin_c = float(tasmin) - 273.15
                        tasmax_c = float(tasmax) - 273.15
                        
                        dates.append(str(day))
                        precip_values.append(precip_mm)
                        tasmin_values.append(tasmin_c)
                        tasmax_values.append(tasmax_c)
                        csv_data.append({
                            'date': str(day),
                            'precipitation_mm': precip_mm,
                            'min_temperature_c': tasmin_c,
                            'max_temperature_c': tasmax_c
                        })
                    else:
                        logger.debug(f"No data for {day}")
                except ee.EEException as e:
                    logger.debug(f"Error processing data for {day}: {e}")
                    continue

            if dates and precip_values and tasmin_values and tasmax_values:
                # Create three Plotly figures
                # Precipitation
                fig_precip = go.Figure()
                fig_precip.add_trace(go.Scatter(x=dates, y=precip_values, mode='lines+markers', name='Precipitation'))
                fig_precip.update_layout(
                    title="Daily Precipitation (Future Projection)",
                    xaxis_title="Date",
                    yaxis_title="Precipitation (mm/day)",
                    template="plotly_white"
                )
                
                # Minimum Temperature
                fig_tasmin = go.Figure()
                fig_tasmin.add_trace(go.Scatter(x=dates, y=tasmin_values, mode='lines+markers', name='Min Temperature'))
                fig_tasmin.update_layout(
                    title="Daily Minimum Temperature (Future Projection)",
                    xaxis_title="Date",
                    yaxis_title="Temperature (°C)",
                    template="plotly_white"
                )
                
                # Maximum Temperature
                fig_tasmax = go.Figure()
                fig_tasmax.add_trace(go.Scatter(x=dates, y=tasmax_values, mode='lines+markers', name='Max Temperature'))
                fig_tasmax.update_layout(
                    title="Daily Maximum Temperature (Future Projection)",
                    xaxis_title="Date",
                    yaxis_title="Temperature (°C)",
                    template="plotly_white"
                )

                # Display plots
                st.plotly_chart(fig_precip)
                st.plotly_chart(fig_tasmin)
                st.plotly_chart(fig_tasmax)

                # Generate CSV for download
                output = StringIO()
                writer = csv.DictWriter(output, fieldnames=['date', 'precipitation_mm', 'min_temperature_c', 'max_temperature_c'])
                writer.writeheader()
                writer.writerows(csv_data)
                csv_string = output.getvalue()
                output.close()

                st.download_button(
                    label="Download CSV",
                    data=csv_string,
                    file_name="climate_projections.csv",
                    mime="text/csv"
                )
                logger.info(f"Displayed data: {len(dates)} days")
            else:
                st.error("No data available for the selected location, date range, model, or scenario.")
        except Exception as e:
            st.error(f"Failed to fetch data: {str(e)}")
            logger.error(f"Unexpected error: {e}")

if __name__ == "__main__":
    st.write("Streamlit app running")
