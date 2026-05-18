# Cyphal-CAN XC ESCs

This project provides a Python library and an interactive Command Line Interface (CLI) for communicating with, controlling, and configuring XC Electronic Speed Controllers (ESCs) over a Cyphal/CAN bus. It leverages a UART-to-CAN adapter via the `pyka-can` module for physical hardware communication.
https://www.xc-esc.com/uav-esc/

## Features

- **Interactive Telemetry Dashboard**: A real-time TUI (Text User Interface) for monitoring ESC vitals such as voltage, current, MOS temperature, RPM, and active faults.
- **Dynamic Throttle Control**: Real-time multi-ESC and single-ESC throttle management using interactive keybindings.
- **ESC Node Management**: A built-in flashing mode for securely provisioning CAN Node IDs on individual ESCs.
- **Register Polling**: Read protocol versions, hardware details, CAN baud rates, and auto-upload configurations natively through Cyphal Service Messages (Registers).
- **Graceful Fault Handling**: Live parsing of operational statuses, ensuring faulty nodes or missing telemetry is highlighted appropriately.

## Installation / Setup

- **Python 3.x** is required.
- The module relies on **pyka-can** being available in the Python path (e.g., as `pyka-can.ws-module`).

For convenience, automated setup scripts are provided for both Linux/macOS and Windows. These scripts will create a Python virtual environment (`venv`) and install the necessary dependencies (`tabulate`, `pyserial`).

**Linux/macOS:**
```bash
./setup.sh
source venv/bin/activate
```

**Windows:**
```cmd
setup.bat
venv\Scripts\activate.bat
```

## Usage

The main entry point for the tool is `cyphal-cli.py`, which provides both operational control and diagnostic modes.

### General Options

- `-p`, `--port`: Specify the UART serial port (default: `/dev/ttyUSB0`)
- `-b`, `--baud`: Specify the serial baud rate (default: `230400`)

### Interactive Throttle Controller TUI

Run the CLI without any special mode flags to launch the interactive throttle dashboard. It will passively discover all ESCs on the bus and launch the TUI.

```bash
python cyphal-cli.py -p /dev/ttyUSB0
```

**TUI Controls:**
- `Up` / `Down`: Select a specific ESC from the list.
- `Left` / `Right`: Trim the throttle for the selected ESC (-1 / +1).
- `[Number] + Enter`: Directly type a throttle value (0-2048) and press Enter to apply it.
- `A`: Toggle control mode between ALL ESCs simultaneously or a SINGLE selected ESC.
- `Backspace`: Delete typed digits.
- `Q`: Safely zero throttles and quit the application.

### Flashing Mode (Set CAN Node ID)

To assign a new CAN Node ID to a connected ESC.
*Note: Due to safety restrictions, only exactly ONE ESC should be powered/connected to the bus when using this mode to avoid accidental mass-flashing collisions.*

```bash
# Prompted mode
python cyphal-cli.py --flash

# Direct mode (supply Hex ID directly, e.g., 0x20)
python cyphal-cli.py --flash 0x20
```

### Read ESC Registers

Query all connected ESCs and print out their internal diagnostic registers including Protocol/Hardware/Software versions, Auto-upload masks, and CAN Baud Rates.

```bash
python cyphal-cli.py --read-registers
```

## Project Structure

- `cyphal.py`: The core Cyphal protocol library. Responsible for decoding raw CAN messages into ESC status dictionaries, encoding 11-bit overlapping throttle payloads, and running the 10Hz heartbeat daemon.
- `cyphal-cli.py`: The frontend application containing the argparse setup, the TUI rendering engine, and the specific functional scripts (flashing, register reading).
- `XC-CyphalCAN Communication Protocol-V1.0.1-231206.pdf`: Official documentation detailing the bespoke data structures and subject IDs.
