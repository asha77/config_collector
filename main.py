#!/usr/bin/env python3
import argparse
import gc
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from decouple import UndefinedValueError, config
from scrapli import Scrapli
from scrapli.driver import GenericDriver
from scrapli.exceptions import (
    ScrapliAuthenticationFailed,
    ScrapliConnectionNotOpened,
    ScrapliException,
    ScrapliTimeout,
)

# import logging
from scrapli.logging import enable_basic_logging

curr_path = None
cnf_save_path = None

AUTH_USERNAME = config('AUTH_USERNAME')
AUTH_PASSWORD = config('AUTH_PASSWORD')
AUTH_SECONDARY = config('AUTH_SECONDARY')
if config('AUTH_STRICT_KEY') == "True":
    AUTH_STRICT_KEY = True
else:
    AUTH_STRICT_KEY = False
TRANSPORT = config('TRANSPORT')
TIMEOUT_SOCKET = config('TIMEOUT_SOCKET')
TIMEOUT_TRANSPORT = config('TIMEOUT_TRANSPORT')
# timeout for one command processing 60s by default may be increased in case of log downloading
TIMEOUT_OPS = int(config('TIMEOUT_OPS', default='60'))
WORKING_DIRECTORY = config('WORKING_DIRECTORY')
BACKUP_CONFIG_FOLDER = config('BACKUP_CONFIG_FOLDER')
try:
    OUTPUT_FOLDER = config('OUTPUT_FOLDER')
except UndefinedValueError:
    print("          ... it seems that no \"OUTPUT_FOLDER\" parameter specified in .env file - using by-default values...")
    OUTPUT_FOLDER = 'output'

# количество одновременных подключений к устройствам при сборе данных
MAX_WORKERS = int(config('MAX_WORKERS', default='10'))

# switch on scrapli debug logging into a file (switched off by default)
SCRAPLI_DEBUG_LOG = config('SCRAPLI_DEBUG_LOG', default='False') == 'True'

family_to_platform = {
    'IOS': 'cisco_iosxe',
    'IOS XE': 'cisco_iosxe',
    'NX-OS': 'cisco_nxos',
    'IOS XR': 'cisco_iosxr',
    'JUNOS': 'juniper_junos',
    'EOS': 'arista_eos',
    'VRP': 'huawei_vrp',
    'ARUBA AOS-S': 'aruba_aoscx',
    'Edgecore SONIC': 'edgecore_sonic',
    'RDP EcoNPB': 'rdp_econpb'
}


# To change if any special list of commands for special platforms
platform_to_commands = {
    'cisco_iosxe': 'cisco_commands.txt',
    'cisco_nxos': 'cisco_commands.txt',
    'cisco_iosxr': 'cisco_commands.txt',
    'juniper_junos': 'juniper_commands.txt',
    'arista_eos': 'cisco_commands.txt',
    'huawei_vrp': 'huawei_commands.txt',
    'aruba_aoscx': 'hpe_aruba_commands.txt',
    'edgecore_sonic': 'edgecore_commands.txt',
    'rdp_econpb': 'rdp_commands.txt',
    'unknown_platform': 'default_commands.txt'
}

# filters description - lines according to these regulars will be not saved into file. Can be expanded.
edgecore_excluded_errors = [
    '/usr/local/lib/python3.7/dist-packages/ax_interface/mib.py',
    '/usr/local/lib/python3.7/dist-packages/sonic_ax_impl/mibs/ietf/rfc1213.py'
]

# regexp to replace string content for data obfuscation
obfuscations = {
    r"radius-server key (.*)": r"radius-server key ХХХ",
    r"snmp-server community (.*) RO": r"snmp-server community XXX RO",
    r"snmp-server community (.*) RW": r"snmp-server community XXX RW",
    r"\skey (\d) (.*)": r" key XXX YYY",
    r"username ([a-zA-Z0-9_.-]+) privilege (\d+) password (.*)": r"username XXX privilege YYY password ZZZ",
    r"username ([a-zA-Z0-9_.-]+) privilege (\d+) secret (\d+) (.*)": r"username XXX privilege YYY secret ZZZ WWW",
    r"enable secret (\d) (.*)": r"enable secret XXX YYY",
    r"radius server shared-key (.*)": r"radius server shared-key XXX",
    r"\s*local-user ([a-zA-Z0-9_.-]+)": r" local-user XXX",
    r"\s*ospf authentication (.*)": r" ospf authentication XXX",
    r"\s*(.*)\scipher(.*)": r" XXX cipher YYY",
    r"\s*pre-shared-key(.*)": r" pre-shared-key XXX",
    r"\s*ssh user\s([a-zA-Z0-9_.-]+)(.*)": r" ssh user XXX YYY"
}

