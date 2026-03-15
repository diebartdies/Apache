#!/usr/bin/env python3

import datetime
import os

print("Content-Type: text/plain")
print()
print("Hello from Python CGI running inside httpd container!")
print(f"UTC time: {datetime.datetime.utcnow().isoformat()}Z")
print(f"Python: {os.popen('python3 --version').read().strip()}")
