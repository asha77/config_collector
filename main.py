from scrapli import Scrapli
from scrapli.exceptions import ScrapliException, ScrapliAuthenticationFailed, ScrapliConnectionNotOpened
from decouple import config
import argparse
from datetime import datetime
import os
from scrapli.driver import GenericDriver
import re
import logging
import time


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

family_to_platform = {
    'IOS': 'cisco_iosxe',
    'IOS XE': 'cisco_iosxe',
    'Nexus': 'cisco_nxos',
    'IOS XR': 'cisco_iosxr',
    'JUNOS': 'juniper_junos',
    'EOS': 'arista_eos',
    'VRP': 'huawei_vrp'
}

if __debug__:
    logging.basicConfig(filename="scrapli.log", level=logging.DEBUG)
else:
    logging.basicConfig(filename="scrapli.log", level=logging.INFO)


def sendlog(path, message):
    file_name = os.path.join(path, 'logfile.log')
    resfile = open(file_name, 'a', encoding='utf-8')
    resfile.write(str(datetime.now().strftime("%Y-%m-%d %H:%M:%S")) + " YAUCC  INFO: " + message + "\n")
    resfile.close()
    print(str(datetime.now()) + " YAUCC INFO: " + message)


def saveoutfile(path, ip, message):
    file_name = os.path.join(path, ip+'.log')
    resfile = open(file_name, "a", encoding='utf-8')
    resfile.write(message)
    resfile.close()


def createparser():
    parser = argparse.ArgumentParser(prog='YAUCC - Yet Another Universal Config Collector', description='Python app for executing commands on network equipment using SSH', epilog='author: asha77@gmail.com')
    parser.add_argument('-d', '--devfile', required=True, help='Specify file with set of devices')
    parser.add_argument('-c', '--comfiles', required=True, help='Specify file with set of commands')
    return parser


def obtain_model(config):
    '''
    Extract model number
    '''
    match = re.search("Model\s+\wumber\s*:\s+(.*)", config)
    if match:
        return match.group(1).strip()
    else:
        match = re.search("\wisco\s+(\S+)\s+.*\s+(with)*\d+K\/\d+K\sbytes\sof\smemory.", config)
        if match:
            return match.group(1).strip()
        else:
            match = re.search("\s+cisco Nexus9000 (.*) Chassis", config)
            if match:
                return "N9K-"+match.group(1).strip()
            else:
                match = re.search("Arista vEOS", config)
                if match:
                    return "Arista vEOS"
                else:
                    match = re.search("ROM: Bootstrap program is Linux", config)
                    if match:
                        return "Cisco IOS vRouter "
                    else:
                        match = re.search('(Quidway|HUAWEI)\s(\S+)\s+Routing\sSwitch\S*', config)
                        if match:
                            return 'Huawei ' +match.group(2).strip()
                        else:
                            match = re.search('HUAWEI\sCE(\S+)\s+uptime\S*', config)
                            if match:
                                return 'Huawei CE' + match.group(1).strip()
                            else:
                                match = re.search('Huawei\s(\S+)\s+Router\s\S*', config)
                                if match:
                                    return 'Huawei ' + match.group(1).strip()
                                else:
                                    match = re.search('Build\sID\s+: (\S-\S).*', config)
                                    if match:
                                        return match.group(1).strip()
                                    else:
                                        return "Not Found"


def obtain_software_version(config, family):
    '''
    Extract software version
    '''

    if family == 'IOS XE':
        match = re.search("Cisco .+ Version ([0-9.()A-Za-z]+)", config)
        if match:
            return match.group(1).strip()
    elif family == 'IOS':
        match = re.search("Cisco .+ Version ([0-9.()A-Za-z]+)", config)
        if match:
            return match.group(1).strip()
    elif family == 'NX-OS':
        match = re.search("\s*NXOS: version (.*)", config)
        if match:
            return match.group(1).strip()
    elif family == 'EOS':
        match = re.search("Software image version: (.*)", config)
        if match:
            return match.group(1).strip()
    elif family == 'VRP':
        match = re.search("VRP \(R\) software, Version (.*)", config)
        if match:
            return match.group(1).strip()
    elif family == 'ARUBAOS':
        match = re.search("Version\s+:\s(.*)", config)
        if match:
            return match.group(1).strip()
    else:
        return "Not Found"