# block writing into common log for correct multi threading writing into one log file
_log_lock = threading.Lock()


_LOG_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"


def send_log(path, message):
    """Запись сообщения в общий журнал и вывод в терминал (thread-safe)."""
    timestamp = datetime.now().strftime(_LOG_TIMESTAMP_FMT)
    log_line = f"{timestamp} YAUCC  INFO: {message}\n"

    with _log_lock:
        try:
            with open(os.path.join(path, 'logfile.log'), 'a', encoding='utf-8') as log_file:
                log_file.write(log_line)
        except OSError as exc:
            print(f"[send_log] Ошибка записи в logfile.log: {exc}")
            return False

    print(f"{timestamp} YAUCC INFO: {message.strip(chr(10))}")
    return True


def saveoutfile(path, ip, message):
    file_name = os.path.join(path, ip)
    with open(file_name, "a", encoding='utf-8') as out_file:
        out_file.write(message)


def rewrite_out_file(path, ip, message):
    file_name = os.path.join(path, ip)
    with open(file_name, "w", encoding='utf-8') as out_file:
        out_file.write(message)


def create_parser():
    parser = argparse.ArgumentParser(prog='YAUCC - Yet Another Universal Config Collector', description='Python app for executing commands on network equipment using SSH', epilog='author: asha77@gmail.com')
    parser.add_argument('-d', '--devfile', dest="devices", required=True, help='Path to file with list of devices')
    parser.add_argument('-c', '--comfiles', dest="commands",  required=False, help='Path to file with Commands to be executed on devices (cancels autodetection of command set according to device platform)')
    parser.add_argument('-o', '--overwrite', required=False, action='store_true', help='Save and overwrite files into the same folder e.g. "output" folder')
    parser.add_argument('-b', '--backup_configs', nargs='?', type=int, const=1, default=0, required=False, help='Save config backup files into "BACKUP_CONFIG_FOLDER" once or days to keep backups')
    parser.add_argument('-w', '--workers', type=int, default=None, required=False, help='Number of parallel connections to devices (overrides MAX_WORKERS from .env)')
    return parser


def obtain_model(vendor, configuration):
    """
    Extract model number
    """

    # cisco and arista a treated as the same - they are similar
    if vendor == 'cisco':
        match = re.search(r"Model\s+\wumber\s*:\s+(.*)", configuration)
        if match:
            return match.group(1).strip()
        else:
            match = re.search(r"\wisco\s+(\S+)\s+.*\s+(with)*\d+K/\d+K\sbytes\sof\smemory.", configuration)
            if match:
                return match.group(1).strip()
            else:
                match = re.search(r"\s+cisco Nexus9000 (.*) Chassis", configuration)
                if match:
                    return "N9K-"+match.group(1).strip()
                else:
                    match = re.search(r"ROM: Bootstrap program is Linux", configuration)
                    if match:
                        return "Cisco IOS vRouter "
                    else:
                        match = re.search(r"Arista vEOS", configuration)
                        if match:
                            return "Arista vEOS"
                        else:
                            match = re.search(r"Arista (\S+)", configuration)
                            if match:
                                return match.group(1).strip()
                            else:
                                return "Not_found"

    if vendor == 'huawei':
        match = re.search(r'(Quidway|HUAWEI)\s(\S+)\s+Routing\sSwitch\S*', configuration)
        if match:
            return 'Huawei ' +match.group(2).strip()
        else:
            match = re.search(r'HUAWEI\sCE(\S+)\s+uptime\S*', configuration)
            if match:
                return 'Huawei CE' + match.group(1).strip()
            else:
                match = re.search(r'Huawei\s(\S+)\s+Router\s\S*', configuration)
                if match:
                    return 'Huawei ' + match.group(1).strip()
                else:
                    return "Not_found"

    if vendor == 'aruba':
        match = re.search(r'Build\sID\s+: (\S-\S).*', configuration)
        if match:
            return match.group(1).strip()
        else:
            match = re.search(r'\s*Product\sSKU\s*:\s(\S*)', configuration)
            if match:
                return match.group(1).strip()
            else:
                return "Not_found"

    if vendor == 'edgecore':
        match = re.search(r'\s*HwSKU:\s(\S*)', configuration)
        if match:
            return match.group(1).strip()
        else:
            return "Not_found"

    if vendor == 'rdp':
        match = re.search(r'\s+product-name\s+(\S+)', configuration)
        if match:
            return match.group(1).strip()
        else:
            return "Not_found"

    return "Model_vendor_not_found"


