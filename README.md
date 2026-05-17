# DREAM_Machine Environmental Control, Monitoring and Logging System

This repository contains the control, monitoring, plotting and logging scripts for the **DREAM_Machine** environmental chamber system.

The DREAM system is designed for controlled-environment plant physiology experiments, with emphasis on whole-canopy gas exchange, environmental monitoring, CO2 regulation, LED control, fan/mixing control, and long-term data logging.

---

## 1. Project Overview

The DREAM_Machine system integrates multiple hardware and software components to support dynamic environmental control and measurement inside a plant growth/gas-exchange chamber.

Main functions include:

- Environmental sensor monitoring
- CO2, temperature, relative humidity and VPD logging
- Air velocity and PAR monitoring
- Fan speed and RPM monitoring
- LED channel control
- CO2 feedback control using MFCs
- Chamber gas-exchange calculation
- Real-time plotting and CSV data storage
- Wi-Fi-based microcontroller communication
- PC-side data collection and dashboard support

The system is intended for experiments involving dynamic plant responses to light, CO2, humidity, airflow and canopy microclimate.

---

## 2. Main Repository Structure

```text
DREAM_Env_Control/
│
├── DREAM_sensor_helper_PC.py
├── DREAM_Sensors_helper.py
├── DREAM_Env_logger_plot.py
│
├── DREAM_LED_control/
│   ├── DREAM_LED_PC.py
│   ├── DREAM_LED_RP2040.py
│   ├── run_dream_led.py
│   └── main.py
│
├── DREAM_CO2_control/
│   ├── CO2_feedback_control.py
│   ├── MFC_control.py
│   └── plot_CO2_feedback.py
│
├── DREAM_fan_control/
│   ├── DREAM_BotFan_code.py
│   └── DREAM_BotFan_helper.py
│
├── DREAM_env_logs/
│   └── DREAM_env_log_YYYY-MM-DD.csv
│
├── plots/
│   └── generated figures
│
├── requirements.txt
└── README.md
```

The exact file names may differ depending on the latest version of the control scripts. Update this section when new modules are added or renamed.

---

## 3. Main Components

### 3.1 Environmental Sensor Nodes

The environmental sensor nodes are based on microcontrollers such as:

- Adafruit QT Py ESP32-S3
- Raspberry Pi Pico / RP2040 boards, where applicable

Typical sensors include:

- SCD41 for CO2, temperature and relative humidity
- BME688 / BME680 for temperature, humidity, pressure and gas resistance
- BMP388 / BMP390 for pressure
- FS3000 air velocity sensor
- RS485 PAR sensor
- Optional fan, DAC or control-board status sensors

Typical logged variables include:

```text
timestamp
device
epoch
elapsed_s
temp_c
rh_percent
co2_ppm
pressure_hpa
vpd_kpa
air_velocity_ms
par_raw
par_umol_m2_s
fan_speed_percent
rpm
dac_voltage
```

Missing or unavailable values are recorded as `NaN`.

---

### 3.2 PC-Side Logger

The PC-side logger receives sensor data from Wi-Fi microcontrollers and stores the data into daily CSV files.

Typical output path:

```text
DREAM_env_logs/DREAM_env_log_YYYY-MM-DD.csv
```

The logger is responsible for:

- Receiving HTTP data from multiple DREAM sensor nodes
- Formatting data into a consistent table
- Saving data to CSV
- Printing live status reports
- Supporting real-time plotting
- Handling missing sensors gracefully

---

### 3.3 LED Control

The LED control system regulates multiple LED channels through connected control boards.

Typical functions include:

- Detecting connected LED boards
- Controlling individual LED channels
- Setting all channels under the same I2C address to the same value
- Applying override commands
- Supporting manual and scripted light regimes
- Generating reproducible light treatments for experiments

Example use cases:

- Low-light and high-light treatments
- Dynamic irradiance transitions
- Canopy light-gradient experiments
- Mixing × irradiance interaction experiments

---

### 3.4 Fan and Mixing Control

Fan control is used to regulate internal air circulation and canopy mixing.

Typical controlled or logged variables include:

```text
fan_speed_percent
rpm
dac_voltage
co2_ppm
temp_c
rh_percent
vpd_kpa
```

The fan system supports:

