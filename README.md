# audit-lnd
Tool that audits your LND logs for specific events and presents the result in an easy to read table.

## Features
Currently implemented log events:
Log event | Description
-|-
Bandwidth failures | Routing failures because local balance was insufficient
Remote failures | Any failure because of the remote node (needs LND to configure debug logging on subsystem HSWC)
Watchtower peers | Returns the peers that have connected to the watchtower server
Watchtower client failures | Client connection errors

## Installation
Python 3 must be installed on your system. You might want to create a [virtual env](https://docs.python.org/3/library/venv.html) before continuing with the installation. Install dependencies by running:

    pip install -r requirements.txt

## Usage
Run `./audit-lnd.py --help` to see all possible commands and arguments that can be used.

### Example
To get all bandwidth failures for the last 5 days:

    ./audit-lnd.py --days 5 bandwidth-failures

## License
MIT
