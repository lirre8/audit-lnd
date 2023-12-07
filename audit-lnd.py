#!/usr/bin/env python3

import argparse, os, re, codecs, requests, gzip
from collections import defaultdict
from datetime import datetime, timedelta
from prettytable import PrettyTable

### CONSTANTS ###

DEFAULT_LOGDIR = '~/.lnd/logs/bitcoin/mainnet'
DEFAULT_RESTSERVER = 'localhost:8080'
DEFAULT_TLSCERT = '~/.lnd/tls.cert'
DEFAULT_MACAROON = '~/.lnd/data/chain/bitcoin/mainnet/admin.macaroon'
DESCRIPTION = 'audit lnd'
REGEX_LOG_START = r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\..+'
REGEX_BANDWIDTH_FAILURE = r'ChannelLink\((.+)\): insufficient bandwidth to route htlc: (\d+) mSAT'
REGEX_REMOTE_FAILURE = r'ChannelLink\((.+)\): Failed to send (\d+) mSAT'
REGEX_WATCHTOWER_PEERS = r'WTWR: Accepted incoming peer .+@(.+):\d+'
REGEX_WTCLIENT_FAILURES = r'WTCL: .+ unable to dial tower at any available Addresses:.+->(.+:\d+): (.+)'

### GLOBAL VARIABLES ###

settings = {}
channel_point_map = {}

### FUNCTIONS ###

def parse_args():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('cmd', help='the command to execute {bandwidth-failures, remote-failures, watchtower-peers, wtclient-failures}')
    parser.add_argument('--days', help='days back to search log (default=1)', default=1, type=int)
    parser.add_argument('--logdir', metavar='/path/to/logdir', help=f'lnd log dir path (default={DEFAULT_LOGDIR})', default=DEFAULT_LOGDIR)
    parser.add_argument('--restserver', metavar='host:port', help=f'lnd rest server (default={DEFAULT_RESTSERVER})', default=DEFAULT_RESTSERVER)
    parser.add_argument('--tlscert', metavar='/path/to/tls.cert', help=f'lnd tls cert path (default={DEFAULT_TLSCERT})', default=DEFAULT_TLSCERT)
    parser.add_argument('--macaroon', metavar='/path/to/macaroon', help=f'lnd macaroon path (default={DEFAULT_MACAROON})', default=DEFAULT_MACAROON)
    args = parser.parse_args()
    settings['now'] = datetime.now()
    settings['days'] = args.days
    settings['days_back'] = timedelta(days = args.days)
    settings['logdir'] = os.path.expanduser(args.logdir)
    settings['rest_baseurl'] = 'https://' + args.restserver
    settings['tlscert'] = os.path.expanduser(args.tlscert)
    macaroon_path = os.path.expanduser(args.macaroon)
    macaroon = codecs.encode(open(macaroon_path, 'rb').read(), 'hex')
    settings['rest_headers'] = {'Grpc-Metadata-macaroon': macaroon}
    return args.cmd

def collect_channel_data():
    for channel in get_channels():
        node_info = get_node_info(channel['remote_pubkey'])
        if 'node' in node_info:
            channel['peer_alias'] = node_info['node']['alias']
        channel_point_map[channel['channel_point']] = channel

def get_channels():
    url = settings['rest_baseurl'] + '/v1/channels'
    res = requests.get(url, headers=settings['rest_headers'], verify=settings['tlscert'])
    return res.json()['channels']

def get_node_info(pubkey):
    url = settings['rest_baseurl'] + f'/v1/graph/node/{pubkey}'
    res = requests.get(url, headers=settings['rest_headers'], verify=settings['tlscert'])
    return res.json()

def get_logs():
    logs = []
    used_all_logs = True
    with open(settings['logdir'] + '/lnd.log', 'r') as logfile:
        used_all_logs = parse_log_file(logfile, logs)
    if used_all_logs:
        used_all_logs = parse_gz_log_files(logs)
    if used_all_logs:
        last_log_time = re.match(REGEX_LOG_START, logs[len(logs)-1]).group(1)
        print(f'Warning - Logs might not go back far enough for {settings["days"]} days. Last log time found was: {last_log_time}')
        print('Suggestion: Use configs maxlogfiles and maxlogfilesize to adjust how much logs are saved')
    return logs

def parse_log_file(logfile, parsed_logs):
    for line in reversed(list(logfile)):
        if not re.match(REGEX_LOG_START, line):
            continue
        try:
            log_time = datetime.strptime(line.split('.')[0], '%Y-%m-%d %H:%M:%S')
            if settings['now'] - log_time > settings['days_back']:
                return False
            parsed_logs.append(line)
        except:
            print('Error - Failed to parse: ' + line)
    return True