- Manual fan-speed control
- RPM-based feedback monitoring
- DAC voltage output
- Web-based status reporting
- Synchronised logging with other environmental sensors

This is used to test how airflow and mixing affect chamber CO2 gradients, humidity gradients and canopy gas-exchange measurements.

---

### 3.5 CO2 and MFC Feedback Control

The CO2 feedback control system uses gas cylinders, MFCs and a CO2 analyser to regulate chamber CO2 concentration.

Typical components include:

- LI-COR LI-850 CO2/H2O analyser
- Bronkhorst MFC
- CO2 cylinder
- Optional compressed air and N2 cylinders
- PC-based feedback controller

Main control variables include:

```text
li850_co2_ppm
co2_avg_ppm
target_co2_ppm
mfc_setpoint_mln_min
mfc_actual_mln_min
dCdt_regression_ppm_s
A_regression_umol_s
A_regression_smoothed_umol_s
NE_regression_umol_s
A_from_avg_supply_umol_s
A_smoothed_umol_s
A_MFC_only_umol_s
A_MFC_only_smoothed_umol_s
```

The control script supports:

- Target CO2 regulation
- Deadband control
- MFC rate limiting
- Minimum MFC on/off time
- Manual MFC override
- CO2 supply averaging
- Regression-based chamber CO2 slope calculation
- Canopy gas-exchange estimation

---

## 4. Python Environment Setup

Create and activate a virtual environment:

```powershell
cd C:\Users\JII_DREAM_Machine\Documents\DREAM_Env_Control

python -m venv venv

.\venv\Scripts\activate
```

Install required packages:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If `requirements.txt` is not yet available, install the common packages manually:

```powershell
python -m pip install pandas numpy matplotlib requests pyserial flask
```

Optional packages may be required depending on the active control modules.

---

## 5. Git Setup

Check Git installation:

```powershell
git --version
```

Check repository status:

```powershell
git status
```

Add and commit changes:

```powershell
git add .
git commit -m "Update DREAM_Machine control scripts"
```

Push to GitHub:

```powershell
git branch -M main
git push -u origin main
```

Remote repository:

```text
https://github.com/HaoZhouJII/DREAM_Machine.git
```

---

## 6. Running the Environmental Logger

Activate the Python environment:

```powershell
.\venv\Scripts\activate
```

Run the PC logger:

```powershell
python DREAM_sensor_helper_PC.py
```

The logger should show live reports such as:

```text
PC logger target: http://192.168.x.x:8080/data
CSV file: DREAM_env_logs/DREAM_env_log_YYYY-MM-DD.csv
Total records in memory: xxx

DREAM_Sensors_1: time=YYYY-MM-DD HH:MM:SS, Temp=xx.xx °C, RH=xx.xx %, CO2=xxx ppm, P=xxx hPa, VPD=x.xxx kPa, Air=x.xxx m/s, PAR=xxx
DREAM_Sensors_2: ...
DREAM_Sensors_3: ...
DREAM_Sensors_4: ...
DREAM_BotFan_1: ...
```

---

## 7. Running the Plotting Script

To generate or update plots from logged environmental data:

```powershell
python DREAM_Env_logger_plot.py
```

Typical plots include:

- CO2 concentration
- Temperature
- Relative humidity
- VPD
- Air velocity
- PAR
- Fan speed
- RPM
- DAC voltage

The x-axis is usually formatted as time of day, for example:

```text
HH:MM
```

---

## 8. Running LED Control

Go to the LED control folder:

```powershell
cd DREAM_LED_control
```

Run the LED control panel or PC-side controller:

```powershell
python DREAM_LED_PC.py
```

or:

```powershell
python run_dream_led.py
```

The LED controller should detect connected LED boards and allow individual or grouped channel regulation.

---

## 9. Running CO2 Feedback Control

Go to the CO2 control folder:

```powershell
cd DREAM_CO2_control
```

Run the CO2 feedback controller:

```powershell
python CO2_feedback_control.py
```

Before running experiments, verify:

- LI-850 communication
- MFC communication
- Correct COM ports
- Correct CO2 cylinder concentration
- Correct chamber volume
- Correct target CO2
- Correct deadband and safety limits
- Correct CSV logging path

---

## 10. Recommended Data Logging Format

All logged data should use clear column names, SI-compatible units and consistent precision.