def obtain_software_family(config):
    '''
    Extract software family from show version
    '''
    match = re.search("Cisco IOS.XE .oftware", config)
    if match:
        return "IOS XE"
    else:
        match = re.search("Cisco Nexus Operating System", config)
        if match:
            return "NX-OS"
        else:
            match = re.search("Cisco IOS Software", config)
            if match:
                return "IOS"
            else:
                match = re.search("Arista", config)
                if match:
                    return "EOS"
                else:
                    match = re.search("Huawei Versatile Routing Platform", config)
                    if match:
                        return "VRP"
                    else:
                        match = re.search("ArubaOS", config)
                        if match:
                            return "ARUBAOS"
                        else:
                            return "Not Found"


def obtain_hostname(config):
    '''
    Extract device hostname
    '''

    match = re.search("hostname (.*)", config)
    if match:
        return match.group(1).strip()
    else:
        return "Not Found"


def assign_platform(dev_family):
    '''
    Assign device platform based on device family
    '''

    try:
        platform = family_to_platform[dev_family]
    except KeyError as error:
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
            str = line.split(";")

            if len(str) < 2:
                print('Error - wrong devices file format')
                return [], []

            if len(str) > 2:
                if not str[2] == "":
                    uname = str[2]
                else:
                    uname = AUTH_USERNAME
            else:
                uname = AUTH_USERNAME

            if len(str) > 3:
                if not str[3] == "":
                    passw = str[3]
                else:
                    passw = AUTH_PASSWORD
            else:
                passw = AUTH_PASSWORD

            if len(str) > 4:
                if not str[4] == "":
                    ena_pass = str[4]
                else:
                    ena_pass = AUTH_SECONDARY
            else:
                ena_pass = AUTH_SECONDARY

            showver, hname = get_show_version(str[1], uname, passw)

            if((showver == '') and (hname == '')):
                continue

            if __debug__:
                sendlog(cnf_save_path, "show version:\n " + showver)

            device_model = obtain_model(showver)

            if __debug__:
                sendlog(cnf_save_path, "Device model:\n " + device_model)

            device_family = obtain_software_family(showver)
            device_soft_ver = obtain_software_version(showver, device_family)

            if __debug__:
                sendlog(cnf_save_path, "Device software:\n " + device_soft_ver)

            if __debug__:
                sendlog(cnf_save_path, "Device family:\n " + device_family)

            if str[0]:
                device_platform = str[0]
            else:
                device_platform = assign_platform(device_family)

            if device_platform:
                sendlog(cnf_save_path, "IP: " + str[1] + ". Device model is: " + device_model + ". Software version is: " + device_soft_ver + ". Selected platform: " + device_platform)
            else:
                sendlog(cnf_save_path, "IP: " + str[1] + " Device and platform not recognized.")
                if showver:
                    saveoutfile(cnf_save_path, str[1] + "_" + get_hostname_by_ip(str[1]), "\n" + showver)
                continue

            dev = {
                'platform': device_platform,
                'host': str[1],
                'auth_username': uname,
                'auth_password': passw,
                'auth_secondary': ena_pass,
                "auth_strict_key": AUTH_STRICT_KEY,
                "transport": TRANSPORT,
                "timeout_socket": int(TIMEOUT_SOCKET),          # timeout for establishing socket/initial connection in seconds
                "timeout_transport": int(TIMEOUT_TRANSPORT)    # timeout for ssh|telnet transport in seconds
            }
            devices.append(dev)

            hn = {
                "hostname": hname,
                "ip": str[1]
            }

            hostnames.append(hn)

    return(devices, hostnames)


def get_commands_from_file(file):
    commands = []
    with open(file) as f:
        for line in f.readlines():
            commands.append(line)
    return(commands)

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
    return prompt


