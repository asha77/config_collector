#!/usr/bin/env python3
from scrapli import Scrapli
from scrapli.exceptions import ScrapliException, ScrapliAuthenticationFailed, ScrapliConnectionNotOpened
from decouple import config, UndefinedValueError
import argparse
from datetime import datetime, timedelta
import os
import shutil
from scrapli.driver import GenericDriver
import time
import re
# import logging
from scrapli.logging import enable_basic_logging
from pathlib import Path


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
WORKING_DIRECTORY = config('WORKING_DIRECTORY')
BACKUP_CONFIG_FOLDER = config('BACKUP_CONFIG_FOLDER')
try:
    OUTPUT_FOLDER = config('OUTPUT_FOLDER')
except UndefinedValueError:
    print("          ... it seems that no \"OUTPUT_FOLDER\" parameter specified in .env file - using by-default values...")
    OUTPUT_FOLDER = 'output'


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

def sendlog(path, message):
    file_name = os.path.join(path, 'logfile.log')
    resfile = open(file_name, 'a', encoding='utf-8')
    resfile.write(str(datetime.now().strftime("%Y-%m-%d %H:%M:%S")) + " YAUCC  INFO: " + message + "\n")
    resfile.close()
    print(str(datetime.now()) + " YAUCC INFO: " + message.strip('\n'))
    return True


def saveoutfile(path, ip, message):
    file_name = os.path.join(path, ip)
    resfile = open(file_name, "a", encoding='utf-8')
    resfile.write(message)
    resfile.close()


def rewrite_out_file(path, ip, message):
    file_name = os.path.join(path, ip)
    resfile = open(file_name, "w", encoding='utf-8')
    resfile.write(message)
    resfile.close()


def create_parser():
    parser = argparse.ArgumentParser(prog='YAUCC - Yet Another Universal Config Collector', description='Python app for executing commands on network equipment using SSH', epilog='author: asha77@gmail.com')
    parser.add_argument('-d', '--devfile', dest="devices", required=True, help='Path to file with list of devices')
    parser.add_argument('-c', '--comfiles', dest="commands",  required=False, help='Path to file with Commands to be executed on devices (cancels autodetection of command set according to device platform)')
    parser.add_argument('-o', '--overwrite', required=False, action='store_true', help='Save and overwrite files into the same folder e.g. \"output\" folder')
    parser.add_argument('-b', '--backup_configs', nargs='?', type=int, const=1, default=0, required=False, help='Save config backup files into \"BACKUP_CONFIG_FOLDER\" once or days to keep backups')
    return parser


def obtain_model(vendor, configuration):
    """
    Extract model number
    """

    # cisco and arista a treated as the same - they are similar
    if vendor == 'cisco':
        match = re.search("Model\s+\wumber\s*:\s+(.*)", configuration)
        if match:
            return match.group(1).strip()
        else:
            match = re.search("\wisco\s+(\S+)\s+.*\s+(with)*\d+K/\d+K\sbytes\sof\smemory.", configuration)
            if match:
                return match.group(1).strip()
            else:
                match = re.search("\s+cisco Nexus9000 (.*) Chassis", configuration)
                if match:
                    return "N9K-"+match.group(1).strip()
                else:
                    match = re.search("ROM: Bootstrap program is Linux", configuration)
                    if match:
                        return "Cisco IOS vRouter "
                    else:
                        match = re.search("Arista vEOS", configuration)
                        if match:
                            return "Arista vEOS"
                        else:
                            match = re.search("Arista (\S+)", configuration)
                            if match:
                                return match.group(1).strip()
                            else:
                                return "Not_found"

    if vendor == 'huawei':
        match = re.search('(Quidway|HUAWEI)\s(\S+)\s+Routing\sSwitch\S*', configuration)
        if match:
            return 'Huawei ' +match.group(2).strip()
        else:
            match = re.search('HUAWEI\sCE(\S+)\s+uptime\S*', configuration)
            if match:
                return 'Huawei CE' + match.group(1).strip()
            else:
                match = re.search('Huawei\s(\S+)\s+Router\s\S*', configuration)
                if match:
                    return 'Huawei ' + match.group(1).strip()
                else:
                    return "Not_found"

    if vendor == 'aruba':
        match = re.search('Build\sID\s+: (\S-\S).*', configuration)
        if match:
            return match.group(1).strip()
        else:
            match = re.search('\s*Product\sSKU\s*:\s(\S*)', configuration)
            if match:
                return match.group(1).strip()
            else:
                return "Not_found"

    if vendor == 'edgecore':
        match = re.search('\s*HwSKU:\s(\S*)', configuration)
        if match:
            return match.group(1).strip()
        else:
            return "Not_found"

    if vendor == 'rdp':
        match = re.search('\s+product-name\s+(\S+)', configuration)
        if match:
            return match.group(1).strip()
        else:
            return "Not_found"

    return "Model_vendor_not_found"