def parse_gz_log_files(logs):
    gz_logfiles = []
    for file_name in os.listdir(settings['logdir']):
        match = re.match(r'lnd\.log\.(\d+)\.gz', file_name)
        if match:
            gz_count = int(match.group(1))
            gz_logfiles.append((gz_count, file_name))
    for n, gz_logfile in sorted(gz_logfiles, reverse=True):
        with gzip.open(settings['logdir'] + '/' + gz_logfile, 'r') as b_logfile:
            logfile = [line.decode('utf-8') for line in b_logfile]
            used_all_logs = parse_log_file(logfile, logs)
            if not used_all_logs:
                return False
    return True

def routing_failures(regex):
    res = parse_routing_failures(regex)
    table = PrettyTable()
    table.field_names = ['Peer alias', 'Count', 'Total sats', 'Avarage tx', 'Min tx', 'Max tx', 'Capacity', 'Channel ID']
    table.align['Peer alias'] = 'l'
    table.align['Count'] = 'r'
    table.align['Total sats'] = 'r'
    table.align['Avarage tx'] = 'r'
    table.align['Min tx'] = 'r'
    table.align['Max tx'] = 'r'
    table.align['Capacity'] = 'r'
    table.align['Channel ID'] = 'c'
    table.sortby = 'Count'
    table.reversesort = True
    for channel_point, values in res.items():
        channel_id = channel_point_map[channel_point]['chan_id']
        capacity = int(channel_point_map[channel_point]['capacity'])
        peer_alias = channel_point_map[channel_point]['peer_alias']
        count = values['count']
        total = int(values["total"]/1000)
        avgtx = int(total/count)
        mintx = int(values['min']/1000)
        maxtx = int(values['max']/1000)
        table.add_row([peer_alias, count, f'{total:,}', f'{avgtx:,}', f'{mintx:,}', f'{maxtx:,}', f'{capacity:,}', channel_id])
    print()
    print(table)
    print()

def parse_routing_failures(regex):
    res = defaultdict(lambda:{'count': 0, 'total': 0, 'min': 0, 'max': 0})
    for line in get_logs():
        match = re.search(regex, line)
        if match:
            channel_point = match.group(1)
            if not channel_point in channel_point_map:
                continue
            amount = int(match.group(2))
            res[channel_point]['count'] += 1
            res[channel_point]['total'] += amount
            if res[channel_point]['min'] == 0 or amount < res[channel_point]['min']:
                res[channel_point]['min'] = amount
            if amount > res[channel_point]['max']:
                res[channel_point]['max'] = amount
    return res

def watchtower_peers():
    res = parse_watchtower_connections()
    table = PrettyTable()
    table.field_names = ['Peer', 'Connections']
    table.align['Peer'] = 'l'
    table.align['Connections'] = 'r'
    table.sortby = 'Connections'
    table.reversesort = True
    for peer_ip, connections in res.items():
        table.add_row([peer_ip, connections])
    print()
    print(table)
    print()

def parse_watchtower_connections():
    res = defaultdict(int)
    for line in get_logs():
        match = re.search(REGEX_WATCHTOWER_PEERS, line)
        if match:
            res[match.group(1)] += 1
    return res

def wtclient_failures():
    res = parse_wtclient_failures()
    table = PrettyTable()
    table.field_names = ['Address', 'Error', 'Count']
    table.align['Address'] = 'l'
    table.align['Error'] = 'l'
    table.align['Count'] = 'r'
    table.sortby = 'Count'
    table.reversesort = True
    for address, errors in res.items():
        for error, count in errors.items():
            table.add_row([address, error, count])
    print()
    print(table)
    print()

def parse_wtclient_failures():
    res = defaultdict(lambda:defaultdict(int))
    for line in get_logs():
        match = re.search(REGEX_WTCLIENT_FAILURES, line)
        if match:
            res[match.group(1)][match.group(2)] += 1
    return res


### MAIN SCRIPT ###

cmd = parse_args()

if cmd == 'bandwidth-failures':
    collect_channel_data()
    routing_failures(REGEX_BANDWIDTH_FAILURE)
elif cmd == 'remote-failures':
    collect_channel_data()
    routing_failures(REGEX_REMOTE_FAILURE)
elif cmd == 'watchtower-peers':
    watchtower_peers()
elif cmd == 'wtclient-failures':
    wtclient_failures()
else:
    print('Invalid command: ' + cmd)
    exit(1)

