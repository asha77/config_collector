from scrapli import Scrapli
from scrapli.exceptions import ScrapliException
from decouple import config
import argparse
from datetime import datetime
import os

AUTH_USERNAME = config('AUTH_USERNAME')
AUTH_PASSWORD = config('AUTH_PASSWORD')
AUTH_SECONDARY = config('AUTH_SECONDARY')
if (config('AUTH_STRICT_KEY') == "True"):
    AUTH_STRICT_KEY = True
else:
    AUTH_STRICT_KEY = False
TRANSPORT = config('TRANSPORT')
TIMEOUT_SOCKET = config('TIMEOUT_SOCKET')
TIMEOUT_TRANSPORT = config('TIMEOUT_TRANSPORT')
WORKING_DIRECTORY = config('WORKING_DIRECTORY')


def sendlog(path, message):
    file_name = os.path.join(path, 'logfile.log')
    resfile = open(file_name, "a")
    resfile.write(str(datetime.now().strftime("%Y-%m-%d %H:%M:%S")) + " devbot  INFO: "+ message + "\n")
    resfile.close()
    print(str(datetime.now()) + " devbot INFO: "+ message)


def saveoutfile(path, ip, message):
    file_name = os.path.join(path, ip+'.log')
    resfile = open(file_name, "a")
    resfile.write(message)
    resfile.close()


def createparser():
    parser = argparse.ArgumentParser(prog='YAUCC - Yet Another Universal Config Collector', description='Python app for executing commands on network equipment using SSH', epilog='author: asha77@gmail.com')
    parser.add_argument('-d', '--devfile', required=True, help='Specify file with set of devices')
    parser.add_argument('-c', '--comfiles', required=True, help='Specify file with set of commands')
    return parser


def get_devices_from_file(file):
    devices = []
    with open(file) as f:
        for line in f.readlines():
            str = line.split(";")

            if str[2] == "":
                uname = AUTH_USERNAME
            else:
                uname = str[2]

            if str[3] == "":
                passw = AUTH_PASSWORD
            else:
                passw = str[3]

            if str[4] == "":
                ena_pass = AUTH_SECONDARY
            else:
                ena_pass = str[4]

            dev = {
                'platform': str[0],
                'host': str[1],
                'auth_username': uname,
                'auth_password': passw,
                'auth_secondary': ena_pass,
                "auth_strict_key": AUTH_STRICT_KEY,
                "transport": TRANSPORT,
                "timeout_socket": int(TIMEOUT_SOCKET),  # timeout for establishing socket/initial connection in seconds
                "timeout_transport": int(TIMEOUT_TRANSPORT)  # timeout for ssh|telnet transport in seconds
            }
            devices.append(dev)
    return(devices)


def get_commands_from_file(file):
    commands = []
    with open(file) as f:
        for line in f.readlines():
            commands.append(line)
    return(commands)


def send_show(device, show_command):
    try:
        with Scrapli(**device) as ssh:
            reply = ssh.send_command(show_command)
            return reply.result
    except ScrapliException as error:
        print(error)
#        sendlog(cnf_save_path, str(error) + " " + device["host"])


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

    sendlog(curr_path, "Starting at "+date)

    os.chdir(curr_path)

    if not os.path.isdir("output"):
        os.mkdir("output")

    cnf_save_path = os.path.join(curr_path,'output')
    os.chdir(cnf_save_path)
    os.mkdir("cnf_"+date)
    cnf_save_path = os.path.join(cnf_save_path,"cnf_"+date)
    os.chdir(cnf_save_path)

    sendlog(curr_path, "Config save folder is is: " + str(cnf_save_path))

    # Get list of available device files
    devices = get_devices_from_file(os.path.join(curr_path, namespace.devfile))

    # Get list of available device files
    commands = get_commands_from_file(os.path.join(curr_path, namespace.comfiles))

    sendlog(curr_path, str(len(devices)) + " devices loaded")
    sendlog(curr_path, str(len(commands)) + " commands loaded")

    # connect to devices
    for device in devices:
        devStartTime = datetime.now()
        for command in commands:
            res = send_show(device, command)
            saveoutfile(cnf_save_path, device['host'], "\n" + "# " + command + res+"\n")

        sendlog(curr_path, "Device {} processed in {}".format(device['host'], datetime.now() - devStartTime))


if __name__ == '__main__':
    start()