def obtain_software_version(configuration, family):
    """
    Extract software version
    """

    if family == 'IOS XE':
        match = re.search("Cisco .+ Version ([0-9.()A-Za-z]+)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'IOS':
        match = re.search("Cisco .+ Version ([0-9.()A-Za-z]+)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'NX-OS':
        match = re.search("\s*NXOS: version (.*)", configuration)
        if match:
            return match.group(1).strip()
        else:
            match = re.search("\s*system:\s+version\s*(.*)", configuration)
            if match:
                return match.group(1).strip()
    elif family == 'EOS':
        match = re.search("Software image version: (.*)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'VRP':
        match = re.search("VRP \(R\) software, Version (.*)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'ARUBA AOS-S':
        match = re.search("\s*Software revision\s*:\s*(\S+)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'Edgecore SONIC':
        match = re.search("\s*SONiC Software Version:\s*(\S+)", configuration)
        if match:
            return match.group(1).strip()
    elif family == 'RDP EcoNPB':
        match = re.search("\s+serial-number\s(\S+)", configuration)
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
    elif re.search("\s*Software revision\s*:\s*(\S+)", configuration):
        return "ARUBA AOS-S"
    elif re.search("\s*SONiC Software Version:\s*(\S+)", configuration):
        return "Edgecore SONIC"
    elif re.search("\s*FlowBalancer\s*(\S+)", configuration):
        return "RDP EcoNPB"
    elif re.search("\s*SDNSwitch-packet-broker\s*(\S+)", configuration):
        return "RDP EcoNPB"
    elif re.search("\s*EcoNPB\s*(\S+)", configuration):
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
        # можно также присвоить значение по умолчанию вместо бросания исключения
        sendlog(cnf_save_path, "No suitable platform for device family {}".format(dev_family))
#        raise ValueError('Undefined unit: {}'.format(e.args[0]))
        platform = ""
    return platform


def get_devices_from_file(file):
    devices = []
    hostnames = []
    with open(file) as f:
        for line in f.readlines():
            if line.startswith('#'):
                continue

            if line == ['\n'] or line == [' \n'] or line == ['']:
                continue

            device_string = line.split(";")

            if len(device_string) < 2:
                print('Error - wrong devices file format')
                return [], []

            if len(device_string) > 2:
                if not "".join(device_string[2:3]) == "":
                    uname = "".join(device_string[2:3])
                else:
                    uname = AUTH_USERNAME
            else:
                uname = AUTH_USERNAME

            if len(device_string) > 3:
                if not "".join(device_string[3:4]) == "":
                    passw = "".join(device_string[3:4])
                else:
                    passw = AUTH_PASSWORD
            else:
                passw = AUTH_PASSWORD

            if len(device_string) > 4:
                if not "".join(device_string[4:5]) == "":
                    ena_pass = "".join(device_string[4:5])
                else:
                    ena_pass = AUTH_SECONDARY
            else:
                ena_pass = AUTH_SECONDARY

            vendor, show_ver, hname = get_show_version("".join(device_string[1:2]), uname, passw)

            if (show_ver == '') and (hname == ''):
                continue

            if __debug__:
                sendlog(cnf_save_path, "show version:\n " + show_ver)

            device_model = obtain_model(vendor, show_ver)

            if __debug__:
                sendlog(cnf_save_path, "Device model:\n " + device_model)

            device_family = obtain_software_family(show_ver)
            device_soft_ver = obtain_software_version(show_ver, device_family)

            if __debug__:
                sendlog(cnf_save_path, "Device software:\n " + device_soft_ver)
                sendlog(cnf_save_path, "Device family:\n " + device_family)

            if "".join(device_string[0:1]):
                device_platform = "".join(device_string[0:1])
            else:
                device_platform = assign_platform(device_family)

            if device_platform:
                sendlog(cnf_save_path, "IP: " + "".join(device_string[1:2]) + ". Device model is: " + device_model + ". Software version is: " + device_soft_ver + ". Selected platform: " + device_platform)
            else:
                sendlog(cnf_save_path, "IP: " + "".join(device_string[1:2]) + " Device and platform not recognized.")
                if show_ver:
                    saveoutfile(cnf_save_path, str(device_string[1:2]) + '_' + hname + '.log', "\n" + show_ver)
                continue

            if __debug__:
                chlog = True
            else:
                chlog = False

            dev = {
                'platform': device_platform,
                'host': "".join(device_string[1:2]),
                'auth_username': uname,
                'auth_password': passw,
                'auth_secondary': ena_pass,
                'channel_log': chlog,
                "auth_strict_key": AUTH_STRICT_KEY,
                "ssh_config_file": True,
                "transport": TRANSPORT,
                "timeout_socket": int(TIMEOUT_SOCKET),          # timeout for establishing socket/initial connection in seconds
                "timeout_transport": int(TIMEOUT_TRANSPORT)    # timeout for ssh|telnet transport in seconds
            }
            devices.append(dev)

            hn = {
                "hostname": hname,
                "ip": "".join(device_string[1:2])
            }

            hostnames.append(hn)

    return devices, hostnames


def get_commands_from_file(file):
    commands = []
    with open(file) as f:
        for line in f.readlines():
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
        "transport": "ssh2"
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
                sendlog(cnf_save_path, "IP: " + ip + " INFO " + "Response: " + response.result)

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

    except ScrapliAuthenticationFailed as error:
        sendlog(cnf_save_path, "IP: " + ip + " Authentication Error " +str(error) + " - please, check username, password and driver.")
        return '', '', ''
    except ScrapliConnectionNotOpened as error:
        sendlog(cnf_save_path, "IP: " + ip + " Connection Error " +str(error) + " - please, check device exist or online.")
        return '', '', ''
    except ScrapliException as error:
        sendlog(cnf_save_path, "IP: " + ip + " Scrapli Error " + str(error))
        if hasattr(response, 'result'):
            if (not response.result == '') and (not hname == ''):
                return vendor, response.result, strip_characters_from_prompt(hname)
            else:
                return '', '', ''
        else:
            return '', '', ''
    finally:
        if hasattr(response, 'result'):
            if (not response.result == '') and (not hname == ''):
                return vendor, response.result, strip_characters_from_prompt(hname)
            else:
                return '', '', ''
        else:
            return '', '', ''


def output_filter(reply):
    """
    Output data obfuscation and filtering:
    radius-server key XXXX
    snmp-server community XXX RX
    tacacs server <server>
        key 6 ХХХ
    """

    lines = reply.split('\n')
    lines_out = []

    for line in lines:
        match = re.search("radius-server key (.*)", line)
        if match:
            lines_out.append("radius-server key ХХХ")
        else:
            match = re.search("snmp-server community (.*) RO", line)
            if match:
                lines_out.append("snmp-server community XXX RO")
            else:
                match = re.search("snmp-server community (.*) RW", line)
                if match:
                    lines_out = "snmp-server community XXX RW"
                else:
                    match = re.search("\skey (\d) (.*)", line)
                    if match:
                        lines_out.append(" key " + match.group(1).strip() + " XXX")
                    else:
                        match = re.search("username (\w+) privilege (\d+) password (.*)", line)
                        if match:
                            lines_out.append("username XXX privilege " + match.group(2).strip() + " password XXX")
                        else:
                            match = re.search("enable secret (\d) (.*)", line)
                            if match:
                                lines_out.append("enable secret " + match.group(1).strip() + " XXX")
                            else:
                                match = re.search("radius server shared-key(.*)", line)
                                if match:
                                    lines_out.append("radius server shared-key cipher XXX")
                                else:
                                    match = re.search("\s*local-user(.*)", line)
                                    if match:
                                        lines_out.append(" local-user XXX")
                                    else:
                                        match = re.search("\s*ospf authentication(.*)", line)
                                        if match:
                                            lines_out.append(" ospf authentication XXX")
                                        else:
                                            match = re.search("\s*(.*)\scipher(.*)", line)
                                            if match:
                                                lines_out.append(' ' +  match.group(1).strip() + ' cipher XXX')
                                            else:
                                                match = re.search("\s*pre-shared-key(.*)", line)
                                                if match:
                                                    lines_out.append(" pre-shared-key XXX")
                                                else:
                                                    match = re.search("\s*ssh user\s(\w+)(.*)", line)
                                                    if match:
                                                        lines_out.append(" ssh user XXX " + match.group(2).strip())
                                                    else:
                                                        matched = False
                                                        for error_regexp in edgecore_excluded_errors:
                                                            match = re.search(error_regexp, line)
                                                            if match:
                                                                matched = True
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
            if not match:
                if not line == '':
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
            except Exception as e:
                print(f"Error during file deletion {item}: {e}")


def start():
    global curr_path
    global cnf_save_path

    parser = create_parser()
    namespace = parser.parse_args()
    comm_file_path_specified = False

    if __debug__:
        # enable_basic_logging(file=True, level="debug")
        enable_basic_logging(file="scrapli.log", level="debug")
#        logging.basicConfig(file=True, filename="scrapli.log", level=logging.DEBUG)
    else:
        enable_basic_logging(file=False)
 #       logging.basicConfig(file=False, filename="scrapli.log", level=logging.INFO)

    if namespace.devices is None:
        print("Path to file with list of devices required! Key: -d <path>")
        exit()

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
        os.mkdir("cnf_"+date)
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

    sendlog(cnf_save_path, "============ Acquisition section =================")
    sendlog(cnf_save_path, "Starting at "+date)
    sendlog(cnf_save_path, "Config save folder is: " + str(cnf_save_path))

    # Get list of available device files
    devices, hostnames = get_devices_from_file(os.path.join(curr_path, namespace.devices))

    sendlog(cnf_save_path, str(len(devices)) + " devices loaded")
#    sendlog(cnf_save_path, str(len(commands)) + " commands loaded")

    # connect to devices
    for device in devices:
        dev_start_time = datetime.now()

        if comm_file_path_specified:
            commands = get_commands_from_file(os.path.join(curr_path, namespace.commands))
        else:
            commands = get_commands_from_file(os.path.join(curr_path, platform_to_commands[device['platform']]))

        sendlog(cnf_save_path, "============ Processing section =================")
        sendlog(cnf_save_path, "Starting processing of device {}".format(device['host']))
        try:
            with Scrapli(**device, timeout_ops=180) as ssh:

                if overwrite:
                    rewrite_out_file(cnf_save_path, device['host'] + "_" + get_hostname_by_ip(device['host'], hostnames) + '.log', "Data collected: " + date + "\n")

                for command in commands:
                    if __debug__:
                        sendlog(cnf_save_path, device['host'] + " send command: " + command)

                    time.sleep(0.2)
                    reply = ssh.send_command(command, timeout_ops=30)
                    time.sleep(0.8)

                    if __debug__:
                        sendlog(cnf_save_path, reply.result[0:30].replace('\n', ' '))

                    if reply.result:
                        filtered_result = output_filter(reply.result)

                        if __debug__:
                            ln = len(filtered_result)
                            if ln > 20:
                                ln = 20
                                sendlog(cnf_save_path, device['host'] + " elapsed time: " + str(reply.elapsed_time) + ' received: ' + filtered_result[0:ln-1].replace('\n', ' ') + ' ...')

                        saveoutfile(cnf_save_path, device['host'] + "_" + get_hostname_by_ip(device['host'], hostnames) + '.log', "\n" + "# " + command +"\n" + filtered_result + "\n")
                    else:
                        sendlog(cnf_save_path, device['host'] + " elapsed time: " + str(reply.elapsed_time) + ' send: ' + command + ' - nothing received!')
        except ScrapliException as error:
            print(error)
        sendlog(cnf_save_path, "Device {} processed in {}".format(device['host'], datetime.now() - dev_start_time))

    # collect configuration and save them into 'config' folder for backup
    if save_backups:
        if days_backups > 1:
            backups_save_path = os.path.join(backups_save_path, 'bckp_' + date)
            if not os.path.isdir(backups_save_path):
                os.mkdir(backups_save_path)

        os.chdir(backups_save_path)
        for device in devices:
            sendlog(cnf_save_path, "Starting collection config backups from {}".format(device['host']))
            try:
                with Scrapli(**device, timeout_ops=180) as ssh:
                    if device['platform'] == 'edgecore_sonic':
                        time.sleep(0.2)
                        reply = ssh.send_command('show runningconfiguration all')
                        time.sleep(0.2)

                        if __debug__:
                            sendlog(cnf_save_path, reply.result[0:30].replace('\n', ' '))

                        rewrite_out_file(backups_save_path, get_hostname_by_ip(device['host'], hostnames) + '_config_db.json', output_config_files_filter(reply.result))

                        time.sleep(0.2)
                        reply = ssh.send_command('show runningconfiguration bgp')
                        time.sleep(0.2)

                        if __debug__:
                            sendlog(cnf_save_path, reply.result[0:30].replace('\n', ' '))

                        rewrite_out_file(backups_save_path, get_hostname_by_ip(device['host'], hostnames) + '_frr.conf', output_config_files_filter(reply.result))
                    elif device['platform'] == 'huawei_vrp':
                        time.sleep(0.2)
                        reply = ssh.send_command('display current-configuration')
                        time.sleep(0.2)
                        if __debug__:
                            sendlog(cnf_save_path, reply.result[0:30].replace('\n', ' '))
                        rewrite_out_file(backups_save_path, get_hostname_by_ip(device['host'], hostnames) + '_config.txt', output_config_files_filter(reply.result))
                    elif device['platform'] == 'rdp_econpb':
                        time.sleep(0.2)
                        reply = ssh.send_command('show | view set')
                        time.sleep(0.2)
                        if __debug__:
                            sendlog(cnf_save_path, reply.result[0:30].replace('\n', ' '))

                        rewrite_out_file(backups_save_path, 'RDP_' + device['host'] + '_config.txt', output_config_files_filter(reply.result))
            except ScrapliException as error:
                print(error)

        delete_old_backups(".", days_old=days_backups)

if __name__ == '__main__':
    start()
