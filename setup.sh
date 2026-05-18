#!/bin/bash

echo "=== Cyphal-CAN XC ESCs Setup (Linux/macOS) ==="

# Check if python3 is installed
if ! command -v python3 &> /dev/null
then
    echo "Error: python3 could not be found. Please install Python 3."
    exit 1
fi

echo "Creating virtual environment 'venv'..."
python3 -m venv venv

echo "Activating virtual environment..."
source venv/bin/activate

echo "Upgrading pip..."
pip install --upgrade pip

echo "Installing requirements..."
pip install -r requirements.txt

echo ""
echo "=== Setup Complete! ==="
echo "To begin using the CLI, run the following command to activate your virtual environment:"
echo "    source venv/bin/activate"
echo ""
echo "Then you can start the application:"
echo "    python cyphal-cli.py"