def obtain_software_version(configuration, family):
    """
    Extract software version
    """

    if family == 'IOS XE' or family == 'IOS':
        match = re.search("Cisco .+ Version ([0-9.()A-Za-z]+)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'NX-OS':
        match = re.search(r"\s*NXOS: version (.*)", configuration)
        if match:
            return match.group(1).strip()
        else:
            match = re.search(r"\s*system:\s+version\s*(.*)", configuration)
            if match:
                return match.group(1).strip()
    elif family == 'EOS':
        match = re.search("Software image version: (.*)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'VRP':
        match = re.search(r"VRP \(R\) software, Version (.*)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'ARUBA AOS-S':
        match = re.search(r"\s*Software revision\s*:\s*(\S+)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'Edgecore SONIC':
        match = re.search(r"\s*SONiC Software Version:\s*(\S+)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'RDP EcoNPB':
        match = re.search(r"\s+serial-number\s(\S+)", configuration)
        if match:
            return match.group(1).strip()
    else:
        return "Not Found"
    return "Soft_not_found"


def obtain_software_family(configuration):
    """
    Extract software family from show version
    """
    if re.search("Cisco IOS.XE .oftware", configuration):
        return "IOS XE"
    elif re.search("Cisco Nexus Operating System", configuration):
        return "NX-OS"
    elif re.search("Cisco IOS Software", configuration):
        return "IOS"
    elif re.search("Arista", configuration):
        return "EOS"
    elif re.search("Huawei Versatile Routing Platform", configuration):
        return "VRP"
    elif re.search("ArubaOS", configuration):
        return "ARUBAOS"
    elif re.search(r"\s*Software revision\s*:\s*(\S+)", configuration):
        return "ARUBA AOS-S"
    elif re.search(r"\s*SONiC Software Version:\s*(\S+)", configuration):
        return "Edgecore SONIC"
    elif re.search(r"\s*FlowBalancer\s*(\S+)", configuration) or re.search(r"\s*SDNSwitch-packet-broker\s*(\S+)", configuration) or re.search(r"\s*EcoNPB\s*(\S+)", configuration):
        return "RDP EcoNPB"
    else:
        return "unknown_platform"


def obtain_hostname(configuration):
    """
    Extract device hostname
    """

    match = re.search("hostname (.*)", configuration)
    if match:
        return match.group(1).strip()
    else:
        return "Not Found"


def assign_platform(dev_family):
    """
    Assign device platform based on device family
    """

    try:
        platform = family_to_platform[dev_family]
    except KeyError:
        send_log(cnf_save_path, f"No suitable platform for device family {dev_family}")
        platform = ""
    return platform


def parse_device_line(line):
    """
    Split device line on connection parameters
    """
    device_string = line.split(";")

    if len(device_string) < 2:
        print('Error - wrong line in devices file - skipping...')
        return None

    if len(device_string) > 2:
        if "".join(device_string[2:3]) != "":
            uname = "".join(device_string[2:3])
        else:
            uname = AUTH_USERNAME
    else:
        uname = AUTH_USERNAME

    if len(device_string) > 3:
        if "".join(device_string[3:4]) != "":
            passw = "".join(device_string[3:4])
        else:
            passw = AUTH_PASSWORD
    else:
        passw = AUTH_PASSWORD

    if len(device_string) > 4:
        if "".join(device_string[4:5]) != "":
            ena_pass = "".join(device_string[4:5])
        else:
            ena_pass = AUTH_SECONDARY
    else:
        ena_pass = AUTH_SECONDARY

    return {
        'ip': "".join(device_string[1:2]),
        'auth_username': uname,
        'auth_password': passw,
        'auth_secondary': ena_pass,
        'specified_platform': "".join(device_string[0:1])
    }


def process_device_record(record, chlog):
    """
    Finding device model, software family and platform for device (in separate thread)
    """
    ip = record['ip']
    uname = record['auth_username']
    passw = record['auth_password']

    vendor, show_ver, hname = get_show_version(ip, uname, passw)

    if (show_ver == '') and (hname == ''):
        return None

    if __debug__:
        send_log(cnf_save_path, "show version:\n " + show_ver)

    device_model = obtain_model(vendor, show_ver)

    if __debug__:
        send_log(cnf_save_path, "Device model:\n " + device_model)

    device_family = obtain_software_family(show_ver)
    device_soft_ver = obtain_software_version(show_ver, device_family)

    if __debug__:
        send_log(cnf_save_path, "Device software:\n " + device_soft_ver)
        send_log(cnf_save_path, "Device family:\n " + device_family)

    if record['specified_platform']:
        device_platform = record['specified_platform']
    else:
        device_platform = assign_platform(device_family)

    if device_platform:
        send_log(cnf_save_path, "IP: " + ip + ". Device model is: " + device_model + ". Software version is: " + device_soft_ver + ". Selected platform: " + device_platform)
    else:
        send_log(cnf_save_path, "IP: " + ip + " Device and platform not recognized.")
        if show_ver:
            saveoutfile(cnf_save_path, str(ip) + '_' + hname + '.log', "\n" + show_ver)
        return None

    dev = {
        'platform': device_platform,
        'host': ip,
        'auth_username': uname,
        'auth_password': passw,
        'auth_secondary': record['auth_secondary'],
        'channel_log': chlog,
        "auth_strict_key": AUTH_STRICT_KEY,
        "ssh_config_file": True,
        "transport": TRANSPORT,
        "timeout_socket": int(TIMEOUT_SOCKET),          # timeout for establishing socket/initial connection in seconds
        "timeout_transport": int(TIMEOUT_TRANSPORT)    # timeout for ssh|telnet transport in seconds
    }

    hn = {
        "hostname": hname,
        "ip": ip
    }

    return dev, hn


def get_devices_from_file(file, max_workers):
    """
    Loading list of devices and their parameters
    """
    records = []
    with open(file) as f:
        for line in f:
            if line.startswith('#'):
                continue

            if line == ['\n'] or line == [' \n'] or line == [''] or line == [" "]:
                print('Error - empty line in devices file - skipping...')
                continue

            record = parse_device_line(line)
            if record is None:
                continue

            records.append(record)

    devices = []
    hostnames = []

    if __debug__:
        chlog = True
    else:
        chlog = False

    # Finding different device parameters executing in different threads
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_device_record, record, chlog): record for record in records}
        for future in as_completed(futures):
            result = future.result()
            if result is None:
                continue
            dev, hn = result
            devices.append(dev)
            hostnames.append(hn)

    return devices, hostnames


def get_commands_from_file(file):
    commands = []
    with open(file) as f:
        for line in f:
            if line.find('#') == -1:
                commands.append(line.strip('\n'))
    return commands


'''
def send_show(device, show_command):
    try:
        with Scrapli(**device) as ssh:
            reply = ssh.send_command(show_command)
            return reply.result
    except ScrapliException as error:
        print(error)
'''


def strip_characters_from_prompt(prompt):
    prompt = prompt.replace('#', '')
    prompt = prompt.replace('<', '')
    prompt = prompt.replace('>', '')
    prompt = prompt.replace('[', '')
    prompt = prompt.replace(']', '')
    prompt = prompt.replace(':', '')
    prompt = prompt.replace('~', '')
    prompt = prompt.replace('$', '')
    prompt = prompt.replace('\x00', '')

    if "@" in prompt:
        prompt = prompt.split('@',1)[1]

    return prompt


def get_show_version(ip, login, passw):
    my_device = {
        "host": ip,
        "auth_username": login,
        "auth_password": passw,
        "auth_strict_key": False,
        "ssh_config_file": True,
        "transport": TRANSPORT
    }

    vendor = 'cisco'
    hname = ''
    response = ''

    try:
        with GenericDriver(**my_device, comms_prompt_pattern=r"^\S{0,700}[#>$~@:\]]\s*$", timeout_ops=15) as conn:
            time.sleep(0.1)
            hname = conn.get_prompt().strip()
            time.sleep(0.1)
            response = conn.send_command("terminal length 0", strip_prompt = False)
            # if '% Invalid input detected' in response1:  Cisco error string

            # if not Cisco and we get error try Huawei
            if 'Error: Unrecog' in response.result:
                response = conn.send_command("screen-length 0 temporary", strip_prompt=False)
                vendor = 'huawei'

            if 'Invalid input:' in response.result:
                response = conn.send_command("no page", strip_prompt=False)
                vendor = 'aruba'

            if '-bash: terminal: command not found' in response.result:
#               response = conn.send_command("no page", strip_prompt=False)
                vendor = 'edgecore'

            if 'undefined command: ' in response.result:
#                response = conn.send_command("no page", strip_prompt=False)
                vendor = 'rdp'

            if __debug__:
                send_log(cnf_save_path, "IP: " + ip + " INFO " + "Response: " + response.result)

            time.sleep(0.5)

            if vendor == 'cisco':
                response = conn.send_command("show version", strip_prompt = False)
                time.sleep(0.2)
            elif vendor == 'huawei':
                response = conn.send_command("display version", strip_prompt = False)
                time.sleep(0.2)
            elif vendor == 'aruba':
                response = conn.send_command("show system", strip_prompt = False)
                time.sleep(0.2)
                response1 = conn.send_command("show system mem", strip_prompt = False)
                response.result = response.result + '\n' + response1.result
            elif vendor == 'edgecore':
                response = conn.send_command("show version", strip_prompt = False)
                time.sleep(0.2)
            elif vendor == 'rdp':
                response = conn.send_command("show rdp-firmware", strip_prompt = False)
                time.sleep(0.2)
                response1 = conn.send_command("show hardware-info platform-info", strip_prompt=False)
                response.result = response.result + '\n' + response1.result

        return vendor, response.result, strip_characters_from_prompt(hname)

    except ScrapliAuthenticationFailed as error:
        send_log(cnf_save_path, "IP: " + ip + " Authentication Error " +str(error) + " - please, check username, password and driver.")
        return '', '', ''
    except ScrapliConnectionNotOpened as error:
        send_log(cnf_save_path, "IP: " + ip + " Connection Error " +str(error) + " - please, check device exist or online.")
        return '', '', ''
    except ScrapliException as error:
        send_log(cnf_save_path, "IP: " + ip + " Scrapli Error " + str(error))
        if hasattr(response, 'result') and response.result != '' and hname != '':
            return vendor, response.result, strip_characters_from_prompt(hname)
        else:
            return '', '', ''


def obfuscation_filter(reply):
    result_text = reply
    for regex, replacement in obfuscations.items():
        result_text = re.sub(regex, replacement, result_text)
    return result_text


def thrash_string_filter(reply):
    lines = reply.split('\n')
    lines_out = []

    for line in lines:
        matched = False
        for error_regexp in edgecore_excluded_errors:
            match = re.search(error_regexp, line)
            if match:
                matched = True
                break
        if not matched:
            lines_out.append(line)

    return '\n'.join(map(str, lines_out))


def output_config_files_filter(input_line):
    """
    Filter unnecessary lines

   Building configuration...
   Current configuration:
   !
   end
    """

    lines = input_line.split('\n')
    lines_out = []

    for line in lines:
        match = re.search("Building configuration...", line)
        if not match:
            match = re.search("Current configuration:", line)
            if not match and line != '':
                match = re.search("end", line)
                if not match:
                    lines_out.append(line)

    if lines_out[0:1] == '!':
        # noinspection PyTypeChecker
        lines_out.pop(0)

    return '\n'.join(map(str, lines_out))


def get_hostname_by_ip(ip, hostnames):
    for record in hostnames:
        if record["ip"] == ip:
            return record["hostname"]


def delete_old_backups(target_directory, days_old):
    # calculating threshold data
    threshold_date = datetime.now() - timedelta(days=days_old)
    print(f"Removing files and folders older then: {threshold_date.strftime('%Y-%m-%d %H:%M:%S')}")

    target_path = Path(target_directory)

    for item in target_path.iterdir():
        # Get last modified date (mtime)
        mtime = datetime.fromtimestamp(item.stat().st_mtime)

        if mtime < threshold_date:
            try:
                if item.is_file():
                    item.unlink()
#                    print(f"File deleted: {item}")
                elif item.is_dir():
                    shutil.rmtree(item)
                    print(f"Folder deleted: {item}")
            except OSError as e:
                print(f"Error during file deletion {item}: {e}")


def _describe_scrapli_error(error, device, stage, cnf_path):
    """
    Diagnose scrapli error: writing into log exception type, cause, state of transport 
    to troubleshoot root case and point of error
    """
    exc_type = type(error).__name__
    exc_msg = str(error)

    # collecting cause chain (original -> wrapped), because context manager 
    # include original exception in new ScrapliConnectionError
    causes = []
    cur = error
    depth = 0
    while cur is not None and depth < 5:
        causes.append(f"{type(cur).__name__}: {cur!s}")
        cur = getattr(cur, '__cause__', None) or getattr(cur, '__context__', None)
        depth += 1
    cause_chain = " | ".join(causes) if causes else "(no cause)"

    send_log(cnf_path, "[DIAG] stage={} host={} platform={} transport={} timeout_socket={} timeout_transport={}".format(
        stage, device.get('host'), device.get('platform'), device.get('transport'),
        device.get('timeout_socket'), device.get('timeout_transport')))
    send_log(cnf_path, f"[DIAG] exception type={exc_type} msg={exc_msg!r}")
    send_log(cnf_path, f"[DIAG] cause chain: {cause_chain}")

    # if Error is "connection not open", check it was lost before sending command 
    # or during command execution
    if isinstance(error, ScrapliConnectionNotOpened):
        send_log(cnf_path, "[DIAG] CONNECTION NOT OPENED at stage={} — соединение с устройством {} было потеряно".format(stage, device.get('host')))

    # timeout for send/receive: device does not get input during TIMEOUT_OPS;
    # it it not a connection close, but slow CLI or long operation — you need to to increase TIMEOUT_OPS in .env
    if isinstance(error, ScrapliTimeout):
        send_log(cnf_path, "[DIAG] TIMEOUT at stage={} host={} — устройство не приняло ввод за {} сек (TIMEOUT_OPS). Увеличьте TIMEOUT_OPS в .env, если это повторяется.".format(
            stage, device.get('host'), TIMEOUT_OPS))


def collect_device_data(device, commands, overwrite, date, cnf_path, hostnames):
    """
    Collecting data from one device: command execution and  saving results
     executing in separate thread
    """
    dev_start_time = datetime.now()
    hostname = get_hostname_by_ip(device['host'], hostnames)
    out_name = device['host'] + "_" + hostname + '.log'

    try:
        with Scrapli(**device, timeout_ops=180) as ssh:

            if overwrite:
                rewrite_out_file(cnf_path, out_name, "Data collected: " + date + "\n")

            total_cmds = len(commands)
            for cmd_index, command in enumerate(commands, start=1):
                if __debug__:
                    send_log(cnf_path, f"{device['host']} send command [{cmd_index} of {total_cmds}]: {command}")

                time.sleep(0.2)
                # each command has its own try to understand where connection may be lost
                try:
                    reply = ssh.send_command(command, timeout_ops=TIMEOUT_OPS)
                except ScrapliException as cmd_err:
                    _describe_scrapli_error(cmd_err, device, f"command #{cmd_index}/{total_cmds} ({command})", cnf_path)
                    raise
                time.sleep(0.8)

                if __debug__:
                    send_log(cnf_path, reply.result[0:30].replace('\n', ' '))

                if reply.result:
                    filtered_result = thrash_string_filter(obfuscation_filter(reply.result))

                    if __debug__:
                        ln = len(filtered_result)
                        if ln > 20:
                            ln = 20
                            send_log(cnf_path, f"{device['host']} elapsed time: {reply.elapsed_time} received: {filtered_result[0:ln-1].replace(chr(10), ' ')} ...")

                    saveoutfile(cnf_path, out_name, f"\n# {command}\n{filtered_result}\n")
                else:
                    send_log(cnf_path, f"{device['host']} elapsed time: {reply.elapsed_time} send: {command} - nothing received!")

                # del reply to free memory
                del reply

            # garbage collection to free memory from driver objects and replies between commands

            gc.collect()
    except ScrapliException as error:
        _describe_scrapli_error(error, device, "outer handler", cnf_path)
        print(error)
        # free pointer on raised exception: its __cause__ and traceback
        del error
        gc.collect()
    send_log(cnf_path, "Device {} processed in {}".format(device['host'], datetime.now() - dev_start_time))


def collect_device_backup(device, hostnames, backups_path_now):
    """
    Collecting device configuration backup for one device 
    executed in separate thread
    """
    def _backup_send(ssh, command):
        """
        Sending command with diagnostics: fixing command which cause connection lost
        and state of exception
        """
        try:
            return ssh.send_command(command, timeout_ops=TIMEOUT_OPS)
        except ScrapliException as cmd_err:
            _describe_scrapli_error(cmd_err, device, f"backup command ({command})", cnf_save_path)
            raise

    try:
        with Scrapli(**device, timeout_ops=180) as ssh:
            if device['platform'] == 'edgecore_sonic':
                time.sleep(0.2)
                reply = _backup_send(ssh, 'show runningconfiguration all')
                time.sleep(0.2)

                if __debug__:
                    send_log(cnf_save_path, reply.result[0:30].replace('\n', ' '))

                rewrite_out_file(backups_path_now, get_hostname_by_ip(device['host'], hostnames) + '_config_db.json', output_config_files_filter(reply.result))
                del reply

                time.sleep(0.2)
                reply = _backup_send(ssh, 'show runningconfiguration bgp')
                time.sleep(0.2)

                if __debug__:
                    send_log(cnf_save_path, reply.result[0:30].replace('\n', ' '))

                rewrite_out_file(backups_path_now, get_hostname_by_ip(device['host'], hostnames) + '_frr.conf', output_config_files_filter(reply.result))
                del reply
            elif device['platform'] == 'huawei_vrp':
                time.sleep(0.2)
                reply = _backup_send(ssh, 'display current-configuration')
                time.sleep(0.2)
                if __debug__:
                    send_log(cnf_save_path, reply.result[0:30].replace('\n', ' '))
                rewrite_out_file(backups_path_now, get_hostname_by_ip(device['host'], hostnames) + '_config.txt', output_config_files_filter(reply.result))
                del reply
            elif device['platform'] == 'rdp_econpb':
                time.sleep(0.2)
                reply = _backup_send(ssh, 'show | view set')
                time.sleep(0.2)
                if __debug__:
                    send_log(cnf_save_path, reply.result[0:30].replace('\n', ' '))

                rewrite_out_file(backups_path_now, 'RDP_' + device['host'] + '_config.txt', output_config_files_filter(reply.result))
                del reply

            # periodical garbage collection to free memory
            gc.collect()
    except ScrapliException as error:
        _describe_scrapli_error(error, device, "backup outer handler", cnf_save_path)
        print(error)
        # free pointer on raised exception: its __cause__ and traceback
        del error
        gc.collect()


def start():
    global curr_path
    global cnf_save_path

    parser = create_parser()
    namespace = parser.parse_args()
    comm_file_path_specified = False

    # number of parallel connections: CLI argument or MAX_WORKERS parameter in .env
    max_workers = namespace.workers if namespace.workers else MAX_WORKERS
    max_workers = max(max_workers, 1)

    # recording of scrapli debug log into file switched on using SCRAPLI_DEBUG_LOG parameter in .env;
    # by default debug log is switched off, because common log processor from all threads 
    # consume memory during parallel work
    if SCRAPLI_DEBUG_LOG:
        enable_basic_logging(file="scrapli.log", level="debug")
    else:
        enable_basic_logging(file=False)

    if namespace.devices is None:
        print("Path to file with list of devices required! Key: -d <path>")
        sys.exit()

    if namespace.commands is not None:
        print("          ... path to file with commands specified")
        comm_file_path_specified = True

    if namespace.overwrite:
        print("          ... files will be overwritten - you'll find just last result in OUTPUT_FOLDER")
        overwrite = True
    else:
        print("          ... each set of files will be saved in separate folder - see the dates on folder names in OUTPUT_FOLDER folder and get a warning for disk space")
        overwrite = False

    if namespace.backup_configs == 0:
        print("          ... configuration backups not activated")
        save_backups = False
        days_backups = 0
    elif namespace.backup_configs > 0:
        print("          ... config files will be collected and overwritten - you'll find result in BACKUP_CONFIG_FOLDER folder")
        save_backups = True
        days_backups = namespace.backup_configs
    else:
        print("          ... configuration backups not activated")
        save_backups = False
        days_backups = 0

    print("          ... parallel connections per phase: " + str(max_workers))

    start_time = datetime.now()
    date = str(start_time.date()) + "-" + str(start_time.strftime("%H-%M-%S"))

    if not WORKING_DIRECTORY:
        curr_path = os.path.abspath(os.getcwd())
    else:
        curr_path = WORKING_DIRECTORY

    if OUTPUT_FOLDER == '':
        print("          ... no output folder specified in .env file - result should be saved in \"output\" folder")
        os.chdir(curr_path)
        if not os.path.isdir("output"):
            os.mkdir("output")
        cnf_save_path = os.path.join(curr_path, 'output')
    else:
        cnf_save_path = OUTPUT_FOLDER
        if not os.path.isdir(cnf_save_path):
            os.mkdir(cnf_save_path)

    os.chdir(cnf_save_path)

    if not overwrite:
        os.mkdir("cnf_" + date)
        cnf_save_path = os.path.join(cnf_save_path,"cnf_" + date)
        os.chdir(cnf_save_path)

    if BACKUP_CONFIG_FOLDER == '':
        backups_save_path = os.path.join(curr_path, 'configs')
        if not os.path.isdir("configs"):
            os.mkdir("configs")
    else:
        backups_save_path = BACKUP_CONFIG_FOLDER
        if not os.path.isdir(backups_save_path):
            os.mkdir(backups_save_path)

    send_log(cnf_save_path, "============ Acquisition section =================")
    send_log(cnf_save_path, "Starting at "+date)
    send_log(cnf_save_path, "Config save folder is: " + str(cnf_save_path))

    # Get list of available device files
    devices, hostnames = get_devices_from_file(os.path.join(curr_path, namespace.devices), max_workers)

    send_log(cnf_save_path, str(len(devices)) + " devices loaded")

    # connect to devices - collecting data from devices in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for device in devices:
            if comm_file_path_specified:
                commands = get_commands_from_file(os.path.join(curr_path, namespace.commands))
            else:
                commands = get_commands_from_file(os.path.join(curr_path, platform_to_commands[device['platform']]))

            send_log(cnf_save_path, "============ Processing section =================")
            send_log(cnf_save_path, "Starting processing of device {}".format(device['host']))
            futures.append(executor.submit(collect_device_data, device, commands, overwrite, date, cnf_save_path, hostnames))
        for future in as_completed(futures):
            future.result()

    # collect configuration and save them into 'config' folder for backup
    if save_backups:
        if days_backups > 1:
            backups_save_path_now = os.path.join(backups_save_path, 'bckp_' + date)
            if not os.path.isdir(backups_save_path_now):
                os.mkdir(backups_save_path_now)
        else:
            backups_save_path_now = backups_save_path

        os.chdir(backups_save_path_now)
        for device in devices:
            send_log(cnf_save_path, "Starting collection config backups from {}".format(device['host']))

        # collecting backup configuration data data from devices doing in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(collect_device_backup, device, hostnames, backups_save_path_now) for device in devices]
            for future in as_completed(futures):
                future.result()

        delete_old_backups(backups_save_path, days_old=days_backups)

if __name__ == '__main__':
    start()