Recommended precision:

| Variable | Recommended precision |
|---|---:|
| Temperature | 0.00 °C |
| Relative humidity | 0.00 % |
| VPD | 0.000 kPa |
| Air velocity | 0.000 m s⁻¹ |
| CO2 | 0 or 0.0 ppm |
| Pressure | 0.00 hPa |
| PAR | 0.0 µmol m⁻² s⁻¹ |
| Fan speed | 0.0 % |
| RPM | integer |
| DAC voltage | 0.00 V |

Unavailable values should be written as:

```text
NaN
```

---

## 11. Experimental Applications

The DREAM_Machine platform can be used for experiments such as:

- Whole-canopy CO2 exchange
- Canopy photosynthesis under dynamic irradiance
- Airflow and mixing effects on gas-exchange measurements
- Vertical CO2 and humidity gradients across canopy layers
- Light × airflow interaction experiments
- Daytime photosynthesis and nighttime respiration
- Chamber leak-rate and background CO2 drift tests
- Dynamic environmental perturbation experiments
- Validation of chamber control stability
- Method development for controlled-environment plant physiology

---

## 12. Safety Notes

This system may involve electrical power supplies, gas cylinders, compressed gases, MFCs, fans, LEDs, solenoids and environmental control hardware.

Before operation:

- Secure all compressed gas cylinders.
- Use appropriate regulators for each gas cylinder.
- Check MFC pressure and flow limits.
- Avoid over-pressurising the chamber.
- Use fuses or circuit breakers where appropriate.
- Use emergency cut-off switches for high-power devices.
- Keep mains-voltage wiring enclosed and labelled.
- Keep low-voltage control wiring separated from mains wiring.
- Confirm shared grounds where required by control electronics.
- Avoid condensation dripping onto electrical components.
- Test all devices at low power before full operation.

---

## 13. Troubleshooting

### Git is not recognised in VS Code

Check:

```powershell
git --version
where.exe git
```

If Git is installed but not recognised, add this to Windows PATH:

```text
C:\Program Files\Git\cmd
```

Then restart VS Code.

---

### Sensor data are not appearing

Check:

- Sensor power
- Wi-Fi connection
- Microcontroller IP address
- PC logger IP address
- Firewall settings
- Correct HTTP endpoint
- Serial monitor output
- I2C scan result
- Sensor address conflicts

---

### Time is incorrect on microcontrollers

Check:

- PC time-sync server
- NTP connection
- Time-zone correction
- Daylight-saving offset
- Whether the microcontroller is using UTC or local time

---

### Plotting does not update

Check:

- Correct CSV file path
- Correct column names
- Date/time parsing
- Whether new data are being appended
- Whether missing values are recorded as `NaN`
- Whether the plotting time window is too narrow

---

### MFC does not respond

Check:

- COM port
- FlowBus or RS232 connection
- MFC power supply
- Gas supply pressure
- Correct MFC full-scale value
- Correct gas conversion settings
- Whether manual override is active
- Whether safety cutoff has forced the setpoint to zero

---

## 14. Development Notes

Recommended development workflow:

```powershell
git status
git add .
git commit -m "Describe the change"
git push
```

Before committing:

- Remove temporary test files
- Avoid committing large raw datasets unless necessary
- Avoid committing passwords, tokens or private IP credentials
- Check that file paths are not hard-coded unnecessarily
- Keep scripts modular and clearly named
- Update this README when hardware or file structure changes

---

## 15. Suggested `.gitignore`

A suitable `.gitignore` should include:

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
*.pyd
.venv/
venv/

# VS Code
.vscode/

# Logs and generated data
DREAM_env_logs/
*.log
plots/
figures/

# Temporary files
*.tmp
*.bak
*.old

# OS files
.DS_Store
Thumbs.db

# Secrets
*.env
secrets.json
config_private.json
```

If some example logs or plots are useful for documentation, place them in a separate folder such as:

```text
examples/
```

and commit only small, representative files.

---

## 16. Repository Purpose

This repository is maintained as the software profile for the DREAM_Machine environmental control platform. It is intended to support reproducible chamber operation, experimental logging, control-code development and future method publication.

The long-term goal is to provide a robust, transparent and extensible software framework for dynamic whole-canopy environmental control and gas-exchange experiments.
