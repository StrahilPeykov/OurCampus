# OurCampus Amsterdam Diemen Apartment Monitor

A tool to monitor apartment availability at OurCampus Amsterdam Diemen and send notifications when units become available.

## Features

- Automatically checks for apartment availability with smart timing
- Priority-based checking schedule (more frequent on high-activity days)
- Telegram notifications when apartments become available
- Database tracking of all availability history
- Health check server for monitoring (optional)
- Works both locally and on servers

## Requirements

- Python 3.7 or higher
- Chrome browser
- Required Python packages (see `requirements.txt`)

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/ourcampus-monitor.git
   cd ourcampus-monitor
   ```

2. Install the required packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Create your configuration:
   ```bash
   cp .env.example .env
   ```

4. Edit the `.env` file to add your Telegram bot token and chat ID.

## Running the Monitor

### Local Usage

```bash
python watch_units.py
```

To run with a visible browser (not headless):
```bash
python watch_units.py --no-headless
```

### Server Deployment

For continuous operation on a server, you can use Supervisor:

1. Install supervisor:
   ```bash
   sudo apt-get install supervisor
   ```

2. Create a supervisor configuration file:
   ```bash
   sudo nano /etc/supervisor/conf.d/ourcampus_monitor.conf
   ```

3. Add the following content (adjust paths as needed):
   ```ini
   [program:ourcampus_monitor]
   command=python3 /path/to/watch_units.py
   directory=/path/to/
   user=your_username
   autostart=true
   autorestart=true
   stderr_logfile=/path/to/logs/monitor.err.log
   stdout_logfile=/path/to/logs/monitor.out.log
   ```

4. Update supervisor and start the service:
   ```bash
   sudo supervisorctl reread
   sudo supervisorctl update
   sudo supervisorctl start ourcampus_monitor
   ```

## Health Check Server

The monitor includes an optional health check server that provides:

- `/health` - Simple HTTP endpoint that returns 200 OK if the monitor is running
- `/metrics` - JSON endpoint with detailed metrics
- `/status` - HTML dashboard with system status

To enable it:

1. Set `HEALTH_CHECK_ENABLED=true` in your `.env` file
2. Specify a port with `HEALTH_CHECK_PORT=8080` (or your preferred port)

## Telegram Commands

When the monitor is running, you can use these commands in your Telegram chat:

- `/last` - Show the last check time and status
- `/status` - Show full monitor status
- `/stats` - Show statistics about apartments found
- `/help` - Show available commands
- `/restart` - Show instructions for restarting the monitor

## Priority Schedule

The monitor uses a smart schedule to check more frequently during high-activity periods:

- **High Priority** (Wed 12:00-15:30): Checks every 20-40 seconds
- **Medium Priority** (Tue/Wed/Thu/Fri afternoons): Checks every 45-75 seconds
- **Normal Priority** (All other times): Checks every 1-4 minutes

## Database

The monitor stores all availability history in a SQLite database located at `data/apartment_history.db`. You can query this database directly for custom reports.

## License

This project is licensed under the MIT License - see the LICENSE file for details.