def get_show_version(ip, login, passw):
    my_device = {
        "host": ip,
        "auth_username": login,
        "auth_password": passw,
        "auth_strict_key": False,
        "transport": "ssh2"
    }

    vendor = 'cisco'
    hname = ''
    response = ''

    try:
        with GenericDriver(**my_device) as conn:
            time.sleep(0.1)
            hname = conn.get_prompt()
            time.sleep(0.1)

            response = conn.send_command("terminal length 0", strip_prompt = False)
            time.sleep(0.1)

            # if '% Invalid input detected' in response1:  Cisco error string

            # if not Cisco and we get error try Huawei
            if 'Error: Unrecog' in response.result:
                response = conn.send_command("screen-length 0 temporary", strip_prompt=False)
                time.sleep(0.5)
                vendor = 'huawei'

            if 'Invalid input: 0' in response.result:
                response = conn.send_command("no page", strip_prompt=False)
                time.sleep(0.5)
                vendor = 'aruba'


            if __debug__:
                sendlog(cnf_save_path, "IP: " + ip + " INFO " + "Response: " + response.result)

            if vendor == 'cisco':
                response = conn.send_command("show version", strip_prompt = False)
                time.sleep(0.5)
            elif vendor == 'huawei':
                response = conn.send_command("display version", strip_prompt = False)
                time.sleep(0.5)
            elif vendor == 'aruba':
                response = conn.send_command("show system", strip_prompt = False)
                time.sleep(0.5)

    except ScrapliAuthenticationFailed as error:
        sendlog(cnf_save_path, "IP: " + ip + " Authentification Error " +str(error) + " - please, check username, password and driver.")
        return '', ''
    except ScrapliConnectionNotOpened as error:
        sendlog(cnf_save_path, "IP: " + ip + " Connection Error " +str(error) + " - please, check device exist or online.")
        return '', ''
    except ScrapliException as error:
        sendlog(cnf_save_path, "IP: " + ip + " Scrapli Error " + str(error))
        if hasattr(response, 'result'):
            if ((not response.result == '') and (not hname == '')):
                return response.result, strip_characters_from_prompt(hname)
            else:
                return '', ''
        else:
            return '', ''
    finally:
        if hasattr(response, 'result'):
            if ((not response.result == '') and (not hname == '')):
                return response.result, strip_characters_from_prompt(hname)
            else:
                return '', ''
        else:
            return '', ''


def get_show_run(ip, login, passw):
    my_device = {
        "host": ip,
        "auth_username": login,
        "auth_password": passw,
        "auth_strict_key": False,
        "transport": "ssh2",
    }

    try:
        with GenericDriver(**my_device) as conn:
            time.sleep(0.1)
            response1 = conn.send_command("terminal length 0", False)
            if __debug__:
                sendlog(cnf_save_path, "IP: " + ip + " INFO " + "Response: " + response1.result)
            time.sleep(0.1)
            response = conn.send_command("show running-config", False)
            time.sleep(0.1)
    except ScrapliAuthenticationFailed as error:
        sendlog(cnf_save_path, "IP: " + ip + " Authentification Error " +str(error) + " - please, check username, password and driver.")
        return ""
    except ScrapliException as error:
        sendlog(cnf_save_path, "IP: " + ip + " Scrapli Error " + str(error))
        return ""
    return response.result


