#!/usr/bin/env python3
"""
Health Check Server for OurCampus Apartment Monitor

This script runs a simple HTTP server that provides health check endpoints:
- /health - Returns 200 OK if the monitor is running
- /metrics - Returns basic metrics about the monitor
- /status - Returns detailed status information

Run this alongside watch_units.py script.
"""

import http.server
import socketserver
import json
import os
import sqlite3
import psutil
import time
from datetime import datetime
import threading

# Configuration
PORT = int(os.getenv("HEALTH_CHECK_PORT", 8080))
MONITOR_PROCESS_NAME = "watch_units.py"
DB_DIR = os.getenv("DB_DIR", "data")
DB_FILE = os.getenv("DB_FILE", "apartment_history.db")
DATABASE_PATH = os.path.join(DB_DIR, DB_FILE)

# Global metrics
last_metrics_update = 0
metrics_cache = {}
metrics_lock = threading.Lock()

def get_monitor_status():
    """Check if the monitor process is running."""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if MONITOR_PROCESS_NAME in ' '.join(proc.info['cmdline'] or []):
                process = psutil.Process(proc.info['pid'])
                return {
                    "running": True,
                    "pid": proc.info['pid'],
                    "cpu_percent": process.cpu_percent(),
                    "memory_percent": process.memory_percent(),
                    "create_time": datetime.fromtimestamp(process.create_time()).isoformat(),
                    "uptime_seconds": time.time() - process.create_time()
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    
    return {"running": False}

def get_database_metrics():
    """Get metrics from the monitor database."""
    if not os.path.exists(DATABASE_PATH):
        return {"database_exists": False}
    
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        
        # Get total checks
        c.execute("SELECT COUNT(*) FROM availability_history")
        total_checks = c.fetchone()[0]
        
        # Get most recent check
        c.execute("SELECT timestamp FROM availability_history ORDER BY timestamp DESC LIMIT 1")
        last_check = c.fetchone()
        last_check_time = last_check[0] if last_check else None
        
        # Get availability counts
        c.execute("SELECT COUNT(*) FROM availability_history WHERE available = 1")
        total_available = c.fetchone()[0]
        
        # Get today's stats
        today = datetime.now().strftime('%Y-%m-%d')
        c.execute("SELECT * FROM stats WHERE date = ?", (today,))
        today_stats = c.fetchone()
        
        conn.close()
        
        return {
            "database_exists": True,
            "total_checks": total_checks,
            "last_check_time": last_check_time,
            "total_available": total_available,
            "today_stats": {
                "checks": today_stats[1] if today_stats else 0,
                "availabilities": today_stats[2] if today_stats else 0,
                "errors": today_stats[3] if today_stats else 0
            }
        }
    except Exception as e:
        return {
            "database_exists": True,
            "error": str(e)
        }

def update_metrics():
    """Update cached metrics."""
    global last_metrics_update, metrics_cache
    
    current_time = time.time()
    # Only update metrics every 10 seconds to reduce load
    if current_time - last_metrics_update < 10:
        return metrics_cache
    
    with metrics_lock:
        # System metrics
        system_metrics = {
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": psutil.virtual_memory().percent,
            "timestamp": datetime.now().isoformat()
        }
        
        # Monitor process metrics
        monitor_status = get_monitor_status()
        
        # Database metrics
        db_metrics = get_database_metrics()
        
        # Combine all metrics
        metrics_cache = {
            "system": system_metrics,
            "monitor": monitor_status,
            "database": db_metrics
        }
        
        last_metrics_update = current_time
        
    return metrics_cache

class HealthCheckHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            # Simple health check endpoint
            monitor_status = get_monitor_status()
            if monitor_status["running"]:
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(503)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b"Monitor Not Running")
        
        elif self.path == '/metrics':
            # Metrics endpoint
            metrics = update_metrics()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(metrics).encode())
        
        elif self.path == '/status':
            # Detailed status page
            metrics = update_metrics()
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>OurCampus Monitor Status</title>
                <meta http-equiv="refresh" content="30">
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; }}
                    .card {{ background: #f8f9fa; border-radius: 5px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
                    .status-ok {{ color: green; }}
                    .status-error {{ color: red; }}
                    h1, h2 {{ color: #333; }}
                    pre {{ background: #eee; padding: 10px; border-radius: 3px; overflow-x: auto; }}
                </style>
            </head>
            <body>
                <h1>OurCampus Apartment Monitor Status</h1>
                <div class="card">
                    <h2>Monitor Process</h2>
                    <p class="{'status-ok' if metrics['monitor']['running'] else 'status-error'}">
                        Status: {'Running' if metrics['monitor']['running'] else 'Not Running'}
                    </p>
                    {'<p>PID: ' + str(metrics['monitor']['pid']) + '</p>' if metrics['monitor']['running'] else ''}
                    {'<p>Uptime: ' + str(round(metrics['monitor']['uptime_seconds'] / 3600, 2)) + ' hours</p>' if metrics['monitor']['running'] else ''}
                    {'<p>CPU Usage: ' + str(round(metrics['monitor']['cpu_percent'], 2)) + '%</p>' if metrics['monitor']['running'] else ''}
                    {'<p>Memory Usage: ' + str(round(metrics['monitor']['memory_percent'], 2)) + '%</p>' if metrics['monitor']['running'] else ''}
                </div>
                
                <div class="card">
                    <h2>System Resources</h2>
                    <p>CPU Usage: {metrics['system']['cpu_percent']}%</p>
                    <p>Memory Usage: {metrics['system']['memory_percent']}%</p>
                    <p>Time: {metrics['system']['timestamp']}</p>
                </div>
                
                <div class="card">
                    <h2>Database Status</h2>
                    <p class="{'status-ok' if metrics['database']['database_exists'] else 'status-error'}">
                        Database: {'Found' if metrics['database']['database_exists'] else 'Not Found'}
                    </p>
                    {'<p>Total Checks: ' + str(metrics['database']['total_checks']) + '</p>' if metrics['database']['database_exists'] and 'total_checks' in metrics['database'] else ''}
                    {'<p>Total Availabilities Found: ' + str(metrics['database']['total_available']) + '</p>' if metrics['database']['database_exists'] and 'total_available' in metrics['database'] else ''}
                    {'<p>Last Check: ' + str(metrics['database']['last_check_time']) + '</p>' if metrics['database']['database_exists'] and metrics['database'].get('last_check_time') else ''}
                </div>
                
                <div class="card">
                    <h2>Today\'s Stats</h2>
                    {'<p>Checks Today: ' + str(metrics['database']['today_stats']['checks']) + '</p>' if metrics['database']['database_exists'] and 'today_stats' in metrics['database'] else '<p>No stats available for today</p>'}
                    {'<p>Availabilities Today: ' + str(metrics['database']['today_stats']['availabilities']) + '</p>' if metrics['database']['database_exists'] and 'today_stats' in metrics['database'] else ''}
                    {'<p>Errors Today: ' + str(metrics['database']['today_stats']['errors']) + '</p>' if metrics['database']['database_exists'] and 'today_stats' in metrics['database'] else ''}
                </div>
            </body>
            </html>
            """
            self.wfile.write(html.encode())
        
        else:
            # Default handler for other routes
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Not Found")

def run_server():
    handler = HealthCheckHandler
    with socketserver.TCPServer(("", PORT), handler) as httpd:
        print(f"Health check server started at port {PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()