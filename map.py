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
import pandas as pd
import numpy as np

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize Earth Engine using service account credentials from st.secrets
try:
    # Get Earth Engine credentials dict from secrets.toml
    credentials_dict = st.secrets["earthengine"]
    logger.debug(f"Raw credentials: {credentials_dict}")
    
    # Handle case where credentials_dict is a string or nested
    if isinstance(credentials_dict, str):
        credentials_dict = json.loads(credentials_dict)
    elif isinstance(credentials_dict, dict) and "credentials" in credentials_dict:
        credentials_dict = credentials_dict["credentials"]
        if isinstance(credentials_dict, str):
            credentials_dict = json.loads(credentials_dict)
    
    # Ensure required fields exist
    required_fields = ["client_email", "private_key"]
    for field in required_fields:
        if field not in credentials_dict:
            raise KeyError(f"{field} not found in credentials. Check secrets.toml.")
    
    # Convert credentials_dict to JSON string for key_data
    credentials_json = json.dumps(credentials_dict)
    logger.debug(f"Credentials JSON: {credentials_json}")
    
    # Initialize EE with ServiceAccountCredentials
    service_account = credentials_dict["client_email"]
    credentials = ee.ServiceAccountCredentials(service_account, key_data=credentials_json)
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
            point = ee.Geometry.Point([lon, lat])
            dataset = ee.ImageCollection('NASA/GDDP-CMIP6') \
                .filterDate(str(start_date), str(end_date)) \
                .filter(ee.Filter.eq('model', selected_model)) \
                .filter(ee.Filter.eq('scenario', selected_scenario)) \
                .filterBounds(point)

            # Define a function to aggregate daily data to monthly
            def aggregate_monthly(image):
                date = ee.Date(image.get('system:time_start'))
                year_month = date.format('YYYY-MM')
                precip_sum = image.select('pr').multiply(86400)  # Convert kg/m^2/s to mm/day and sum
                tasmin_mean = image.select('tasmin').subtract(273.15)  # Convert to Celsius
                tasmax_mean = image.select('tasmax').subtract(273.15)  # Convert to Celsius
                return ee.Image(image).set({
                    'year_month': year_month,
                    'precip_sum': precip_sum,
                    'tasmin_mean': tasmin_mean,
                    'tasmax_mean': tasmax_mean
                })

            # Aggregate to monthly data
            monthly_collection = dataset.map(aggregate_monthly).aggregate_array('year_month').distinct()
            monthly_data_list = []
            
            for year_month in monthly_collection.getInfo():
                month_start = datetime.datetime.strptime(year_month, '%Y-%m')
                month_end = (month_start + datetime.timedelta(days=31)).replace(day=1) - datetime.timedelta(days=1)
                
                monthly_dataset = dataset.filterDate(
                    ee.Date(year_month + '-01'),
                    ee.Date(month_end.strftime('%Y-%m-%d')).advance(1, 'day')
                )
                
                # Aggregate for the month
                monthly_stats = monthly_dataset.reduce(ee.Reducer.sum().combine(
                    reducer2=ee.Reducer.mean(),
                    sharedInputs=True
                ))
                
                data = monthly_stats.reduceRegion(
                    reducer=ee.Reducer.first(),
                    geometry=point,
                    scale=25000  # NEX-GDDP resolution (~25 km)
                ).getInfo()
                
                precip_sum = data.get('pr_sum')
                tasmin_mean = data.get('tasmin_mean')
                tasmax_mean = data.get('tasmax_mean')
                
                if all(v is not None for v in [precip_sum, tasmin_mean, tasmax_mean]):
                    precip_mm = float(precip_sum) * 86400  # Convert to mm/month
                    tasmin_c = float(tasmin_mean) - 273.15
                    tasmax_c = float(tasmax_mean) - 273.15
                    
                    monthly_data_list.append({
                        'date': year_month,
                        'precipitation_mm': precip_mm,
                        'min_temperature_c': tasmin_c,
                        'max_temperature_c': tasmax_c
                    })

            if monthly_data_list:
                # Convert to DataFrame
                df = pd.DataFrame(monthly_data_list)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)

                # Calculate flood and drought risks
                df['flood_risk'] = df['precipitation_mm'].apply(
                    lambda x: 'High' if x > 100 else 'Moderate' if x > 50 else 'Low'
                )
                df['drought_risk'] = df.apply(
                    lambda row: 'High' if (row['precipitation_mm'] < 30 and row['max_temperature_c'] > 30) 
                    else 'Moderate' if (row['precipitation_mm'] < 50 and row['max_temperature_c'] > 25) 
                    else 'Low', axis=1
                )

                # Create Plotly figures for monthly data
                fig_precip = go.Figure()
                fig_precip.add_trace(go.Bar(
                    x=df.index.strftime('%Y-%m'),
                    y=df['precipitation_mm'],
                    name='Monthly Precipitation'
                ))
                fig_precip.update_layout(
                    title="Monthly Precipitation (Future Projection)",
                    xaxis_title="Month",
                    yaxis_title="Precipitation (mm/month)",
                    template="plotly_white"
                )

                fig_tasmin = go.Figure()
                fig_tasmin.add_trace(go.Scatter(
                    x=df.index.strftime('%Y-%m'),
                    y=df['min_temperature_c'],
                    mode='lines+markers',
                    name='Min Temperature'
                ))
                fig_tasmin.update_layout(
                    title="Monthly Minimum Temperature (Future Projection)",
                    xaxis_title="Month",
                    yaxis_title="Temperature (°C)",
                    template="plotly_white"
                )

                fig_tasmax = go.Figure()
                fig_tasmax.add_trace(go.Scatter(
                    x=df.index.strftime('%Y-%m'),
                    y=df['max_temperature_c'],
                    mode='lines+markers',
                    name='Max Temperature'
                ))
                fig_tasmax.update_layout(
                    title="Monthly Maximum Temperature (Future Projection)",
                    xaxis_title="Month",
                    yaxis_title="Temperature (°C)",
                    template="plotly_white"
                )

                # Display plots
                st.plotly_chart(fig_precip)
                st.plotly_chart(fig_tasmin)
                st.plotly_chart(fig_tasmax)

                # Display monthly data with risks
                st.subheader("Monthly Climate Data and Risk Assessment")
                st.dataframe(df)

                # Generate CSV for download
                output = StringIO()
                df.reset_index().to_csv(output, index=False)
                csv_string = output.getvalue()
                output.close()

                st.download_button(
                    label="Download Monthly CSV",
                    data=csv_string,
                    file_name="monthly_climate_projections.csv",
                    mime="text/csv"
                )
                logger.info(f"Displayed data: {len(df)} months")
            else:
                st.error("No data available for the selected location, date range, model, or scenario.")
        except Exception as e:
            st.error(f"Failed to fetch data: {str(e)}")
            logger.error(f"Unexpected error: {e}")

if __name__ == "__main__":
    st.write("Streamlit app running")
