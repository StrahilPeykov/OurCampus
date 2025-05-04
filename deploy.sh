#!/bin/bash
# OurCampus Apartment Monitor Deployment Script
# This script sets up and deploys the OurCampus Apartment Monitor on a Linux server

# Exit on any error
set -e

# Print header
echo "================================================================"
echo "       OurCampus Amsterdam Diemen Apartment Monitor Setup       "
echo "================================================================"
echo

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root or with sudo"
  exit 1
fi

# Ask for confirmation
echo "This script will:"
echo "  1. Install required system dependencies"
echo "  2. Install Python packages"
echo "  3. Setup Supervisor for running the monitor"
echo "  4. Configure the monitor to run on startup"
echo
read -p "Continue? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Installation canceled."
  exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
apt-get update
apt-get install -y python3 python3-pip chromium-browser chromium-chromedriver supervisor

# Create application directory
APP_DIR="/opt/ourcampus-monitor"
mkdir -p $APP_DIR
mkdir -p $APP_DIR/logs

# Copy files
echo "Copying application files..."
cp apartment_monitor_server.py $APP_DIR/
cp health_check.py $APP_DIR/
cp requirements.txt $APP_DIR/
cp .env $APP_DIR/

# Adjust file permissions
chmod 755 $APP_DIR/*.py
chmod 600 $APP_DIR/.env

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install -r $APP_DIR/requirements.txt

# Setup supervisor configuration
echo "Setting up Supervisor..."
cat > /etc/supervisor/conf.d/ourcampus_monitor.conf << EOF
[program:ourcampus_monitor]
command=python3 /opt/ourcampus-monitor/apartment_monitor_server.py
directory=/opt/ourcampus-monitor
user=root
autostart=true
autorestart=true
startsecs=10
startretries=3
stopwaitsecs=60
stderr_logfile=/opt/ourcampus-monitor/logs/monitor.err.log
stdout_logfile=/opt/ourcampus-monitor/logs/monitor.out.log
redirect_stderr=true
environment=
    PYTHONUNBUFFERED=1
EOF

# Start the service
echo "Starting the service..."
supervisorctl reread
supervisorctl update
supervisorctl start ourcampus_monitor

# Check service status
echo "Checking service status..."
sleep 3
supervisorctl status ourcampus_monitor

# Final instructions
echo
echo "================================================================"
echo "Installation complete!"
echo
echo "The OurCampus Apartment Monitor is now running."
echo "You can check logs in $APP_DIR/logs/"
echo
echo "Useful commands:"
echo "  - Check status: supervisorctl status ourcampus_monitor"
echo "  - View logs: tail -f $APP_DIR/logs/monitor.out.log"
echo "  - Stop service: supervisorctl stop ourcampus_monitor"
echo "  - Start service: supervisorctl start ourcampus_monitor"
echo "  - Restart service: supervisorctl restart ourcampus_monitor"
echo "================================================================"