def output_filter(input):
    '''
    Output data obfuscation:
    radius-server key XXXX
    snmp-server community XXX RX
    tacacs server server
        key 6 ХХХ
    '''

    lines = []
    lines = input.split('\n')

    for i in range(len(lines)):
        match = re.search("radius-server key (.*)", lines[i])
        if match:
            lines[i] = "radius-server key ХХХ"
        else:
            match = re.search("snmp-server community (.*) RO", lines[i])
            if match:
                lines[i] = "snmp-server community XXX RO"
            else:
                match = re.search("snmp-server community (.*) RW", lines[i])
                if match:
                    lines[i] = "snmp-server community XXX RW"
                else:
                    match = re.search("\skey (\d) (.*)", lines[i])
                    if match:
                        lines[i] = " key " + match.group(1).strip() + " XXX"
                    else:
                        match = re.search("username (\w+) privilege (\d+) password (.*)", lines[i])
                        if match:
                            lines[i] = "username XXX priviledge " + match.group(2).strip() + " password XXX"
                        else:
                            match = re.search("enable secret (\d) (.*)", lines[i])
                            if match:
                                lines[i] = "enable secret " + match.group(1).strip() + " XXX"
                            else:
                                match = re.search("radius server shared-key(.*)", lines[i])
                                if match:
                                    lines[i] = "radius server shared-key cipher XXX"
                                else:
                                    match = re.search("\s*local-user(.*)", lines[i])
                                    if match:
                                        lines[i] = " local-user XXX"
                                    else:
                                        match = re.search("\s*ospf authentication(.*)", lines[i])
                                        if match:
                                            lines[i] = " ospf authentication XXX"
                                        else:
                                            match = re.search("\s*(.*)\scipher(.*)", lines[i])
                                            if match:
                                                lines[i] = ' ' +  match.group(1).strip() + ' cipher XXX'
                                            else:
                                                match = re.search("\s*pre-shared-key(.*)", lines[i])
                                                if match:
                                                    lines[i] = " pre-shared-key XXX"
                                                else:
                                                    match = re.search("\s*ssh user\s(\w+)(.*)", lines[i])
                                                    if match:
                                                        lines[i] = " ssh user XXX " + match.group(2).strip()
    return '\n'.join(map(str, lines))


def get_hostname_by_ip(ip, hostnames):
    for record in hostnames:
        if record["ip"] == ip:
            return record["hostname"]


def start():
    parser = createparser()
    namespace = parser.parse_args()
    global curr_path
    global cnf_save_path

    if (namespace.devfile is None):
        print("Path to file with list of devices required! Key: -d <path>")
        exit()

    if (namespace.comfiles is None):
        print("Path to file with list of commands required! Key: -с <path>")
        exit()

    startTime = datetime.now()

    date = str(startTime.date()) + "-" + str(startTime.strftime("%H-%M-%S"))

    if not WORKING_DIRECTORY:
        curr_path = os.path.abspath(os.getcwd())
    else:
        curr_path = WORKING_DIRECTORY

    os.chdir(curr_path)
    if not os.path.isdir("output"):
        os.mkdir("output")

    cnf_save_path = os.path.join(curr_path,'output')
    os.chdir(cnf_save_path)
    os.mkdir("cnf_"+date)
    cnf_save_path = os.path.join(cnf_save_path,"cnf_"+date)
    os.chdir(cnf_save_path)

    sendlog(cnf_save_path, "Starting at "+date)
    sendlog(cnf_save_path, "Config save folder is: " + str(cnf_save_path))

    # Get list of available device files
    devices, hostnames = get_devices_from_file(os.path.join(curr_path, namespace.devfile))
    # Get list of available device files
    commands = get_commands_from_file(os.path.join(curr_path, namespace.comfiles))
    sendlog(cnf_save_path, str(len(devices)) + " devices loaded")
    sendlog(cnf_save_path, str(len(commands)) + " commands loaded")

    # connect to devices
    for device in devices:
        devStartTime = datetime.now()
        sendlog(cnf_save_path, "Starting processing of device {}".format(device['host']))
        try:
            with Scrapli(**device, timeout_ops=180) as ssh:
                time.sleep(1)
                for command in commands:
                    reply = ssh.send_command(command)
                    time.sleep(1)
                    filtered_result = output_filter(reply.result)
                    saveoutfile(cnf_save_path, device['host'] + "_" + get_hostname_by_ip(device['host'], hostnames), "\n" + "# " + command +"\n" + filtered_result + "\n")
        except ScrapliException as error:
            print(error)
        sendlog(cnf_save_path, "Device {} processed in {}".format(device['host'], datetime.now() - devStartTime))


if __name__ == '__main__':
    start()
